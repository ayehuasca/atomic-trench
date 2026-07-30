from typing import Any

from backrunner.pools import (
    LEGACY_TOKEN_PROGRAM,
    PUMP_AMM_PROGRAM,
    WSOL,
    MeteoraPoolProvider,
    canonical_pump_pool,
    discover_direct_routes,
)

MINT = "US517G5965aydkZ46HS38QLi7UQiSojurfbQfKCELFx"
PUMP_POOL = "3GVnYDHddtPRfxyn6MrwQbw2uPd4pgNniHAN7ve2Zrc1"
METEORA_POOL = "11111111111111111111111111111111"


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class _Session:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.params: dict[str, Any] | None = None

    def get(self, _url: str, *, params: dict[str, Any], timeout: int) -> _Response:
        assert timeout == 30
        self.params = params
        return _Response(self.payload)


class _Rpc:
    def __init__(self, owners: dict[str, str]) -> None:
        self.owners = owners

    def account_info(self, address: str) -> dict[str, str] | None:
        owner = self.owners.get(address)
        return None if owner is None else {"owner": owner}


def _pool_row(*, tvl: float = 2_000, blacklisted: bool = False) -> dict[str, Any]:
    return {
        "address": METEORA_POOL,
        "token_x": {"address": MINT},
        "token_y": {"address": WSOL},
        "tvl": tvl,
        "volume": {"24h": 1234.5},
        "is_blacklisted": blacklisted,
    }


def test_python_pump_pool_derivation_matches_official_sdk() -> None:
    assert canonical_pump_pool(MINT) == PUMP_POOL


def test_meteora_provider_filters_to_exact_wsol_pair() -> None:
    session = _Session({"data": [_pool_row(), {**_pool_row(), "token_y": {"address": MINT}}]})
    provider = MeteoraPoolProvider(session=session)

    pools = provider.pools_for_mint(MINT)

    assert len(pools) == 1
    assert pools[0].address == METEORA_POOL
    assert session.params is not None
    assert session.params["query"] == MINT


def test_discovery_accepts_token_2022_mints_with_existing_pump_pool() -> None:
    """pump.fun tokens use Token-2022 but have Pump AMM + Meteora pools."""
    owners = {
        MINT: "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        PUMP_POOL: str(PUMP_AMM_PROGRAM),
    }
    rpc = _Rpc(owners)
    meteora = MeteoraPoolProvider(session=_Session({"data": [_pool_row(tvl=2_000)]}))

    routes = discover_direct_routes(mint=MINT, rpc=rpc, meteora=meteora)
    assert len(routes) == 1
    assert routes[0].pump_pool == PUMP_POOL
    assert routes[0].meteora_pool == METEORA_POOL


def test_discovery_requires_existing_pump_pool_and_minimum_meteora_tvl() -> None:
    owners = {MINT: LEGACY_TOKEN_PROGRAM, PUMP_POOL: str(PUMP_AMM_PROGRAM)}
    rpc = _Rpc(owners)
    low_tvl = MeteoraPoolProvider(session=_Session({"data": [_pool_row(tvl=999)]}))
    valid = MeteoraPoolProvider(session=_Session({"data": [_pool_row(tvl=2_000)]}))

    assert discover_direct_routes(mint=MINT, rpc=rpc, meteora=low_tvl) == ()
    routes = discover_direct_routes(mint=MINT, rpc=rpc, meteora=valid)
    assert len(routes) == 1
    assert routes[0].pump_pool == PUMP_POOL
    assert routes[0].meteora_pool == METEORA_POOL
