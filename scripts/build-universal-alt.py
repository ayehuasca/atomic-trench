#!/usr/bin/env python3
"""Create a universal ALT covering accounts for all trending pump.fun routes.

Collects all unique accounts from the top trending tokens' routes and
creates a single large ALT that covers them. This avoids per-route ALT
creation during live trading.
"""

import json
import os
import sys
from pathlib import Path

WORKDIR = Path(os.environ.get("ATOMIC_TRENCH_DIR", str(Path.home() / "atomic-trench")))
os.chdir(WORKDIR)
sys.path.insert(0, str(WORKDIR))

ALT_CACHE_PATH = WORKDIR / "data" / "alt-cache.json"


def main() -> int:
    if not ALT_CACHE_PATH.exists():
        print("No route cache found. Run warm-routes.py first.")
        return 1

    cache = json.loads(ALT_CACHE_PATH.read_text())

    # Collect all unique pool addresses from cached routes
    pool_addresses: set[str] = set()
    for mint, routes in cache.items():
        for route in routes:
            pool_addresses.add(route["pump_pool"])
            pool_addresses.add(route["meteora_pool"])

    print(f"Unique pool addresses from {len(cache)} tokens: {len(pool_addresses)}")

    # Common program addresses that should be in every ALT
    COMMON_READONLY = [
        "11111111111111111111111111111111",  # System
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
        "ComputeBudget111111111111111111111111111111",  # Compute Budget
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # ATA program
        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # Pump AMM
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",  # Meteora DLMM
        "EBBCNegFwVq4Vas6aEnhVKmVExxZ1kwFhvSNqNxpuWzs",  # Executor
        "So11111111111111111111111111111111111111112",  # WSOL mint
        "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ",  # Pump fee
        "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",  # Memo
    ]

    # Pools go as writable (they get written during swaps)
    writable = sorted(pool_addresses)
    readonly = list(COMMON_READONLY)

    # v0 ALT max: 256 writable + 256 readonly
    if len(writable) > 256:
        print(f"⚠️  {len(writable)} writable accounts exceeds 256 limit, truncating")
        writable = writable[:256]

    print(f"Writable: {len(writable)} (pool addresses)")
    print(f"Readonly: {len(readonly)} (common programs)")

    # Output for the ALT creation script
    output = {
        "writable": writable,
        "readonly": readonly,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    raise SystemExit(main())
