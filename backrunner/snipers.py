"""
Real-time pump.fun token creation sniper.

Watches the pump.fun program via Helius WebSocket, filters for create/create_v2
instructions, parses the creator field from instruction data, and emits signals
when a watched creator deploys a new token.
"""

import base58
import json
import struct
import time
from dataclasses import dataclass
from queue import Queue
from threading import Thread
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from websocket import WebSocket, WebSocketTimeoutException, create_connection

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# create  (legacy) discriminator: [24, 30, 200, 40, 5, 28, 7, 119]
CREATE_DISCRIMINATOR = bytes([24, 30, 200, 40, 5, 28, 7, 119])
# create_v2 discriminator:       [214, 144, 76, 236, 95, 139, 49, 180]
CREATE_V2_DISCRIMINATOR = bytes([214, 144, 76, 236, 95, 139, 49, 180])

# How many transactions to cache for dedup
MAX_SEEN = 5000

# Polling interval for block-based fallback
BLOCK_POLL_INTERVAL = 2.0


def _borsh_parse_string(data: bytes, offset: int) -> tuple[str, int]:
    """Parse a Borsh string at offset: u32 length + UTF-8 data. Returns (value, new_offset)."""
    length = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    value = data[offset : offset + length].decode("utf-8", errors="replace")
    offset += length
    return value, offset


def parse_creator_from_instruction(ix_data: bytes) -> str | None:
    """Extract creator Pubkey from create/create_v2 instruction data.

    Layout (after 8-byte discriminator):
        name: string (u32 len + data)
        symbol: string
        uri: string
        creator: Pubkey (32 bytes)
    Returns the creator as a base58 string, or None if parsing fails.
    """
    if len(ix_data) < 8:
        return None
    discriminator = ix_data[:8]
    if discriminator != CREATE_DISCRIMINATOR and discriminator != CREATE_V2_DISCRIMINATOR:
        return None

    offset = 8
    try:
        # Skip name, symbol, uri
        _, offset = _borsh_parse_string(ix_data, offset)
        _, offset = _borsh_parse_string(ix_data, offset)
        _, offset = _borsh_parse_string(ix_data, offset)
        # Read 32-byte creator Pubkey
        if offset + 32 > len(ix_data):
            return None
        creator_bytes = ix_data[offset : offset + 32]
        # Base58 encode
        import base58
        return base58.b58encode(creator_bytes)
    except (struct.error, IndexError):
        return None


