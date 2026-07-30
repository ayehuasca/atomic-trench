import {createRequire} from "node:module";
import {Connection, PublicKey, ComputeBudgetProgram} from "@solana/web3.js";
import {buildDirectRoundTrip} from "./lib/direct-roundtrip.mjs";

const conn = new Connection("https://api.mainnet-beta.solana.com", "confirmed");
const user = new PublicKey("xPnLubiTDu3kdLcDk1nTnCjhJhuM1TB4tB2hVnEd6AT");

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
  const existing = uniqueAccounts.get(ix.programId.toBase58());
  if (existing) {
    existing.signer ||= false;
    existing.writable ||= false;
  } else {
    uniqueAccounts.set(ix.programId.toBase58(), { signer: false, writable: false });
  }
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

// Load existing ALT to see which accounts are already covered
const altAddress = new PublicKey("AK5uWtuHpWShk71NsEMWQBQN8o7une1LdYqwVU1UKUEu");
const altResponse = await conn.getAddressLookupTable(altAddress);
const altAddresses = new Set();
if (altResponse.value) {
  altResponse.value.state.addresses.forEach(a => altAddresses.add(a.toBase58()));
}

// Accounts that need to go in new ALT (non-signers not in existing ALT)
const writableForAlt = [];
const readonlyForAlt = [];

for (const [addr, roles] of uniqueAccounts) {
  if (roles.signer) continue; // signers go inline
  if (altAddresses.has(addr)) continue; // already in existing ALT
  if (roles.writable) {
    writableForAlt.push(addr);
  } else {
    readonlyForAlt.push(addr);
  }
}

console.log("=== Accounts for new ALT ===");
console.log("Writable (max 128 in ALT):", writableForAlt.length);
writableForAlt.forEach((a, i) => console.log(`  [w${i}] ${a}`));
console.log("Readonly (max 128 in ALT):", readonlyForAlt.length);
readonlyForAlt.forEach((a, i) => console.log(`  [r${i}] ${a}`));
console.log("Total new ALT entries:", writableForAlt.length + readonlyForAlt.length);

// v0 inline limits: 4 signers + 3 writable + 7 readonly = 14 max inline
// We have 1 signer (fine), but need to check if writable/readonly overflow
const writableInline = Math.min(3, writableForAlt.length);
const readonlyInline = Math.min(7, readonlyForAlt.length);
console.log("\nInline after ALT:", `signers=1, writable=${writableInline}, readonly=${readonlyInline}`);
console.log("ALT writable entries needed:", writableForAlt.length - writableInline);
console.log("ALT readonly entries needed:", readonlyForAlt.length - readonlyInline);

// Output as JSON for the ALT creation script
console.log("\n=== JSON for ALT creation ===");
console.log(JSON.stringify({
  writable: writableForAlt,
  readonly: readonlyForAlt,
}));
