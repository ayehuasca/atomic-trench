import json
from unittest.mock import patch

from backrunner.composer import DirectRouteRequest, compose_direct_round_trip


def test_direct_composer_stays_unsigned_and_passes_context_slot() -> None:
    completed = type(
        "Completed",
        (),
        {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "unsignedTransactionBase64": "AA==",
                    "recentBlockhash": "blockhash",
                    "lastValidBlockHeight": 123,
                    "serializedBytes": 598,
                    "accountCount": 42,
                    "instructionCount": 8,
                    "lookupTableAddresses": ["alt"],
                    "fundingLamports": "10000000",
                    "quoteAccount": "quote",
                    "intermediateAccount": "token",
                    "firstProgram": "meteora",
                    "secondProgram": "pump",
                    "executorProgram": "executor",
                    "signed": False,
                    "submitted": False,
                }
            ),
            "stderr": "",
        },
    )()
    request = DirectRouteRequest(
        rpc_url="https://rpc.test",
        executor_program_id="executor",
        user="user",
        pump_pool="pump-pool",
        meteora_pool="meteora-pool",
        intermediate_mint="mint",
        input_lamports=10_000_000,
        direction="meteora_to_pump",
        minimum_profit_lamports=1_000_000,
        valid_until_slot=999,
        min_context_slot=997,
        lookup_table_addresses=("alt",),
    )

    with patch("backrunner.composer.subprocess.run", return_value=completed) as run:
        result = compose_direct_round_trip(request)

    payload = json.loads(run.call_args.kwargs["input"])
    assert payload["minContextSlot"] == 997
    assert payload["validUntilSlot"] == 999
    assert payload["lookupTableAddresses"] == ["alt"]
    assert result.serialized_size == 598
    assert result.signed is False
    assert result.submitted is False
