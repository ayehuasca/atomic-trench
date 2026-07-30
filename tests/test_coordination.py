from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backrunner.coordination import (
    ActiveAttemptError,
    AttemptLock,
    DuplicateCandidateError,
    ShadowEvidence,
    ShadowEvidenceRecord,
)


def record(signature: str, *, net_lamports: int = 100) -> ShadowEvidenceRecord:
    return ShadowEvidenceRecord(
        signature=signature,
        observed_at="2026-07-30T00:00:00+00:00",
        simulation_slot=123,
        simulation_succeeded=net_lamports > 0,
        wallet_net_lamports=net_lamports,
        rejection_reasons=(),
    )


def test_attempt_lock_is_exclusive_and_owner_releases_it(tmp_path: Path) -> None:
    path = tmp_path / "active-attempt.lock"
    with AttemptLock(path, candidate_id="sig-a"):
        assert path.exists()
        with pytest.raises(ActiveAttemptError), AttemptLock(
            path, candidate_id="sig-b"
        ):
            pass

    assert not path.exists()


def test_shadow_evidence_rejects_duplicate_signatures_and_summarizes(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    evidence = ShadowEvidence(path)
    started = datetime(2026, 7, 30, tzinfo=UTC)
    for hour in range(73):
        evidence.heartbeat(at=started + timedelta(hours=hour), maximum_gap_seconds=3_601)
    evidence.record(record("sig-a", net_lamports=500))
    evidence.record(record("sig-b", net_lamports=-200))

    with pytest.raises(DuplicateCandidateError):
        evidence.record(record("sig-a"))

    summary = evidence.summary()
    assert summary.candidate_count == 2
    assert summary.succeeded_count == 1
    assert summary.wallet_net_lamports == 300
    assert summary.runtime_hours == 72.0
    assert summary.ready(minimum_hours=72, minimum_candidates=2) is True


def test_shadow_runtime_does_not_count_heartbeat_gaps(tmp_path: Path) -> None:
    evidence = ShadowEvidence(tmp_path / "evidence.json")
    started = datetime(2026, 7, 30, tzinfo=UTC)
    evidence.heartbeat(at=started, maximum_gap_seconds=120)
    evidence.heartbeat(at=started + timedelta(hours=72), maximum_gap_seconds=120)

    assert evidence.summary().runtime_hours == 0.0
