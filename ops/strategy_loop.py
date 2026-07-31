#!/usr/bin/env python3
"""
Strategy Loop — dispatcher for momentum, sniping, and copy-trade strategies.

Signal sources (all public, confirmed on-chain data):
  1. Momentum  — buy after a large confirmed buy, exit on price spike
  2. Snipe     — GMGN trending / new liquidity spike, buy at launch
  3. CopyTrade — watch specific wallets, mirror their buys

Each strategy emits a TradeSignal. The dispatcher validates the signal against
risk limits, executes the buy, and tracks the position for timed exit.
"""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from typing import Any

import requests
import yaml

# ── Add project root to path ────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from backrunner.copytrader import CopyTradeWatcher, CopyTradeSignal
from backrunner.detector import detect_large_buys
from backrunner.jito_sender import JitoSender, random_tip_account
from backrunner.momentum import MomentumWatcher, MomentumSignal
from backrunner.providers import GmgnProvider, SolanaRpc, WSOL
from backrunner.scorer import CreatorScorer, SnipeDecision
from backrunner.snipers import PumpSniper, SnipeSignal
from backrunner.tip_stream import start_tip_stream, get_dynamic_tip_lamports

# ── Config ───────────────────────────────────────────────────────────────────

CONFIG_PATH = ROOT / "config.yaml"
HOT_KEYPAIR_PATH = Path.home() / ".config" / "atomic-trench" / "hot-wallet.json"
DATA_DIR = ROOT / "data"
TRADES_LOG = DATA_DIR / "strategy-trades.jsonl"
CANDIDATES_LOG = DATA_DIR / "strategy-candidates.jsonl"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    # Merge strategy config defaults
    strategies = raw.get("strategies", {})
    strategies.setdefault("momentum", {"enabled": True, "buy_usd": 5, "hold_seconds": 15, "take_profit_pct": 3, "stop_loss_pct": 5, "max_positions": 3})
    strategies.setdefault("snipe", {"enabled": True, "buy_usd": 5, "hold_seconds": 30, "take_profit_pct": 10, "stop_loss_pct": 8, "max_positions": 2})
    strategies.setdefault("copytrade", {"enabled": True, "buy_usd": 5, "hold_seconds": 20, "take_profit_pct": 5, "stop_loss_pct": 6, "max_positions": 3, "watch_wallets": raw.get("copy_wallets", [])})
    raw["strategies"] = strategies
    raw.setdefault("rpc_url", "https://mainnet.helius-rpc.com/?api-key=a6b53727-2ed9-4b83-84fd-d05bc430fc90")
    raw.setdefault("dry_run", True)
    raw.setdefault("minimum_buy_usd", 200)
    raw.setdefault("shadow_input_lamports", 50_000_000)
    raw.setdefault("slippage_bps", 100)
    raw.setdefault("daily_loss_pct", 3)
    raw.setdefault("maximum_drawdown_pct", 5)
    raw.setdefault("trade_cap_usd", 25)
    raw.setdefault("wallet_reserve_usd", 10)
    raw.setdefault("sol_price_usd", 74)
    raw.setdefault("direct_compute_unit_price_micro_lamports", 50000)
    raw.setdefault("maximum_transaction_fee_lamports", 100000)
    return raw


# ── Position Management ──────────────────────────────────────────────────────

@dataclass
class Position:
    id: str
    strategy: str          # "momentum" | "snipe" | "copytrade"
    mint: str
    pump_pool: str
    buy_sig: str
    entry_slot: int
    entry_time: float
    buy_sol: float
    entry_price_sol: float
    take_profit_pct: float
    stop_loss_pct: float
    hold_seconds: float
    closed: bool = False
    exit_sig: str | None = None
    exit_time: float | None = None
    exit_reason: str | None = None
    pnl_sol: float | None = None
    _last_price_check: float = 0.0


