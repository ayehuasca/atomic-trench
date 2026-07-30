import assert from "node:assert/strict";
import test from "node:test";
import { TOKEN_2022_PROGRAM_ID } from "@solana/spl-token";
import { PublicKey, TransactionInstruction } from "@solana/web3.js";

import { buildExecutorInstruction } from "../lib/atomic-executor.mjs";
import { dynamicAmountOffset } from "../lib/direct-venues.mjs";
import { tokenProgramForMint } from "../lib/direct-roundtrip.mjs";

const PUMP = new PublicKey("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
const METEORA = new PublicKey("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo");
const key = (byte) => new PublicKey(new Uint8Array(32).fill(byte));


test("encodes merged Pump/Meteora CPI templates for runtime amount patching", () => {
  const user = key(1);
  const quote = key(2);
  const intermediate = key(3);
  const sharedPool = key(4);
  const first = new TransactionInstruction({
    programId: METEORA,
    keys: [
      { pubkey: user, isSigner: true, isWritable: false },
      { pubkey: quote, isSigner: false, isWritable: true },
      { pubkey: intermediate, isSigner: false, isWritable: true },
      { pubkey: sharedPool, isSigner: false, isWritable: true },
    ],
    data: Buffer.from([10, 11]),
  });
  const second = new TransactionInstruction({
    programId: PUMP,
    keys: [
      { pubkey: user, isSigner: true, isWritable: false },
      { pubkey: intermediate, isSigner: false, isWritable: true },
      { pubkey: quote, isSigner: false, isWritable: true },
      { pubkey: sharedPool, isSigner: false, isWritable: false },
    ],
    data: Buffer.alloc(24, 9),
  });

  const instruction = buildExecutorInstruction({
    executorProgramId: key(9),
    user,
    quoteAccount: quote,
    intermediateAccount: intermediate,
    firstInstruction: first,
    secondInstruction: second,
    secondAmountOffset: 8,
    minimumProfit: 123n,
    validUntilSlot: 456n,
  });

  assert.equal(instruction.keys[0].pubkey.toBase58(), user.toBase58());
  assert.equal(instruction.keys[1].pubkey.toBase58(), quote.toBase58());
  assert.equal(instruction.keys[2].pubkey.toBase58(), intermediate.toBase58());
  assert.equal(instruction.keys[3].pubkey.toBase58(), METEORA.toBase58());
  assert.equal(instruction.keys[4].pubkey.toBase58(), PUMP.toBase58());
  assert.equal(instruction.keys[5].pubkey.toBase58(), sharedPool.toBase58());
  assert.equal(instruction.keys[5].isWritable, true);
  assert.equal(instruction.data.subarray(0, 4).toString(), "WABR");
  assert.equal(instruction.data[4], 1);
  assert.deepEqual([...instruction.data.subarray(5, 11)], [3, 4, 1, 2, 4, 4]);
  assert.equal(instruction.data.readUInt16LE(11), 8);
  assert.equal(instruction.data.readBigUInt64LE(13), 123n);
  assert.equal(instruction.data.readBigUInt64LE(21), 456n);
  assert.equal(instruction.data.readUInt16LE(29), 2);
  assert.equal(instruction.data.readUInt16LE(31), 24);
});


test("rejects anything except one Pump and one Meteora leg", () => {
  const user = key(1);
  const quote = key(2);
  const intermediate = key(3);
  const leg = new TransactionInstruction({ programId: PUMP, keys: [], data: Buffer.alloc(16) });

  assert.throws(
    () => buildExecutorInstruction({
      executorProgramId: key(9),
      user,
      quoteAccount: quote,
      intermediateAccount: intermediate,
      firstInstruction: leg,
      secondInstruction: leg,
      secondAmountOffset: 8,
      minimumProfit: 1n,
      validUntilSlot: 1n,
    }),
    /Pump AMM and Meteora DLMM/,
  );
});


test("accepts only the pinned Pump sell ABI template", () => {
  const data = Buffer.alloc(24);
  Buffer.from([51, 230, 133, 164, 1, 127, 131, 173]).copy(data);
  data.writeBigUInt64LE(1n, 8);
  data.writeBigUInt64LE(1n, 16);

  assert.equal(dynamicAmountOffset("Pump", data), 8);
  assert.throws(
    () => dynamicAmountOffset("Pump", Buffer.from(data).fill(0, 0, 8)),
    /discriminator/,
  );
  const wrongPlaceholder = Buffer.from(data);
  wrongPlaceholder.writeBigUInt64LE(2n, 8);
  assert.throws(() => dynamicAmountOffset("Pump", wrongPlaceholder), /placeholder/);
  assert.throws(() => dynamicAmountOffset("Pump", Buffer.concat([data, Buffer.from([0])])), /length/);
});


test("accepts only the pinned Meteora swap2 ABI template", () => {
  const data = Buffer.alloc(28);
  Buffer.from([65, 75, 63, 76, 235, 91, 91, 136]).copy(data);
  data.writeBigUInt64LE(1n, 8);
  data.writeBigUInt64LE(0n, 16);
  data.writeUInt32LE(0, 24);

  assert.equal(dynamicAmountOffset("Meteora", data), 8);
  assert.throws(
    () => dynamicAmountOffset("Meteora", Buffer.from(data).fill(0, 0, 8)),
    /discriminator/,
  );
  const wrongMinimum = Buffer.from(data);
  wrongMinimum.writeBigUInt64LE(1n, 16);
  assert.throws(() => dynamicAmountOffset("Meteora", wrongMinimum), /minimum output/);
  assert.throws(
    () => dynamicAmountOffset("Meteora", Buffer.concat([data, Buffer.from([0])])),
    /length/,
  );
});


test("rejects Token-2022 intermediate mints until extensions are reviewed", async () => {
  const connection = {
    async getAccountInfo() {
      return { owner: TOKEN_2022_PROGRAM_ID };
    },
  };

  await assert.rejects(
    tokenProgramForMint(connection, key(7)),
    /Token-2022 intermediate mints are unsupported/,
  );
});
