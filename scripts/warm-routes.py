#!/usr/bin/env python3
"""Pre-warm ALTs for trending pump.fun tokens with both Pump AMM + Meteora pools.

This script discovers routes for all trending tokens, then creates ALTs for
tokens that have viable routes but don't fit in existing ALTs. The ALTs are
saved to a cache file that live-loop.py reads on startup.
"""

import json
import os
import sys
import time
from pathlib import Path

WORKDIR = Path(os.environ.get("ATOMIC_TRENCH_DIR", str(Path.home() / "atomic-trench")))
os.chdir(WORKDIR)
sys.path.insert(0, str(WORKDIR))

from backrunner.config import load_config  # noqa: E402
from backrunner.pools import (  # noqa: E402
    MeteoraPoolProvider,
    WSOL,
    canonical_pump_pool,
    discover_direct_routes,
)
from backrunner.providers import GmgnProvider, SolanaRpc  # noqa: E402

ALT_CACHE_PATH = WORKDIR / "data" / "alt-cache.json"


def main() -> int:
    config = load_config(WORKDIR / "config.yaml")
    gmgn = GmgnProvider(
        api_key=os.getenv("GMGN_API_KEY"),
        fallback_sol_price_usd=config.sol_price_usd,
    )
    rpc = SolanaRpc(config.rpc_url, "confirmed")
    meteora = MeteoraPoolProvider()

    trending = gmgn.trending_mints()
    print(f"Trending: {len(trending)} mints")

    cache: dict[str, list[dict]] = {}
    if ALT_CACHE_PATH.exists():
        cache = json.loads(ALT_CACHE_PATH.read_text())

    found = 0
    for mint in trending:
        if mint in cache:
            continue
        routes = discover_direct_routes(
            mint=mint,
            rpc=rpc,
            meteora=meteora,
        )
        if routes:
            cache[mint] = [
                {
                    "pump_pool": r.pump_pool,
                    "meteora_pool": r.meteora_pool,
                    "meteora_tvl_usd": r.meteora_tvl_usd,
                    "meteora_volume_24h_usd": r.meteora_volume_24h_usd,
                }
                for r in routes
            ]
            found += 1
            print(f"  {mint[:24]}... routes={len(routes)}")
        time.sleep(0.1)  # Rate limit

    ALT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALT_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    print(f"\nCached {found} new tokens, {len(cache)} total in cache")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
