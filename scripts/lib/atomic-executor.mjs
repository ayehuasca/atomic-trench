import { PublicKey, TransactionInstruction } from "@solana/web3.js";

export const PUMP_AMM_PROGRAM_ID = new PublicKey(
  "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
);
export const METEORA_DLMM_PROGRAM_ID = new PublicKey(
  "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
);

const U64_MAX = (1n << 64n) - 1n;

function asU64(value, name) {
  const result = BigInt(value);
  if (result < 0n || result > U64_MAX) {
    throw new RangeError(`${name} must fit in u64`);
  }
  return result;
}

function assertDexPair(first, second) {
  const firstPump = first.equals(PUMP_AMM_PROGRAM_ID);
  const firstMeteora = first.equals(METEORA_DLMM_PROGRAM_ID);
  const secondPump = second.equals(PUMP_AMM_PROGRAM_ID);
  const secondMeteora = second.equals(METEORA_DLMM_PROGRAM_ID);
  if (!((firstPump && secondMeteora) || (firstMeteora && secondPump))) {
    throw new Error("executor requires one Pump AMM and Meteora DLMM leg each");
  }
}

export function buildExecutorInstruction({
  executorProgramId,
  user,
  quoteAccount,
  intermediateAccount,
  firstInstruction,
  secondInstruction,
  secondAmountOffset,
  minimumProfit,
  validUntilSlot,
}) {
  assertDexPair(firstInstruction.programId, secondInstruction.programId);
  if (!Number.isSafeInteger(secondAmountOffset) || secondAmountOffset < 0
      || secondAmountOffset + 8 > secondInstruction.data.length) {
    throw new RangeError("secondAmountOffset must address eight bytes inside leg two data");
  }
  if (firstInstruction.data.length > 0xffff || secondInstruction.data.length > 0xffff) {
    throw new RangeError("DEX instruction data exceeds executor encoding");
  }
  if (firstInstruction.keys.length > 0xff || secondInstruction.keys.length > 0xff) {
    throw new RangeError("DEX instruction account count exceeds executor encoding");
  }

  const metas = [];
  const indexes = new Map();
  const addMeta = (pubkey, isSigner, isWritable) => {
    const key = pubkey.toBase58();
    const existingIndex = indexes.get(key);
    if (existingIndex !== undefined) {
      const existing = metas[existingIndex];
      existing.isSigner ||= isSigner;
      existing.isWritable ||= isWritable;
      return existingIndex;
    }
    if (metas.length >= 0x100) {
      throw new RangeError("merged executor account count exceeds u8 index space");
    }
    const index = metas.length;
    metas.push({ pubkey, isSigner, isWritable });
    indexes.set(key, index);
    return index;
  };

  addMeta(user, true, false);
  addMeta(quoteAccount, false, true);
  addMeta(intermediateAccount, false, true);
  const firstProgramIndex = addMeta(firstInstruction.programId, false, false);
  const secondProgramIndex = addMeta(secondInstruction.programId, false, false);

  const refsFor = (instruction) => instruction.keys.map((meta) => {
    const index = addMeta(meta.pubkey, meta.isSigner, meta.isWritable);
    const flags = Number(meta.isSigner) | (Number(meta.isWritable) << 1);
    return [index, flags];
  });
  const firstRefs = refsFor(firstInstruction);
  const secondRefs = refsFor(secondInstruction);

  const refsLength = (firstRefs.length + secondRefs.length) * 2;
  const data = Buffer.alloc(
    33 + refsLength + firstInstruction.data.length + secondInstruction.data.length,
  );
  data.write("WABR", 0, "ascii");
  data[4] = 1;
  data[5] = firstProgramIndex;
  data[6] = secondProgramIndex;
  data[7] = 1;
  data[8] = 2;
  data[9] = firstRefs.length;
  data[10] = secondRefs.length;
  data.writeUInt16LE(secondAmountOffset, 11);
  data.writeBigUInt64LE(asU64(minimumProfit, "minimumProfit"), 13);
  data.writeBigUInt64LE(asU64(validUntilSlot, "validUntilSlot"), 21);
  data.writeUInt16LE(firstInstruction.data.length, 29);
  data.writeUInt16LE(secondInstruction.data.length, 31);

  let cursor = 33;
  for (const [index, flags] of [...firstRefs, ...secondRefs]) {
    data[cursor] = index;
    data[cursor + 1] = flags;
    cursor += 2;
  }
  firstInstruction.data.copy(data, cursor);
  cursor += firstInstruction.data.length;
  secondInstruction.data.copy(data, cursor);

  return new TransactionInstruction({
    programId: executorProgramId,
    keys: metas,
    data,
  });
}
