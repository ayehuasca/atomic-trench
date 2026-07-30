"""
Jito tip stream WebSocket + dynamic tip computation.

Connects to Jito's tip_stream WSS, maintains the latest tip percentiles,
and computes dynamic tip lamports based on urgency.
"""

import asyncio
import json
import time
from typing import Literal, Optional

import aiohttp
import websockets

TIP_STREAM_URL = "wss://bundles.jito.wtf/api/v1/bundles/tip_stream"
TIP_FLOOR_REST = "https://bundles.jito.wtf/api/v1/bundles/tip_floor"

MIN_TIP_LAMPORTS = 10_000
MAX_TIP_LAMPORTS = 50_000_000

_latest: Optional[dict] = None
_last_update: float = 0.0
_ws_task: Optional[asyncio.Task] = None


def _update(data: dict) -> None:
    global _latest, _last_update
    _latest = data
    _last_update = time.time()


async def _fetch_rest_fallback() -> None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TIP_FLOOR_REST, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status != 200:
                    return
                arr = await resp.json()
                if isinstance(arr, list) and arr:
                    _update(arr[-1])
    except Exception:
        pass


async def _tip_stream_loop() -> None:
    backoff = 2.0
    while True:
        try:
            async with websockets.connect(TIP_STREAM_URL, ping_interval=20) as ws:
                print("[tip-stream] connected")
                await _fetch_rest_fallback()
                backoff = 2.0
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        entry = msg[-1] if isinstance(msg, list) else msg
                        if "landed_tips_50th_percentile" in entry:
                            _update(entry)
                    except Exception:
                        continue
        except Exception as e:
            print(f"[tip-stream] disconnected: {e} — reconnecting in {backoff:.1f}s")
            await _fetch_rest_fallback()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 30.0)


def start_tip_stream() -> None:
    global _ws_task
    if _ws_task is not None and not _ws_task.done():
        return

    # Run the event loop in a background daemon thread
    def _run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_tip_stream_loop())

    import threading
    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    # Store a placeholder so we know it's started
    _ws_task = asyncio.Future()  # non-None marker


def get_latest_tip_floor() -> Optional[dict]:
    return _latest


def tip_stream_age_ms() -> float:
    if not _latest:
        return float("inf")
    return (time.time() - _last_update) * 1000


def get_dynamic_tip_lamports(
    urgency: Literal["low", "normal", "high", "extreme"] = "normal"
) -> int:
    sol = 0.00001  # fallback
    if _latest:
        if urgency == "low":
            sol = _latest.get("ema_landed_tips_50th_percentile") or _latest.get("landed_tips_50th_percentile", sol)
        elif urgency == "normal":
            sol = _latest.get("landed_tips_75th_percentile", sol)
        elif urgency == "high":
            sol = _latest.get("landed_tips_95th_percentile", sol)
        else:  # extreme
            sol = _latest.get("landed_tips_99th_percentile", sol)

    mult = {"low": 1.1, "normal": 1.3, "high": 1.6, "extreme": 2.0}[urgency]
    lamports = int(sol * 1e9 * mult)
    return max(MIN_TIP_LAMPORTS, min(MAX_TIP_LAMPORTS, lamports))
