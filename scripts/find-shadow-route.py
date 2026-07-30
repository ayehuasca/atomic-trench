#!/usr/bin/env python3
"""Find current legacy-SPL Pump/Meteora routes without building or submitting transactions."""

import os
import sys
from pathlib import Path

import requests

WORKDIR = Path(os.environ.get("ATOMIC_TRENCH_DIR", str(Path.home() / "atomic-trench")))
os.chdir(WORKDIR)
sys.path.insert(0, str(WORKDIR))

from backrunner.config import load_config  # noqa: E402
from backrunner.pools import (  # noqa: E402
    LEGACY_TOKEN_PROGRAM,
    PUMP_AMM_PROGRAM,
    WSOL,
    canonical_pump_pool,
)
from backrunner.providers import GmgnProvider, SolanaRpc  # noqa: E402


def main() -> int:
    config = load_config(WORKDIR / "config.yaml")
    gmgn = GmgnProvider(
        api_key=os.getenv("GMGN_API_KEY"),
        fallback_sol_price_usd=config.sol_price_usd,
    )
    rpc = SolanaRpc(config.rpc_url, "confirmed")
    trending = gmgn.trending_mints()
    response = requests.get(
        "https://dlmm.datapi.meteora.ag/pools",
        params={
            "page": 1,
            "page_size": 1000,
            "sort_by": "volume_24h:desc",
            "filter_by": "is_blacklisted=false && tvl>=1000",
        },
        timeout=60,
    )
    response.raise_for_status()
    rows = response.json()["data"]

    examined: set[str] = set()
    found = 0
    for row in rows:
        token_x = str((row.get("token_x") or {}).get("address") or "")
        token_y = str((row.get("token_y") or {}).get("address") or "")
        if WSOL not in (token_x, token_y):
            continue
        mint = token_y if token_x == WSOL else token_x
        if mint not in trending or mint in examined:
            continue
        examined.add(mint)
        mint_info = rpc.account_info(mint)
        if mint_info is None or mint_info.get("owner") != LEGACY_TOKEN_PROGRAM:
            continue
        pump_pool = canonical_pump_pool(mint)
        pump_info = rpc.account_info(pump_pool)
        if pump_info is None or pump_info.get("owner") != str(PUMP_AMM_PROGRAM):
            continue
        print(
            f"mint={mint} pump_pool={pump_pool} meteora_pool={row['address']} "
            f"tvl={float(row.get('tvl') or 0):.2f} "
            f"volume24h={float((row.get('volume') or {}).get('24h') or 0):.2f}"
        )
        found += 1
        if found >= 10:
            break
    print(f"summary trending={len(trending)} meteora_rows={len(rows)} legacy_routes={found}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
