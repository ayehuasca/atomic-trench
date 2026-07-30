import {createRequire} from "node:module";
import {
  Connection, Keypair, PublicKey, SystemProgram,
  TransactionMessage, VersionedTransaction,
  AddressLookupTableProgram,
  sendAndConfirmTransaction,
} from "@solana/web3.js";
import {readFileSync} from "node:fs";
import { homedir } from "node:os";

const require = createRequire(import.meta.url);

const conn = new Connection("https://api.mainnet-beta.solana.com", "confirmed");

// Load hot wallet keypair
const keyPath = `${homedir()}/.config/atomic-trench/hot-wallet.json`;
const keyData = JSON.parse(readFileSync(keyPath, "utf8"));
const payer = Keypair.fromSecretKey(new Uint8Array(keyData));
console.log("Payer:", payer.publicKey.toBase58());

// Load existing ALT
const existingAlt = new PublicKey("AK5uWtuHpWShk71NsEMWQBQN8o7une1LdYqwVU1UKUEu");

// Account lists for new ALT
const writableAccounts = [
  "3yWN19uJ74rQrmCbcvGyHJ5mqTYEgPZU4VkMj7MsXDFF",
  "G1VzjofXgWdkP8ZBdnCehcdad4ZxVaFyXC4eCMnvUcFx",
  "4w2cysotX6czaUGmmWg13hDpY4QEMG2CzeKYEQyK9Ama",
  "8SmW82qNZ7BpdBp5PZk7znqt3VswFzX3nkRgHBBE1x4e",
  "657gpdF5TtxfXaW88MwuideK2pWhwRyoiVNnLDzS5q2K",
  "Bvtgim23rfocUzxVX9j9QFxTbBnH8JZxnaGLCEkXvjKS",
  "DGGpxm8H8Bj5Dc1wZaLzPPVuiXsJTHoxvP5iR8tje1BK",
  "AFwd2jFtJHzxYTFoPKpjvpyKQ1yiTeK6Ct84qXpwfsQa",
  "AktftA98kSWAxn6kVSoqBXBELUArjKu2H9WmKB48ULFY",
  "Cgnuirsk5dQ9Ka1Grnru7J8YW1sYncYUjiXvYxT7G4iZ",
  "87Xb8p257SgaVpssnTZJBgXKJFeXMiDAALEGR4U9vV1f",
  "5z1c6Je1d1cR2y51qUimyUWYHUjGeb91ZiABVRbyPk6y",
  "Psui6AUiwCfoydurtqTBJ9ps3djQmSLZyuuGPQ3a8Zj",
  "DgXDDXvS6xowgKY42WoFAqSJRZCww1rzpsauEq5hcPxF",
  "GvuDaHqraV8x7QYVmwM6HXSnTvjNAjuZA2LAesU2dF5j",
];

const readonlyAccounts = [
  "ComputeBudget111111111111111111111111111111",
  "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
  "5UUH9RTDiSpq6HKS6bp4NdU9PNJpXRXuiw6ShBTBhgH2",
  "EBBCNegFwVq4Vas6aEnhVKmVExxZ1kwFhvSNqNxpuWzs",
  "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw",
  "9rPYyANsfQZw3DnDmKE3YCQF5E8oD89UXoHn9JFEhJUz",
  "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR",
  "CUdmpcWRWqE6Q3wkGeFXrQ4WyukBzhiuVeDTNV5sMknJ",
  "C2aFPdENg4A2HQsmrd5rTw5TaYBX5Ku887cWjbFKtZpw",
  "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx",
  "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ",
  "7ksJEP8TRm1YxCSKrEfu9BzkDheKZJgS1yctmBS4mWY8",
  "GXPFM2caqTtQYC2cJ5yJRi9VDkpsYZXzYdwYpGnLmtDL",
  "MemoSq4gqrNTor2Gdt6g6LrEk3Kvp5SV1vU8Nj4GfCq",
  "D1ZN9Wj1fRSUQfCjhvnu1hqDMT7hzjzBBpi12nVniYD6",
];

