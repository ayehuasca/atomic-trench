#!/usr/bin/env python3
"""Fetch tx details for Wallet A route."""
import requests

sig = "55GVmkDCRDY6WeXM5b142oiSUnJ4qatSveVrLQN3AETsem1zCa4zWHGyDD8fPirTuLPY2hesXHp4bH9dcY4p8DF3"
rpc = "https://api.mainnet-beta.solana.com"
resp = requests.post(rpc, json={
    "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
    "params": [sig, {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}]
}, timeout=30)
tx = resp.json()["result"]
accts = tx["transaction"]["message"]["accountKeys"]

print("=== ACCOUNTS ===")
for i, a in enumerate(accts):
    pk = a["pubkey"] if isinstance(a, dict) else str(a)
    print(f"{i}: {pk}")

# Find pump and meteora pool addresses
# Pump: pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA
# Meteora: LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo
pre = tx["meta"].get("preTokenBalances", [])
print("\n=== TOKEN MINT ===")
for p in pre:
    if p.get("mint") and p["mint"] != "So11111111111111111111111111111111111111112":
        print(f"Mint: {p['mint']}, Owner: {p['owner']}, Amount: {p.get('uiTokenAmount',{}).get('uiAmount')}")