class PositionManager:
    def __init__(self, max_positions: int):
        self.positions: list[Position] = []
        self.max_positions = max_positions

    def can_open(self) -> bool:
        active = [p for p in self.positions if not p.closed]
        return len(active) < self.max_positions

    def open(self, pos: Position) -> None:
        self.positions.append(pos)

    def active(self) -> list[Position]:
        return [p for p in self.positions if not p.closed]

    def check_exits(self, config: dict) -> list[Position]:
        """Check positions for TP/SL/timeout using on-chain pool price."""
        exited = []
        now = time.time()
        for pos in self.active():
            elapsed = now - pos.entry_time

            # Hard timeout — always fires, no price check needed
            if elapsed >= pos.hold_seconds:
                pos.exit_reason = "timeout"
                pos.exit_time = now
                pos.pnl_sol = 0  # unknown without price
                pos.closed = True
                exited.append(pos)
                continue

            # Minimum 3s hold before checking price (avoid noise)
            if elapsed < 3:
                continue

            # Price check cooldown: at most once per 3s per position
            if now - pos._last_price_check < 3:
                continue
            pos._last_price_check = now

            # Fetch current pool price
            price = get_pool_price(pos.pump_pool, config)
            if price is None or price <= 0 or pos.entry_price_sol <= 0:
                continue

            change_pct = (price - pos.entry_price_sol) / pos.entry_price_sol * 100

            if change_pct >= pos.take_profit_pct:
                pos.exit_reason = "take_profit"
                pos.exit_time = now
                pos.pnl_sol = pos.buy_sol * change_pct / 100
                pos.closed = True
                exited.append(pos)
            elif change_pct <= -pos.stop_loss_pct:
                pos.exit_reason = "stop_loss"
                pos.exit_time = now
                pos.pnl_sol = pos.buy_sol * change_pct / 100
                pos.closed = True
                exited.append(pos)
        return exited


# ── Buy/Sell Execution ───────────────────────────────────────────────────────

def get_pool_price(pump_pool: str, config: dict) -> float | None:
    """Get current pool price (SOL per token) from Pump AMM via Node.js helper.
    Returns price in SOL/token, or None on error.
    """
    payload = {
        "rpcUrl": config["rpc_url"],
        "pumpPool": pump_pool,
    }
    result = _run_node("scripts/get-pool-state.mjs", payload)
    if result.get("error") or not result.get("price"):
        return None
    # price is in lamports/token from the script, convert to SOL/token
    return float(result["price"]) / 1e9


def get_pump_pool(mint: str) -> str:
    """Get Pump AMM pool address for a mint."""
    from solders.pubkey import Pubkey
    mint_key = Pubkey.from_string(mint)
    quote_key = Pubkey.from_string(WSOL)
    pu_prog = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
    pu_amm = Pubkey.from_string("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")
    authority, _ = Pubkey.find_program_address([b"pool-authority", bytes(mint_key)], pu_prog)
    pool, _ = Pubkey.find_program_address(
        [b"pool", (0).to_bytes(2, "little"), bytes(authority), bytes(mint_key), bytes(quote_key)],
        pu_amm,
    )
    return str(pool)


def buy_token(mint: str, buy_sol: float, slippage_bps: int, config: dict) -> dict[str, Any]:
    """Execute a buy via Pump AMM. Returns {submitted, signature, error}."""
    pump_pool = get_pump_pool(mint)
    payload = {
        "action": "buy",
        "rpcUrl": config["rpc_url"],
        "keypairPath": str(HOT_KEYPAIR_PATH),
        "mint": mint,
        "pumpPool": pump_pool,
        "buySol": f"{buy_sol:.9f}",
        "slippageBps": slippage_bps,
        "computeUnitPriceMicroLamports": config.get("direct_compute_unit_price_micro_lamports", 50000),
        "maxFeeLamports": config.get("maximum_transaction_fee_lamports", 100000),
    }
    result = _run_node("scripts/buy-token.mjs", payload)
    result["mint"] = mint
    result["pump_pool"] = pump_pool
    result["buy_sol"] = buy_sol
    return result


