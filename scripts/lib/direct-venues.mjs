import { createRequire } from "node:module";

import BN from "bn.js";
import { PublicKey } from "@solana/web3.js";

import {
  METEORA_DLMM_PROGRAM_ID,
  PUMP_AMM_PROGRAM_ID,
} from "./atomic-executor.mjs";

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
    discriminator = METEORA_SWAP2_DISCRIMINATOR;
    expectedLength = 28;
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
  if (venue === "Meteora" && data.readUInt32LE(24) !== 0) {
    throw new Error("Meteora dynamic instruction contains unsupported remaining-account slices");
  }
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
  const binArrays = await dlmm.getBinArrayForSwap(swapForY);
  const transaction = await dlmm.swap({
    inToken: inMint,
    outToken: outMint,
    inAmount: asPositiveBN(inputAmount, "inputAmount"),
    minOutAmount: new BN(minimumOutput.toString()),
    lbPair: dlmm.pubkey,
    user: asPublicKey(user),
    binArraysPubkey: binArrays.map((entry) => entry.publicKey),
  });
  return oneProgramInstruction(
    transaction.instructions,
    METEORA_DLMM_PROGRAM_ID,
    "Meteora",
  );
}

export async function buildMeteoraDynamicExactInputInstruction(options) {
  // Request bin arrays with a conservative amount estimate so the set of
  // included bin arrays covers the potentially larger real output that the
  // executor patches in at runtime.
  const dlmm = await loadMeteoraPool(options.connection, options.pool);
  const inMint = options.inputMint instanceof PublicKey
    ? options.inputMint
    : new PublicKey(options.inputMint);
  const outMint = options.outputMint instanceof PublicKey
    ? options.outputMint
    : new PublicKey(options.outputMint);
  const swapForY = inMint.equals(dlmm.tokenX.publicKey);
  // Fetch all active bin arrays in the swap direction to cover any runtime
  // amount that the executor patches in.
  const binArrays = await dlmm.getBinArrayForSwap(swapForY);

  // Build the placeholder instruction (amount=1) for the executor template
  const transaction = await dlmm.swap({
    inToken: inMint,
    outToken: outMint,
    inAmount: new BN(1),
    minOutAmount: new BN(0),
    lbPair: dlmm.pubkey,
    user: options.user instanceof PublicKey ? options.user : new PublicKey(options.user),
    binArraysPubkey: binArrays.map((entry) => entry.publicKey),
  });
  const instruction = oneProgramInstruction(
    transaction.instructions,
    METEORA_DLMM_PROGRAM_ID,
    "Meteora",
  );
  if (instruction.data.length !== 28) {
    throw new Error("Meteora dynamic instruction has unexpected ABI length");
  }
  // Return bin arrays so the composer includes the full conservative set
  return {
    instruction,
    amountOffset: dynamicAmountOffset("Meteora", instruction.data),
    binArrays,
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
  return oneProgramInstruction(instructions, PUMP_AMM_PROGRAM_ID, "Pump");
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
