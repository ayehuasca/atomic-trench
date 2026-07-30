"""Read-only Jupiter Swap V2 `/build` routing.

This module has no execute or submit method by design.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

WSOL = "So11111111111111111111111111111111111111112"
BUILD_URL = "https://api.jup.ag/swap/v2/build"


@dataclass(frozen=True)
class SwapBuild:
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    other_amount_threshold: int
    slippage_bps: int
    pool_keys: tuple[str, ...]
    pool_labels: tuple[str, ...]
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SwapBuild":
        plans = payload.get("routePlan") or []
        if not plans:
            raise ValueError("Jupiter returned no route plan")
        return cls(
            input_mint=str(payload["inputMint"]),
            output_mint=str(payload["outputMint"]),
            in_amount=int(payload["inAmount"]),
            out_amount=int(payload["outAmount"]),
            other_amount_threshold=int(payload["otherAmountThreshold"]),
            slippage_bps=int(payload["slippageBps"]),
            pool_keys=tuple(str(plan["swapInfo"]["ammKey"]) for plan in plans),
            pool_labels=tuple(str(plan["swapInfo"]["label"]) for plan in plans),
            raw=payload,
        )


@dataclass(frozen=True)
class RoundTripBuild:
    input_lamports: int
    buy: SwapBuild
    sell: SwapBuild

    @property
    def distinct_pools(self) -> bool:
        return set(self.buy.pool_keys).isdisjoint(self.sell.pool_keys)

    @property
    def conservative_gross_lamports(self) -> int:
        return self.sell.other_amount_threshold - self.input_lamports


class JupiterBuildClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        session: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()
        self.sleeper = sleeper

    @property
    def headers(self) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def build(
        self,
        *,
        input_mint: str,
        output_mint: str,
        amount: int,
        taker: str,
        slippage_bps: int,
        max_accounts: int,
    ) -> SwapBuild:
        if amount <= 0:
            raise ValueError("Jupiter build amount must be positive")
        response = self.session.get(
            BUILD_URL,
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "taker": taker,
                "slippageBps": str(slippage_bps),
                "maxAccounts": str(max_accounts),
                "mode": "fast",
                "blockhashSlotsToExpiry": "30",
            },
            headers=self.headers,
            timeout=60,
        )
        response.raise_for_status()
        return SwapBuild.from_payload(response.json())

    def build_round_trip(
        self,
        *,
        token_mint: str,
        input_lamports: int,
        taker: str,
        slippage_bps: int,
        max_accounts: int,
    ) -> RoundTripBuild:
        buy = self.build(
            input_mint=WSOL,
            output_mint=token_mint,
            amount=input_lamports,
            taker=taker,
            slippage_bps=slippage_bps,
            max_accounts=max_accounts,
        )
        # Keyless Jupiter is limited to 0.5 RPS. The second leg deliberately
        # uses the first leg's minimum output so it cannot overspend tokens.
        if self.api_key is None:
            self.sleeper(2.1)
        sell = self.build(
            input_mint=token_mint,
            output_mint=WSOL,
            amount=buy.other_amount_threshold,
            taker=taker,
            slippage_bps=slippage_bps,
            max_accounts=max_accounts,
        )
        return RoundTripBuild(input_lamports=input_lamports, buy=buy, sell=sell)
