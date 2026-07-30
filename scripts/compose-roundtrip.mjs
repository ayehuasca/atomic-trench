import {
  AddressLookupTableAccount,
  ComputeBudgetProgram,
  PublicKey,
  TransactionInstruction,
  TransactionMessage,
  VersionedTransaction,
} from "@solana/web3.js";
import bs58 from "bs58";

function toInstruction(ix) {
  return new TransactionInstruction({
    programId: new PublicKey(ix.programId),
    keys: ix.accounts.map((account) => ({
      pubkey: new PublicKey(account.pubkey),
      isSigner: account.isSigner,
      isWritable: account.isWritable,
    })),
    data: Buffer.from(ix.data, "base64"),
  });
}

function legInstructions(build) {
  return [
    ...build.setupInstructions.map(toInstruction),
    toInstruction(build.swapInstruction),
    ...(build.cleanupInstruction ? [toInstruction(build.cleanupInstruction)] : []),
    ...build.otherInstructions.map(toInstruction),
  ];
}

function lookupTables(...builds) {
  const merged = new Map();
  for (const build of builds) {
    for (const [key, addresses] of Object.entries(build.addressesByLookupTableAddress ?? {})) {
      const previous = merged.get(key);
      if (previous && JSON.stringify(previous) !== JSON.stringify(addresses)) {
        throw new Error(`conflicting lookup table contents for ${key}`);
      }
      merged.set(key, addresses);
    }
  }
  return [...merged.entries()].map(
    ([key, addresses]) =>
      new AddressLookupTableAccount({
        key: new PublicKey(key),
        state: {
          deactivationSlot: 18446744073709551615n,
          lastExtendedSlot: 0,
          lastExtendedSlotStartIndex: 0,
          authority: undefined,
          addresses: addresses.map((address) => new PublicKey(address)),
        },
      }),
  );
}

let input = "";
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
const buy = request.buy;
const sell = request.sell;
const payer = new PublicKey(request.taker);
const blockhash = bs58.encode(Buffer.from(sell.blockhashWithMetadata.blockhash));
const tables = lookupTables(buy, sell);
const instructions = [
  ComputeBudgetProgram.setComputeUnitLimit({ units: 1_400_000 }),
  ...legInstructions(buy),
  ...legInstructions(sell),
];
const message = new TransactionMessage({
  payerKey: payer,
  recentBlockhash: blockhash,
  instructions,
}).compileToV0Message(tables);
const transaction = new VersionedTransaction(message);
const serialized = transaction.serialize();
const lookupAccountCount = message.addressTableLookups.reduce(
  (sum, lookup) => sum + lookup.readonlyIndexes.length + lookup.writableIndexes.length,
  0,
);
process.stdout.write(
  JSON.stringify({
    transactionBase64: Buffer.from(serialized).toString("base64"),
    version: 0,
    instructionCount: instructions.length,
    serializedSize: serialized.length,
    accountCount: message.staticAccountKeys.length + lookupAccountCount,
    signatureCount: transaction.signatures.length,
  }),
);
