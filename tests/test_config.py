from pathlib import Path

import pytest

from backrunner.config import load_config


def test_live_mode_is_hard_disabled_in_phase_one(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("dry_run: false\nrpc_url: https://example.invalid\n")

    with pytest.raises(ValueError, match="live execution is not implemented"):
        load_config(path)


def test_dry_run_config_loads_without_private_key(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "dry_run: true\n"
        "rpc_url: https://solana-rpc.publicnode.com\n"
        "minimum_buy_usd: 300\n"
        "maximum_transaction_gap: 3\n"
    )

    config = load_config(path)

    assert config.dry_run is True
    assert config.minimum_buy_usd == 300
    assert config.maximum_transaction_gap == 3
    assert config.failed_attempt_reserve_lamports == 815_123
    assert config.minimum_net_profit_lamports == 279_929
    assert config.shadow_taker == "MRiYA4oN3158fCV8evhuCofrDzbHyYvYnGZUDJvoCsa"
    assert config.shadow_input_lamports == 10_000_000
    assert config.slippage_bps == 100
    assert config.risk_state_path == Path("data/runtime-state.json")
    assert config.pilot_risk_pct == 5.0
    assert config.validated_risk_pct == 10.0
    assert config.trade_cap_usd == 25.0
    assert config.wallet_reserve_usd == 10.0
    assert config.daily_loss_pct == 3.0
    assert config.maximum_drawdown_pct == 5.0
    assert config.executor_program_id == ""
    assert config.direct_lookup_table_addresses == ()
    assert config.direct_compute_unit_limit == 600_000
    assert config.direct_compute_unit_price_micro_lamports == 800_000
    assert config.direct_opportunity_slot_ttl == 2
    assert config.maximum_transaction_fee_lamports == 700_000
    assert config.required_gross_profit_lamports == 1_895_052
