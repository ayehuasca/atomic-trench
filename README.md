# Wallet A Backrunner

Dry-run-first Solana observer and replay engine modeled on wallet
`MRiYA4oN3158fCV8evhuCofrDzbHyYvYnGZUDJvoCsa`.

## What Wallet A actually did

The diagnosed transactions were zero-hold atomic round trips, not ordinary
buy/hold/sell trades. In the captured 200-signature window:

- 16 successful GMGN-trending atomic round trips;
- 12 had a same-slot buy of at least $300 before the wallet transaction;
- 10 followed within three transaction positions;
- 46 additional trending attempts failed and still paid fees;
- corrected cohort result after failed fees: `+0.1829156689 SOL`.

The correct label is **same-slot atomic arbitrage/back-running**, not
front-running. This project consumes only already-executed block data. It does
not consume pending transactions, preconfirmations, or raw shreds.

## Current status

| Component | Status |
|---|---|
| Deterministic Wallet A replay | Working |
| Live GMGN top-100 trend feed | Working |
| Live SOL/USD feed | Working |
| Finalized Solana block scanner | Working |
| >$300 owner-level buy detector | Working |
| Transaction index/order preservation | Working |
| Round-trip cost/profit engine | Working |
| Jupiter V2 `/build` generic routing | Working; not Wallet A's venue path |
| Direct Pump AMM ↔ Meteora DLMM construction | Working and tested |
| Unsigned direct atomic v0 composition | Working and tested |
| Exact simulation with `minContextSlot` | Working; direct mainnet proof awaits deployment/ALT |
| Processed log stream + confirmed reconciliation | Working |
| On-chain two-leg atomic executor | SBF builds; six ProgramTest atomicity checks pass |
| Persistent single-attempt lock + signature deduplication | Working and tested |
| Exact simulated wallet-net balance delta | Working and tested |
| Heartbeat-covered shadow evidence ledger | Working; current evidence is 0 hours / 0 candidates |
| Signing or submission | **Hard disabled** |

This is a working route-and-simulation shadow artifact, not a live trader.
Setting `dry_run: false` fails closed.

## Timing contract

A trigger observed at `confirmed`/`finalized` cannot reliably be back-run in the
same slot: that block has already been produced. The processed WebSocket path
reacts earlier and reconciles the signature at confirmed commitment; a missing
transaction is reported as `unavailable_or_reorged`. This still cannot promise
same-slot landing and is not a full durable fork-state machine.

Live-equivalent atomic execution would require one bot transaction containing:

```text
record starting quote balance
→ swap quote→token on explicit pool A
→ swap actual token delta→quote on explicit pool B
→ assert final quote balance >= start + minimum profit
```

If either swap or the final assertion fails, Solana reverts state changes, but
the failed transaction fee remains payable.

## Install

Windows / Git Bash:

```bash
cd /c/Users/user/huasxa-site/wallet-a-backrunner
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -e '.[dev]'
npm install --ignore-scripts
```

GMGN ranking discovery is read-only. A fresh GMGN OpenAPI key enables the
read-only `/v1/market/rank` and `/v1/token/info` endpoints:

```bash
export GMGN_API_KEY='<set locally; never paste it into chat or commit it>'
```

No GMGN signing private key, Solana private key, or trading endpoint is accepted.
The authenticated GMGN router is deliberately excluded because it returns an
aggregated remote transaction rather than the required direct Pump/Meteora
atomic-executor route.

## Run

```bash
# Replay the captured Wallet A evidence
.venv/Scripts/python.exe -m backrunner.cli --config config.yaml replay

# Scan one currently finalized block; read-only, submits nothing
.venv/Scripts/python.exe -m backrunner.cli --config config.yaml observe-once

# Observe one processed Pump/Meteora transaction and reconcile it at confirmed
.venv/Scripts/python.exe -m backrunner.cli --config config.yaml observe-processed-once

# Build two Jupiter routes, compile one unsigned v0 transaction, and simulate it
.venv/Scripts/python.exe -m backrunner.cli --config config.yaml shadow-route \
  --token-mint 5UUH9RTDiSpq6HKS6bp4NdU9PNJpXRXuiw6ShBTBhgH2

# Report persistent 72-hour / 100-candidate promotion evidence
.venv/Scripts/python.exe -m backrunner.cli --config config.yaml shadow-evidence-status

# Quality gates
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy backrunner
npm test
```