def _ws_url(rpc_url: str) -> str:
    parsed = urlparse(rpc_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


@dataclass(frozen=True)
class SnipeSignal:
    creator: str
    mint: str
    signature: str
    slot: int
    is_v2: bool  # True = create_v2 (Token-2022)


class PumpSniper:
    """Watcher for pump.fun token creations — snipes NEW tokens with creator scoring.

    Detects create/create_v2 instructions on pump.fun, scores the creator's
    on-chain history, and only emits signals for high-quality launches.
    """

    def __init__(
        self,
        rpc_url: str,
        signal_queue: Queue,
        *,
        enable_block_poll: bool = True,
        max_per_minute: int = 10,
        scorer: Any | None = None,
        cache_path: str | None = None,  # JSON file for persistent cache
    ):
        self.rpc_url = rpc_url
        self.signal_queue = signal_queue
        self.enable_block_poll = enable_block_poll
        self.max_per_minute = max_per_minute
        self.scorer = scorer
        self.cache_path = cache_path
        self._running = False
        self._ws_thread: Thread | None = None
        self._poll_thread: Thread | None = None
        self._seen_signatures: set[str] = set()
        self._signals_this_minute: list[float] = []
        self._decisions: dict[str, float] = {}  # mint -> score, for dedup  # timestamps

    def _rate_limited(self) -> bool:
        """Check if we've exceeded the rate limit for this minute."""
        now = time.time()
        self._signals_this_minute = [t for t in self._signals_this_minute if now - t < 60]
        return len(self._signals_this_minute) >= self.max_per_minute

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._ws_thread = Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()
        if self.enable_block_poll:
            self._poll_thread = Thread(target=self._poll_loop, daemon=True)
            self._poll_thread.start()
        print(f"  [sniper] watching ALL new tokens (max {self.max_per_minute}/min)")

    def stop(self) -> None:
        self._running = False

    def _check_tx(self, signature: str) -> SnipeSignal | None:
        """Fetch a transaction and check if it's a create/create_v2."""
        if signature in self._seen_signatures:
            return None
        self._seen_signatures.add(signature)
        if len(self._seen_signatures) > MAX_SEEN:
            self._seen_signatures.clear()

        # REST call rate limiter (separate from signal rate limiter)
        now = time.time()
        self._rest_calls = [t for t in getattr(self, '_rest_calls', []) if now - t < 60]
        if len(self._rest_calls) >= self.max_per_minute * 3:
            return None
        self._rest_calls.append(now)

        try:
            data = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            }).encode()
            resp = requests.post(self.rpc_url, data=data, headers={"Content-Type": "application/json"}, timeout=15)
            tx = resp.json().get("result")
            if not tx:
                return None
        except Exception:
            return None

        meta = tx.get("meta", {})
        if meta.get("err"):
            return None

        msg = tx.get("transaction", {}).get("message", {})
        account_keys_raw = msg.get("accountKeys", [])
        account_keys = [str(k.get("pubkey") if isinstance(k, dict) else k) for k in account_keys_raw]
        instructions = msg.get("instructions", [])

        is_create = False
        creator = None

        def _check_disc(ix: dict) -> tuple[bool, str | None]:
            """Check a single instruction for create discriminator. Returns (is_create, creator)."""
            prog = str(ix.get("programId", ix.get("program", "")))
            if prog != PUMP_PROGRAM:
                return False, None
            ix_data_raw = ix.get("data") or ix.get("data")
            if not ix_data_raw:
                return False, None
            try:
                # jsonParsed: hex for Anchor, base58 for non-Anchor
                if isinstance(ix_data_raw, str):
                    if all(c in "0123456789abcdefABCDEF" for c in ix_data_raw):
                        ix_data = bytes.fromhex(ix_data_raw)
                    elif len(ix_data_raw) == 88:
                        ix_data = base58.b58decode(ix_data_raw)
                    else:
                        import base64
                        ix_data = base64.b64decode(ix_data_raw)
                else:
                    return False, None
            except Exception:
                return False, None
            d = ix_data[:8]
            if d == CREATE_DISCRIMINATOR or d == CREATE_V2_DISCRIMINATOR:
                return True, parse_creator_from_instruction(ix_data)
            return False, None

        # Check top-level instructions
        for ix in instructions:
            found, c = _check_disc(ix)
            if found:
                is_create = True
                creator = c
                break

        # Check inner instructions (CPI) if not found in top-level
        if not is_create:
            for inner in meta.get("innerInstructions", []):
                for ix in inner.get("instructions", []):
                    found, c = _check_disc(ix)
                    if found:
                        is_create = True
                        creator = c
                        break
                if is_create:
                    break

        if not is_create:
            return None

        # Find the new mint
        mint = None
        pre_mints = set()
        for bal in meta.get("preTokenBalances", []):
            pre_mints.add(str(bal.get("mint")))
        for bal in meta.get("postTokenBalances", []):
            m = str(bal.get("mint"))
            if m not in pre_mints and m not in ("So11111111111111111111111111111111111111112",
                                                 "So11111111111111111111111111111111111111111"):
                mint = m
                break

        if not mint:
            return None

        slot = meta.get("slot", 0)

        # Score the creator if a scorer is available
        if self.scorer is not None and creator and creator != "unknown":
            decision = self.scorer.score(creator, mint)
            cache_size = self.scorer.cache_size() if hasattr(self.scorer, 'cache_size') else '?'
            if not decision.approved:
                print(f"  [sniper] ⛔ rejected {mint[:16]}... creator={creator[:12]}... reason={decision.reason} cache={cache_size}")
                self._signals_this_minute.append(time.time())
                return None
            else:
                print(f"  [sniper] ✅ approved {mint[:16]}... creator={creator[:12]}... score={decision.profile.score:.2f if decision.profile else '?'} cache={cache_size}")
        elif creator:
            print(f"  [sniper] ⚠️ no scorer — emitting anyway {mint[:16]}... creator={creator[:12]}")

        # Rate limit tracking
        self._signals_this_minute.append(time.time())

        print(f"  [sniper] 🎯 new token {mint[:16]}... create_v2={d == CREATE_V2_DISCRIMINATOR}")
        return SnipeSignal(
            creator=creator or "unknown",
            mint=mint,
            signature=signature,
            slot=slot,
            is_v2=(d == CREATE_V2_DISCRIMINATOR),
        )

    def _ws_loop(self) -> None:
        """WebSocket logsSubscribe for real-time pump.fun creation detection."""
        while self._running:
            try:
                ws_url = _ws_url(self.rpc_url)
                ws = create_connection(ws_url, timeout=15)
                ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [PUMP_PROGRAM]},
                        {"commitment": "processed"},
                    ],
                }))
                confirmation = json.loads(ws.recv())
                if confirmation.get("error") or "result" not in confirmation:
                    raise RuntimeError(f"subscription failed: {confirmation}")
                print("  [sniper] WebSocket connected")
                ws.settimeout(1.0)

                while self._running:
                    try:
                        raw = ws.recv()
                    except WebSocketTimeoutException:
                        continue
                    payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                    if payload.get("method") != "logsNotification":
                        continue
                    result = payload.get("params", {}).get("result", {})
                    value = result.get("value", {})
                    sig = value.get("signature")
                    if not sig:
                        continue

                    # No fast-check — pump.fun emits events via Program data: (base64),
                    # not Program log:. The _check_tx discriminator check handles filtering.
                    # Cost: one REST call per notification mentioning Pump program.

                    signal = self._check_tx(sig)
                    if signal:
                        self.signal_queue.put(signal)
                        print(f"  [sniper] 🔥 signal: {signal.mint[:16]}...")

            except Exception as e:
                if self._running:
                    print(f"  [sniper] WS error: {e}")
                time.sleep(3)

    def _poll_loop(self) -> None:
        """Fallback block polling for create events (less lag than WS)."""
        from backrunner.providers import SolanaRpc
        rpc = SolanaRpc(self.rpc_url)

        while self._running:
            try:
                latest_slot = rpc.latest_slot()
                for slot in range(latest_slot, latest_slot - 4, -1):
                    try:
                        block = rpc.block_accounts(slot)
                    except RuntimeError:
                        continue

                    for record in block.get("transactions", []):
                        meta = record.get("meta", {})
                        if meta.get("err"):
                            continue
                        tx = record.get("transaction", {})
                        sig = (tx.get("signatures") or [""])[0]
                        if not sig or sig in self._seen_signatures:
                            continue
                        self._seen_signatures.add(sig)
                        if len(self._seen_signatures) > MAX_SEEN:
                            self._seen_signatures.clear()

                        # Fast check: does this tx interact with pump program?
                        account_keys_raw = tx.get("accountKeys", [])
                        account_keys = [str(k.get("pubkey") if isinstance(k, dict) else k) for k in account_keys_raw]
                        if PUMP_PROGRAM not in account_keys:
                            continue

                        instructions = tx.get("instructions", [])
                        for ix in instructions:
                            prog = str(ix.get("programId", ix.get("program", "")))
                            if prog != PUMP_PROGRAM:
                                continue
                            ix_data_raw = ix.get("data") or ix.get("data")
                            if not ix_data_raw:
                                continue
                            try:
                                if isinstance(ix_data_raw, str):
                                    if all(c in "0123456789abcdefABCDEF" for c in ix_data_raw):
                                        ix_data = bytes.fromhex(ix_data_raw)
                                    elif len(ix_data_raw) == 88:
                                        ix_data = base58.b58decode(ix_data_raw)
                                    else:
                                        import base64
                                        ix_data = base64.b64decode(ix_data_raw)
                                else:
                                    continue
                            except Exception:
                                continue

                            creator = parse_creator_from_instruction(ix_data)
                            if creator:
                                # Need full tx with token balances
                                full = rpc.transaction(sig)
                                if full:
                                    signal = self._check_tx(sig)
                                    if signal:
                                        self.signal_queue.put(signal)

                time.sleep(BLOCK_POLL_INTERVAL)
            except Exception as e:
                if self._running:
                    print(f"  [sniper] poll error: {e}")
                time.sleep(5)