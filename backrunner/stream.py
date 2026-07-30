"""Processed log observation with confirmed transaction reconciliation."""

import json
import time
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import urlparse, urlunparse

from websocket import WebSocket, WebSocketTimeoutException, create_connection

from backrunner.detector import detect_large_buys
from backrunner.providers import GmgnProvider, SolanaRpc

PUMP_AMM_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
METEORA_DLMM_PROGRAM_ID = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"


@dataclass(frozen=True)
class ProcessedLogEvent:
    signature: str
    slot: int
    error: Any
    logs: tuple[str, ...]

    @property
    def succeeded(self) -> bool:
        return self.error is None


def parse_log_notification(payload: dict[str, Any]) -> ProcessedLogEvent | None:
    if payload.get("method") != "logsNotification":
        return None
    result = payload.get("params", {}).get("result", {})
    value = result.get("value") or {}
    signature = value.get("signature")
    slot = result.get("context", {}).get("slot")
    if not signature or slot is None:
        return None
    return ProcessedLogEvent(
        signature=str(signature),
        slot=int(slot),
        error=value.get("err"),
        logs=tuple(str(line) for line in value.get("logs") or []),
    )


def normalize_transaction(payload: dict[str, Any]) -> dict[str, Any]:
    transaction = payload.get("transaction") or {}
    message = transaction.get("message") or {}
    record = {
        "meta": payload.get("meta") or {},
        "transaction": {
            "accountKeys": message.get("accountKeys") or [],
            "signatures": transaction.get("signatures") or [],
        },
    }
    return {"blockTime": payload.get("blockTime") or 0, "transactions": [record]}


def websocket_url(rpc_url: str) -> str:
    parsed = urlparse(rpc_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


class ProcessedLogStream:
    def __init__(self, rpc_url: str, *, timeout_seconds: float = 30.0) -> None:
        self.url = websocket_url(rpc_url)
        self.timeout_seconds = timeout_seconds
        self.sockets: list[WebSocket] = []
        self.seen_signatures: set[str] = set()

    def __enter__(self) -> Self:
        try:
            socket = create_connection(self.url, timeout=self.timeout_seconds)
            socket.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": ["all", {"commitment": "processed"}],
                    }
                )
            )
            confirmation = json.loads(socket.recv())
            if confirmation.get("error") or "result" not in confirmation:
                socket.close()
                raise RuntimeError(f"processed log subscription failed: {confirmation}")
            socket.settimeout(0.5)
            self.sockets.append(socket)
        except Exception:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        for socket in self.sockets:
            socket.close()
        self.sockets.clear()

    def next_event(self) -> ProcessedLogEvent:
        if not self.sockets:
            raise RuntimeError("processed stream is not connected")
        while True:
            for socket in self.sockets:
                try:
                    raw = socket.recv()
                except WebSocketTimeoutException:
                    continue
                payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                event = parse_log_notification(payload)
                if event is None or event.signature in self.seen_signatures:
                    continue
                if not any(
                    PUMP_AMM_PROGRAM_ID in line or METEORA_DLMM_PROGRAM_ID in line
                    for line in event.logs
                ):
                    continue
                self.seen_signatures.add(event.signature)
                return event


def observe_processed_once(
    *,
    rpc: SolanaRpc,
    gmgn: GmgnProvider,
    minimum_buy_usd: float,
    transaction_retries: int = 12,
    retry_delay_seconds: float = 0.1,
) -> dict[str, Any]:
    trending_mints = gmgn.trending_mints()
    sol_price = gmgn.sol_price_usd()
    with ProcessedLogStream(rpc.url) as stream:
        event = stream.next_event()
    if not event.succeeded:
        return {
            "mode": "DRY_RUN_PROCESSED_OBSERVE",
            "observation_slot": event.slot,
            "signature": event.signature,
            "fork_status": "failed_processed_transaction",
            "events": [],
            "transactions_submitted": 0,
            "live_execution_enabled": False,
        }

    transaction = None
    for _ in range(transaction_retries):
        transaction = rpc.transaction(event.signature, commitment="confirmed")
        if transaction is not None:
            break
        time.sleep(retry_delay_seconds)
    if transaction is None:
        fork_status = "unavailable_or_reorged"
        events: list[Any] = []
    else:
        fork_status = "confirmed"
        events = detect_large_buys(
            block=normalize_transaction(transaction),
            slot=event.slot,
            trending_mints=trending_mints,
            sol_price_usd=sol_price,
            minimum_buy_usd=minimum_buy_usd,
        )
    return {
        "mode": "DRY_RUN_PROCESSED_OBSERVE",
        "observation_slot": event.slot,
        "signature": event.signature,
        "fork_status": fork_status,
        "trending_mints": len(trending_mints),
        "events": [
            {
                "buyer": item.buyer,
                "mint": item.mint,
                "buy_sol": item.buy_sol,
                "buy_usd": item.buy_usd,
                "token_received": item.token_received,
            }
            for item in events
        ],
        "transactions_submitted": 0,
        "live_execution_enabled": False,
    }
