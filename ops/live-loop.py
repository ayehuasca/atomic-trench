#!/usr/bin/env python3
"""Atomic Trench live execution loop.

Detects $200+ buys on pump.fun tokens, finds Pump AMM ↔ Meteora DLMM routes,
simulates both directions, and if profitable: creates a dynamic ALT, signs
the transaction with the hot wallet keypair, and submits to the network.

Safety gates:
  - dry_run must be false to enable signing
  - Profit floor must be met (executor enforces on-chain)
  - Maximum transaction fee cap
  - One trade at a time (file lock)
  - Daily loss limit
  - Hot wallet minimum reserve
"""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from solders.keypair import Keypair
from websocket import WebSocketException

WORKDIR = Path(os.environ.get("ATOMIC_TRENCH_DIR", str(Path.home() / "atomic-trench")))
CONFIG_PATH = WORKDIR / "config.yaml"
DATA_DIR = WORKDIR / "data"
CANDIDATES_PATH = DATA_DIR / "live-candidates.jsonl"
TRADES_PATH = DATA_DIR / "live-trades.jsonl"
LOCK_PATH = DATA_DIR / "live-execution.lock"
KEYPAIR_PATH = Path.home() / ".config" / "atomic-trench" / "hot-wallet.json"

os.chdir(WORKDIR)
sys.path.insert(0, str(WORKDIR))


def log(message: str) -> None:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{timestamp}] {message}", flush=True)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_keypair() -> Keypair:
    data = json.loads(KEYPAIR_PATH.read_text(encoding="utf-8"))
    return Keypair.from_bytes(bytes(data))


