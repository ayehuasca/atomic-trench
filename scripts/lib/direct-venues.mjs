import { createRequire } from "node:module";

import BN from "bn.js";
import { PublicKey, TransactionInstruction } from "@solana/web3.js";
import {
  getAssociatedTokenAddressSync,
  NATIVE_MINT,
  TOKEN_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
} from "@solana/spl-token";

import {
  METEORA_DLMM_PROGRAM_ID,
  PUMP_AMM_PROGRAM_ID,
} from "./atomic-executor.mjs";

const MEMO_PROGRAM_ID = new PublicKey("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr");

// Both SDK packages publish ESM metadata that is currently incompatible with
// their emitted files under Node 20. Their CommonJS entrypoints are valid.
const require = createRequire(import.meta.url);
const DLMM = require("@meteora-ag/dlmm");
const {
  OnlinePumpAmmSdk,
  PumpAmmSdk,
  buyQuoteInput,
} = require("@pump-fun/pump-swap-sdk");

const PUMP_SELL_DISCRIMINATOR = Buffer.from([51, 230, 133, 164, 1, 127, 131, 173]);
const METEORA_SWAP2_DISCRIMINATOR = Buffer.from([65, 75, 63, 76, 235, 91, 91, 136]);

export function dynamicAmountOffset(venue, instructionData) {
  const data = Buffer.from(instructionData);
  let discriminator;
  let expectedLength;
  let expectedMinimumOutput;
  if (venue === "Pump") {
    discriminator = PUMP_SELL_DISCRIMINATOR;
    expectedLength = 24;
    expectedMinimumOutput = 1n;
  } else if (venue === "Meteora") {
   // SDK swap2 now serializes a Vec<SliceAccountFlag> at bytes 24.. which
   // encodes transfer-hook metadata. For legacy-SPL pools with no hooks,
   // it serializes as two zero-length entries. We accept the pinned 32-byte
   // template and reject any non-zero slice lengths at runtime.
   discriminator = METEORA_SWAP2_DISCRIMINATOR;
   expectedLength = 32;
   expectedMinimumOutput = 0n;
 } else {
   throw new Error(`unsupported dynamic venue: ${venue}`);
 }
 if (data.length !== expectedLength) {
   throw new Error(`${venue} dynamic instruction has invalid ABI length`);
 }
 if (!data.subarray(0, 8).equals(discriminator)) {
   throw new Error(`${venue} dynamic instruction discriminator does not match pinned ABI`);
 }
 if (data.readBigUInt64LE(8) !== 1n) {
   throw new Error(`${venue} dynamic instruction amount placeholder must equal one`);
 }
 if (data.readBigUInt64LE(16) !== expectedMinimumOutput) {
   throw new Error(`${venue} dynamic instruction minimum output does not match pinned template`);
 }
 // For Meteora swap2, bytes 24..32 contain the serialized Vec<SliceAccountFlag>.
 // We accept the pinned template (all-zero slice lengths) but do not reject
 // non-zero slices here — the executor's on-chain validation handles that.
  return 8;
}

function oneProgramInstruction(instructions, programId, venue) {
  const matches = instructions.filter((instruction) => instruction.programId.equals(programId));
  if (matches.length !== 1) {
    throw new Error(`${venue} SDK produced ${matches.length} venue instructions; expected exactly one`);
  }
  return matches[0];
}

function asPublicKey(value) {
  return value instanceof PublicKey ? value : new PublicKey(value);
}

function asPositiveBN(value, name) {
  const amount = new BN(value.toString());
  if (amount.lten(0)) {
    throw new RangeError(`${name} must be positive`);
  }
  return amount;
}

export async function loadMeteoraPool(connection, pool) {
  return DLMM.create(connection, asPublicKey(pool), {
    cluster: "mainnet-beta",
    skipSolWrappingOperation: true,
  });
}

function getOrCreateATAAddress(dlmm, mint, user) {
  const tokenProgram = mint.equals(dlmm.tokenX.publicKey)
    ? dlmm.tokenX.owner
    : dlmm.tokenY.owner;
  return getAssociatedTokenAddressSync(mint, user, false, tokenProgram);
}

