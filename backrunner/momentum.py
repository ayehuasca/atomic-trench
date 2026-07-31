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

WSOL = "So11111111111111111111111111111111111111112"
STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

MAX_SEEN = 5000

# Pool existence cache: mint -> (pool_exists, cached_at)
_pool_cache: dict[str, tuple[bool, float]] = {}
POOL_CACHE_TTL = 300  # 5 min


def _get_pump_pool(mint: str) -> str | None:
    """Derive the Pump AMM pool address for a mint. Returns None on error."""
    try:
        from solders.pubkey import Pubkey
        mint_key = Pubkey.from_string(mint)
        quote_key = Pubkey.from_string(WSOL)
        pu_prog = Pubkey.from_string(PUMP_PROGRAM)
        pu_amm = Pubkey.from_string(PUMP_AMM_PROGRAM)
        authority, _ = Pubkey.find_program_address([b"pool-authority", bytes(mint_key)], pu_prog)
        pool, _ = Pubkey.find_program_address(
            [b"pool", (0).to_bytes(2, "little"), bytes(authority), bytes(mint_key), bytes(quote_key)],
            pu_amm,
        )
        return str(pool)
    except Exception:
        return None


def _pool_exists(rpc_url: str, pool: str) -> bool:
    """Check if a Pump AMM pool account exists on-chain. Cached."""
    global _pool_cache
    now = time.time()
    if pool in _pool_cache:
        exists, cached_at = _pool_cache[pool]
        if now - cached_at < POOL_CACHE_TTL:
            return exists
    try:
        data = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "getAccountInfo",
            "params": [pool, {"encoding": "base64"}],
        }).encode()
        resp = requests.post(rpc_url, data=data, headers={"Content-Type": "application/json"}, timeout=10)
        result = resp.json().get("result", {})
        exists = result.get("value") is not None
        _pool_cache[pool] = (exists, now)
        if len(_pool_cache) > 5000:
            _pool_cache.clear()
        return exists
    except Exception:
        return False


@dataclass(frozen=True)
class MomentumSignal:
    buyer: str
    mint: str
    buy_sol: float
    buy_usd: float
    received: float
    signature: str
    slot: int


class HotFlowAggregator:
    """Aggregate successful buys on trending mints over a short window."""

    def __init__(self, *, min_successful_buys: int = 2, min_combined_sol: float = 0.01, window_seconds: float = 5.0):
        self.min_successful_buys = min_successful_buys
        self.min_combined_sol = min_combined_sol
        self.window_seconds = window_seconds
        self._trending: set[str] = set()
        self._seen_signatures: set[str] = set()
        self._flows: dict[str, list[tuple[float, str, float, float]]] = {}
        self._triggered: dict[str, float] = {}

    def set_trending(self, mints: set[str]) -> None:
        self._trending = set(mints)
        # A mint leaving the universe must be eligible again if it returns.
        self._triggered = {mint: ts for mint, ts in self._triggered.items() if mint in self._trending}

    def add(self, mint: str, buyer: str, buy_sol: float, sol_price_usd: float, *, signature: str = "", now: float | None = None) -> MomentumSignal | None:
        timestamp = time.time() if now is None else now
        if self._trending and mint not in self._trending:
            return None
        if signature and signature in self._seen_signatures:
            return None
        if signature:
            self._seen_signatures.add(signature)
        rows = [row for row in self._flows.get(mint, []) if timestamp - row[0] <= self.window_seconds]
        rows.append((timestamp, buyer, buy_sol, sol_price_usd))
        self._flows[mint] = rows
        total_sol = sum(row[2] for row in rows)
        if len(rows) < self.min_successful_buys or total_sol < self.min_combined_sol:
            return None
        if mint in self._triggered:
            return None
        self._triggered[mint] = timestamp
        return MomentumSignal(buyer=buyer, mint=mint, buy_sol=total_sol, buy_usd=total_sol * sol_price_usd, received=0.0, signature=signature, slot=0)


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
        minimum_buy_usd: float = 0,
        sol_price_usd: float = 74,
    ):
        self.rpc_url = rpc_url
        self.signal_queue = signal_queue
        self.minimum_buy_usd = minimum_buy_usd
        self.sol_price_usd = sol_price_usd
        self._running = False
        self._thread: Thread | None = None
        self._seen_signatures: set[str] = set()
        self._trending_mints: set[str] = set()
        self._flow = HotFlowAggregator(min_successful_buys=2, min_combined_sol=0.01, window_seconds=5.0)

    def set_trending(self, mints: set[str]) -> None:
        """Replace the current GMGN trending-token allowlist."""
        self._trending_mints = set(mints)
        self._flow.set_trending(self._trending_mints)
        print(f"  [momentum] trending filter: {len(self._trending_mints)} mints")

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
            # Subscribe to logs mentioning Pump AMM (more reliable than transactionSubscribe)
            ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "logsSubscribe",
                "params": [
                    {"mentions": [PUMP_AMM_PROGRAM]},
                    {"commitment": "processed"},
                ],
            }))
            confirmation = json.loads(ws.recv())
            if confirmation.get("error") or "result" not in confirmation:
                raise RuntimeError(f"subscription failed: {confirmation}")
            print("  [momentum] logsSubscribe: Pump AMM")
            ws.settimeout(1.0)

            while self._running:
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue

                # Handle non-JSON data
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                raw = raw.strip()
                if not raw.startswith("{"):
                    continue

                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if payload.get("method") != "logsNotification":
                    continue

                result = payload.get("params", {}).get("result", {})
                value = result.get("value", {})
                signature = value.get("signature")
                err = value.get("err")
                if not signature or err:
                    continue

                slot = result.get("context", {}).get("slot", 0)

                if signature in self._seen_signatures:
                    continue
                self._seen_signatures.add(signature)
                if len(self._seen_signatures) > MAX_SEEN:
                    self._seen_signatures.clear()

                # Fetch full transaction via REST
                try:
                    data = json.dumps({
                        "jsonrpc": "2.0", "id": 1,
                        "method": "getTransaction",
                        "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    }).encode()
                    resp = requests.post(self.rpc_url, data=data, headers={"Content-Type": "application/json"}, timeout=15)
                    tx = resp.json().get("result")
                    if not tx:
                        continue
                except Exception:
                    continue

                meta = tx.get("meta", {})
                if meta.get("err"):
                    continue

                msg = tx.get("transaction", {}).get("message", {})
                account_keys = [_pubkey(k) for k in msg.get("accountKeys", [])]

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
                        # Fail closed until a non-empty trending snapshot exists.
                        if not self._trending_mints or mint not in self._trending_mints:
                            continue
                        # Aggregate hot flow before checking the pool and emitting.
                        aggregated = self._flow.add(
                            mint, buyer, buy_sol, self.sol_price_usd,
                            signature=signature, now=time.time(),
                        )
                        if aggregated is None:
                            continue
                        # Check if the Pump AMM pool still exists (not graduated)
                        pump_pool = _get_pump_pool(mint)
                        if pump_pool is None:
                            continue
                        if not _pool_exists(self.rpc_url, pump_pool):
                            continue
                        self.signal_queue.put(aggregated)

        finally:
            ws.close()