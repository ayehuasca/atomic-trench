import {createRequire} from "node:module";
import {Connection, PublicKey} from "@solana/web3.js";
import {buildPumpBuyQuoteInputInstruction} from "./lib/direct-venues.mjs";

const require = createRequire(import.meta.url);

const conn = new Connection("https://api.mainnet-beta.solana.com", "confirmed");
const user = new PublicKey("xPnLubiTDu3kdLcDk1nTnCjhJhuM1TB4tB2hVnEd6AT");
const mint = new PublicKey("5UUH9RTDiSpq6HKS6bp4NdU9PNJpXRXuiw6ShBTBhgH2");
const pool = new PublicKey("4w2cysotX6czaUGmmWg13hDpY4QEMG2CzeKYEQyK9Ama");

const {getAssociatedTokenAddressSync, NATIVE_MINT, TOKEN_PROGRAM_ID} = await import("@solana/spl-token");
const quoteAccount = getAssociatedTokenAddressSync(NATIVE_MINT, user, false, TOKEN_PROGRAM_ID);
const intermediateAccount = getAssociatedTokenAddressSync(mint, user, false, TOKEN_PROGRAM_ID);

try {
  const result = await buildPumpBuyQuoteInputInstruction({
    connection: conn,
    pool,
    user,
    baseAccount: intermediateAccount,
    quoteAccount,
    quoteAmount: 10000000n,
    slippageBps: 100,
  });
  const d = Buffer.from(result.instruction.data);
  console.log("Pump buy instruction data length:", d.length);
  console.log("hex:", d.toString("hex"));
  console.log("discriminator:", [...d.subarray(0, 8)]);
  console.log("expected buy disc:", [102, 6, 61, 18, 1, 218, 235, 234]);
  console.log("match:", d.subarray(0, 8).equals(Buffer.from([102, 6, 61, 18, 1, 218, 235, 234])));
  console.log("amount@8:", d.readBigUInt64LE(8).toString());
  console.log("maxQuote@16:", d.readBigUInt64LE(16).toString());
} catch(e) { console.error("ERROR:", e.message); }
