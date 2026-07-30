import {createRequire} from "node:module";
import {
  Connection, Keypair, PublicKey,
  TransactionMessage, VersionedTransaction,
  AddressLookupTableProgram,
} from "@solana/web3.js";
import {readFileSync} from "node:fs";
import {homedir} from "node:os";

const require = createRequire(import.meta.url);

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));

const conn = new Connection(input.rpcUrl, "confirmed");
const keyPath = `${homedir()}/.config/atomic-trench/hot-wallet.json`;
const keyData = JSON.parse(readFileSync(keyPath, "utf8"));
const payer = Keypair.fromSecretKey(new Uint8Array(keyData));

const writableKeys = input.writable.map(a => new PublicKey(a));
const readonlyKeys = input.readonly.map(a => new PublicKey(a));

console.error(`Creating universal ALT: ${writableKeys.length} writable + ${readonlyKeys.length} readonly`);

// TX1: Create ALT — wait for finalized confirmation
const slot = await conn.getSlot("confirmed");
const [createAltIx, altPubkey] = AddressLookupTableProgram.createLookupTable({
  authority: payer.publicKey,
  payer: payer.publicKey,
  recentSlot: slot,
});
console.error(`ALT address: ${altPubkey.toBase58()}`);

{
  const blockhash = await conn.getLatestBlockhash("confirmed");
  const msg = new TransactionMessage({
    payerKey: payer.publicKey,
    recentBlockhash: blockhash.blockhash,
    instructions: [createAltIx],
  }).compileToV0Message();
  const tx = new VersionedTransaction(msg);
  tx.sign([payer]);
  const sig = await conn.sendTransaction(tx, {skipPreflight: false});
  await conn.confirmTransaction(sig, "finalized");
  console.error("ALT created (finalized)");
}

// Split writable extends into chunks of 30 (tx size limit)
for (let i = 0; i < writableKeys.length; i += 30) {
  const chunk = writableKeys.slice(i, i + 30);
  const extendIx = AddressLookupTableProgram.extendLookupTable({
    lookupTable: altPubkey,
    authority: payer.publicKey,
    payer: payer.publicKey,
    addresses: chunk,
  });
  const blockhash = await conn.getLatestBlockhash("confirmed");
  const msg = new TransactionMessage({
    payerKey: payer.publicKey,
    recentBlockhash: blockhash.blockhash,
    instructions: [extendIx],
  }).compileToV0Message();
  const tx = new VersionedTransaction(msg);
  tx.sign([payer]);
  const sig = await conn.sendTransaction(tx, {skipPreflight: false});
  await conn.confirmTransaction(sig, "confirmed");
  console.error(`Extended writable ${i}-${i + chunk.length}`);
}

// Extend with readonly
const extendReadonlyIx = AddressLookupTableProgram.extendLookupTable({
  lookupTable: altPubkey,
  authority: payer.publicKey,
  payer: payer.publicKey,
  addresses: readonlyKeys,
});
const blockhash = await conn.getLatestBlockhash("confirmed");
const msg = new TransactionMessage({
  payerKey: payer.publicKey,
  recentBlockhash: blockhash.blockhash,
  instructions: [extendReadonlyIx],
}).compileToV0Message();
const tx = new VersionedTransaction(msg);
tx.sign([payer]);
const sig = await conn.sendTransaction(tx, {skipPreflight: false});
await conn.confirmTransaction(sig, "confirmed");
console.error(`Extended readonly ${readonlyKeys.length}`);

// Verify
const altInfo = await conn.getAddressLookupTable(altPubkey);
if (altInfo.value) {
  console.error(`ALT active: ${altInfo.value.state.addresses.length} entries`);
}

const balance = await conn.getBalance(payer.publicKey);
console.error(`Remaining balance: ${balance / 1e9} SOL`);

process.stdout.write(JSON.stringify({altAddress: altPubkey.toBase58()}));
