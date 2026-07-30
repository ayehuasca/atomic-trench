"""Command-line entry point for replay and read-only observation."""

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .config import load_config
from .coordination import (
    AttemptLock,
    DuplicateCandidateError,
    ShadowEvidence,
    ShadowEvidenceRecord,
)
from .jupiter import JupiterBuildClient
from .observer import observe_once
from .providers import GmgnProvider, SolanaRpc
from .replay import load_replay, replay_precursors
from .shadow import run_direct_shadow_route, run_shadow_route
from .stream import observe_processed_once


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wallet-a-backrunner")
    parser.add_argument("--config", default="config.yaml")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("replay", help="replay the diagnosed Wallet A transaction window")
    subcommands.add_parser("observe-once", help="scan one finalized block without submitting")
    subcommands.add_parser(
        "observe-processed-once",
        help="observe processed Pump/Meteora logs and reconcile at confirmed commitment",
    )
    shadow = subcommands.add_parser(
        "shadow-route", help="build and simulate one unsigned atomic round trip"
    )
    shadow.add_argument("--token-mint", required=True)
    shadow.add_argument("--min-context-slot", type=int)
    direct = subcommands.add_parser(
        "shadow-direct", help="build and simulate a direct Pump/Meteora executor transaction"
    )
    direct.add_argument("--token-mint", required=True)
    direct.add_argument("--pump-pool", required=True)
    direct.add_argument("--meteora-pool", required=True)
    direct.add_argument(
        "--direction",
        choices=("pump_to_meteora", "meteora_to_pump"),
        required=True,
    )
    direct.add_argument("--min-context-slot", type=int, required=True)
    direct.add_argument("--executor-program-id")
    direct.add_argument("--lookup-table", action="append", default=[])
    direct.add_argument("--trigger-signature")
    direct.add_argument("--evidence-path", default="data/shadow-evidence.json")
    direct.add_argument("--attempt-lock-path", default="data/active-attempt.lock")
    status = subcommands.add_parser(
        "shadow-evidence-status",
        help="report persistent lock and extended shadow promotion gates",
    )
    status.add_argument("--evidence-path", default="data/shadow-evidence.json")
    status.add_argument("--attempt-lock-path", default="data/active-attempt.lock")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(Path(args.config))
    if args.command == "shadow-evidence-status":
        summary = ShadowEvidence(Path(args.evidence_path)).summary()
        active_attempt = Path(args.attempt_lock_path).exists()
        report = {
            "mode": "SHADOW_EVIDENCE_STATUS",
            **asdict(summary),
            "required_runtime_hours": 72,
            "required_candidates": 100,
            "positive_wallet_net": summary.wallet_net_lamports > 0,
            "active_attempt_lock": active_attempt,
            "promotion_ready": summary.ready(
                minimum_hours=72, minimum_candidates=100
            )
            and not active_attempt,
            "transactions_submitted": 0,
            "live_execution_enabled": False,
        }
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "replay":
        result = replay_precursors(
            load_replay(config.replay_fixture),
            minimum_precursor_usd=config.minimum_buy_usd,
            maximum_transaction_gap=config.maximum_transaction_gap,
        )
        print(
            json.dumps(
                {
                    "mode": "DRY_RUN_REPLAY",
                    "live_execution_enabled": False,
                    "total_round_trips": result.total_round_trips,
                    "same_slot_precursors": result.same_slot_precursors,
                    "tight_backrun_candidates": result.tight_backrun_candidates,
                    "profitable_tight_candidates": result.profitable_tight_candidates,
                    "observed_profit_sol": round(result.observed_profit_sol, 9),
                    "signatures": result.signatures,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "observe-once":
        report = observe_once(
            rpc=SolanaRpc(config.rpc_url, config.commitment),
            gmgn=GmgnProvider(
                api_key=os.getenv("GMGN_API_KEY"),
                fallback_sol_price_usd=config.sol_price_usd,
            ),
            minimum_buy_usd=config.minimum_buy_usd,
        )
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "observe-processed-once":
        report = observe_processed_once(
            rpc=SolanaRpc(config.rpc_url, "processed"),
            gmgn=GmgnProvider(
                api_key=os.getenv("GMGN_API_KEY"),
                fallback_sol_price_usd=config.sol_price_usd,
            ),
            minimum_buy_usd=config.minimum_buy_usd,
        )
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "shadow-route":
        report = run_shadow_route(
            token_mint=args.token_mint,
            input_lamports=config.shadow_input_lamports,
            taker=config.shadow_taker,
            slippage_bps=config.slippage_bps,
            max_accounts=config.max_accounts,
            failed_attempt_reserve_lamports=config.failed_attempt_reserve_lamports,
            minimum_net_profit_lamports=config.minimum_net_profit_lamports,
            jito_tip_lamports=config.jito_tip_lamports,
            safety_margin_lamports=config.safety_margin_lamports,
            jupiter=JupiterBuildClient(api_key=os.getenv("JUPITER_API_KEY")),
            rpc=SolanaRpc(config.rpc_url, config.commitment),
            min_context_slot=args.min_context_slot,
        )
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "shadow-direct":
        executor_program_id = args.executor_program_id or config.executor_program_id
        if not executor_program_id:
            raise ValueError("shadow-direct requires a deployed executor program id")
        lookup_tables = tuple(
            dict.fromkeys((*config.direct_lookup_table_addresses, *args.lookup_table))
        )
        rpc = SolanaRpc(config.rpc_url, config.commitment)

        def simulate_direct() -> dict:
            return run_direct_shadow_route(
                token_mint=args.token_mint,
                pump_pool=args.pump_pool,
                meteora_pool=args.meteora_pool,
                direction=args.direction,
                input_lamports=config.shadow_input_lamports,
                taker=config.shadow_taker,
                executor_program_id=executor_program_id,
                rpc_url=config.rpc_url,
                min_context_slot=args.min_context_slot,
                slot_ttl=config.direct_opportunity_slot_ttl,
                lookup_table_addresses=lookup_tables,
                slippage_bps=config.slippage_bps,
                compute_unit_limit=config.direct_compute_unit_limit,
                compute_unit_price_micro_lamports=(
                    config.direct_compute_unit_price_micro_lamports
                ),
                required_gross_profit_lamports=(
                    config.required_gross_profit_lamports
                ),
                tip_lamports=config.direct_tip_lamports,
                tip_recipient=config.direct_tip_recipient,
                maximum_transaction_fee_lamports=(
                    config.maximum_transaction_fee_lamports
                ),
                rpc=rpc,
            )

        if args.trigger_signature is None:
            report = simulate_direct()
        else:
            evidence = ShadowEvidence(Path(args.evidence_path))
            with AttemptLock(
                Path(args.attempt_lock_path), candidate_id=args.trigger_signature
            ):
                if evidence.contains(args.trigger_signature):
                    raise DuplicateCandidateError(
                        f"candidate signature already recorded: {args.trigger_signature}"
                    )
                if rpc.transaction(args.trigger_signature, commitment="confirmed") is None:
                    raise RuntimeError("trigger signature is not confirmed; refusing evidence")
                evidence.heartbeat()
                report = simulate_direct()
                economics = report.get("economics") or {}
                wallet_net = economics.get("wallet_net_lamports")
                if wallet_net is None:
                    raise RuntimeError("exact wallet-net simulation evidence is unavailable")
                simulation = report.get("simulation") or {}
                evidence.record(
                    ShadowEvidenceRecord(
                        signature=args.trigger_signature,
                        observed_at=datetime.now(UTC).isoformat(),
                        simulation_slot=int(simulation["context_slot"]),
                        simulation_succeeded=bool(simulation["succeeded"]),
                        wallet_net_lamports=int(wallet_net),
                        rejection_reasons=tuple(
                            str(value)
                            for value in report.get("rejection_reasons") or []
                        ),
                    )
                )
                evidence.heartbeat()
                report["evidence"] = asdict(evidence.summary())
        print(json.dumps(report, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
