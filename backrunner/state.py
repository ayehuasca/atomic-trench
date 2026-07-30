"""Durable fail-closed runtime accounting state."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeState:
    high_water_equity_usd: float
    day_start_equity_usd: float
    daily_wallet_pnl_usd: float
    live_settled_attempts: int
    live_wallet_pnl_usd: float
    active_transactions: int
    residual_inventory: bool
    direct_executor_proven: bool
    profit_guard_proven: bool
    shadow_started_at: str
    shadow_candidates: int
    shadow_net_profit_usd: float

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RuntimeState":
        try:
            return cls(**payload)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("runtime state schema is invalid") from exc


def load_runtime_state(path: Path) -> RuntimeState:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"runtime state is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise TypeError("runtime state root must be an object")
    return RuntimeState.from_dict(payload)


def save_runtime_state(path: Path, state: RuntimeState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(asdict(state), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
