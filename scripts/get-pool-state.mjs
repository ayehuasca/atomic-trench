#!/usr/bin/env node
/**
 * get-pool-state.mjs — Fetch Pump AMM pool state and return reserves + price.
 *
 * Input (stdin JSON): { rpcUrl, pumpPool }
 * Output (stdout JSON): { baseReserve, quoteReserve, price, virtualBaseReserve, virtualQuoteReserve }
 */

import {createRequire} from "node:module";
import {Connection, PublicKey} from "@solana/web3.js";

const require = createRequire(import.meta.url);
const {OnlinePumpAmmSdk} = require("@pump-fun/pump-swap-sdk");

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));

const {rpcUrl, pumpPool} = input;
const conn = new Connection(rpcUrl, "confirmed");
const poolPk = new PublicKey(pumpPool);

const online = new OnlinePumpAmmSdk(conn);
const state = await online.swapSolanaState(poolPk, PublicKey.default, PublicKey.default, PublicKey.default);

const baseReserve = Number(state.poolBaseAmount);
const quoteReserve = Number(state.poolQuoteAmount);
const virtualBase = Number(state.pool?.virtualBaseReserves || 0n);
const virtualQuote = Number(state.pool?.virtualQuoteReserves || 0n);

// Price = quote per base (SOL per token)
// Use virtual reserves for bonding curve, actual reserves for PumpSwap
const effectiveBase = virtualBase > 0 ? virtualBase : baseReserve;
const effectiveQuote = virtualQuote > 0 ? virtualQuote : quoteReserve;
const price = effectiveBase > 0 ? effectiveQuote / effectiveBase : 0;

process.stdout.write(JSON.stringify({
  baseReserve,
  quoteReserve,
  virtualBaseReserve: virtualBase,
  virtualQuoteReserve: virtualQuote,
  price,
}));
