import {Connection, Keypair, VersionedTransaction} from "@solana/web3.js";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));

const conn = new Connection(input.rpcUrl, "confirmed");
const signer = Keypair.fromSecretKey(new Uint8Array(input.signerKeypair));
const txBytes = Buffer.from(input.transactionBase64, "base64");

const tx = VersionedTransaction.deserialize(txBytes);
tx.sign([signer]);

const serialized = tx.serialize();
if (serialized.length > 1232) {
  process.stdout.write(JSON.stringify({
    submitted: false,
    error: `transaction too large: ${serialized.length} bytes (max 1232)`,
  }));
  process.exit(0);
}

try {
  const sig = await conn.sendTransaction(tx, {
    skipPreflight: false,
    maxRetries: 0,
    preflightCommitment: "confirmed",
  });
  process.stdout.write(JSON.stringify({
    submitted: true,
    signature: sig,
  }));
} catch (err) {
  process.stdout.write(JSON.stringify({
    submitted: false,
    error: err.message || String(err),
  }));
}