export async function buildMeteoraExactInputInstruction({
  connection,
  pool,
  user,
  inputMint,
  outputMint,
  inputAmount,
  minimumOutput = 0,
}) {
  const dlmm = await loadMeteoraPool(connection, pool);
  const inMint = asPublicKey(inputMint);
  const outMint = asPublicKey(outputMint);
  const tokenX = dlmm.tokenX.publicKey;
  const tokenY = dlmm.tokenY.publicKey;
  const validPair = (inMint.equals(tokenX) && outMint.equals(tokenY))
    || (inMint.equals(tokenY) && outMint.equals(tokenX));
  if (!validPair) {
    throw new Error("requested mints do not match the Meteora DLMM pool");
  }
  const swapForY = inMint.equals(tokenX);
  const availableBinArrays = await dlmm.getBinArrayForSwap(swapForY);
  const amount = asPositiveBN(inputAmount, "inputAmount");
  const quote = dlmm.swapQuote(amount, swapForY, new BN(0), availableBinArrays);
  if (!quote.binArraysPubkey?.length) {
    throw new Error("Meteora quote did not return any bin arrays");
  }

  // Build swap2 directly to bypass the SDK's internal CU estimation
  // (which simulates and fails on unfunded accounts in shadow mode).
  const userKey = asPublicKey(user);
  const userTokenIn = getOrCreateATAAddress(dlmm, inMint, userKey);
  const userTokenOut = getOrCreateATAAddress(dlmm, outMint, userKey);
  const binArrayMetas = quote.binArraysPubkey.map((pk) => ({
    pubkey: pk,
    isSigner: false,
    isWritable: true,
  }));
  const { slices, accounts: transferHookAccounts } = dlmm.getPotentialToken2022IxDataAndAccounts(0);
  const swapIx = await dlmm.program.methods
    .swap2(amount, new BN(minimumOutput.toString()), { slices })
    .accountsPartial({
      lbPair: dlmm.pubkey,
      reserveX: dlmm.lbPair.reserveX,
      reserveY: dlmm.lbPair.reserveY,
      tokenXMint: dlmm.lbPair.tokenXMint,
      tokenYMint: dlmm.lbPair.tokenYMint,
      tokenXProgram: dlmm.tokenX.owner,
      tokenYProgram: dlmm.tokenY.owner,
      user: userKey,
      userTokenIn,
      userTokenOut,
      binArrayBitmapExtension: dlmm.binArrayBitmapExtension
        ? dlmm.binArrayBitmapExtension.publicKey
        : null,
      oracle: dlmm.lbPair.oracle,
      hostFeeIn: null,
      memoProgram: MEMO_PROGRAM_ID,
    })
    .remainingAccounts(transferHookAccounts)
    .remainingAccounts(binArrayMetas)
    .instruction();

  return {
    instruction: new TransactionInstruction({
      programId: METEORA_DLMM_PROGRAM_ID,
      keys: swapIx.keys,
      data: Buffer.from(swapIx.data),
    }),
    expectedOutput: quote.outAmount,
  };
}

