import json
from pathlib import Path

import pytest

from backrunner import cli
from backrunner.cli import main


class FakeGmgn:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def trending_mints(self) -> set[str]:
        return {"MintA"}

    def sol_price_usd(self) -> float:
        return 100.0


class FakeRpc:
    def __init__(self, url: str, commitment: str) -> None:
        pass

    def latest_slot(self) -> int:
        return 99

    def block_accounts(self, slot: int) -> dict:
        return {"blockTime": 1, "transactions": []}


def test_replay_cli_emits_machine_readable_shadow_report(capsys) -> None:
    exit_code = main(["--config", "config.yaml", "replay"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "DRY_RUN_REPLAY"
    assert payload["tight_backrun_candidates"] == 7
    assert payload["live_execution_enabled"] is False


def test_observe_once_cli_never_submits(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "GmgnProvider", FakeGmgn)
    monkeypatch.setattr(cli, "SolanaRpc", FakeRpc)

    exit_code = main(["--config", "config.yaml", "observe-once"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "DRY_RUN_OBSERVE"
    assert payload["transactions_submitted"] == 0


def test_processed_observe_cli_reconciles_without_submitting(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "observe_processed_once",
        lambda **_kwargs: {
            "mode": "DRY_RUN_PROCESSED_OBSERVE",
            "fork_status": "confirmed",
            "transactions_submitted": 0,
            "live_execution_enabled": False,
        },
    )

    exit_code = main(["--config", "config.yaml", "observe-processed-once"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["fork_status"] == "confirmed"
    assert payload["transactions_submitted"] == 0


def test_shadow_route_cli_remains_unsigned_and_non_broadcasting(monkeypatch, capsys) -> None:
    def fake_shadow(**_kwargs):
        return {
            "mode": "DRY_RUN_SHADOW_ROUTE",
            "shadow_approved": True,
            "transactions_submitted": 0,
            "live_execution_enabled": False,
        }

    monkeypatch.setattr(cli, "run_shadow_route", fake_shadow)

    exit_code = main(
        [
            "--config",
            "config.yaml",
            "shadow-route",
            "--token-mint",
            "MintA",
            "--min-context-slot",
            "99",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "DRY_RUN_SHADOW_ROUTE"
    assert payload["transactions_submitted"] == 0
    assert payload["live_execution_enabled"] is False


def test_direct_shadow_cli_remains_unsigned_and_non_broadcasting(monkeypatch, capsys) -> None:
    def fake_direct(**kwargs):
        assert kwargs["min_context_slot"] == 99
        assert kwargs["executor_program_id"] == "Executor111111111111111111111111111111111"
        return {
            "mode": "DRY_RUN_DIRECT_PUMP_METEORA",
            "transaction": {"signed": False},
            "transactions_submitted": 0,
            "live_execution_enabled": False,
        }

    monkeypatch.setattr(cli, "run_direct_shadow_route", fake_direct)

    exit_code = main(
        [
            "--config",
            "config.yaml",
            "shadow-direct",
            "--token-mint",
            "MintA",
            "--pump-pool",
            "PumpPool",
            "--meteora-pool",
            "MeteoraPool",
            "--direction",
            "meteora_to_pump",
            "--min-context-slot",
            "99",
            "--executor-program-id",
            "Executor111111111111111111111111111111111",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["transaction"]["signed"] is False
    assert payload["transactions_submitted"] == 0
    assert payload["live_execution_enabled"] is False


def test_shadow_evidence_status_reports_gate_failures(tmp_path: Path, capsys) -> None:
    evidence = tmp_path / "shadow-evidence.json"
    attempt_lock = tmp_path / "active-attempt.lock"

    exit_code = main(
        [
            "--config",
            "config.yaml",
            "shadow-evidence-status",
            "--evidence-path",
            str(evidence),
            "--attempt-lock-path",
            str(attempt_lock),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runtime_hours"] == 0.0
    assert payload["candidate_count"] == 0
    assert payload["positive_wallet_net"] is False
    assert payload["active_attempt_lock"] is False
    assert payload["promotion_ready"] is False


def test_direct_shadow_confirmed_trigger_is_locked_and_deduplicated(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    class ConfirmedRpc(FakeRpc):
        def transaction(self, signature: str, *, commitment: str = "confirmed") -> dict:
            assert signature == "trigger-sig"
            assert commitment == "confirmed"
            return {"slot": 99}

    monkeypatch.setattr(cli, "SolanaRpc", ConfirmedRpc)
    monkeypatch.setattr(
        cli,
        "run_direct_shadow_route",
        lambda **_kwargs: {
            "mode": "DRY_RUN_DIRECT_PUMP_METEORA",
            "transaction": {"signed": False},
            "simulation": {"succeeded": True, "context_slot": 99},
            "economics": {"wallet_net_lamports": 500},
            "rejection_reasons": [],
            "transactions_submitted": 0,
            "live_execution_enabled": False,
        },
    )
    evidence = tmp_path / "shadow-evidence.json"
    attempt_lock = tmp_path / "active-attempt.lock"
    argv = [
        "--config",
        "config.yaml",
        "shadow-direct",
        "--token-mint",
        "MintA",
        "--pump-pool",
        "PumpPool",
        "--meteora-pool",
        "MeteoraPool",
        "--direction",
        "meteora_to_pump",
        "--min-context-slot",
        "99",
        "--executor-program-id",
        "Executor111111111111111111111111111111111",
        "--trigger-signature",
        "trigger-sig",
        "--evidence-path",
        str(evidence),
        "--attempt-lock-path",
        str(attempt_lock),
    ]

    assert main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["evidence"]["candidate_count"] == 1
    assert not attempt_lock.exists()

    with pytest.raises(cli.DuplicateCandidateError):
        main(argv)
