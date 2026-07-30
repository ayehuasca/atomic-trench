import {createRequire} from "node:module";
import {Connection, PublicKey, TransactionInstruction} from "@solana/web3.js";
import {getAssociatedTokenAddressSync, NATIVE_MINT, TOKEN_PROGRAM_ID} from "@solana/spl-token";
import BN from "bn.js";

const require = createRequire(import.meta.url);
const DLMM = require("@meteora-ag/dlmm");

const MEMO_PROGRAM_ID = new PublicKey("MemoSq4gqrNTor2Gdt6g6LrEk3Kvp5SV1vU8Nj4GfCq");
const METEORA_DLMM_PROGRAM_ID = new PublicKey("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo");

const conn = new Connection("https://api.mainnet-beta.solana.com", "confirmed");
const pool = new PublicKey("Cgnuirsk5dQ9Ka1Grnru7J8YW1sYncYUjiXvYxT7G4iZ");
const user = new PublicKey("2C76RkmQ8VE7NaKZVJ6qVyCnuBfqRtXbH7kQXsPvn9Yf");
const inMint = new PublicKey("5UUH9RTDiSpq6HKS6bp4NdU9PNJpXRXuiw6ShBTBhgH2");
const outMint = NATIVE_MINT;

const dlmm = await DLMM.create(conn, pool, { cluster: "mainnet-beta", skipSolWrappingOperation: true });
const swapForY = inMint.equals(dlmm.tokenX.publicKey);
const binArrays = await dlmm.getBinArrayForSwap(swapForY);
const amount = new BN(1000000);
const quote = dlmm.swapQuote(amount, swapForY, new BN(0), binArrays);

const userTokenIn = getAssociatedTokenAddressSync(inMint, user, false, dlmm.tokenX.owner);
const userTokenOut = getAssociatedTokenAddressSync(outMint, user, false, dlmm.tokenY.owner);
const binArrayMetas = quote.binArraysPubkey.map((pk) => ({ pubkey: pk, isSigner: false, isWritable: true }));
const { slices, accounts: transferHookAccounts } = dlmm.getPotentialToken2022IxDataAndAccounts(0);

console.log("slices object:", JSON.stringify(slices));
console.log("slices type:", typeof slices, Array.isArray(slices));

const swapIx = await dlmm.program.methods
  .swap2(amount, new BN(0), { slices })
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
    binArrayBitmapExtension: dlmm.binArrayBitmapExtension ? dlmm.binArrayBitmapExtension.publicKey : null,
    oracle: dlmm.lbPair.oracle,
    hostFeeIn: null,
    memoProgram: MEMO_PROGRAM_ID,
  })
  .remainingAccounts(transferHookAccounts)
  .remainingAccounts(binArrayMetas)
  .instruction();

const d = Buffer.from(swapIx.data);
console.log("instruction data length:", d.length);
console.log("instruction data hex:", d.toString("hex"));
console.log("bytes 0-8 (disc):", [...d.subarray(0, 8)]);
console.log("bytes 8-16 (amount):", d.readBigUInt64LE(8).toString());
console.log("bytes 16-24 (minOut):", d.readBigUInt64LE(16).toString());
console.log("bytes 24-32 (slices):", [...d.subarray(24, 32)]);
console.log("u32@24:", d.readUInt32LE(24));
console.log("u32@28:", d.readUInt32LE(28));
console.log("account count:", swapIx.keys.length);
