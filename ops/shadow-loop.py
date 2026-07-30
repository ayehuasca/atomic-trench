#!/usr/bin/env python3
"""Continuous no-submit shadow observer loop for Atomic Trench VPS deployment.

Runs observe-once + shadow-direct in a loop, recording evidence and heartbeats.
Submits zero transactions. Fails closed without configured executor/ALT.
"""
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

WORKDIR = Path(os.environ.get("ATOMIC_TRENCH_DIR", str(Path.home() / "atomic-trench")))
DATA_DIR = WORKDIR / "data"
CONFIG = WORKDIR / "config.yaml"
VENV_PYTHON = WORKDIR / ".venv" / "bin" / "python"
EVIDENCE_PATH = DATA_DIR / "shadow_evidence.jsonl"
LOCK_DIR = DATA_DIR / "locks"
MIN_OBSERVE_INTERVAL = 30  # seconds between observation ticks
HEARTBEAT_INTERVAL = 60   # seconds between evidence heartbeats


def log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def run_shadow_direct(trigger: dict) -> dict | None:
    """Run direct shadow simulation for a trigger event. Returns report dict or None."""
    mint = trigger.get("mint", "")
    pump_pool = trigger.get("pump_pool", "")
    meteora_pool = trigger.get("meteora_pool", "")
    direction = trigger.get("direction", "pump_to_meteora")
    slot = trigger.get("slot", 0)
    
    if not mint or not pump_pool or not meteora_pool:
        return None
    
    try:
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", str(WORKDIR))
        result = subprocess.run(
            [str(VENV_PYTHON), "-m", "backrunner.cli", "--config", str(CONFIG),
             "shadow-direct",
             "--token-mint", mint,
             "--pump-pool", pump_pool,
             "--meteora-pool", meteora_pool,
             "--direction", direction,
             "--min-context-slot", str(max(0, slot - 10)),
             "--executor-program-id", "",
             "--lookup-table", str(CONFIG.parent / ".keys" / "ALT")],
            capture_output=True, text=True, timeout=120, cwd=str(WORKDIR), env=env,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception as exc:
        log(f"shadow-direct error: {exc}")
        return None


def run_observe() -> dict | None:
    """Run the processed-commitment observer. Returns report dict or None on error."""
    try:
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", str(WORKDIR))
        result = subprocess.run(
            [str(VENV_PYTHON), "-m", "backrunner.cli", "--config", str(CONFIG),
             "observe-processed-once"],
            capture_output=True, text=True, timeout=120, cwd=str(WORKDIR), env=env,
        )
        if result.returncode != 0:
            log(f"observer failed (rc={result.returncode}): {result.stderr.strip()[:200]}")
            return None
        report = json.loads(result.stdout)
        log(f"observe: slot={report.get('slot')}, events={len(report.get('large_buy_events', []))}")
        return report
    except subprocess.TimeoutExpired:
        log("observer timed out")
        return None
    except (json.JSONDecodeError, ValueError) as exc:
        log(f"observer output parse error: {exc}")
        return None


def heartbeat(evidence: "ShadowEvidence | None" = None) -> None:
    """Write a heartbeat to evidence store or simply echo."""
    if evidence is not None:
        try:
            evidence.heartbeat()
            summary = evidence.summary()
            log(f"heartbeat: {summary.runtime_hours:.1f}h, "
                f"{summary.candidate_count} candidates, "
                f"{summary.wallet_net_lamports} lamports net")
        except Exception as exc:
            log(f"heartbeat error: {exc}")
    else:
        log("heartbeat (no evidence store)")


def main() -> int:
    log("Atomic Trench shadow observer starting")
    log(f"workdir={WORKDIR}, evidence={EVIDENCE_PATH}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_DIR.mkdir(parents=True, exist_ok=True)

    # Load evidence store for persistent recordkeeping
    try:
        from backrunner.coordination import ShadowEvidence
        evidence = ShadowEvidence(EVIDENCE_PATH)
        log("evidence store ready")
    except Exception as exc:
        log(f"cannot load evidence store: {exc}")
        evidence = None

    last_heartbeat = 0.0
    tick = 0

    while True:
        tick += 1
        now = time.monotonic()

        # Heartbeat on schedule
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            heartbeat(evidence)
            last_heartbeat = now

        # Run observation
        report = run_observe()

        # TODO: when executor and ALT are configured, wire direct-shadow
        # simulation here for each trigger event.

        # Throttle
        elapsed = time.monotonic() - now
        sleep = max(0, MIN_OBSERVE_INTERVAL - elapsed)
        if sleep > 0:
            time.sleep(sleep)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
