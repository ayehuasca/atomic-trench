"""Fail-closed shadow routing and simulation assessment."""

from dataclasses import dataclass
from typing import Any

from backrunner.composer import (
    CompiledRoundTrip,
    DirectRouteRequest,
    compose_direct_round_trip,
    compose_round_trip,
)
from backrunner.engine import CostModel, evaluate_round_trip
from backrunner.jupiter import JupiterBuildClient, RoundTripBuild
from backrunner.providers import SimulationResult, SolanaRpc


@dataclass(frozen=True)
class ShadowDecision:
    approved: bool
    rejection_reasons: tuple[str, ...]
    gross_profit_lamports: int
    net_profit_lamports: int
    return_bps: int


def assess_shadow(
    *,
    route: RoundTripBuild,
    compiled: CompiledRoundTrip,
    simulation: SimulationResult,
    failed_attempt_reserve_lamports: int,
    minimum_net_profit_lamports: int,
    jito_tip_lamports: int,
    safety_margin_lamports: int,
) -> ShadowDecision:
    reasons: list[str] = []
    if not route.distinct_pools:
        reasons.append("pools_overlap")
    if not compiled.fits_packet_limit:
        reasons.append("transaction_too_large")
    if not compiled.fits_account_limit:
        reasons.append("too_many_accounts")
    if not simulation.succeeded:
        reasons.append("simulation_failed")

    result = evaluate_round_trip(
        buy_lamports=route.input_lamports,
        sell_lamports=route.sell.other_amount_threshold,
        costs=CostModel(
            transaction_fee_lamports=simulation.fee_lamports,
            jito_tip_lamports=jito_tip_lamports,
            safety_margin_lamports=safety_margin_lamports,
            minimum_net_profit_lamports=minimum_net_profit_lamports,
            failed_attempt_reserve_lamports=failed_attempt_reserve_lamports,
        ),
    )
    if not result.executable:
        reasons.append("insufficient_net_profit")
    return ShadowDecision(
        approved=not reasons,
        rejection_reasons=tuple(reasons),
        gross_profit_lamports=result.gross_profit_lamports,
        net_profit_lamports=result.net_profit_lamports,
        return_bps=result.return_bps,
    )


def run_shadow_route(
    *,
    token_mint: str,
    input_lamports: int,
    taker: str,
    slippage_bps: int,
    max_accounts: int,
    failed_attempt_reserve_lamports: int,
    minimum_net_profit_lamports: int,
    jito_tip_lamports: int,
    safety_margin_lamports: int,
    jupiter: JupiterBuildClient,
    rpc: SolanaRpc,
    min_context_slot: int | None = None,
) -> dict[str, Any]:
    route = jupiter.build_round_trip(
        token_mint=token_mint,
        input_lamports=input_lamports,
        taker=taker,
        slippage_bps=slippage_bps,
        max_accounts=max_accounts,
    )
    compiled = compose_round_trip(route=route, taker=taker)
    simulation = rpc.simulate_transaction(
        compiled.transaction_base64, min_context_slot=min_context_slot
    )
    decision = assess_shadow(
        route=route,
        compiled=compiled,
        simulation=simulation,
        failed_attempt_reserve_lamports=failed_attempt_reserve_lamports,
        minimum_net_profit_lamports=minimum_net_profit_lamports,
        jito_tip_lamports=jito_tip_lamports,
        safety_margin_lamports=safety_margin_lamports,
    )
    return {
        "mode": "DRY_RUN_SHADOW_ROUTE",
        "token_mint": token_mint,
        "input_lamports": input_lamports,
        "buy": {
            "minimum_output": route.buy.other_amount_threshold,
            "pools": list(route.buy.pool_keys),
            "labels": list(route.buy.pool_labels),
        },
        "sell": {
            "input_amount": route.sell.in_amount,
            "minimum_output_lamports": route.sell.other_amount_threshold,
            "pools": list(route.sell.pool_keys),
            "labels": list(route.sell.pool_labels),
        },
        "transaction": {
            "version": compiled.version,
            "instructions": compiled.instruction_count,
            "bytes": compiled.serialized_size,
            "accounts": compiled.account_count,
            "signature_slots": compiled.signature_count,
            "signed": False,
        },
        "simulation": {
            "succeeded": simulation.succeeded,
            "context_slot": simulation.context_slot,
            "units_consumed": simulation.units_consumed,
            "fee_lamports": simulation.fee_lamports,
            "error": simulation.error,
            "logs_tail": list(simulation.logs[-8:]),
        },
        "economics": {
            "conservative_gross_lamports": decision.gross_profit_lamports,
            "net_after_all_reserves_lamports": decision.net_profit_lamports,
            "return_bps": decision.return_bps,
        },
        "shadow_approved": decision.approved,
        "rejection_reasons": list(decision.rejection_reasons),
        "transactions_submitted": 0,
        "live_execution_enabled": False,
    }


