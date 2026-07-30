from backrunner.risk import RiskPolicy, RiskSnapshot, assess_risk


def ready_snapshot(**overrides: object) -> RiskSnapshot:
    values: dict[str, object] = {
        "equity_usd": 100.0,
        "high_water_equity_usd": 100.0,
        "day_start_equity_usd": 100.0,
        "daily_wallet_pnl_usd": 0.0,
        "live_settled_attempts": 0,
        "live_wallet_pnl_usd": 0.0,
        "active_transactions": 0,
        "residual_inventory": False,
        "direct_executor_proven": True,
        "profit_guard_proven": True,
        "shadow_runtime_hours": 72.0,
        "shadow_candidates": 100,
        "shadow_net_profit_usd": 1.0,
    }
    values.update(overrides)
    return RiskSnapshot(**values)  # type: ignore[arg-type]


def test_ready_pilot_risks_five_percent_of_current_equity() -> None:
    decision = assess_risk(snapshot=ready_snapshot(), policy=RiskPolicy())

    assert decision.allowed is True
    assert decision.stage == "pilot"
    assert decision.maximum_trade_usd == 5.0
    assert decision.reasons == ()


def test_live_risk_is_zero_until_every_shadow_and_executor_gate_passes() -> None:
    decision = assess_risk(
        snapshot=ready_snapshot(
            direct_executor_proven=False,
            profit_guard_proven=False,
            shadow_runtime_hours=71.9,
            shadow_candidates=99,
            shadow_net_profit_usd=0.0,
        ),
        policy=RiskPolicy(),
    )

    assert decision.allowed is False
    assert decision.stage == "locked"
    assert decision.maximum_trade_usd == 0.0
    assert decision.reasons == (
        "direct_executor_unproven",
        "profit_guard_unproven",
        "insufficient_shadow_runtime",
        "insufficient_shadow_candidates",
        "shadow_net_not_positive",
    )


def test_wallet_and_concurrency_kill_switches_force_zero_risk() -> None:
    decision = assess_risk(
        snapshot=ready_snapshot(
            equity_usd=94.0,
            high_water_equity_usd=100.0,
            day_start_equity_usd=100.0,
            daily_wallet_pnl_usd=-3.0,
            active_transactions=1,
            residual_inventory=True,
        ),
        policy=RiskPolicy(),
    )

    assert decision.allowed is False
    assert decision.maximum_trade_usd == 0.0
    assert decision.reasons == (
        "active_transaction_exists",
        "residual_inventory",
        "daily_loss_limit",
        "maximum_drawdown",
    )


def test_twenty_live_attempts_scale_only_when_wallet_net_is_positive() -> None:
    losing = assess_risk(
        snapshot=ready_snapshot(live_settled_attempts=20, live_wallet_pnl_usd=0.0),
        policy=RiskPolicy(),
    )
    profitable = assess_risk(
        snapshot=ready_snapshot(live_settled_attempts=20, live_wallet_pnl_usd=1.0),
        policy=RiskPolicy(),
    )

    assert losing.allowed is False
    assert losing.maximum_trade_usd == 0.0
    assert losing.reasons == ("pilot_not_wallet_net_positive",)
    assert profitable.allowed is True
    assert profitable.stage == "validated"
    assert profitable.maximum_trade_usd == 10.0
