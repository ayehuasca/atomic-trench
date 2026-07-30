"""Read-only Pump/Meteora pool discovery for exact shadow routes."""

from dataclasses import asdict, dataclass
from typing import Any

import requests
from solders.pubkey import Pubkey

WSOL = "So11111111111111111111111111111111111111112"
LEGACY_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
PUMP_AMM_PROGRAM = Pubkey.from_string("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")


@dataclass(frozen=True)
class MeteoraPool:
    address: str
    token_x: str
    token_y: str
    tvl_usd: float
    volume_24h_usd: float
    blacklisted: bool


@dataclass(frozen=True)
class DirectPoolRoute:
    mint: str
    pump_pool: str
    meteora_pool: str
    meteora_tvl_usd: float
    meteora_volume_24h_usd: float


def canonical_pump_pool(mint: str) -> str:
    mint_key = Pubkey.from_string(mint)
    quote_key = Pubkey.from_string(WSOL)
    authority, _ = Pubkey.find_program_address(
        [b"pool-authority", bytes(mint_key)], PUMP_PROGRAM
    )
    pool, _ = Pubkey.find_program_address(
        [
            b"pool",
            (0).to_bytes(2, "little"),
            bytes(authority),
            bytes(mint_key),
            bytes(quote_key),
        ],
        PUMP_AMM_PROGRAM,
    )
    return str(pool)


class MeteoraPoolProvider:
    URL = "https://dlmm.datapi.meteora.ag/pools"

    def __init__(self, session: Any | None = None) -> None:
        self.session = session or requests.Session()

    def pools_for_mint(self, mint: str) -> tuple[MeteoraPool, ...]:
        params: dict[str, str | int] = {
            "page": 1,
            "page_size": 100,
            "query": mint,
            "sort_by": "tvl:desc",
            "filter_by": "is_blacklisted=false",
        }
        response = self.session.get(
            self.URL,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise TypeError("Meteora pools response does not contain a data list")

        pools: list[MeteoraPool] = []
        for row in rows:
            token_x = str((row.get("token_x") or {}).get("address") or "")
            token_y = str((row.get("token_y") or {}).get("address") or "")
            if {token_x, token_y} != {mint, WSOL}:
                continue
            pools.append(
                MeteoraPool(
                    address=str(row["address"]),
                    token_x=token_x,
                    token_y=token_y,
                    tvl_usd=float(row.get("tvl") or 0),
                    volume_24h_usd=float((row.get("volume") or {}).get("24h") or 0),
                    blacklisted=bool(row.get("is_blacklisted", False)),
                )
            )
        return tuple(pools)


def discover_direct_routes(
    *,
    mint: str,
    rpc: Any,
    meteora: MeteoraPoolProvider,
    minimum_meteora_tvl_usd: float = 1_000.0,
) -> tuple[DirectPoolRoute, ...]:
    """Return only routes whose Pump pool exists on-chain.

    Accepts both legacy SPL and Token-2022 mints, as long as the Meteora
    pool's transfer-hook slices are zero (no active hooks). Token-2022
    mints with TransferHook extensions are rejected by the executor.
    """
    mint_info = rpc.account_info(mint)
    if mint_info is None:
        return ()
    owner = mint_info.get("owner")
    if owner not in (LEGACY_TOKEN_PROGRAM, "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"):
        return ()

    pump_pool = canonical_pump_pool(mint)
    pump_info = rpc.account_info(pump_pool)
    if pump_info is None or pump_info.get("owner") != str(PUMP_AMM_PROGRAM):
        return ()

    return tuple(
        DirectPoolRoute(
            mint=mint,
            pump_pool=pump_pool,
            meteora_pool=pool.address,
            meteora_tvl_usd=pool.tvl_usd,
            meteora_volume_24h_usd=pool.volume_24h_usd,
        )
        for pool in meteora.pools_for_mint(mint)
        if not pool.blacklisted and pool.tvl_usd >= minimum_meteora_tvl_usd
    )


def routes_as_dicts(routes: tuple[DirectPoolRoute, ...]) -> list[dict[str, Any]]:
    return [asdict(route) for route in routes]
