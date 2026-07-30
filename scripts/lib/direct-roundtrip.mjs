import {
  NATIVE_MINT,
  TOKEN_2022_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  createAssociatedTokenAccountIdempotentInstruction,
  createCloseAccountInstruction,
  createSyncNativeInstruction,
  getAssociatedTokenAddressSync,
} from "@solana/spl-token";
import { PublicKey, SystemProgram } from "@solana/web3.js";

import { buildExecutorInstruction } from "./atomic-executor.mjs";
import {
  buildMeteoraDynamicExactInputInstruction,
  buildMeteoraExactInputInstruction,
  buildPumpBuyQuoteInputInstruction,
  buildPumpDynamicSellInstruction,
} from "./direct-venues.mjs";

export const DIRECTION_METEORA_TO_PUMP = "meteora_to_pump";
export const DIRECTION_PUMP_TO_METEORA = "pump_to_meteora";

function publicKey(value) {
  return value instanceof PublicKey ? value : new PublicKey(value);
}

export async function tokenProgramForMint(connection, mint) {
  const info = await connection.getAccountInfo(mint, "confirmed");
  if (info === null) {
    throw new Error(`intermediate mint does not exist: ${mint.toBase58()}`);
  }
  if (info.owner.equals(TOKEN_PROGRAM_ID)) {
    return TOKEN_PROGRAM_ID;
  }
  if (info.owner.equals(TOKEN_2022_PROGRAM_ID)) {
    throw new Error("Token-2022 intermediate mints are unsupported until extensions are reviewed");
  }
  throw new Error("intermediate mint is not owned by a supported token program");
}

export async function buildDirectRoundTrip({
  connection,
  executorProgramId,
  user,
  pumpPool,
  meteoraPool,
  intermediateMint,
  inputAmount,
  direction,
  minimumProfit,
  validUntilSlot,
  slippageBps = 100,
}) {
  const owner = publicKey(user);
  const mint = publicKey(intermediateMint);
  const tokenProgram = await tokenProgramForMint(connection, mint);
  const quoteAccount = getAssociatedTokenAddressSync(
    NATIVE_MINT,
    owner,
    false,
    TOKEN_PROGRAM_ID,
  );
  const intermediateAccount = getAssociatedTokenAddressSync(
    mint,
    owner,
    false,
    tokenProgram,
  );

  let firstInstruction;
  let secondInstruction;
  let secondAmountOffset;
  let fundingLamports;
  if (direction === DIRECTION_METEORA_TO_PUMP) {
    firstInstruction = await buildMeteoraExactInputInstruction({
      connection,
      pool: meteoraPool,
      user: owner,
      inputMint: NATIVE_MINT,
      outputMint: mint,
      inputAmount,
      minimumOutput: 0,
    });
    const second = await buildPumpDynamicSellInstruction({
      connection,
      pool: pumpPool,
      user: owner,
      baseAccount: intermediateAccount,
      quoteAccount,
    });
    secondInstruction = second.instruction;
    secondAmountOffset = second.amountOffset;
    fundingLamports = BigInt(inputAmount);
  } else if (direction === DIRECTION_PUMP_TO_METEORA) {
    firstInstruction = await buildPumpBuyQuoteInputInstruction({
      connection,
      pool: pumpPool,
      user: owner,
      baseAccount: intermediateAccount,
      quoteAccount,
      quoteAmount: inputAmount,
      slippageBps,
    });
    if (firstInstruction.data.length < 24) {
      throw new Error("Pump buy instruction is too short for its max quote amount");
    }
    fundingLamports = firstInstruction.data.readBigUInt64LE(16);
    const second = await buildMeteoraDynamicExactInputInstruction({
      connection,
      pool: meteoraPool,
      user: owner,
      inputMint: mint,
      outputMint: NATIVE_MINT,
    });
    secondInstruction = second.instruction;
    secondAmountOffset = second.amountOffset;
  } else {
    throw new Error(`unsupported direction: ${direction}`);
  }
  if (fundingLamports <= 0n) {
    throw new Error("round-trip funding must be positive");
  }

  const executorInstruction = buildExecutorInstruction({
    executorProgramId: publicKey(executorProgramId),
    user: owner,
    quoteAccount,
    intermediateAccount,
    firstInstruction,
    secondInstruction,
    secondAmountOffset,
    minimumProfit: BigInt(minimumProfit),
    validUntilSlot: BigInt(validUntilSlot),
  });

  const setupInstructions = [
    createAssociatedTokenAccountIdempotentInstruction(
      owner,
      quoteAccount,
      owner,
      NATIVE_MINT,
      TOKEN_PROGRAM_ID,
    ),
    createAssociatedTokenAccountIdempotentInstruction(
      owner,
      intermediateAccount,
      owner,
      mint,
      tokenProgram,
    ),
    SystemProgram.transfer({
      fromPubkey: owner,
      toPubkey: quoteAccount,
      lamports: fundingLamports,
    }),
    createSyncNativeInstruction(quoteAccount, TOKEN_PROGRAM_ID),
  ];
  const cleanupInstructions = [
    createCloseAccountInstruction(
      quoteAccount,
      owner,
      owner,
      [],
      TOKEN_PROGRAM_ID,
    ),
  ];

  return {
    quoteAccount,
    intermediateAccount,
    tokenProgram,
    fundingLamports,
    firstInstruction,
    secondInstruction,
    executorInstruction,
    setupInstructions,
    cleanupInstructions,
    instructions: [
      ...setupInstructions,
      executorInstruction,
      ...cleanupInstructions,
    ],
  };
}
