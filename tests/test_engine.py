from backrunner.engine import CostModel, evaluate_round_trip


def test_profitable_atomic_round_trip_passes_after_all_costs() -> None:
    result = evaluate_round_trip(
        buy_lamports=10_290_914_964,
        sell_lamports=10_400_348_403,
        costs=CostModel(
            transaction_fee_lamports=491_732,
            jito_tip_lamports=250_000,
            safety_margin_lamports=1_000_000,
            minimum_net_profit_lamports=500_000,
        ),
    )

    assert result.executable is True
    assert result.net_profit_lamports == 107_691_707
    assert result.return_bps == 104


def test_route_is_rejected_when_profit_does_not_clear_cost_floor() -> None:
    result = evaluate_round_trip(
        buy_lamports=100_000_000,
        sell_lamports=100_600_000,
        costs=CostModel(
            transaction_fee_lamports=100_000,
            jito_tip_lamports=200_000,
            safety_margin_lamports=200_000,
            minimum_net_profit_lamports=200_000,
        ),
    )

    assert result.executable is False
    assert result.net_profit_lamports == 100_000


def test_failed_attempt_reserve_is_deducted_from_every_success() -> None:
    result = evaluate_round_trip(
        buy_lamports=10_000_000,
        sell_lamports=11_000_000,
        costs=CostModel(
            transaction_fee_lamports=5_000,
            jito_tip_lamports=0,
            safety_margin_lamports=0,
            minimum_net_profit_lamports=200_000,
            failed_attempt_reserve_lamports=815_123,
        ),
    )

    assert result.executable is False
    assert result.net_profit_lamports == 179_877
