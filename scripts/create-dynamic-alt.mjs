import {createRequire} from "node:module";
import {
  Connection, Keypair, PublicKey,
  TransactionMessage, VersionedTransaction,
  AddressLookupTableProgram,
} from "@solana/web3.js";

const require = createRequire(import.meta.url);

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));

const conn = new Connection(input.rpcUrl, "confirmed");
const payer = Keypair.fromSecretKey(new Uint8Array(input.payerKeypair));

const writableKeys = input.writableAccounts.map(a => new PublicKey(a));
const readonlyKeys = input.readonlyAccounts.map(a => new PublicKey(a));

// TX1: Create ALT
const slot = await conn.getSlot("confirmed");
const [createAltIx, altPubkey] = AddressLookupTableProgram.createLookupTable({
  authority: payer.publicKey,
  payer: payer.publicKey,
  recentSlot: slot,
});

// TX2: Extend with writable
const extendWritableIx = AddressLookupTableProgram.extendLookupTable({
  lookupTable: altPubkey,
  authority: payer.publicKey,
  payer: payer.publicKey,
  addresses: writableKeys,
});

// TX3: Extend with readonly
const extendReadonlyIx = AddressLookupTableProgram.extendLookupTable({
  lookupTable: altPubkey,
  authority: payer.publicKey,
  payer: payer.publicKey,
  addresses: readonlyKeys,
});

// Send all 3 txs sequentially
for (const [name, ix] of [["create", createAltIx], ["writable", extendWritableIx], ["readonly", extendReadonlyIx]]) {
  const blockhash = await conn.getLatestBlockhash("confirmed");
  const msg = new TransactionMessage({
    payerKey: payer.publicKey,
    recentBlockhash: blockhash.blockhash,
    instructions: [ix],
  }).compileToV0Message();
  const tx = new VersionedTransaction(msg);
  tx.sign([payer]);
  const sig = await conn.sendTransaction(tx, {skipPreflight: false});
  await conn.confirmTransaction(sig, "confirmed");
}

process.stdout.write(JSON.stringify({altAddress: altPubkey.toBase58()}));
