"""Read-only detection of confirmed price-moving buys in Solana blocks."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

WSOL = "So11111111111111111111111111111111111111112"
STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


@dataclass(frozen=True)
class LargeBuyEvent:
    slot: int
    transaction_index: int
    block_time: int
    signature: str
    buyer: str
    mint: str
    buy_sol: float
    buy_usd: float
    token_received: float


def _pubkey(value: Any) -> str:
    return str(value["pubkey"] if isinstance(value, dict) else value)


def detect_large_buys(
    *,
    block: dict[str, Any],
    slot: int,
    trending_mints: set[str],
    sol_price_usd: float,
    minimum_buy_usd: float,
) -> list[LargeBuyEvent]:
    events: list[LargeBuyEvent] = []
    for transaction_index, record in enumerate(block.get("transactions", [])):
        meta = record.get("meta") or {}
        if meta.get("err") is not None:
            continue
        transaction = record.get("transaction") or {}
        account_keys = [_pubkey(key) for key in transaction.get("accountKeys", [])]
        deltas: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for field, sign in (("preTokenBalances", -1.0), ("postTokenBalances", 1.0)):
            for balance in meta.get(field, []):
                owner = balance.get("owner")
                if not owner:
                    continue
                amount = balance["uiTokenAmount"]
                value = int(amount["amount"]) / (10 ** int(amount["decimals"]))
                deltas[str(owner)][str(balance["mint"])] += sign * value

        native_delta = {
            key: (int(meta["postBalances"][i]) - int(meta["preBalances"][i])) / 1e9
            for i, key in enumerate(account_keys)
        }
        signature = str((transaction.get("signatures") or [""])[0])
        for buyer, mint_deltas in deltas.items():
            for mint in trending_mints:
                received = mint_deltas.get(mint, 0.0)
                if received <= 0:
                    continue
                token_quote_sol = max(0.0, -mint_deltas.get(WSOL, 0.0))
                stable_quote_sol = sum(
                    max(0.0, -mint_deltas.get(stable, 0.0)) / sol_price_usd
                    for stable in STABLE_MINTS
                )
                native_quote_sol = max(0.0, -native_delta.get(buyer, 0.0))
                buy_sol = max(token_quote_sol + stable_quote_sol, native_quote_sol)
                buy_usd = buy_sol * sol_price_usd
                if buy_usd < minimum_buy_usd:
                    continue
                events.append(
                    LargeBuyEvent(
                        slot=slot,
                        transaction_index=transaction_index,
                        block_time=int(block.get("blockTime") or 0),
                        signature=signature,
                        buyer=buyer,
                        mint=mint,
                        buy_sol=buy_sol,
                        buy_usd=buy_usd,
                        token_received=received,
                    )
                )
    return events
