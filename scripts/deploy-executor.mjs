#!/usr/bin/env node
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import {
  Connection, Keypair, BpfLoader,
} from "@solana/web3.js";

const RPC = "https://solana-rpc.publicnode.com";
const KEYPAIR_PATH = ".keys/atomic-trench-deploy.json";
const SBF_PATH = "programs/atomic-executor/target/deploy/wallet_a_atomic_executor.so";
const PROG_ID_PATH = ".keys/program-id.txt";

async function main() {
  const deployData = JSON.parse(readFileSync(KEYPAIR_PATH, "utf8"));
  const deployKp = Keypair.fromSecretKey(new Uint8Array(deployData));
  const elf = readFileSync(SBF_PATH);

  // Generate separate program keypair (the legacy BpfLoader needs one)
  const programKp = Keypair.generate();

  const sha256 = createHash("sha256").update(elf).digest("hex");
  console.log(`Deployer: ${deployKp.publicKey.toBase58()}`);
  console.log(`Program ID: ${programKp.publicKey.toBase58()}`);
  console.log(`SBF size: ${elf.length} bytes`);
  console.log(`SHA-256: ${sha256}`);

  const connection = new Connection(RPC, "confirmed");
  const balance = await connection.getBalance(deployKp.publicKey);
  console.log(`Balance: ${(balance / 1e9).toFixed(6)} SOL`);

  // Check if already deployed
  const existing = await connection.getAccountInfo(programKp.publicKey);
  if (existing?.executable) {
    console.log("Already deployed!");
    writeFileSync(PROG_ID_PATH, programKp.publicKey.toBase58(), "utf8");
    return;
  }

  if (balance < 0.25e9) {
    console.error(`Need >= 0.25 SOL, have ${(balance / 1e9).toFixed(6)}`);
    process.exit(1);
  }

  console.log("Deploying via legacy BpfLoader (immutable, no upgrade)...");
  // BpfLoader.load handles everything: create account, write chunks, finalize
  const sig = await BpfLoader.load(connection, deployKp, programKp, elf, "confirmed");
  console.log(`Deploy sig: ${sig}`);

  const deployed = await connection.getAccountInfo(programKp.publicKey);
  if (!deployed?.executable) {
    console.error("Deploy verification failed");
    process.exit(1);
  }

  console.log(`\n✅ SUCCESS!`);
  console.log(`Program ID: ${programKp.publicKey.toBase58()}`);
  writeFileSync(PROG_ID_PATH, programKp.publicKey.toBase58(), "utf8");
  console.log(`Saved to ${PROG_ID_PATH}`);
}

main().catch(err => { console.error(err); process.exit(1); });
