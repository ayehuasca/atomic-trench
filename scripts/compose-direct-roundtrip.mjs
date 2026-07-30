#!/usr/bin/env node

import {
  ComputeBudgetProgram,
  Connection,
  PublicKey,
  SystemProgram,
  TransactionMessage,
  VersionedTransaction,
} from "@solana/web3.js";

import { buildDirectRoundTrip } from "./lib/direct-roundtrip.mjs";

function instructionCountAccounts(message, lookupTables) {
  return message.getAccountKeys({ addressLookupTableAccounts: lookupTables }).length;
}

async function loadLookupTables(connection, addresses) {
  const tables = [];
  for (const address of addresses ?? []) {
    const key = new PublicKey(address);
    const response = await connection.getAddressLookupTable(key);
    if (response.value === null) {
      throw new Error(`lookup table not found: ${key.toBase58()}`);
    }
    if (response.value.isActive() === false) {
      throw new Error(`lookup table is inactive: ${key.toBase58()}`);
    }
    tables.push(response.value);
  }
  return tables;
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const connection = new Connection(input.rpcUrl, input.commitment ?? "confirmed");
  const user = new PublicKey(input.user);
  const route = await buildDirectRoundTrip({
    connection,
    executorProgramId: input.executorProgramId,
    user,
    pumpPool: input.pumpPool,
    meteoraPool: input.meteoraPool,
    intermediateMint: input.intermediateMint,
    inputAmount: BigInt(input.inputAmount),
    direction: input.direction,
    minimumProfit: BigInt(input.minimumProfit),
    validUntilSlot: BigInt(input.validUntilSlot),
    slippageBps: input.slippageBps,
  });

  const instructions = [];
  if (input.computeUnitLimit !== undefined) {
    instructions.push(ComputeBudgetProgram.setComputeUnitLimit({
      units: Number(input.computeUnitLimit),
    }));
  }
  if (input.computeUnitPriceMicroLamports !== undefined) {
    instructions.push(ComputeBudgetProgram.setComputeUnitPrice({
      microLamports: BigInt(input.computeUnitPriceMicroLamports),
    }));
  }
  instructions.push(...route.instructions);
  if (input.tipLamports !== undefined && BigInt(input.tipLamports) > 0n) {
    if (!input.tipRecipient) throw new Error("tipRecipient is required when tipLamports is nonzero");
    instructions.push(SystemProgram.transfer({
      fromPubkey: user,
      toPubkey: new PublicKey(input.tipRecipient),
      lamports: BigInt(input.tipLamports),
    }));
  }

  const lookupTables = await loadLookupTables(connection, input.lookupTableAddresses);
  const blockhash = await connection.getLatestBlockhash({
    commitment: input.commitment ?? "confirmed",
    minContextSlot: input.minContextSlot,
  });
  const message = new TransactionMessage({
    payerKey: user,
    recentBlockhash: blockhash.blockhash,
    instructions,
  }).compileToV0Message(lookupTables);
  const transaction = new VersionedTransaction(message);
  const serialized = transaction.serialize();
  if (serialized.length > 1232) {
    throw new Error(
      `direct transaction is ${serialized.length} bytes; provide a lookup table that reduces it to <=1232`,
    );
  }

  process.stdout.write(JSON.stringify({
    unsignedTransactionBase64: Buffer.from(serialized).toString("base64"),
    recentBlockhash: blockhash.blockhash,
    lastValidBlockHeight: blockhash.lastValidBlockHeight,
    serializedBytes: serialized.length,
    accountCount: instructionCountAccounts(message, lookupTables),
    instructionCount: instructions.length,
    lookupTableAddresses: lookupTables.map((table) => table.key.toBase58()),
    fundingLamports: route.fundingLamports.toString(),
    quoteAccount: route.quoteAccount.toBase58(),
    intermediateAccount: route.intermediateAccount.toBase58(),
    firstProgram: route.firstInstruction.programId.toBase58(),
    secondProgram: route.secondInstruction.programId.toBase58(),
    executorProgram: route.executorInstruction.programId.toBase58(),
    signed: false,
    submitted: false,
  }));
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error.message}\n`);
  process.exitCode = 1;
});
