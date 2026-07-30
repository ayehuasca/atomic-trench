#!/usr/bin/env python3
"""Persistent, strictly no-submit Pump/Meteora observer.

This process keeps one processed-commitment WebSocket open, reconciles matching
transactions at confirmed commitment, and records qualifying $300+ observations.
It never loads a keypair, builds a signed transaction, or calls sendTransaction.
"""

import json
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from websocket import WebSocketException

WORKDIR = Path(os.environ.get("ATOMIC_TRENCH_DIR", str(Path.home() / "atomic-trench")))
CONFIG_PATH = WORKDIR / "config.yaml"
DATA_DIR = WORKDIR / "data"
CANDIDATES_PATH = DATA_DIR / "processed-candidates.jsonl"
EVIDENCE_PATH = DATA_DIR / "shadow_evidence.jsonl"

os.chdir(WORKDIR)
sys.path.insert(0, str(WORKDIR))


def log(message: str) -> None:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{timestamp}] {message}", flush=True)


def append_candidate(record: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CANDIDATES_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    from backrunner.config import load_config
    from backrunner.coordination import ShadowEvidence
    from backrunner.detector import detect_large_buys
    from backrunner.pools import MeteoraPoolProvider, discover_direct_routes, routes_as_dicts
    from backrunner.providers import GmgnProvider, SolanaRpc
    from backrunner.stream import ProcessedLogStream, normalize_transaction

    config = load_config(CONFIG_PATH)
    if not config.dry_run:
        raise RuntimeError("persistent observer refuses non-dry-run configuration")

    evidence = ShadowEvidence(EVIDENCE_PATH)
    gmgn = GmgnProvider(
        api_key=os.getenv("GMGN_API_KEY"),
        fallback_sol_price_usd=config.sol_price_usd,
    )
    rpc = SolanaRpc(config.rpc_url, "processed")
    meteora = MeteoraPoolProvider()

    trending_mints = gmgn.trending_mints()
    sol_price = gmgn.sol_price_usd()
    last_heartbeat = 0.0
    last_refresh = time.monotonic()
    seen_candidates: set[str] = set()
    venue_events = 0
    confirmed_events = 0
    candidate_count = 0

    log(
        "no-submit observer starting: "
        f"threshold>=${config.minimum_buy_usd:.2f}, trending={len(trending_mints)}, "
        f"SOL=${sol_price:.2f}"
    )

    while True:
        try:
            with ProcessedLogStream(config.rpc_url) as stream:
                log("WebSocket connected; transaction submission is disabled")
                while True:
                    now = time.monotonic()
                    if now - last_heartbeat >= 60:
                        evidence.heartbeat()
                        log(
                            "heartbeat: "
                            f"venue_events={venue_events}, confirmed={confirmed_events}, "
                            f"candidates={candidate_count}, submissions=0"
                        )
                        last_heartbeat = now
                    if now - last_refresh >= 120:
                        refreshed = gmgn.trending_mints()
                        if refreshed:
                            trending_mints = refreshed
                        sol_price = gmgn.sol_price_usd()
                        log(f"trending refresh: {len(trending_mints)} mints, SOL=${sol_price:.2f}")
                        last_refresh = now

                    event = stream.poll_event()
                    if event is None:
                        continue
                    venue_events += 1
                    if not event.succeeded:
                        continue

                    transaction = None
                    for _ in range(12):
                        transaction = rpc.transaction(event.signature, commitment="confirmed")
                        if transaction is not None:
                            break
                        time.sleep(0.1)
                    if transaction is None:
                        continue
                    confirmed_events += 1

                    candidates = detect_large_buys(
                        block=normalize_transaction(transaction),
                        slot=event.slot,
                        trending_mints=trending_mints,
                        sol_price_usd=sol_price,
                        minimum_buy_usd=config.minimum_buy_usd,
                    )
                    for candidate in candidates:
                        candidate_id = f"{candidate.signature}:{candidate.transaction_index}:{candidate.mint}"
                        if candidate_id in seen_candidates:
                            continue
                        seen_candidates.add(candidate_id)
                        candidate_count += 1
                        routes = discover_direct_routes(
                            mint=candidate.mint,
                            rpc=rpc,
                            meteora=meteora,
                        )
                        record = {
                            "observed_at": datetime.now(UTC).isoformat(),
                            "mode": "NO_SUBMIT_PROCESSED_CANDIDATE",
                            **asdict(candidate),
                            "threshold_usd": config.minimum_buy_usd,
                            "direct_routes": routes_as_dicts(routes),
                            "transactions_submitted": 0,
                            "live_execution_enabled": False,
                        }
                        append_candidate(record)
                        log(
                            f"candidate: signature={candidate.signature}, mint={candidate.mint}, "
                            f"buy_usd=${candidate.buy_usd:.2f}, routes={len(routes)}, "
                            "submissions=0"
                        )
        except KeyboardInterrupt:
            log("observer stopped")
            return 0
        except (
            json.JSONDecodeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            requests.RequestException,
            WebSocketException,
        ) as exc:
            log(f"stream error: {type(exc).__name__}: {exc}; reconnecting in 5s")
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