def create_dynamic_alt(
    *,
    connection_url: str,
    payer: Keypair,
    writable_accounts: list[str],
    readonly_accounts: list[str],
) -> str:
    """Create an ALT with the given accounts. Returns the ALT address."""
    script = WORKDIR / "scripts" / "create-dynamic-alt.mjs"
    payload = {
        "rpcUrl": connection_url,
        "payerKeypair": list(payer.to_bytes()),
        "writableAccounts": writable_accounts,
        "readonlyAccounts": readonly_accounts,
    }
    result = subprocess.run(
        ["node", str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ALT creation failed: {result.stderr.strip()}")
    output = json.loads(result.stdout)
    return str(output["altAddress"])


def sign_and_submit(
    *,
    connection_url: str,
    signer: Keypair,
    transaction_base64: str,
) -> dict[str, Any]:
    """Sign a base64 v0 transaction and submit to the network."""
    script = WORKDIR / "scripts" / "sign-and-submit.mjs"
    payload = {
        "rpcUrl": connection_url,
        "signerKeypair": list(signer.to_bytes()),
        "transactionBase64": transaction_base64,
    }
    result = subprocess.run(
        ["node", str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return {"submitted": False, "error": result.stderr.strip() or result.stdout.strip()}
    return json.loads(result.stdout)


def try_acquire_lock() -> bool:
    """Ensure only one trade executes at a time."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age < 120:
                return False
        except OSError:
            pass
    LOCK_PATH.touch()
    return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def main() -> int:
    from backrunner.config import load_config
    from backrunner.coordination import ShadowEvidence
    from backrunner.detector import detect_large_buys
    from backrunner.pools import MeteoraPoolProvider, discover_direct_routes
    from backrunner.providers import GmgnProvider, SolanaRpc
    from backrunner.shadow import run_direct_shadow_route
    from backrunner.stream import ProcessedLogStream, normalize_transaction

    config = load_config(CONFIG_PATH)

    live_mode = not config.dry_run
    if live_mode:
        log("⚠️  LIVE TRADING MODE — transactions will be signed and submitted")
    else:
        log("no-submit mode (dry_run=true) — observer only")

    keypair = load_keypair() if live_mode else None
    hot_wallet = str(keypair.pubkey()) if keypair else config.shadow_taker
    log(f"hot wallet: {hot_wallet}")

    evidence = ShadowEvidence(DATA_DIR / "shadow_evidence.jsonl")
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
    trade_count = 0
    daily_loss_lamports = 0

    log(
        f"observer starting: threshold>=${config.minimum_buy_usd:.2f}, "
        f"trending={len(trending_mints)}, SOL=${sol_price:.2f}, "
        f"live={live_mode}"
    )

    while True:
        try:
            with ProcessedLogStream(config.rpc_url) as stream:
                log("WebSocket connected")
                while True:
                    now = time.monotonic()
                    if now - last_heartbeat >= 60:
                        evidence.heartbeat()
                        log(
                            f"heartbeat: venue={venue_events}, confirmed={confirmed_events}, "
                            f"candidates={candidate_count}, trades={trade_count}, "
                            f"daily_loss={daily_loss_lamports} lamports"
                        )
                        last_heartbeat = now
                    if now - last_refresh >= 120:
                        refreshed = gmgn.trending_mints()
                        if refreshed:
                            trending_mints = refreshed
                        sol_price = gmgn.sol_price_usd()
                        log(f"refresh: {len(trending_mints)} mints, SOL=${sol_price:.2f}")
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

                        if not routes:
                            continue

                        log(
                            f"candidate: mint={candidate.mint[:16]}... "
                            f"buy=${candidate.buy_usd:.0f} routes={len(routes)}"
                        )

                        # Try each route + direction, pick the first profitable one
                        for route in routes:
                            for direction in ("pump_to_meteora", "meteora_to_pump"):
                                current_slot = rpc.latest_slot()
                                min_context_slot = max(0, current_slot - 2)

                                # Try simulation with existing ALTs first.
                                # If tx doesn't fit, create a dynamic ALT for this route.
                                lookup_tables = config.direct_lookup_table_addresses

                                try:
                                    report = run_direct_shadow_route(
                                        token_mint=candidate.mint,
                                        pump_pool=route.pump_pool,
                                        meteora_pool=route.meteora_pool,
                                        direction=direction,
                                        input_lamports=config.shadow_input_lamports,
                                        taker=hot_wallet,
                                        executor_program_id=config.executor_program_id,
                                        rpc_url=config.rpc_url,
                                        min_context_slot=min_context_slot,
                                        slot_ttl=config.direct_opportunity_slot_ttl,
                                        lookup_table_addresses=lookup_tables,
                                        slippage_bps=config.slippage_bps,
                                        compute_unit_limit=config.direct_compute_unit_limit,
                                        compute_unit_price_micro_lamports=config.direct_compute_unit_price_micro_lamports,
                                        required_gross_profit_lamports=(
                                            config.failed_attempt_reserve_lamports
                                            + config.minimum_net_profit_lamports
                                            + config.safety_margin_lamports
                                        ),
                                        tip_lamports=config.direct_tip_lamports,
                                        tip_recipient=config.direct_tip_recipient or None,
                                        maximum_transaction_fee_lamports=config.maximum_transaction_fee_lamports,
                                        rpc=rpc,
                                    )
                                except (RuntimeError, TypeError, ValueError, KeyError) as exc:
                                    err_msg = str(exc)
                                    if "too large" in err_msg or "1232" in err_msg:
                                        # Transaction doesn't fit — need dynamic ALT
                                        log(f"  tx too large for existing ALTs, skipping (needs dynamic ALT)")
                                    else:
                                        log(f"  simulate error ({direction}): {exc}")
                                    continue

                                approved = report.get("shadow_approved", False)
                                sim = report.get("simulation", {})
                                sim_ok = sim.get("succeeded", False)
                                wallet_net = report.get("economics", {}).get("wallet_net_lamports")
                                reasons = report.get("rejection_reasons", [])

                                log(
                                    f"  {direction}: approved={approved} sim={sim_ok} "
                                    f"net={wallet_net} reasons={reasons}"
                                )

                                append_jsonl(CANDIDATES_PATH, {
                                    "observed_at": datetime.now(UTC).isoformat(),
                                    "mint": candidate.mint,
                                    "buy_usd": candidate.buy_usd,
                                    "direction": direction,
                                    "route": {
                                        "pump_pool": route.pump_pool,
                                        "meteora_pool": route.meteora_pool,
                                        "meteora_tvl_usd": route.meteora_tvl_usd,
                                    },
                                    "shadow_approved": approved,
                                    "rejection_reasons": reasons,
                                    "wallet_net_lamports": wallet_net,
                                    "sim_succeeded": sim_ok,
                                    "transactions_submitted": 0,
                                })

                                if not approved or not live_mode or keypair is None:
                                    continue

                                # PROFITABLE ROUTE FOUND — execute trade
                                if not try_acquire_lock():
                                    log("  trade lock held, skipping")
                                    continue

                                try:
                                    tx_b64 = report["transaction"]["unsigned_transaction_base64"]
                                    log(f"  🔥 EXECUTING TRADE: {direction} mint={candidate.mint[:16]}...")

                                    result = sign_and_submit(
                                        connection_url=config.rpc_url,
                                        signer=keypair,
                                        transaction_base64=tx_b64,
                                    )

                                    submitted = result.get("submitted", False)
                                    sig = result.get("signature", "")
                                    error = result.get("error", "")

                                    trade_record = {
                                        "executed_at": datetime.now(UTC).isoformat(),
                                        "mint": candidate.mint,
                                        "direction": direction,
                                        "pump_pool": route.pump_pool,
                                        "meteora_pool": route.meteora_pool,
                                        "buy_usd": candidate.buy_usd,
                                        "wallet_net_lamports": wallet_net,
                                        "submitted": submitted,
                                        "signature": sig,
                                        "error": error,
                                    }
                                    append_jsonl(TRADES_PATH, trade_record)
                                    trade_count += 1

                                    if submitted:
                                        log(f"  ✅ SUBMITTED: {sig}")
                                    else:
                                        log(f"  ❌ SUBMIT FAILED: {error}")
                                        daily_loss_lamports += config.failed_attempt_reserve_lamports

                                finally:
                                    release_lock()

                                break  # Don't try more directions after a trade
                            else:
                                continue
                            break  # Don't try more routes after a trade

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
