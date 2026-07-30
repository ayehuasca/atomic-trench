from unittest.mock import Mock, patch

from backrunner.composer import DirectCompiledRoundTrip
from backrunner.providers import SimulationResult
from backrunner.shadow import run_direct_shadow_route


def test_direct_shadow_simulates_exact_unsigned_transaction_without_submission() -> None:
    compiled = DirectCompiledRoundTrip(
        transaction_base64="AA==",
        recent_blockhash="blockhash",
        last_valid_block_height=100,
        instruction_count=8,
        serialized_size=598,
        account_count=42,
        lookup_table_addresses=("alt",),
        funding_lamports=10_000_000,
        quote_account="quote",
        intermediate_account="token",
        first_program="meteora",
        second_program="pump",
        executor_program="executor",
        signed=False,
        submitted=False,
    )
    rpc = Mock()
    rpc.balance.side_effect = [1_000_000_000, 1_000_000_000]
    rpc.simulate_transaction.return_value = SimulationResult(
        context_slot=998,
        error=None,
        logs=("Program LBUZKhR swap2 executing",),
        units_consumed=300_000,
        fee_lamports=100_000,
        account_lamports=(1_001_500_000,),
    )

    with patch("backrunner.shadow.compose_direct_round_trip", return_value=compiled) as compose:
        report = run_direct_shadow_route(
            token_mint="mint",
            pump_pool="pump-pool",
            meteora_pool="meteora-pool",
            direction="meteora_to_pump",
            input_lamports=10_000_000,
            taker="user",
            executor_program_id="executor",
            rpc_url="https://rpc.test",
            min_context_slot=997,
            slot_ttl=2,
            lookup_table_addresses=("alt",),
            slippage_bps=100,
            compute_unit_limit=600_000,
            compute_unit_price_micro_lamports=1_000,
            required_gross_profit_lamports=1_295_052,
            tip_lamports=100_000,
            tip_recipient="tip",
            maximum_transaction_fee_lamports=700_000,
            rpc=rpc,
        )

    assert compose.call_args.args[0].valid_until_slot == 999
    rpc.simulate_transaction.assert_called_once_with(
        "AA==",
        min_context_slot=997,
        replace_recent_blockhash=False,
        return_accounts=("user",),
    )
    assert rpc.balance.call_args_list == [
        (("user",), {"min_context_slot": 997}),
        (("user",), {"min_context_slot": 998}),
    ]
    assert report["shadow_approved"] is True
    assert report["economics"]["wallet_net_lamports"] == 1_500_000
    assert report["transaction"]["signed"] is False
    assert report["transactions_submitted"] == 0


def test_direct_shadow_rejects_wallet_baseline_change() -> None:
    compiled = DirectCompiledRoundTrip(
        transaction_base64="AA==",
        recent_blockhash="blockhash",
        last_valid_block_height=100,
        instruction_count=8,
        serialized_size=598,
        account_count=42,
        lookup_table_addresses=("alt",),
        funding_lamports=10_000_000,
        quote_account="quote",
        intermediate_account="token",
        first_program="meteora",
        second_program="pump",
        executor_program="executor",
        signed=False,
        submitted=False,
    )
    rpc = Mock()
    rpc.balance.side_effect = [1_000_000_000, 999_000_000]
    rpc.simulate_transaction.return_value = SimulationResult(
        context_slot=998,
        error=None,
        logs=("executor success",),
        units_consumed=300_000,
        fee_lamports=100_000,
        account_lamports=(1_001_500_000,),
    )

    with patch("backrunner.shadow.compose_direct_round_trip", return_value=compiled):
        report = run_direct_shadow_route(
            token_mint="mint",
            pump_pool="pump-pool",
            meteora_pool="meteora-pool",
            direction="meteora_to_pump",
            input_lamports=10_000_000,
            taker="user",
            executor_program_id="executor",
            rpc_url="https://rpc.test",
            min_context_slot=997,
            slot_ttl=2,
            lookup_table_addresses=("alt",),
            slippage_bps=100,
            compute_unit_limit=600_000,
            compute_unit_price_micro_lamports=1_000,
            required_gross_profit_lamports=1_295_052,
            tip_lamports=100_000,
            tip_recipient="tip",
            maximum_transaction_fee_lamports=700_000,
            rpc=rpc,
        )

    assert report["shadow_approved"] is False
    assert report["economics"]["wallet_net_lamports"] is None
    assert "wallet_baseline_changed" in report["rejection_reasons"]
