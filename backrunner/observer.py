"""Single-block, read-only shadow observer."""

from dataclasses import asdict
from typing import Any, Protocol

from .detector import detect_large_buys


class GmgnLike(Protocol):
    def trending_mints(self) -> set[str]: ...

    def sol_price_usd(self) -> float: ...


class RpcLike(Protocol):
    def latest_slot(self) -> int: ...

    def block_accounts(self, slot: int) -> dict[str, Any]: ...


def observe_once(
    *, rpc: RpcLike, gmgn: GmgnLike, minimum_buy_usd: float
) -> dict[str, Any]:
    trending_mints = gmgn.trending_mints()
    sol_price = gmgn.sol_price_usd()
    latest_slot = rpc.latest_slot()
    block = None
    slot = latest_slot
    for candidate_slot in range(latest_slot, latest_slot - 32, -1):
        try:
            block = rpc.block_accounts(candidate_slot)
            slot = candidate_slot
            break
        except RuntimeError as exc:
            message = str(exc).lower()
            if "unavailable" not in message and "not available" not in message and "skipped" not in message:
                raise
    if block is None:
        raise RuntimeError(f"no available block in slots {latest_slot - 31}..{latest_slot}")
    events = detect_large_buys(
        block=block,
        slot=slot,
        trending_mints=trending_mints,
        sol_price_usd=sol_price,
        minimum_buy_usd=minimum_buy_usd,
    )
    return {
        "mode": "DRY_RUN_OBSERVE",
        "slot": slot,
        "sol_price_usd": sol_price,
        "trending_mints": len(trending_mints),
        "large_buy_events": [asdict(event) for event in events],
        "transactions_submitted": 0,
        "live_execution_enabled": False,
    }
