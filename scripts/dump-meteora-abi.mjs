import {Connection, PublicKey} from "@solana/web3.js";
import {buildMeteoraDynamicExactInputInstruction} from "./lib/direct-venues.mjs";

const conn = new Connection("https://api.mainnet-beta.solana.com", "confirmed");
try {
  const result = await buildMeteoraDynamicExactInputInstruction({
    connection: conn,
    pool: new PublicKey("Cgnuirsk5dQ9Ka1Grnru7J8YW1sYncYUjiXvYxT7G4iZ"),
    user: new PublicKey("2C76RkmQ8VE7NaKZVJ6qVyCnuBfqRtXbH7kQXsPvn9Yf"),
    inputMint: new PublicKey("5UUH9RTDiSpq6HKS6bp4NdU9PNJpXRXuiw6ShBTBhgH2"),
    outputMint: new PublicKey("So11111111111111111111111111111111111111112"),
    estimatedInputAmount: 1000000n,
  });
  const d = Buffer.from(result.instruction.data);
  console.log("length:", d.length);
  console.log("hex:", d.toString("hex"));
  console.log("discriminator:", [...d.subarray(0, 8)]);
  console.log("amount@8:", d.readBigUInt64LE(8).toString());
  console.log("minOut@16:", d.readBigUInt64LE(16).toString());
  console.log("bytes 24-32:", [...d.subarray(24, 32)]);
  console.log("u32@24:", d.readUInt32LE(24));
  console.log("u32@28:", d.readUInt32LE(28));
  console.log("accountCount:", result.instruction.keys.length);
  console.log("accounts:", result.instruction.keys.map(k => `${k.pubkey.toBase58().slice(0,8)}... s=${k.isSigner} w=${k.isWritable}`));
} catch(e) { console.error("ERROR:", e.message); }
