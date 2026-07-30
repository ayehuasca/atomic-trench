from pathlib import Path

from backrunner.replay import load_replay, replay_precursors

FIXTURE = Path("fixtures/wallet_a_replay.json")


def test_replay_finds_wallet_a_tight_same_slot_backruns() -> None:
    records = load_replay(FIXTURE)
    result = replay_precursors(
        records,
        minimum_precursor_usd=300,
        maximum_transaction_gap=3,
    )

    assert result.total_round_trips == 16
    assert result.same_slot_precursors == 12
    assert result.tight_backrun_candidates == 10
    assert result.profitable_tight_candidates == 10
    assert "5jGMpPDuQ5mNZUALLm5LeYVcKAweMEMM2DEpDTbVePNtbfDymBVGTiL3y13QRgVG3hUn5cP3zcTAPkyuqeVNqxb3" in result.signatures
