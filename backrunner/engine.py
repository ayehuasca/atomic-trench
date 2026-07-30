"""Pure profitability checks for an all-or-nothing round trip."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    transaction_fee_lamports: int
    jito_tip_lamports: int
    safety_margin_lamports: int
    minimum_net_profit_lamports: int
    failed_attempt_reserve_lamports: int = 0

    @property
    def total_lamports(self) -> int:
        return (
            self.transaction_fee_lamports
            + self.jito_tip_lamports
            + self.safety_margin_lamports
            + self.failed_attempt_reserve_lamports
        )


@dataclass(frozen=True)
class RoundTripResult:
    executable: bool
    gross_profit_lamports: int
    net_profit_lamports: int
    return_bps: int


def evaluate_round_trip(
    *, buy_lamports: int, sell_lamports: int, costs: CostModel
) -> RoundTripResult:
    if buy_lamports <= 0:
        raise ValueError("buy_lamports must be positive")
    gross = sell_lamports - buy_lamports
    net = gross - costs.total_lamports
    return RoundTripResult(
        executable=net >= costs.minimum_net_profit_lamports,
        gross_profit_lamports=gross,
        net_profit_lamports=net,
        return_bps=(net * 10_000) // buy_lamports,
    )