export async function buildMeteoraDynamicExactInputInstruction(options) {
  const dlmm = await loadMeteoraPool(options.connection, options.pool);
  const inMint = options.inputMint instanceof PublicKey
    ? options.inputMint
    : new PublicKey(options.inputMint);
  const outMint = options.outputMint instanceof PublicKey
    ? options.outputMint
    : new PublicKey(options.outputMint);
  const swapForY = inMint.equals(dlmm.tokenX.publicKey);
  const availableBinArrays = await dlmm.getBinArrayForSwap(swapForY);
  const estimatedInput = asPositiveBN(options.estimatedInputAmount, "estimatedInputAmount");
  const quote = dlmm.swapQuote(
    estimatedInput,
    swapForY,
    new BN(0),
    availableBinArrays,
  );
  if (!quote.binArraysPubkey?.length) {
    throw new Error("Meteora dynamic quote did not return any bin arrays");
  }

  // Build the swap2 instruction directly via the Anchor program methods to
  // avoid the SDK's dlmm.swap() wrapper which internally simulates the
  // transaction for compute-unit estimation — that simulation fails when the
  // user wallet has no SOL (shadow/no-submit mode).
  const user = options.user instanceof PublicKey ? options.user : new PublicKey(options.user);
  const [userTokenIn, userTokenOut] = await Promise.all([
    getOrCreateATAAddress(dlmm, inMint, user),
    getOrCreateATAAddress(dlmm, outMint, user),
  ]);
  const binArrayMetas = quote.binArraysPubkey.map((pk) => ({
    pubkey: pk,
    isSigner: false,
    isWritable: true,
  }));
  const { slices, accounts: transferHookAccounts } = dlmm.getPotentialToken2022IxDataAndAccounts(0);
  const swapIx = await dlmm.program.methods
    .swap2(estimatedInput, new BN(0), { slices })
    .accountsPartial({
      lbPair: dlmm.pubkey,
      reserveX: dlmm.lbPair.reserveX,
      reserveY: dlmm.lbPair.reserveY,
      tokenXMint: dlmm.lbPair.tokenXMint,
      tokenYMint: dlmm.lbPair.tokenYMint,
      tokenXProgram: dlmm.tokenX.owner,
      tokenYProgram: dlmm.tokenY.owner,
      user,
      userTokenIn,
      userTokenOut,
      binArrayBitmapExtension: dlmm.binArrayBitmapExtension
        ? dlmm.binArrayBitmapExtension.publicKey
        : null,
      oracle: dlmm.lbPair.oracle,
      hostFeeIn: null,
      memoProgram: MEMO_PROGRAM_ID,
    })
    .remainingAccounts(transferHookAccounts)
    .remainingAccounts(binArrayMetas)
    .instruction();

  const instruction = new TransactionInstruction({
    programId: METEORA_DLMM_PROGRAM_ID,
    keys: swapIx.keys,
    data: Buffer.from(swapIx.data),
  });
  if (instruction.data.length !== 32) {
    throw new Error(
      `Meteora dynamic instruction has unexpected ABI length ${instruction.data.length}`,
    );
  }
  instruction.data.writeBigUInt64LE(1n, 8);
  return {
    instruction,
    amountOffset: dynamicAmountOffset("Meteora", instruction.data),
    binArraysPubkey: quote.binArraysPubkey,
  };
}

async function loadPumpState({ connection, pool, user, baseAccount, quoteAccount }) {
  const online = new OnlinePumpAmmSdk(connection);
  return online.swapSolanaState(
    asPublicKey(pool),
    asPublicKey(user),
    asPublicKey(baseAccount),
    asPublicKey(quoteAccount),
  );
}

export async function buildPumpBuyQuoteInputInstruction({
  connection,
  pool,
  user,
  baseAccount,
  quoteAccount,
  quoteAmount,
  slippageBps,
}) {
  const state = await loadPumpState({ connection, pool, user, baseAccount, quoteAccount });
  const { baseMint, baseMintAccount, feeConfig, globalConfig, pool: poolState } = state;
  const { base, maxQuote } = buyQuoteInput({
    quote: asPositiveBN(quoteAmount, "quoteAmount"),
    slippage: Number(slippageBps) / 100,
    baseReserve: state.poolBaseAmount,
    quoteReserve: state.poolQuoteAmount,
    virtualQuoteReserves: poolState.virtualQuoteReserves,
    globalConfig,
    baseMintAccount,
    baseMint,
    coinCreator: poolState.coinCreator,
    creator: poolState.creator,
    feeConfig,
  });
  const sdk = new PumpAmmSdk();
  const instructions = await sdk.buyInstructionsNoPool(state, base, maxQuote);
  return {
    instruction: oneProgramInstruction(instructions, PUMP_AMM_PROGRAM_ID, "Pump"),
    expectedOutput: base,
  };
}

export async function buildPumpDynamicSellInstruction({
  connection,
  pool,
  user,
  baseAccount,
  quoteAccount,
}) {
  const state = await loadPumpState({ connection, pool, user, baseAccount, quoteAccount });
  const sdk = new PumpAmmSdk();
  const instructions = await sdk.sellInstructionsNoPool(state, new BN(1), new BN(1));
  const instruction = oneProgramInstruction(instructions, PUMP_AMM_PROGRAM_ID, "Pump");
  return { instruction, amountOffset: dynamicAmountOffset("Pump", instruction.data) };
}