const writableKeys = writableAccounts.map(a => new PublicKey(a));
const readonlyKeys = readonlyAccounts.map(a => new PublicKey(a));

// Step 1: Create ALT
console.log("\nCreating ALT...");
const slot = await conn.getSlot("confirmed");
console.log("Current slot:", slot);

const [createAltIx, altPubkey] = AddressLookupTableProgram.createLookupTable({
  authority: payer.publicKey,
  payer: payer.publicKey,
  recentSlot: slot,
});
console.log("New ALT address:", altPubkey.toBase58());

// Step 2: Extend ALT with writable accounts
console.log("\nExtending ALT with writable accounts...");
const extendWritableIx = AddressLookupTableProgram.extendLookupTable({
  lookupTable: altPubkey,
  authority: payer.publicKey,
  payer: payer.publicKey,
  addresses: writableKeys,
});

// Step 3: Extend ALT with readonly accounts
console.log("Extending ALT with readonly accounts...");
const extendReadonlyIx = AddressLookupTableProgram.extendLookupTable({
  lookupTable: altPubkey,
  authority: payer.publicKey,
  payer: payer.publicKey,
  addresses: readonlyKeys,
});

// Build and send the transactions separately (too many accounts for one tx)
// TX1: Create ALT
console.log("\n--- TX1: Creating ALT ---");
const blockhash1 = await conn.getLatestBlockhash("confirmed");
const msg1 = new TransactionMessage({
  payerKey: payer.publicKey,
  recentBlockhash: blockhash1.blockhash,
  instructions: [createAltIx],
}).compileToV0Message();
const tx1 = new VersionedTransaction(msg1);
tx1.sign([payer]);
const sig1 = await conn.sendTransaction(tx1, { skipPreflight: false });
console.log("TX1 signature:", sig1);
await conn.confirmTransaction(sig1, "confirmed");
console.log("TX1 confirmed!");

// TX2: Extend with writable accounts
console.log("\n--- TX2: Extending with writable accounts ---");
const blockhash2 = await conn.getLatestBlockhash("confirmed");
const msg2 = new TransactionMessage({
  payerKey: payer.publicKey,
  recentBlockhash: blockhash2.blockhash,
  instructions: [extendWritableIx],
}).compileToV0Message();
const tx2 = new VersionedTransaction(msg2);
tx2.sign([payer]);
const sig2 = await conn.sendTransaction(tx2, { skipPreflight: false });
console.log("TX2 signature:", sig2);
await conn.confirmTransaction(sig2, "confirmed");
console.log("TX2 confirmed!");

// TX3: Extend with readonly accounts
console.log("\n--- TX3: Extending with readonly accounts ---");
const blockhash3 = await conn.getLatestBlockhash("confirmed");
const msg3 = new TransactionMessage({
  payerKey: payer.publicKey,
  recentBlockhash: blockhash3.blockhash,
  instructions: [extendReadonlyIx],
}).compileToV0Message();
const tx3 = new VersionedTransaction(msg3);
tx3.sign([payer]);
const sig3 = await conn.sendTransaction(tx3, { skipPreflight: false });
console.log("TX3 signature:", sig3);
await conn.confirmTransaction(sig3, "confirmed");
console.log("TX3 confirmed!");

// Verify ALT
const altInfo = await conn.getAddressLookupTable(altPubkey);
if (altInfo.value) {
  console.log("\nALT created successfully!");
  console.log("Address:", altPubkey.toBase58());
  console.log("Entries:", altInfo.value.state.addresses.length);
  console.log("Active:", altInfo.value.isActive());
}

// Check remaining balance
const balance = await conn.getBalance(payer.publicKey);
console.log(`\nRemaining balance: ${balance / 1e9} SOL`);
console.log(`Spent: ${(0.094730612 - balance / 1e9).toFixed(6)} SOL`);
