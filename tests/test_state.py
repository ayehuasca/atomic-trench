from pathlib import Path

from backrunner.state import RuntimeState, load_runtime_state, save_runtime_state


def test_runtime_state_round_trips_without_losing_wallet_net_guards(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    state = RuntimeState(
        high_water_equity_usd=100.0,
        day_start_equity_usd=100.0,
        daily_wallet_pnl_usd=-1.25,
        live_settled_attempts=7,
        live_wallet_pnl_usd=2.5,
        active_transactions=0,
        residual_inventory=False,
        direct_executor_proven=False,
        profit_guard_proven=False,
        shadow_started_at="2026-07-29T00:00:00+00:00",
        shadow_candidates=12,
        shadow_net_profit_usd=-0.5,
    )

    save_runtime_state(path, state)

    assert load_runtime_state(path) == state
    assert not path.with_suffix(".json.tmp").exists()
