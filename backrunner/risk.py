"""Fail-closed equity sizing and live-readiness gates."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskPolicy:
    pilot_risk_pct: float = 5.0
    validated_risk_pct: float = 10.0
    trade_cap_usd: float = 25.0
    wallet_reserve_usd: float = 10.0
    daily_loss_pct: float = 3.0
    maximum_drawdown_pct: float = 5.0
    minimum_live_attempts_for_scale: int = 20
    minimum_shadow_hours: float = 72.0
    minimum_shadow_candidates: int = 100


@dataclass(frozen=True)
class RiskSnapshot:
    equity_usd: float
    high_water_equity_usd: float
    day_start_equity_usd: float
    daily_wallet_pnl_usd: float
    live_settled_attempts: int
    live_wallet_pnl_usd: float
    active_transactions: int
    residual_inventory: bool
    direct_executor_proven: bool
    profit_guard_proven: bool
    shadow_runtime_hours: float
    shadow_candidates: int
    shadow_net_profit_usd: float


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    stage: str
    maximum_trade_usd: float
    reasons: tuple[str, ...]


def assess_risk(*, snapshot: RiskSnapshot, policy: RiskPolicy) -> RiskDecision:
    reasons: list[str] = []
    if not snapshot.direct_executor_proven:
        reasons.append("direct_executor_unproven")
    if not snapshot.profit_guard_proven:
        reasons.append("profit_guard_unproven")
    if snapshot.shadow_runtime_hours < policy.minimum_shadow_hours:
        reasons.append("insufficient_shadow_runtime")
    if snapshot.shadow_candidates < policy.minimum_shadow_candidates:
        reasons.append("insufficient_shadow_candidates")
    if snapshot.shadow_net_profit_usd <= 0:
        reasons.append("shadow_net_not_positive")
    if reasons:
        return RiskDecision(
            allowed=False,
            stage="locked",
            maximum_trade_usd=0.0,
            reasons=tuple(reasons),
        )

    if snapshot.active_transactions > 0:
        reasons.append("active_transaction_exists")
    if snapshot.residual_inventory:
        reasons.append("residual_inventory")
    daily_loss_limit = snapshot.day_start_equity_usd * policy.daily_loss_pct / 100
    if snapshot.daily_wallet_pnl_usd <= -daily_loss_limit:
        reasons.append("daily_loss_limit")
    drawdown_floor = snapshot.high_water_equity_usd * (
        1 - policy.maximum_drawdown_pct / 100
    )
    if snapshot.equity_usd <= drawdown_floor:
        reasons.append("maximum_drawdown")
    if reasons:
        return RiskDecision(
            allowed=False,
            stage="locked",
            maximum_trade_usd=0.0,
            reasons=tuple(reasons),
        )

    if snapshot.live_settled_attempts >= policy.minimum_live_attempts_for_scale:
        if snapshot.live_wallet_pnl_usd <= 0:
            return RiskDecision(
                allowed=False,
                stage="locked",
                maximum_trade_usd=0.0,
                reasons=("pilot_not_wallet_net_positive",),
            )
        stage = "validated"
        risk_pct = policy.validated_risk_pct
    else:
        stage = "pilot"
        risk_pct = policy.pilot_risk_pct
    trade_usd = min(
        snapshot.equity_usd * risk_pct / 100,
        policy.trade_cap_usd,
        max(0.0, snapshot.equity_usd - policy.wallet_reserve_usd),
    )
    return RiskDecision(
        allowed=True,
        stage=stage,
        maximum_trade_usd=round(trade_usd, 2),
        reasons=(),
    )
