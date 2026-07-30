from typing import Any

from backrunner.providers import GmgnProvider, SolanaRpc, parse_sol_price, parse_trending_mints


def test_gmgn_rank_and_sol_price_are_normalized() -> None:
    rank = {
        "code": 0,
        "data": {
            "rank": [
                {"address": "MintA", "symbol": "A"},
                {"address": "MintB", "symbol": "B"},
            ]
        },
    }
    sol = {"price": {"price": "73.385944"}}

    assert parse_trending_mints(rank) == {"MintA", "MintB"}
    assert parse_sol_price(sol) == 73.385944


class FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "result": {
                "context": {"slot": 123},
                "value": {
                    "err": None,
                    "logs": ["Program success"],
                    "unitsConsumed": 456_789,
                    "fee": 5_000,
                    "accounts": [{"lamports": 1_005_000}, None],
                },
            },
        }


class FakeSession:
    def __init__(self) -> None:
        self.body: dict[str, Any] | None = None

    def post(self, _url: str, **kwargs: Any) -> FakeResponse:
        self.body = kwargs["json"]
        return FakeResponse()


def test_unsigned_transaction_simulation_is_non_broadcasting() -> None:
    session = FakeSession()
    rpc = SolanaRpc("https://rpc.invalid", commitment="confirmed", session=session)

    result = rpc.simulate_transaction(
        "base64-transaction",
        min_context_slot=100,
        return_accounts=("taker", "closed-account"),
    )

    assert result.succeeded is True
    assert result.units_consumed == 456_789
    assert result.fee_lamports == 5_000
    assert result.account_lamports == (1_005_000, None)
    assert session.body is not None
    assert session.body["method"] == "simulateTransaction"
    config = session.body["params"][1]
    assert config["sigVerify"] is False
    assert config["replaceRecentBlockhash"] is True
    assert config["minContextSlot"] == 100
    assert config["accounts"] == {
        "addresses": ["taker", "closed-account"],
        "encoding": "base64",
    }


class GmgnResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self.payload


class GmgnSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> GmgnResponse:
        self.calls.append((url, kwargs))
        if url.startswith("https://openapi.gmgn.ai/v1/token/info"):
            return GmgnResponse({"code": 0, "data": {"price": {"price": "75.25"}}})
        if url.startswith("https://openapi.gmgn.ai/v1/market/rank"):
            interval = kwargs["params"]["interval"]
            return GmgnResponse(
                {"code": 0, "data": {"rank": [{"address": f"Mint-{interval}"}]}}
            )
        interval = url.rsplit("/", 1)[-1]
        return GmgnResponse(
            {"code": 0, "data": {"rank": [{"address": f"Mint-{interval}"}]}}
        )


def test_gmgn_provider_uses_public_discovery_without_auth() -> None:
    session = GmgnSession()
    provider = GmgnProvider(session=session, fallback_sol_price_usd=73.0)

    assert provider.trending_mints() == {
        "Mint-5m",
        "Mint-1h",
        "Mint-6h",
        "Mint-24h",
    }
    assert provider.sol_price_usd() == 73.0
    assert all("rank/sol/swaps" in url for url, _kwargs in session.calls)
    assert all("x-route-key" not in kwargs["headers"] for _url, kwargs in session.calls)


def test_gmgn_api_key_is_read_only_token_price_auth() -> None:
    session = GmgnSession()
    provider = GmgnProvider(
        session=session,
        api_key="test-only-key",
        fallback_sol_price_usd=73.0,
    )

    assert provider.sol_price_usd() == 75.25
    url, kwargs = session.calls[0]
    assert url.startswith("https://openapi.gmgn.ai/v1/token/info")
    assert kwargs["headers"]["X-APIKEY"] == "test-only-key"
    assert "/v1/trade/" not in url
    assert "X-Signature" not in kwargs["headers"]


def test_gmgn_api_key_uses_read_only_openapi_ranking() -> None:
    session = GmgnSession()
    provider = GmgnProvider(
        session=session,
        api_key="test-only-key",
        fallback_sol_price_usd=73.0,
    )

    assert len(provider.trending_mints()) == 4
    assert all("/v1/market/rank" in url for url, _kwargs in session.calls)
    assert all(
        kwargs["headers"]["X-APIKEY"] == "test-only-key"
        for _url, kwargs in session.calls
    )
    assert all("X-Signature" not in kwargs["headers"] for _url, kwargs in session.calls)
