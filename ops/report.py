#!/usr/bin/env python3
"""Gather status report for the atomic-trench strategy bot."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / "atomic-trench"
DATA = ROOT / "data"

print("=" * 60)
print("ATOMIC TRENCH — STRATEGY REPORT")
print("=" * 60)

# Service
r = subprocess.run(["systemctl", "--user", "is-active", "atomic-trench-strategy.service"], capture_output=True, text=True, timeout=10)
print(f"\nService: {r.stdout.strip()}")

r = subprocess.run(["systemctl", "--user", "show", "atomic-trench-strategy.service", "--property=ActiveEnterTimestamp"], capture_output=True, text=True, timeout=10)
print(f"Started: {r.stdout.strip()}")

# Config
import yaml
cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
print(f"\nMode: {'DRY RUN' if cfg.get('dry_run', True) else 'LIVE'}")
print(f"Momentum gate: ${cfg.get('minimum_buy_usd', 500)}")
print(f"Strategies: {[k for k, v in cfg.get('strategies', {}).items() if isinstance(v, dict) and v.get('enabled')]}")

# Balance
r = subprocess.run(
    ["solana", "balance", "xPnLubiTDu3kdLcDk1nTnCjhJhuM1TB4tB2hVnEd6AT",
     "--url", "https://mainnet.helius-rpc.com/?api-key=a6b53727-2ed9-4b83-84fd-d05bc430fc90"],
    capture_output=True, text=True, timeout=15, env={"PATH": "/home/ubuntu/.local/share/solana/install/active_release/bin:" + subprocess.DEFAULT_PIPE}
)
sol_balance = r.stdout.strip()
print(f"\nHot wallet: {sol_balance}")

# Trades log
trades = DATA / "strategy-trades.jsonl"
if trades.exists():
    lines = trades.read_text().strip().split("\n")
    print(f"\nTotal trades logged: {len(lines)}")
    print("Last 5:")
    for line in lines[-5:]:
        d = json.loads(line)
        print(f"  {d.get('time','?')[:19]} {d.get('strategy','?'):10} {d.get('mint','?')[:16]}... buy={d.get('buy_sol',0):.4f} SOL")

# Candidates log
candidates = DATA / "strategy-candidates.jsonl"
if candidates.exists():
    lines = candidates.read_text().strip().split("\n")
    print(f"\nTotal candidates logged: {len(lines)}")
    print("Last 5:")
    for line in lines[-5:]:
        d = json.loads(line)
        print(f"  {d.get('time','?')[:19]} {d.get('strategy','?'):10} {d.get('mint','?')[:16]}... buy=${d.get('buy_usd',0):.0f} conf={d.get('confidence','?')} dry_run={d.get('dry_run',True)}")

# Creator cache
cache = DATA / "creator-cache.json"
if cache.exists():
    cdata = json.loads(cache.read_text())
    print(f"\nCreator cache: {len(cdata)} entries")
    # Show top scored
    sorted_c = sorted(cdata.items(), key=lambda x: x[1].get('score', 0), reverse=True)
    for addr, prof in sorted_c[:5]:
        print(f"  {addr[:12]}... score={prof.get('score',0):.2f} creates={prof.get('total_creates',0)} grad={prof.get('graduation_rate',0):.4f} tags={prof.get('tags',[])}")
else:
    print("\nCreator cache: none yet")

# Tip stream
sys.path.insert(0, str(ROOT))
from backrunner.tip_stream import get_latest_tip_floor, tip_stream_age_ms, get_dynamic_tip_lamports
tf = get_latest_tip_floor()
if tf:
    print(f"\nTip stream connected: {tip_stream_age_ms():.0f}ms ago")
    print(f"  50th: {tf.get('landed_tips_50th_percentile', 0)*1e9:.0f} lamports")
    print(f"  75th: {tf.get('landed_tips_75th_percentile', 0)*1e9:.0f} lamports")
    print(f"  95th: {tf.get('landed_tips_95th_percentile', 0)*1e9:.0f} lamports")
    print(f"  Dynamic tip (normal): {get_dynamic_tip_lamports('normal')} lamports")
    print(f"  Dynamic tip (high):   {get_dynamic_tip_lamports('high')} lamports")
else:
    print("\nTip stream: no data yet")

# Recent logs summary
r = subprocess.run(
    ["journalctl", "--user", "-u", "atomic-trench-strategy.service", "--no-pager", "--since", "5 min ago"],
    capture_output=True, text=True, timeout=15
)
logs = r.stdout
print(f"\nLast 5 min:")
print(f"  Heartbeats: {logs.count('heartbeat')}")
print(f"  Momentum signals: {logs.count('[momentum] SIGNAL')}")
print(f"  Sniper create events: {logs.count('[sniper]')}")
print(f"  Sniper rejections: {logs.count('rejected')}")
print(f"  Errors: {logs.count('error')}")

# Strategy config summary
print(f"\n{'='*60}")
print("STRATEGY CONFIGURATION")
print("=" * 60)
strat = cfg.get('strategies', {})
for name, s in strat.items():
    if isinstance(s, dict):
        print(f"\n{name.upper()}:")
        print(f"  Enabled: {s.get('enabled', False)}")
        print(f"  Buy: ${s.get('buy_usd', 5)} ({s.get('buy_sol', '0.05')} SOL)")
        print(f"  Hold: {s.get('hold_seconds', 15)}s")
        print(f"  TP: {s.get('take_profit_pct', 3)}% / SL: {s.get('stop_loss_pct', 5)}%")
        print(f"  Max positions: {s.get('max_positions', 3)}")
        if name == 'copytrade':
            wallets = s.get('watch_wallets', [])
            print(f"  Watched wallets: {len(wallets)}")
        if name == 'snipe':
            print(f"  Rate limit: 10/min")
            print(f"  Creator filter: age>60min, grad_rate>0.1%, <20/day, <500 total")

print(f"\n{'='*60}")
print("RISK LIMITS")
print("=" * 60)
print(f"  Daily loss: {cfg.get('daily_loss_pct', 3)}%")
print(f"  Max drawdown: {cfg.get('maximum_drawdown_pct', 5)}%")
print(f"  Trade cap: ${cfg.get('trade_cap_usd', 25)}")
print(f"  Wallet reserve: ${cfg.get('wallet_reserve_usd', 10)}")
print(f"  Priority fee: {cfg.get('direct_compute_unit_price_micro_lamports', 50000)} micro-lamports")
print(f"  Max tx fee: {cfg.get('maximum_transaction_fee_lamports', 100000)} lamports")