def sell_token(mint: str, pump_pool: str, config: dict) -> dict[str, Any]:
    """Execute a sell via Pump AMM. Returns {submitted, signature, error}."""
    payload = {
        "action": "sell",
        "rpcUrl": config["rpc_url"],
        "keypairPath": str(HOT_KEYPAIR_PATH),
        "mint": mint,
        "pumpPool": pump_pool,
        "computeUnitPriceMicroLamports": config.get("direct_compute_unit_price_micro_lamports", 50000),
        "maxFeeLamports": config.get("maximum_transaction_fee_lamports", 100000),
    }
    result = _run_node("scripts/buy-token.mjs", payload)
    return result


def _run_node(script_rel: str, payload: dict) -> dict[str, Any]:
    script = ROOT / script_rel
    if not script.exists():
        return {"submitted": False, "error": f"script not found: {script}"}
    try:
        proc = subprocess.run(
            ["node", str(script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            return {"submitted": False, "error": proc.stderr.strip() or f"exit code {proc.returncode}"}
        result = json.loads(proc.stdout.strip())
        return result
    except Exception as e:
        return {"submitted": False, "error": str(e)}


# ── Price Oracle ─────────────────────────────────────────────────────────────

def get_current_prices(mints: list[str]) -> dict[str, float]:
    """Get prices for a list of mints from Jupiter price API."""
    if not mints:
        return {}
    try:
        ids = ",".join(mints)
        resp = requests.get(f"https://price.jup.ag/v6/price?ids={ids}", timeout=15)
        data = resp.json().get("data", {})
        return {m: float(data.get(m, {}).get("price", 0)) for m in mints if data.get(m, {}).get("price")}
    except Exception:
        return {}


# ── Signal Detectors ─────────────────────────────────────────────────────────

@dataclass
class TradeSignal:
    strategy: str
    mint: str
    pump_pool: str
    buy_sol: float
    source_id: str  # buyer wallet / signature / GMGN id
    confidence: str  # high / medium / low


def _pubkey(value: Any) -> str:
    return str(value["pubkey"] if isinstance(value, dict) else value)


def detect_momentum_signals(
    rpc: SolanaRpc,
    sol_price: float,
    minimum_buy_usd: float,
    trending_mints: set[str],  # unused; kept for interface compatibility
    seen_mints: set[str],  # skip mints already seen in this run
) -> list[TradeSignal]:
    """Strategy 3: Buy after a confirmed large buy on ANY token."""
    try:
        latest_slot = rpc.latest_slot()
        # Only scan the latest slot to avoid duplicates
        for candidate_slot in range(latest_slot, latest_slot - 1, -1):
            try:
                block = rpc.block_accounts(candidate_slot)
            except RuntimeError:
                continue
            signals = []
            for tx_idx, record in enumerate(block.get("transactions", [])):
                meta = record.get("meta") or {}
                if meta.get("err") is not None:
                    continue
                transaction = record.get("transaction") or {}
                account_keys = [_pubkey(k) for k in transaction.get("accountKeys", [])]
                deltas: dict[str, dict[str, float]] = {}
                for field, sign in (("preTokenBalances", -1.0), ("postTokenBalances", 1.0)):
                    for bal in meta.get(field, []):
                        owner = bal.get("owner")
                        if not owner:
                            continue
                        amt = bal["uiTokenAmount"]
                        val = int(amt["amount"]) / (10 ** int(amt["decimals"]))
                        d = deltas.setdefault(owner, {})
                        d[bal["mint"]] = d.get(bal["mint"], 0) + sign * val
                native_delta = {
                    k: (int(meta["postBalances"][i]) - int(meta["preBalances"][i])) / 1e9
                    for i, k in enumerate(account_keys)
                }
                for buyer, mint_deltas in deltas.items():
                    for mint, received in mint_deltas.items():
                        if received <= 0:
                            continue
                        if mint in ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                                    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
                                    "So11111111111111111111111111111111111111112"):
                            continue
                        if mint in seen_mints:
                            continue
                        token_quote_sol = max(0, -mint_deltas.get("So11111111111111111111111111111111111111112", 0))
                        native_quote_sol = max(0, -native_delta.get(buyer, 0))
                        buy_sol = max(token_quote_sol, native_quote_sol)
                        buy_usd = buy_sol * sol_price
                        if buy_usd < minimum_buy_usd:
                            continue
                        seen_mints.add(mint)
                        pump_pool = get_pump_pool(mint)
                        entry_sol = min(buy_sol / 10, 0.05)
                        signals.append(TradeSignal(
                            strategy="momentum",
                            mint=mint,
                            pump_pool=pump_pool,
                            buy_sol=max(entry_sol, 0.003),
                            source_id=buyer,
                            confidence="high" if buy_usd >= 500 else "medium",
                        ))
            return signals
    except Exception as e:
        print(f"  [momentum] detection error: {e}")
    return []


def detect_snipe_signals(
    gmgn: GmgnProvider,
    sol_price: float,
    seen_mints: set[str],
) -> list[TradeSignal]:
    """Strategy 1: Snipe new trending listings via GMGN API."""
    try:
        mints = gmgn.trending_mints()
        if not mints:
            return []
        signals = []
        for mint in list(mints)[:5]:
            if mint in seen_mints:
                continue
            seen_mints.add(mint)
            pump_pool = get_pump_pool(mint)
            signals.append(TradeSignal(
                strategy="snipe",
                mint=mint,
                pump_pool=pump_pool,
                buy_sol=0.005,
                source_id="gmgn_trending",
                confidence="medium",
            ))
        return signals
    except Exception as e:
        print(f"  [snipe] detection error: {e}")
    return []


def detect_copytrade_signals(
    rpc: SolanaRpc,
    watch_wallets: list[str],
    sol_price: float,
    minimum_buy_usd: float,
    trending_mints: set[str],  # unused; kept for interface
    seen_mints: set[str],
) -> list[TradeSignal]:
    """Strategy 2: Watch specific wallets, mirror their buys on ANY token."""
    if not watch_wallets:
        return []
    signals = []
    try:
        latest_slot = rpc.latest_slot()
        for candidate_slot in range(latest_slot, latest_slot - 1, -1):
            try:
                block = rpc.block_accounts(candidate_slot)
            except RuntimeError:
                continue
            for tx_idx, record in enumerate(block.get("transactions", [])):
                meta = record.get("meta") or {}
                if meta.get("err") is not None:
                    continue
                transaction = record.get("transaction") or {}
                account_keys = [_pubkey(k) for k in transaction.get("accountKeys", [])]
                deltas: dict[str, dict[str, float]] = {}
                for field, sign in (("preTokenBalances", -1.0), ("postTokenBalances", 1.0)):
                    for bal in meta.get(field, []):
                        owner = bal.get("owner")
                        if not owner:
                            continue
                        amt = bal["uiTokenAmount"]
                        val = int(amt["amount"]) / (10 ** int(amt["decimals"]))
                        d = deltas.setdefault(owner, {})
                        d[bal["mint"]] = d.get(bal["mint"], 0) + sign * val
                native_delta = {
                    k: (int(meta["postBalances"][i]) - int(meta["preBalances"][i])) / 1e9
                    for i, k in enumerate(account_keys)
                }
                for buyer, mint_deltas in deltas.items():
                    if buyer not in watch_wallets:
                        continue
                    for mint, received in mint_deltas.items():
                        if received <= 0:
                            continue
                        if mint in ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                                    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
                                    "So11111111111111111111111111111111111111112"):
                            continue
                        if mint in seen_mints:
                            continue
                        token_quote_sol = max(0, -mint_deltas.get("So11111111111111111111111111111111111111112", 0))
                        native_quote_sol = max(0, -native_delta.get(buyer, 0))
                        buy_sol = max(token_quote_sol, native_quote_sol)
                        buy_usd = buy_sol * sol_price
                        if buy_usd < minimum_buy_usd:
                            continue
                        seen_mints.add(mint)
                        pump_pool = get_pump_pool(mint)
                        signals.append(TradeSignal(
                            strategy="copytrade",
                            mint=mint,
                            pump_pool=pump_pool,
                            buy_sol=min(buy_sol / 5, 0.02),
                            source_id=buyer,
                            confidence="high",
                        ))
            return signals
    except Exception as e:
        print(f"  [copytrade] detection error: {e}")
    return []


# ── Risk Manager ─────────────────────────────────────────────────────────────

class RiskManager:
    def __init__(self, config: dict):
        self.config = config
        self.daily_loss_lamports = 0
        self.daily_trades = 0
        self.daily_reset_time = time.time()
        self.initial_balance = self._get_balance()

    def _get_balance(self) -> int:
        try:
            rpc = SolanaRpc(self.config["rpc_url"])
            return rpc.balance(self.config.get("shadow_taker", ""))
        except Exception:
            return 0

    def _check_daily_reset(self) -> None:
        now = time.time()
        if now - self.daily_reset_time > 86400:
            self.daily_loss_lamports = 0
            self.daily_trades = 0
            self.daily_reset_time = now

    def can_trade(self, buy_sol: float) -> bool:
        self._check_daily_reset()
        max_loss = self.config.get("maximum_drawdown_pct", 5) / 100 * self.initial_balance
        sol_price = self.config.get("sol_price_usd", 74)
        if self.daily_loss_lamports > max_loss:
            return False
        trade_usd = buy_sol * sol_price
        cap = self.config.get("trade_cap_usd", 25)
        return trade_usd <= cap

    def record_trade(self, cost_sol: float) -> None:
        self.daily_trades += 1
        self.daily_loss_lamports += int(cost_sol * 1e9)


# ── Main Loop ────────────────────────────────────────────────────────────────

def main() -> None:
    config = load_config()
    dry_run = config.get("dry_run", True)
    rpc = SolanaRpc(config["rpc_url"])
    gmgn = GmgnProvider(
        api_key=config.get("gmgn_api_key"),
        fallback_sol_price_usd=config.get("sol_price_usd", 74),
    )
    pos_mgr = PositionManager(max_positions=5)
    risk = RiskManager(config)

    sol_price = config.get("sol_price_usd", 74)
    buy_usd = config.get("minimum_buy_usd", 200)
    slippage = config.get("slippage_bps", 100)

    cfg = config["strategies"]
    mode = "DRY_RUN" if dry_run else "LIVE"
    print(f"[{datetime.now(UTC)}] ⚠️  {mode} MODE — {'shadow' if dry_run else 'live'}")
    print(f"  hot wallet: {config.get('shadow_taker', 'unknown')}")
    print(f"  strategies: {' '.join(k for k, v in cfg.items() if isinstance(v, dict) and v.get('enabled'))}")
    print(f"  SOL=${sol_price:.2f}")

    # ── Start Jito tip stream (background asyncio thread) ──
    start_tip_stream()
    jito_sender = JitoSender()

    heartbeat_interval = 60
    last_heartbeat = 0
    iteration = 0
    seen_mints: set[str] = set()
    total_signals: int = 0

    # ── Start momentum WebSocket watcher (replaces block polling) ──
    momentum_queue: Queue = Queue()
    momentum_watcher: MomentumWatcher | None = None
    if cfg.get("momentum", {}).get("enabled", True):
        momentum_watcher = MomentumWatcher(
            rpc_url=config["rpc_url"],
            signal_queue=momentum_queue,
            minimum_buy_usd=buy_usd,
            sol_price_usd=sol_price,
        )
        momentum_watcher.start()

    # ── Start copy-trade WebSocket watcher if wallets configured ──
    copy_wallets = cfg.get("copytrade", {}).get("watch_wallets", [])
    copy_queue: Queue = Queue()
    copy_watcher: CopyTradeWatcher | None = None
    if cfg.get("copytrade", {}).get("enabled", True) and copy_wallets:
        copy_watcher = CopyTradeWatcher(
            rpc_url=config["rpc_url"],
            watched_wallets=copy_wallets,
            signal_queue=copy_queue,
            minimum_buy_usd=buy_usd,
        )
        copy_watcher.start()

    # ── Start pump.fun creator sniper ──
    snipe_queue: Queue = Queue()
    sniper: PumpSniper | None = None
    if cfg.get("snipe", {}).get("enabled", True):
        scorer = CreatorScorer(rpc, cache_path=str(DATA_DIR / "creator-cache.json"), max_sigs_to_scan=200)
        sniper = PumpSniper(
            rpc_url=config["rpc_url"],
            signal_queue=snipe_queue,
            max_per_minute=10,
            scorer=scorer,
        )
        sniper.start()

    while True:
        iteration += 1
        now = time.time()

        try:
            sol_price = gmgn.sol_price_usd()
        except Exception:
            pass

        # ── 1. Get trending mints (shared across all strategies) ──
        trending_mints: set[str] = set()

        # ── 2. Detect signals from all enabled strategies ──
        signals: list[TradeSignal] = []
        # seen_mints prevents duplicate signals within a 60-second window
        if iteration % 40 == 0:
            seen_mints.clear()

        # ── Momentum: check WebSocket queue for real-time buys ──
        if cfg.get("momentum", {}).get("enabled", True) and momentum_watcher is not None:
            while not momentum_queue.empty():
                ms: MomentumSignal = momentum_queue.get_nowait()
                if ms.mint in seen_mints:
                    continue
                seen_mints.add(ms.mint)
                pump_pool = get_pump_pool(ms.mint)
                entry_sol = min(ms.buy_sol / 10, 0.05)
                signals.append(TradeSignal(
                    strategy="momentum",
                    mint=ms.mint,
                    pump_pool=pump_pool,
                    buy_sol=max(entry_sol, 0.003),
                    source_id=ms.buyer,
                    confidence="high" if ms.buy_usd >= 500 else "medium",
                ))

        # Cap to top 3 signals per iteration (highest confidence + buy amount)
        signals.sort(key=lambda s: (0 if s.confidence == "high" else 1, s.buy_sol), reverse=True)
        signals = signals[:3]

        # ── Snipe: check PumpSniper queue for creator-based signals ──
        if cfg.get("snipe", {}).get("enabled", True) and sniper is not None:
            while not snipe_queue.empty():
                ss: SnipeSignal = snipe_queue.get_nowait()
                if ss.mint in seen_mints:
                    continue
                seen_mints.add(ss.mint)
                pump_pool = get_pump_pool(ss.mint)
                signals.append(TradeSignal(
                    strategy="snipe",
                    mint=ss.mint,
                    pump_pool=pump_pool,
                    buy_sol=0.005,
                    source_id=ss.creator,
                    confidence="high",
                ))

        # ── Copy-trade: check WebSocket queue for real-time signals ──
        while not copy_queue.empty():
            cs: CopyTradeSignal = copy_queue.get_nowait()
            if cs.mint in seen_mints:
                continue
            seen_mints.add(cs.mint)
            pump_pool = get_pump_pool(cs.mint)
            signals.append(TradeSignal(
                strategy="copytrade",
                mint=cs.mint,
                pump_pool=pump_pool,
                buy_sol=min(cs.buy_sol / 5, 0.02),
                source_id=cs.buyer,
                confidence="high",
            ))

        # ── 3. Check existing positions for exit conditions ──
        exited = pos_mgr.check_exits(config)
        for pos in exited:
            print(f"  [{pos.strategy}] EXIT {pos.exit_reason} {pos.mint[:16]}... pnl={pos.pnl_sol:+.6f} SOL")
            result = sell_token(pos.mint, pos.pump_pool, config)
            if result.get("submitted"):
                pos.exit_sig = result.get("signature")
                print(f"    ✅ Sold: {result.get('signature', '')[:20]}...")
            else:
                print(f"    ❌ Sell failed: {result.get('error', 'unknown')}")

        # ── 4. Evaluate signals ──
        for signal in signals:
            # Skip if we already have a position on this mint
            if any(p.mint == signal.mint and not p.closed for p in pos_mgr.positions):
                continue

            if not pos_mgr.can_open():
                continue

            if not risk.can_trade(signal.buy_sol):
                continue

            print(f"  [{signal.strategy}] SIGNAL {signal.mint[:16]}... buy=${signal.buy_sol*sol_price:.2f} ({signal.buy_sol:.4f} SOL) confidence={signal.confidence}")

            if dry_run:
                # Log candidate but don't execute
                with open(CANDIDATES_LOG, "a") as f:
                    f.write(json.dumps({
                        "time": datetime.now(UTC).isoformat(),
                        "strategy": signal.strategy,
                        "mint": signal.mint,
                        "buy_sol": signal.buy_sol,
                        "buy_usd": signal.buy_sol * sol_price,
                        "source_id": signal.source_id,
                        "confidence": signal.confidence,
                        "dry_run": True,
                    }) + "\n")
                total_signals += 1
                continue

            # Execute buy
            result = buy_token(signal.mint, signal.buy_sol, slippage, config)
            if not result.get("submitted"):
                print(f"    ❌ Buy failed: {result.get('error', 'unknown')}")
                risk.record_trade(signal.buy_sol)
                total_signals += 1  # assume lost
                continue

            sig = result.get("signature", "unknown")
            print(f"    ✅ Bought: {sig[:20]}...")
            total_signals += 1

            # Fetch actual pool price at entry
            entry_price = get_pool_price(signal.pump_pool, config)

            # Create position
            strat_cfg = cfg.get(signal.strategy, {})
            pos = Position(
                id=str(uuid.uuid4())[:8],
                strategy=signal.strategy,
                mint=signal.mint,
                pump_pool=signal.pump_pool,
                buy_sig=sig,
                entry_slot=rpc.latest_slot(),
                entry_time=time.time(),
                buy_sol=signal.buy_sol,
                entry_price_sol=entry_price or 0,
                take_profit_pct=float(strat_cfg.get("take_profit_pct", 5)),
                stop_loss_pct=float(strat_cfg.get("stop_loss_pct", 5)),
                hold_seconds=float(strat_cfg.get("hold_seconds", 15)),
            )
            pos_mgr.open(pos)
            risk.record_trade(signal.buy_sol * 0.01)  # rough fee estimate

            with open(TRADES_LOG, "a") as f:
                f.write(json.dumps({
                    "time": datetime.now(UTC).isoformat(),
                    "strategy": signal.strategy,
                    "mint": signal.mint,
                    "buy_sol": signal.buy_sol,
                    "buy_sig": sig,
                    "entry_price_sol": pos.entry_price_sol,
                }) + "\n")

        # ── 5. Heartbeat ──
        if now - last_heartbeat >= heartbeat_interval:
            active = len(pos_mgr.active())
            print(f"[{datetime.now(UTC)}] heartbeat: signals_this_iter={len(signals)}, positions={active}, trades={risk.daily_trades}, total_candidates={total_signals}, sol=${sol_price:.2f}")
            last_heartbeat = now

        # Brief sleep between iterations
        time.sleep(0.1)


if __name__ == "__main__":
    main()