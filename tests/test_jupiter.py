from typing import Any

from backrunner.jupiter import JupiterBuildClient

TAKER = "MRiYA4oN3158fCV8evhuCofrDzbHyYvYnGZUDJvoCsa"
WSOL = "So11111111111111111111111111111111111111112"
TOKEN = "TokenMint111111111111111111111111111111111"


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.status_code = 200
        self.text = "ok"

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(self.responses.pop(0))


def build_payload(
    *, input_mint: str, output_mint: str, amount: int, out: int, threshold: int, pool: str
) -> dict[str, Any]:
    instruction = {"programId": "Program1", "accounts": [], "data": "AA=="}
    return {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "inAmount": str(amount),
        "outAmount": str(out),
        "otherAmountThreshold": str(threshold),
        "slippageBps": 100,
        "routePlan": [
            {
                "percent": 100,
                "bps": 10_000,
                "swapInfo": {
                    "ammKey": pool,
                    "label": "Test DEX",
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "inAmount": str(amount),
                    "outAmount": str(out),
                },
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


def test_round_trip_uses_first_leg_minimum_output_and_distinct_pools() -> None:
    session = FakeSession(
        [
            build_payload(
                input_mint=WSOL,
                output_mint=TOKEN,
                amount=10_000_000,
                out=1_000_000,
                threshold=990_000,
                pool="PoolA",
            ),
            build_payload(
                input_mint=TOKEN,
                output_mint=WSOL,
                amount=990_000,
                out=10_200_000,
                threshold=10_098_000,
                pool="PoolB",
            ),
        ]
    )
    sleeps: list[float] = []
    client = JupiterBuildClient(session=session, sleeper=sleeps.append)

    route = client.build_round_trip(
        token_mint=TOKEN,
        input_lamports=10_000_000,
        taker=TAKER,
        slippage_bps=100,
        max_accounts=50,
    )

    assert session.calls[1]["params"]["amount"] == "990000"
    assert sleeps == [2.1]
    assert route.distinct_pools is True
    assert route.conservative_gross_lamports == 98_000
    assert route.buy.pool_keys == ("PoolA",)
    assert route.sell.pool_keys == ("PoolB",)
    assert "x-api-key" not in session.calls[0]["headers"]