def run_direct_shadow_route(
    *,
    token_mint: str,
    pump_pool: str,
    meteora_pool: str,
    direction: str,
    input_lamports: int,
    taker: str,
    executor_program_id: str,
    rpc_url: str,
    min_context_slot: int,
    slot_ttl: int,
    lookup_table_addresses: tuple[str, ...],
    slippage_bps: int,
    compute_unit_limit: int,
    compute_unit_price_micro_lamports: int,
    required_gross_profit_lamports: int,
    tip_lamports: int,
    tip_recipient: str | None,
    maximum_transaction_fee_lamports: int,
    rpc: SolanaRpc,
) -> dict[str, Any]:
    if min_context_slot < 0 or slot_ttl < 1:
        raise ValueError("direct shadow requires a nonnegative context slot and positive slot TTL")
    request = DirectRouteRequest(
        rpc_url=rpc_url,
        executor_program_id=executor_program_id,
        user=taker,
        pump_pool=pump_pool,
        meteora_pool=meteora_pool,
        intermediate_mint=token_mint,
        input_lamports=input_lamports,
        direction=direction,
        minimum_profit_lamports=required_gross_profit_lamports,
        valid_until_slot=min_context_slot + slot_ttl,
        min_context_slot=min_context_slot,
        lookup_table_addresses=lookup_table_addresses,
        slippage_bps=slippage_bps,
        compute_unit_limit=compute_unit_limit,
        compute_unit_price_micro_lamports=compute_unit_price_micro_lamports,
        tip_lamports=tip_lamports,
        tip_recipient=tip_recipient,
    )
    pre_balance_lamports = rpc.balance(taker, min_context_slot=min_context_slot)
    compiled = compose_direct_round_trip(request)
    simulation = rpc.simulate_transaction(
        compiled.transaction_base64,
        min_context_slot=min_context_slot,
        replace_recent_blockhash=False,
        return_accounts=(taker,),
    )
    post_simulation_baseline_lamports = rpc.balance(
        taker, min_context_slot=simulation.context_slot
    )
    reasons: list[str] = []
    if not compiled.fits_packet_limit:
        reasons.append("transaction_too_large")
    if not compiled.fits_account_limit:
        reasons.append("too_many_accounts")
    if simulation.context_slot < min_context_slot:
        reasons.append("stale_simulation_context")
    if not simulation.succeeded:
        reasons.append("simulation_failed")
    if post_simulation_baseline_lamports != pre_balance_lamports:
        reasons.append("wallet_baseline_changed")
        wallet_net_lamports: int | None = None
    elif not simulation.account_lamports or simulation.account_lamports[0] is None:
        reasons.append("wallet_balance_unavailable")
        wallet_net_lamports = None
    else:
        wallet_net_lamports = simulation.account_lamports[0] - pre_balance_lamports
        # Allow trades within configured max loss (minimum_net_profit_lamports
        # is repurposed as max_allowed_loss when failed_attempt_reserve is 0)
        if wallet_net_lamports + required_gross_profit_lamports < 0:
            reasons.append("wallet_net_exceeds_max_loss")
        elif wallet_net_lamports < 0:
            # Trade loses money but within max loss — allow it
            pass
        elif wallet_net_lamports < required_gross_profit_lamports:
            reasons.append("wallet_net_below_required_gross")

    # Validate simulated fee against configured maximum
    if simulation.fee_lamports > maximum_transaction_fee_lamports:
        reasons.append(
            "fee_exceeds_maximum: "
            f"{simulation.fee_lamports} > {maximum_transaction_fee_lamports}"
        )

    # Validate executor logs contain expected venue and success markers
    joined = " ".join(simulation.logs)
    if "Program log: profit floor not met" in joined:
        reasons.append("executor_rejected_profit_floor")
    if simulation.succeeded and wallet_net_lamports is not None and wallet_net_lamports > 0:
        has_expected_inner = any(
            marker in joined
            for marker in ["Program pAMMBay", "Program LBUZKhR", "Program log: ExecutorError"]
        )
        if not has_expected_inner:
            reasons.append("expected_venue_or_executor_logs_absent")
    return {
        "mode": "DRY_RUN_DIRECT_PUMP_METEORA",
        "token_mint": token_mint,
        "pump_pool": pump_pool,
        "meteora_pool": meteora_pool,
        "direction": direction,
        "input_lamports": input_lamports,
        "required_gross_profit_lamports": required_gross_profit_lamports,
        "valid_until_slot": request.valid_until_slot,
        "transaction": {
            "unsigned_transaction_base64": compiled.transaction_base64,
            "instructions": compiled.instruction_count,
            "bytes": compiled.serialized_size,
            "accounts": compiled.account_count,
            "lookup_tables": list(compiled.lookup_table_addresses),
            "funding_lamports": compiled.funding_lamports,
            "quote_account": compiled.quote_account,
            "intermediate_account": compiled.intermediate_account,
            "first_program": compiled.first_program,
            "second_program": compiled.second_program,
            "executor_program": compiled.executor_program,
            "signed": compiled.signed,
        },
        "simulation": {
            "succeeded": simulation.succeeded,
            "context_slot": simulation.context_slot,
            "units_consumed": simulation.units_consumed,
            "fee_lamports": simulation.fee_lamports,
            "error": simulation.error,
            "logs_tail": list(simulation.logs[-12:]),
        },
        "economics": {
            "pre_balance_lamports": pre_balance_lamports,
            "post_simulation_baseline_lamports": post_simulation_baseline_lamports,
            "post_balance_lamports": (
                simulation.account_lamports[0] if simulation.account_lamports else None
            ),
            "wallet_net_lamports": wallet_net_lamports,
        },
        "shadow_approved": not reasons,
        "rejection_reasons": reasons,
        "transactions_submitted": 0,
        "live_execution_enabled": False,
    }
