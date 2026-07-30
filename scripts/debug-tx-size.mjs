import {createRequire} from "node:module";
import {Connection, PublicKey, ComputeBudgetProgram, TransactionMessage, VersionedTransaction} from "@solana/web3.js";
import {buildDirectRoundTrip} from "./lib/direct-roundtrip.mjs";

const conn = new Connection("https://api.mainnet-beta.solana.com", "confirmed");
const user = new PublicKey("2C76RkmQ8VE7NaKZVJ6qVyCnuBfqRtXbH7kQXsPvn9Yf");

const route = await buildDirectRoundTrip({
  connection: conn,
  executorProgramId: "EBBCNegFwVq4Vas6aEnhVKmVExxZ1kwFhvSNqNxpuWzs",
  user,
  pumpPool: "4w2cysotX6czaUGmmWg13hDpY4QEMG2CzeKYEQyK9Ama",
  meteoraPool: "Cgnuirsk5dQ9Ka1Grnru7J8YW1sYncYUjiXvYxT7G4iZ",
  intermediateMint: "5UUH9RTDiSpq6HKS6bp4NdU9PNJpXRXuiw6ShBTBhgH2",
  inputAmount: 100000n,
  direction: "pump_to_meteora",
  minimumProfit: 1n,
  validUntilSlot: 436140000n,
  slippageBps: 100,
});

const instructions = [
  ComputeBudgetProgram.setComputeUnitLimit({ units: 600000 }),
  ...route.instructions,
];

// Build unique account list with merged roles
const uniqueAccounts = new Map();
for (const ix of instructions) {
  uniqueAccounts.set(ix.programId.toBase58(), { signer: false, writable: false, program: true });
  for (const key of ix.keys) {
    const existing = uniqueAccounts.get(key.pubkey.toBase58());
    if (existing) {
      existing.signer ||= key.isSigner;
      existing.writable ||= key.isWritable;
    } else {
      uniqueAccounts.set(key.pubkey.toBase58(), { signer: key.isSigner, writable: key.isWritable });
    }
  }
}

// Load ALT
const altAddress = new PublicKey("AK5uWtuHpWShk71NsEMWQBQN8o7une1LdYqwVU1UKUEu");
const altResponse = await conn.getAddressLookupTable(altAddress);
const alt = altResponse.value;
const altAddresses = new Set(alt.state.addresses.map(a => a.toBase58()));
console.log("ALT addresses:", [...altAddresses]);

// Categorize accounts
const signers = [];
const writableNonSigners = [];
const readonlyNonSigners = [];
const inAltWritable = [];
const inAltReadonly = [];

for (const [addr, roles] of uniqueAccounts) {
  const inAlt = altAddresses.has(addr);
  if (roles.signer) {
    signers.push(addr);
  } else if (roles.writable) {
    if (inAlt) inAltWritable.push(addr);
    else writableNonSigners.push(addr);
  } else {
    if (inAlt) inAltReadonly.push(addr);
    else readonlyNonSigners.push(addr);
  }
}

console.log("\n=== Account breakdown ===");
console.log("Signers (inline):", signers.length);
console.log("Writable non-signers (inline):", writableNonSigners.length, writableNonSigners);
console.log("Readonly non-signers (inline):", readonlyNonSigners.length, readonlyNonSigners);
console.log("In ALT (writable):", inAltWritable.length, inAltWritable);
console.log("In ALT (readonly):", inAltReadonly.length, inAltReadonly);
console.log("\nInline total:", signers.length + writableNonSigners.length + readonlyNonSigners.length);
console.log("v0 max inline:", 4 + 3 + 7 + 21); // header+signers+writable+readonly

// We need to fit: signers (max 4 inline) + writable (max 3 inline + rest in ALT writable) + readonly (max 7 inline + rest in ALT readonly)
// v0 message format: up to 4 signed, 3 unsigned writable, 7 unsigned readonly inline. Rest must be in ALT.
console.log("\nWritable overflow (max 3 inline):", Math.max(0, writableNonSigners.length - 3));
console.log("Readonly overflow (max 7 inline):", Math.max(0, readonlyNonSigners.length - 7));
console.log("\nNeed writable in ALT:", Math.max(0, writableNonSigners.length - 3));
console.log("Need readonly in ALT:", Math.max(0, readonlyNonSigners.length - 7));
