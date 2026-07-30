"""Deterministic replay of the diagnosed Wallet A transaction window."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReplaySummary:
    total_round_trips: int
    same_slot_precursors: int
    tight_backrun_candidates: int
    profitable_tight_candidates: int
    observed_profit_sol: float
    signatures: tuple[str, ...]


def load_replay(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise TypeError("replay fixture must contain a JSON array")
    return data


def replay_precursors(
    records: Iterable[dict[str, Any]],
    *,
    minimum_precursor_usd: float,
    maximum_transaction_gap: int,
) -> ReplaySummary:
    rows = list(records)
    same_slot_count = 0
    candidates: list[dict[str, Any]] = []
    for row in rows:
        precursors = row.get("same_slot_large_buys_before", [])
        qualifying = [
            event
            for event in precursors
            if float(event.get("usd", 0)) >= minimum_precursor_usd
        ]
        if qualifying:
            same_slot_count += 1
        if any(
            int(row["transactionIndex"]) - int(event["transactionIndex"])
            <= maximum_transaction_gap
            for event in qualifying
        ):
            candidates.append(row)

    profitable = [row for row in candidates if float(row["pnl_sol"]) > 0]
    return ReplaySummary(
        total_round_trips=len(rows),
        same_slot_precursors=same_slot_count,
        tight_backrun_candidates=len(candidates),
        profitable_tight_candidates=len(profitable),
        observed_profit_sol=sum(float(row["pnl_sol"]) for row in candidates),
        signatures=tuple(str(row["signature"]) for row in candidates),
    )
