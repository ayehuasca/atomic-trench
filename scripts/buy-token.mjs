#!/usr/bin/env node
/**
 * buy-token.mjs — Standalone buy (or sell) on Pump AMM.
 *
 * Input (stdin JSON):
 *   {action, rpcUrl, keypairPath, mint, pumpPool, buySol, slippageBps,
 *    computeUnitPriceMicroLamports, maxFeeLamports}
 *
 * For action="sell", sells the full token balance back to the Pump AMM bonding curve.
 * For action="buy", buys tokens with buySol WSOL.
 *
 * Output (stdout JSON):
 *   {submitted, signature?, error?}
 */

import {createRequire} from "node:module";
import {readFileSync} from "node:fs";

import {
  Connection, Keypair, PublicKey, ComputeBudgetProgram,
  VersionedTransaction, TransactionMessage, AddressLookupTableAccount,
} from "@solana/web3.js";
import {
  NATIVE_MINT, TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID,
  getAssociatedTokenAddressSync,
  createAssociatedTokenAccountIdempotentInstruction,
  createSyncNativeInstruction, createCloseAccountInstruction,
} from "@solana/spl-token";

const require = createRequire(import.meta.url);
const {OnlinePumpAmmSdk, PumpAmmSdk, buyQuoteInput} = require("@pump-fun/pump-swap-sdk");
const BN = require("bn.js");

const PUMP_AMM_PROGRAM_ID = new PublicKey("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
const PUMP_PROGRAM_ID = new PublicKey("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");
const MEMO_PROGRAM_ID = new PublicKey("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr");
const METADATA_PROGRAM_ID = new PublicKey("metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s");

// ── Parse input ─────────────────────────────────────────────────────────────

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));

const {action, rpcUrl, mint, pumpPool, buySol, slippageBps=100,
       computeUnitPriceMicroLamports=50000, maxFeeLamports=100000} = input;
const keypairPath = input.keypairPath;

// ── Setup ───────────────────────────────────────────────────────────────────

const conn = new Connection(rpcUrl, "confirmed");
const signer = Keypair.fromSecretKey(new Uint8Array(JSON.parse(readFileSync(keypairPath, "utf8"))));
const owner = signer.publicKey;
const mintPk = new PublicKey(mint);
const poolPk = new PublicKey(pumpPool);

// ── Token accounts ──────────────────────────────────────────────────────────

// Determine token program for the mint
const mintInfo = await conn.getAccountInfo(mintPk, "confirmed");
const tokenProgram = mintInfo && mintInfo.owner.equals(TOKEN_2022_PROGRAM_ID)
  ? TOKEN_2022_PROGRAM_ID : TOKEN_PROGRAM_ID;

const quoteAccount = getAssociatedTokenAddressSync(NATIVE_MINT, owner, false, TOKEN_PROGRAM_ID);
const intermediateAccount = getAssociatedTokenAddressSync(mintPk, owner, false, tokenProgram);

// ── Build instruction ───────────────────────────────────────────────────────

let ix;
let setupIxs = [];
let cleanupIxs = [];

if (action === "buy") {
  // Load Pump AMM state
  const online = new OnlinePumpAmmSdk(conn);
  const state = await online.swapSolanaState(poolPk, owner, intermediateAccount, quoteAccount);
  const {baseMint, baseMintAccount, feeConfig, globalConfig, pool: poolState} = state;
  const {base, maxQuote} = buyQuoteInput({
    quote: new BN(Math.floor(parseFloat(buySol) * 1e9)),
    slippage: Number(slippageBps) / 100,
    baseReserve: state.poolBaseAmount,
    quoteReserve: state.poolQuoteAmount,
    virtualQuoteReserves: poolState.virtualQuoteReserves,
    globalConfig, baseMintAccount, baseMint,
    coinCreator: poolState.coinCreator,
    creator: poolState.creator,
    feeConfig,
  });
  const sdk = new PumpAmmSdk();
  const instructions = await sdk.buyInstructionsNoPool(state, base, maxQuote);
  ix = instructions.find(i => i.programId.equals(PUMP_AMM_PROGRAM_ID));
  if (!ix) throw new Error("Pump SDK did not produce a buy instruction");

  const fundingLamports = ix.data.readBigUInt64LE(16);
  setupIxs = [
    createAssociatedTokenAccountIdempotentInstruction(owner, quoteAccount, owner, NATIVE_MINT, TOKEN_PROGRAM_ID),
    createAssociatedTokenAccountIdempotentInstruction(owner, intermediateAccount, owner, mintPk, tokenProgram),
  ];
  cleanupIxs = [
    createCloseAccountInstruction(quoteAccount, owner, owner, [], TOKEN_PROGRAM_ID),
  ];
} else if (action === "sell") {
  // Sell all tokens — use Pump AMM sell instruction
  const online = new OnlinePumpAmmSdk(conn);
  const state = await online.swapSolanaState(poolPk, owner, intermediateAccount, quoteAccount);
  const sdk = new PumpAmmSdk();
  const instructions = await sdk.sellInstructionsNoPool(state, new BN(1), new BN(1));
  ix = instructions.find(i => i.programId.equals(PUMP_AMM_PROGRAM_ID));
  if (!ix) throw new Error("Pump SDK did not produce a sell instruction");
} else {
  throw new Error(`unknown action: ${action}`);
}

// ── Compose transaction ─────────────────────────────────────────────────────

const priorityIx = ComputeBudgetProgram.setComputeUnitPrice({
  microLamports: computeUnitPriceMicroLamports,
});
const limitIx = ComputeBudgetProgram.setComputeUnitLimit({
  units: 600000,
});

// Build lookup tables for any ALTs
const altAddresses = [];  // We don't use ALTs for standalone buys (no round-trip)
const lookupTableAccounts = await Promise.all(
  altAddresses.map(async (addr) => {
    const acc = await conn.getAccountInfo(new PublicKey(addr));
    if (!acc) return null;
    return new AddressLookupTableAccount({
      key: new PublicKey(addr),
      state: AddressLookupTableAccount.deserialize(acc.data),
    });
  })
);
const validTables = lookupTableAccounts.filter(Boolean);

const allIxs = [...setupIxs, priorityIx, limitIx, ix, ...cleanupIxs];
const blockhash = (await conn.getLatestBlockhash("confirmed")).blockhash;

let message;
if (validTables.length > 0) {
  message = TransactionMessage.decompile(
    VersionedTransaction.deserialize((await conn.compileTransaction({
      payerKey: owner,
      instructions: allIxs,
      lookupTableAccounts: validTables,
    })).serialize()).message,
  );
} else {
  message = new TransactionMessage({
    payerKey: owner,
    recentBlockhash: blockhash,
    instructions: allIxs,
  });
}

const tx = new VersionedTransaction(message.compileToV0Message());
tx.sign([signer]);

// ── Submit ──────────────────────────────────────────────────────────────────

const serialized = tx.serialize();
if (serialized.length > 1232) {
  process.stdout.write(JSON.stringify({
    submitted: false,
    error: `transaction too large: ${serialized.length} bytes`,
  }));
  process.exit(0);
}

try {
  const sig = await conn.sendTransaction(tx, {
    skipPreflight: false,
    maxRetries: 0,
    preflightCommitment: "confirmed",
  });
  process.stdout.write(JSON.stringify({submitted: true, signature: sig}));
} catch (err) {
  process.stdout.write(JSON.stringify({
    submitted: false,
    error: err.message || String(err),
  }));
}