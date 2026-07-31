from ops.strategy_loop import calculate_entry_size_sol


def test_entry_size_has_minimum_floor():
    assert calculate_entry_size_sol(0.41, reserve_sol=0.1, balance_pct=0.05, minimum_sol=0.05) == 0.05


def test_entry_size_scales_with_balance():
    assert calculate_entry_size_sol(3.0, reserve_sol=0.1, balance_pct=0.05, minimum_sol=0.05) == 0.145


def test_entry_size_refuses_when_reserve_plus_minimum_unavailable():
    assert calculate_entry_size_sol(0.14, reserve_sol=0.1, balance_pct=0.05, minimum_sol=0.05) is None
