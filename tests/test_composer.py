from backrunner.composer import compose_round_trip
from backrunner.jupiter import RoundTripBuild, SwapBuild

TAKER = "MRiYA4oN3158fCV8evhuCofrDzbHyYvYnGZUDJvoCsa"
WSOL = "So11111111111111111111111111111111111111112"
TOKEN = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def leg(input_mint: str, output_mint: str, amount: int, out: int, pool: str) -> SwapBuild:
    instruction = {
        "programId": "11111111111111111111111111111111",
        "accounts": [],
        "data": "AA==",
    }
    raw = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "inAmount": str(amount),
        "outAmount": str(out),
        "otherAmountThreshold": str(out),
        "slippageBps": 0,
        "routePlan": [
            {
                "swapInfo": {
                    "ammKey": pool,
                    "label": "Test",
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "inAmount": str(amount),
                    "outAmount": str(out),
                }
            }
        ],
        "computeBudgetInstructions": [],
        "setupInstructions": [],
        "swapInstruction": instruction,
        "cleanupInstruction": None,
        "otherInstructions": [],
        "tipInstruction": None,
        "addressesByLookupTableAddress": None,
        "blockhashWithMetadata": {"blockhash": [0] * 32, "lastValidBlockHeight": 10},
    }
    return SwapBuild.from_payload(raw)


def test_composes_two_legs_into_one_unsigned_v0_transaction() -> None:
    route = RoundTripBuild(
        input_lamports=10_000_000,
        buy=leg(WSOL, TOKEN, 10_000_000, 1_000_000, "PoolA"),
        sell=leg(TOKEN, WSOL, 1_000_000, 10_100_000, "PoolB"),
    )

    compiled = compose_round_trip(route=route, taker=TAKER)

    assert compiled.transaction_base64
    assert compiled.version == 0
    assert compiled.instruction_count == 3  # max-CU + two swap instructions
    assert compiled.serialized_size <= 1232
    assert compiled.account_count <= 64
    assert compiled.signature_count == 1
