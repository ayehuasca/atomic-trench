from backrunner.observer import observe_once


class FakeGmgn:
    def trending_mints(self) -> set[str]:
        return {"MintA"}

    def sol_price_usd(self) -> float:
        return 100.0


class FakeRpc:
    def latest_slot(self) -> int:
        return 99

    def block_accounts(self, slot: int) -> dict:
        assert slot == 99
        return {"blockTime": 1, "transactions": []}


def test_observer_is_read_only_and_returns_scanned_slot() -> None:
    report = observe_once(
        rpc=FakeRpc(),
        gmgn=FakeGmgn(),
        minimum_buy_usd=300,
    )

    assert report["mode"] == "DRY_RUN_OBSERVE"
    assert report["slot"] == 99
    assert report["trending_mints"] == 1
    assert report["large_buy_events"] == []
    assert report["transactions_submitted"] == 0


class LaggingRpc(FakeRpc):
    def block_accounts(self, slot: int) -> dict:
        if slot == 99:
            raise RuntimeError("block 99 is unavailable")
        assert slot == 98
        return {"blockTime": 1, "transactions": []}


def test_observer_falls_back_when_latest_finalized_block_is_unavailable() -> None:
    report = observe_once(
        rpc=LaggingRpc(),
        gmgn=FakeGmgn(),
        minimum_buy_usd=300,
    )

    assert report["slot"] == 98