A verified live shadow probe scanned slot `435961995`, loaded 100 GMGN-trending
mints at `$73.61294964/SOL`, detected three buys over $300, and reported
`transactions_submitted: 0`.

A verified multi-pool route proof used Meteora DLMM for the buy and a different
Meteora DLMM plus 1DEX for the sell. Both legs compiled into one unsigned
1,104-byte v0 transaction and simulated successfully at slot `435972828`, using
182,595 CU. It was correctly rejected: conservative gross was `-209710`
lamports and net after fee, tip assumption, safety margin, and failed-attempt
reserve was `-1229833` lamports.

A live processed/no-submit probe observed signature
`2i7QREEhe3aaqB751zi6jVRuCGxrCpFaB2R64qYq8fWso7GG28Gxpn9osNcbw3WWxrQfmmGEV2CsuypzvBp6jrq6`
at slot `436014642`, reconciled it as confirmed, loaded the union of 201 GMGN
ranking mints, and submitted zero transactions.

The Rust executor's ProgramTest suite proves success with runtime-sized second
leg input, first-leg failure rollback, second-leg failure rollback, profit-guard
rollback, expiry rejection, and signer-privilege-escalation rejection. The SBF
artifact is 33,496 bytes with SHA-256
`d4c062d3e03ec0b08ee9f3d5ef98fb3e4176922f816e2e92ee4175427966de0f`.

## Wallet-derived defaults

`config.yaml` contains:

- `minimum_buy_usd: 300`
- `maximum_transaction_gap: 3`
- `failed_attempt_reserve_lamports: 815123`
- `minimum_net_profit_lamports: 279929`
- `shadow_input_lamports: 10000000` (0.01 SOL)
- `jito_tip_lamports: 100000` (shadow cost assumption only)
- `safety_margin_lamports: 100000`

The failed-attempt reserve is Wallet A's observed failed-fee cost amortized over
an eventual successful round trip. The minimum-profit value is its smallest
observed successful net result. These are replay-derived starting values—not a
claim of future profitability.

## Safety boundary

The project never calls `sendTransaction`, `sendBundle`, Jupiter `/execute`,
`tx.jup.ag`, or Helius Sender. It does not load a keypair. Do not replace atomic
execution with two separate Jupiter swaps: the first leg could land while the
second fails, leaving directional inventory.

The direct transaction includes the custom executor, actual first-leg-output
chaining, residual-inventory check, expiry, and final quote-balance floor. It is
still not permission to send: no reviewed deployment or route-compatible
mainnet ALT is configured, no exact direct mainnet simulation has passed, and
the required 72-hour/100-candidate positive wallet-net shadow gate is unmet.

Only `shadow-direct` runs supplied with a confirmed `--trigger-signature` can be
written to the durable evidence ledger. Those runs are serialized by an atomic
cross-process lock, duplicate signatures are rejected, and wallet-net result is
measured from the taker's returned post-simulation system account only when the
real taker balance is unchanged across the simulation context. Shadow runtime
counts only heartbeat intervals below the configured maximum gap; wall-clock
downtime cannot inflate the 72-hour gate.

The VPS shadow observer is defined by `ops/atomic-trench-shadow.service`. It is
systemd-owned, submits nothing, and refuses to start unless the operator creates
`~/.config/atomic-trench/gmgn.env` containing a fresh `GMGN_API_KEY` directly on
the VPS.

Wallet A's historical executor `AN225...` is deployed but upgradeable by Wallet
A, and its active `6YpH...` ALT is controlled externally. They are useful public
evidence, not safe substitutes for this project's reviewed executor deployment
and route-compatible ALT.

## Authoritative implementation references

- Jupiter Swap API V2: https://developers.jup.ag/docs/swap/index
- Jupiter custom `/build`: https://developers.jup.ag/docs/swap/build/index
- Helius LaserStream: https://www.helius.dev/docs/laserstream
- Helius transaction subscription: https://www.helius.dev/docs/api-reference/rpc/websocket/transactionsubscribe
- Jito low-latency sending/bundles: https://docs.jito.wtf/lowlatencytxnsend/
- Solana transaction constraints: https://solana.com/docs/core/transactions
- Solana simulation: https://solana.com/docs/rpc/http/simulatetransaction
