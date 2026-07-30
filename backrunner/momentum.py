"""
Real-time momentum detector using Helius transactionSubscribe WebSocket.

Subscribes to Pump AMM transactions at processed commitment, decodes
token balances, and emits buy signals within ~200ms of landing.
"""

import json
import time
from dataclasses import dataclass
from queue import Queue
from threading import Thread
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from websocket import WebSocket, WebSocketTimeoutException, create_connection

PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSOL = "So11111111111111111111111111111111111111112"
STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}

MAX_SEEN = 5000


@dataclass(frozen=True)
class MomentumSignal:
    buyer: str
    mint: str
    buy_sol: float
    buy_usd: float
    received: float
    signature: str
    slot: int


def _ws_url(rpc_url: str) -> str:
    parsed = urlparse(rpc_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _pubkey(value: Any) -> str:
    return str(value["pubkey"] if isinstance(value, dict) else value)


class MomentumWatcher:
    """Real-time Pump AMM buy detector via Helius transactionSubscribe.

    Subscribes to all transactions involving the Pump AMM program, decodes
    token balance changes, and emits signals for buys >= minimum_buy_usd.
    """

    def __init__(
        self,
        rpc_url: str,
        signal_queue: Queue,
        *,
        minimum_buy_usd: float = 500,
        sol_price_usd: float = 74,
    ):
        self.rpc_url = rpc_url
        self.signal_queue = signal_queue
        self.minimum_buy_usd = minimum_buy_usd
        self.sol_price_usd = sol_price_usd
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

    def update_sol_price(self, price: float) -> None:
        self.sol_price_usd = price

    def _run(self) -> None:
        while self._running:
            try:
                self._connect_and_listen()
            except Exception as e:
                print(f"  [momentum] WS error: {e}")
            time.sleep(3)

    def _connect_and_listen(self) -> None:
        ws_url = _ws_url(self.rpc_url)
        ws = create_connection(ws_url, timeout=15)
        try:
            # Subscribe to transactions that involve Pump AMM
            ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "transactionSubscribe",
                "params": [
                    {
                        "accountInclude": [PUMP_AMM_PROGRAM],
                        "failed": False,
                        "vote": False,
                    },
                    {
                        "commitment": "processed",
                        "encoding": "jsonParsed",
                        "transactionDetails": "full",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            }))
            confirmation = json.loads(ws.recv())
            if confirmation.get("error") or "result" not in confirmation:
                raise RuntimeError(f"subscription failed: {confirmation}")
            print("  [momentum] streaming Pump AMM transactions")
            ws.settimeout(1.0)

            while self._running:
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue

                payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                if payload.get("method") != "transactionNotification":
                    continue

                params = payload.get("params", {})
                result = params.get("result", {})
                tx_data = result.get("transaction", {})
                meta = result.get("meta", {})

                # Extract signature
                tx_msg = tx_data.get("message", {})
                sigs = tx_data.get("signatures", [])
                if not sigs:
                    continue
                signature = str(sigs[0])

                if signature in self._seen_signatures:
                    continue
                self._seen_signatures.add(signature)
                if len(self._seen_signatures) > MAX_SEEN:
                    self._seen_signatures.clear()

                # Build token deltas
                account_keys = [_pubkey(k) for k in tx_msg.get("accountKeys", [])]
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

                # Check each buyer for large buys
                context = result.get("context", {})
                slot = int(context.get("slot", 0))

                for buyer, mint_deltas in deltas.items():
                    for mint, received in mint_deltas.items():
                        if received <= 0:
                            continue
                        if mint in STABLE_MINTS or mint == WSOL:
                            continue
                        token_quote_sol = max(0, -mint_deltas.get(WSOL, 0))
                        native_quote_sol = max(0, -native_delta.get(buyer, 0))
                        buy_sol = max(token_quote_sol, native_quote_sol)
                        buy_usd = buy_sol * self.sol_price_usd
                        if buy_usd < self.minimum_buy_usd:
                            continue
                        self.signal_queue.put(MomentumSignal(
                            buyer=buyer,
                            mint=mint,
                            buy_sol=buy_sol,
                            buy_usd=buy_usd,
                            received=received,
                            signature=signature,
                            slot=slot,
                        ))

        finally:
            ws.close()