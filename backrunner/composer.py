"""Compile two Jupiter swap builds into one unsigned v0 transaction."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backrunner.jupiter import RoundTripBuild


@dataclass(frozen=True)
class CompiledRoundTrip:
    transaction_base64: str
    version: int
    instruction_count: int
    serialized_size: int
    account_count: int
    signature_count: int

    @property
    def fits_packet_limit(self) -> bool:
        return self.serialized_size <= 1232

    @property
    def fits_account_limit(self) -> bool:
        return self.account_count <= 64


@dataclass(frozen=True)
class DirectRouteRequest:
    rpc_url: str
    executor_program_id: str
    user: str
    pump_pool: str
    meteora_pool: str
    intermediate_mint: str
    input_lamports: int
    direction: str
    minimum_profit_lamports: int
    valid_until_slot: int
    min_context_slot: int
    lookup_table_addresses: tuple[str, ...] = ()
    slippage_bps: int = 100
    compute_unit_limit: int = 600_000
    compute_unit_price_micro_lamports: int = 0
    tip_lamports: int = 0
    tip_recipient: str | None = None


@dataclass(frozen=True)
class DirectCompiledRoundTrip:
    transaction_base64: str
    recent_blockhash: str
    last_valid_block_height: int
    instruction_count: int
    serialized_size: int
    account_count: int
    lookup_table_addresses: tuple[str, ...]
    funding_lamports: int
    quote_account: str
    intermediate_account: str
    first_program: str
    second_program: str
    executor_program: str
    signed: bool
    submitted: bool

    @property
    def fits_packet_limit(self) -> bool:
        return self.serialized_size <= 1232

    @property
    def fits_account_limit(self) -> bool:
        return self.account_count <= 64


def compose_round_trip(*, route: RoundTripBuild, taker: str) -> CompiledRoundTrip:
    script = Path(__file__).resolve().parent.parent / "scripts" / "compose-roundtrip.mjs"
    result = subprocess.run(
        ["node", str(script)],
        input=json.dumps({"taker": taker, "buy": route.buy.raw, "sell": route.sell.raw}),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown composer failure"
        raise RuntimeError(f"transaction composition failed: {detail}")
    payload = json.loads(result.stdout)
    return CompiledRoundTrip(
        transaction_base64=str(payload["transactionBase64"]),
        version=int(payload["version"]),
        instruction_count=int(payload["instructionCount"]),
        serialized_size=int(payload["serializedSize"]),
        account_count=int(payload["accountCount"]),
        signature_count=int(payload["signatureCount"]),
    )


def compose_direct_round_trip(request: DirectRouteRequest) -> DirectCompiledRoundTrip:
    script = Path(__file__).resolve().parent.parent / "scripts" / "compose-direct-roundtrip.mjs"
    payload = {
        "rpcUrl": request.rpc_url,
        "executorProgramId": request.executor_program_id,
        "user": request.user,
        "pumpPool": request.pump_pool,
        "meteoraPool": request.meteora_pool,
        "intermediateMint": request.intermediate_mint,
        "inputAmount": request.input_lamports,
        "direction": request.direction,
        "minimumProfit": request.minimum_profit_lamports,
        "validUntilSlot": request.valid_until_slot,
        "minContextSlot": request.min_context_slot,
        "lookupTableAddresses": list(request.lookup_table_addresses),
        "slippageBps": request.slippage_bps,
        "computeUnitLimit": request.compute_unit_limit,
        "computeUnitPriceMicroLamports": request.compute_unit_price_micro_lamports,
        "tipLamports": request.tip_lamports,
        "tipRecipient": request.tip_recipient,
    }
    result = subprocess.run(
        ["node", str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown direct composer failure"
        raise RuntimeError(f"direct transaction composition failed: {detail}")
    output = json.loads(result.stdout)
    signed = bool(output["signed"])
    submitted = bool(output["submitted"])
    if signed or submitted:
        raise RuntimeError("direct composer violated the unsigned no-submit boundary")
    return DirectCompiledRoundTrip(
        transaction_base64=str(output["unsignedTransactionBase64"]),
        recent_blockhash=str(output["recentBlockhash"]),
        last_valid_block_height=int(output["lastValidBlockHeight"]),
        instruction_count=int(output["instructionCount"]),
        serialized_size=int(output["serializedBytes"]),
        account_count=int(output["accountCount"]),
        lookup_table_addresses=tuple(str(value) for value in output["lookupTableAddresses"]),
        funding_lamports=int(output["fundingLamports"]),
        quote_account=str(output["quoteAccount"]),
        intermediate_account=str(output["intermediateAccount"]),
        first_program=str(output["firstProgram"]),
        second_program=str(output["secondProgram"]),
        executor_program=str(output["executorProgram"]),
        signed=signed,
        submitted=submitted,
    )
