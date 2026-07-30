"""Fail-closed configuration. Phase one cannot sign or submit transactions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Config:
    dry_run: bool
    rpc_url: str
    minimum_buy_usd: float
    maximum_transaction_gap: int
    sol_price_usd: float
    commitment: str
    replay_fixture: Path
    failed_attempt_reserve_lamports: int
    minimum_net_profit_lamports: int
    shadow_taker: str
    shadow_input_lamports: int
    slippage_bps: int
    max_accounts: int
    jito_tip_lamports: int
    safety_margin_lamports: int
    risk_state_path: Path
    pilot_risk_pct: float
    validated_risk_pct: float
    trade_cap_usd: float
    wallet_reserve_usd: float
    daily_loss_pct: float
    maximum_drawdown_pct: float
    executor_program_id: str
    direct_lookup_table_addresses: tuple[str, ...]
    direct_compute_unit_limit: int
    direct_compute_unit_price_micro_lamports: int
    direct_opportunity_slot_ttl: int
    maximum_transaction_fee_lamports: int
    direct_tip_lamports: int
    direct_tip_recipient: str | None

    @property
    def required_gross_profit_lamports(self) -> int:
        return (
            self.failed_attempt_reserve_lamports
            + self.minimum_net_profit_lamports
            + self.safety_margin_lamports
            + self.maximum_transaction_fee_lamports
            + self.direct_tip_lamports
        )


def load_config(path: Path) -> Config:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    dry_run = bool(raw.get("dry_run", True))
    gap = int(raw.get("maximum_transaction_gap", 3))
    if gap < 1:
        raise ValueError("maximum_transaction_gap must be at least 1")
    shadow_input = int(raw.get("shadow_input_lamports", 10_000_000))
    if shadow_input <= 0:
        raise ValueError("shadow_input_lamports must be positive")
    slippage_bps = int(raw.get("slippage_bps", 100))
    if not 0 <= slippage_bps <= 5_000:
        raise ValueError("slippage_bps must be between 0 and 5000")
    max_accounts = int(raw.get("max_accounts", 50))
    if not 1 <= max_accounts <= 64:
        raise ValueError("max_accounts must be between 1 and 64")
    lookup_tables = tuple(str(value) for value in raw.get("direct_lookup_table_addresses", []))
    compute_unit_limit = int(raw.get("direct_compute_unit_limit", 600_000))
    if not 1 <= compute_unit_limit <= 1_400_000:
        raise ValueError("direct_compute_unit_limit must be between 1 and 1400000")
    compute_unit_price = int(raw.get("direct_compute_unit_price_micro_lamports", 800_000))
    if compute_unit_price < 0:
        raise ValueError("direct_compute_unit_price_micro_lamports cannot be negative")
    slot_ttl = int(raw.get("direct_opportunity_slot_ttl", 2))
    if not 1 <= slot_ttl <= 32:
        raise ValueError("direct_opportunity_slot_ttl must be between 1 and 32")
    maximum_fee = int(raw.get("maximum_transaction_fee_lamports", 700_000))
    direct_tip = int(raw.get("direct_tip_lamports", 0))
    if maximum_fee < 0 or direct_tip < 0:
        raise ValueError("direct fee and tip limits cannot be negative")
    tip_recipient_raw = raw.get("direct_tip_recipient")
    tip_recipient = str(tip_recipient_raw) if tip_recipient_raw else None
    if direct_tip and tip_recipient is None:
        raise ValueError("direct_tip_recipient is required when direct_tip_lamports is nonzero")
    return Config(
        dry_run=dry_run,
        rpc_url=str(raw.get("rpc_url", "https://solana-rpc.publicnode.com")),
        minimum_buy_usd=float(raw.get("minimum_buy_usd", 300)),
        maximum_transaction_gap=gap,
        sol_price_usd=float(raw.get("sol_price_usd", 73.385944)),
        commitment=str(raw.get("commitment", "finalized")),
        replay_fixture=Path(raw.get("replay_fixture", "fixtures/wallet_a_replay.json")),
        failed_attempt_reserve_lamports=int(
            raw.get("failed_attempt_reserve_lamports", 815_123)
        ),
        minimum_net_profit_lamports=int(raw.get("minimum_net_profit_lamports", 279_929)),
        shadow_taker=str(
            raw.get("shadow_taker", "MRiYA4oN3158fCV8evhuCofrDzbHyYvYnGZUDJvoCsa")
        ),
        shadow_input_lamports=shadow_input,
        slippage_bps=slippage_bps,
        max_accounts=max_accounts,
        jito_tip_lamports=int(raw.get("jito_tip_lamports", 100_000)),
        safety_margin_lamports=int(raw.get("safety_margin_lamports", 100_000)),
        risk_state_path=Path(raw.get("risk_state_path", "data/runtime-state.json")),
        pilot_risk_pct=float(raw.get("pilot_risk_pct", 5.0)),
        validated_risk_pct=float(raw.get("validated_risk_pct", 10.0)),
        trade_cap_usd=float(raw.get("trade_cap_usd", 25.0)),
        wallet_reserve_usd=float(raw.get("wallet_reserve_usd", 10.0)),
        daily_loss_pct=float(raw.get("daily_loss_pct", 3.0)),
        maximum_drawdown_pct=float(raw.get("maximum_drawdown_pct", 5.0)),
        executor_program_id=str(raw.get("executor_program_id", "")),
        direct_lookup_table_addresses=lookup_tables,
        direct_compute_unit_limit=compute_unit_limit,
        direct_compute_unit_price_micro_lamports=compute_unit_price,
        direct_opportunity_slot_ttl=slot_ttl,
        maximum_transaction_fee_lamports=maximum_fee,
        direct_tip_lamports=direct_tip,
        direct_tip_recipient=tip_recipient,
    )
