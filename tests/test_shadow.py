from typing import Any

from backrunner.composer import CompiledRoundTrip
from backrunner.jupiter import RoundTripBuild, SwapBuild
from backrunner.providers import SimulationResult
from backrunner.shadow import assess_shadow


def leg(pool: str, *, threshold: int) -> SwapBuild:
    return SwapBuild(
        input_mint="Input",
        output_mint="Output",
        in_amount=1,
        out_amount=threshold,
        other_amount_threshold=threshold,
        slippage_bps=100,
        pool_keys=(pool,),
        pool_labels=("DEX",),
        raw={},
    )


def compiled(*, size: int = 1000, accounts: int = 20) -> CompiledRoundTrip:
    return CompiledRoundTrip(
        transaction_base64="tx",
        version=0,
        instruction_count=5,
        serialized_size=size,
        account_count=accounts,
        signature_count=1,
    )


def simulation(error: Any = None) -> SimulationResult:
    return SimulationResult(
        context_slot=100,
        error=error,
        logs=(),
        units_consumed=400_000,
        fee_lamports=5_000,
    )


def test_profitable_distinct_pool_simulation_is_shadow_approved() -> None:
    route = RoundTripBuild(
        input_lamports=10_000_000,
        buy=leg("PoolA", threshold=1_000_000),
        sell=leg("PoolB", threshold=12_000_000),
    )

    decision = assess_shadow(
        route=route,
        compiled=compiled(),
        simulation=simulation(),
        failed_attempt_reserve_lamports=815_123,
        minimum_net_profit_lamports=279_929,
        jito_tip_lamports=100_000,
        safety_margin_lamports=100_000,
    )

    assert decision.approved is True
    assert decision.rejection_reasons == ()
    assert decision.net_profit_lamports == 979_877


def test_same_pool_or_failed_simulation_is_rejected() -> None:
    route = RoundTripBuild(
        input_lamports=10_000_000,
        buy=leg("PoolA", threshold=1_000_000),
        sell=leg("PoolA", threshold=12_000_000),
    )

    decision = assess_shadow(
        route=route,
        compiled=compiled(size=1300, accounts=65),
        simulation=simulation({"InstructionError": [2, "Custom"]}),
        failed_attempt_reserve_lamports=0,
        minimum_net_profit_lamports=1,
        jito_tip_lamports=0,
        safety_margin_lamports=0,
    )

    assert decision.approved is False
    assert decision.rejection_reasons == (
        "pools_overlap",
        "transaction_too_large",
        "too_many_accounts",
        "simulation_failed",
    )
