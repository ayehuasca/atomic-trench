"""
Real-time wallet watcher for copy-trade.

Uses Helius WebSocket logsSubscribe with `mentions` filter to detect
watched wallet activity at processed commitment (~200ms latency).
"""

import json
import time
from dataclasses import dataclass
from queue import Queue
from threading import Thread
from typing import Any
from urllib.parse import urlparse, urlunparse

from websocket import WebSocket, WebSocketTimeoutException, create_connection

PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
METEORA_DLMM_PROGRAM = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
WSOL_ADDRESS = "So11111111111111111111111111111111111111112"
STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


@dataclass(frozen=True)
class CopyTradeSignal:
    buyer: str
    mint: str
    buy_sol: float
    buy_usd: float
    received: float
    signature: str
    slot: int


def _parse_log_notification(payload: dict[str, Any]) -> dict | None:
    if payload.get("method") != "logsNotification":
        return None
    result = payload.get("params", {}).get("result", {})
    value = result.get("value", {})
    signature = value.get("signature")
    slot = result.get("context", {}).get("slot")
    if not signature or slot is None:
        return None
    return {
        "signature": str(signature),
        "slot": int(slot),
        "error": value.get("err"),
        "logs": [str(line) for line in (value.get("logs") or [])],
    }


def _ws_url(rpc_url: str) -> str:
    parsed = urlparse(rpc_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _parse_transaction_notification(payload: dict[str, Any]) -> dict | None:
    """Parse a Helius transactionSubscribe notification."""
    if payload.get("method") != "transactionNotification":
        return None
    result = payload.get("params", {}).get("result", {})
    value = result.get("value", {})
    signature = value.get("signature")
    slot = result.get("context", {}).get("slot")
    if not signature or slot is None:
        return None
    tx_data = value.get("transaction", {})
    return {
        "signature": str(signature),
        "slot": int(slot),
        "error": tx_data.get("meta", {}).get("err"),
        "meta": tx_data.get("meta", {}),
        "message": tx_data.get("transaction", {}).get("message", {}),
        "logs": [str(line) for line in (tx_data.get("meta", {}).get("logMessages") or [])],
    }


class CopyTradeWatcher:
    """Watches specific wallet addresses via WebSocket and emits buy signals."""

    def __init__(
        self,
        rpc_url: str,
        watched_wallets: list[str],
        signal_queue: Queue,
        *,
        minimum_buy_usd: float = 200,
        check_interval: float = 0.5,
    ):
        self.rpc_url = rpc_url
        self.watched_wallets = watched_wallets
        self.signal_queue = signal_queue
        self.minimum_buy_usd = minimum_buy_usd
        self.check_interval = check_interval
        self._running = False
        self._thread: Thread | None = None
        self._seen_signatures: set[str] = set()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        while self._running:
            try:
                self._connect_and_listen()
            except Exception as e:
                print(f"  [copytrade] watcher error: {e}")
            time.sleep(3)

    def _connect_and_listen(self) -> None:
        ws_url = _ws_url(self.rpc_url)
        ws = create_connection(ws_url, timeout=15)
        try:
            # Helius transactionSubscribe supports all watched wallets in one request.
            ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "transactionSubscribe",
                "params": [
                    {
                        "accountInclude": self.watched_wallets,
                        "failed": False,
                        "vote": False,
                    },
                    {
                        "commitment": "processed",
                        "encoding": "jsonParsed",
                        "transactionDetails": "full",
                        "showRewards": False,
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            }))
            confirmation = json.loads(ws.recv())
            if confirmation.get("error") or "result" not in confirmation:
                raise RuntimeError(f"subscription failed: {confirmation}")
            print(f"  [copytrade] WebSocket watching {len(self.watched_wallets)} wallet(s)")
            ws.settimeout(1.0)

            while self._running:
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue
                payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                event = _parse_transaction_notification(payload)
                if event is None:
                    continue
                sig = event["signature"]
                if sig in self._seen_signatures:
                    continue
                self._seen_signatures.add(sig)
                if len(self._seen_signatures) > 10000:
                    self._seen_signatures.clear()

                if event.get("error"):
                    continue
                has_venue = any(
                    PUMP_AMM_PROGRAM in line or METEORA_DLMM_PROGRAM in line
                    for line in event["logs"]
                )
                if not has_venue:
                    continue

                # Full transaction is already present; avoid blocking REST fetch.
                meta = event["meta"]
                msg = event["message"]
                account_keys = [
                    str(k.get("pubkey") if isinstance(k, dict) else k)
                    for k in msg.get("accountKeys", [])
                ]

                # Build token deltas
                deltas: dict[str, dict[str, float]] = {}
                for field, sign in (("preTokenBalances", -1.0), ("postTokenBalances", 1.0)):
                    for bal in meta.get(field, []):
                        owner = str(bal.get("owner", ""))
                        if not owner:
                            continue
                        amt = bal["uiTokenAmount"]
                        val = int(amt["amount"]) / (10 ** int(amt["decimals"]))
                        d = deltas.setdefault(owner, {})
                        d[str(bal["mint"])] = d.get(str(bal["mint"]), 0) + sign * val

                native_delta = {
                    k: (int(meta["postBalances"][i]) - int(meta["preBalances"][i])) / 1e9
                    for i, k in enumerate(account_keys)
                }

                block_time = tx.get("blockTime") or 0
                current_time = time.time()
                # Skip if more than 5 seconds old (stale)
                if block_time and current_time - block_time > 5:
                    continue

                # Check each watched wallet for buys
                for buyer in self.watched_wallets:
                    if buyer not in deltas:
                        continue
                    mint_deltas = deltas[buyer]
                    for mint, received in mint_deltas.items():
                        if received <= 0:
                            continue
                        # Skip stable/WSOL
                        if mint in STABLE_MINTS or mint == WSOL_ADDRESS:
                            continue
                        # Check if any venue has activity
                        if not any(PUMP_AMM_PROGRAM in str(ix) or METEORA_DLMM_PROGRAM in str(ix)
                                   for ix in msg.get("instructions", [])):
                            continue
                        token_quote_sol = max(0, -mint_deltas.get(WSOL_ADDRESS, 0))
                        native_quote_sol = max(0, -native_delta.get(buyer, 0))
                        buy_sol = max(token_quote_sol, native_quote_sol)
                        buy_usd = buy_sol * 74  # approximate
                        if buy_usd < self.minimum_buy_usd:
                            continue
                        self.signal_queue.put(CopyTradeSignal(
                            buyer=buyer,
                            mint=mint,
                            buy_sol=buy_sol,
                            buy_usd=buy_usd,
                            received=received,
                            signature=sig,
                            slot=event["slot"],
                        ))
                        print(f"  [copytrade] 💰 {buyer[:16]}... bought ${buy_usd:.0f} {mint[:16]}...")

        finally:
            ws.close()