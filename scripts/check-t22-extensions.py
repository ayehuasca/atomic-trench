import base64, json, struct
from backrunner.providers import SolanaRpc

rpc = SolanaRpc("https://mainnet.helius-rpc.com/?api-key=a6b53727-2ed9-4b83-84fd-d05bc430fc90", "confirmed")

mints = [
    "Cm6fNnMk7NfzStP9CZpsQA2v3jjzbcYGAxdJySmHpump",
    "GB68eELgf864nrQHjNvrXHF6Xywpp1kYsb7fsGBupump",
    "9CGg2mfaL37Nrw3pkdumb1eVtRmPSLp9A3t4fDLFpump",
]

KNOWN = {
    0: "TransferFeeConfig",
    2: "NonTransferable",
    3: "DefaultAccountState",
    4: "ImmutableOwner",
    5: "Memo",
    6: "PermanentDelegate",
    7: "ConfidentialTransferMint",
    8: "InterestBearingConfig",
    9: "CpiGuard",
    11: "TransferHook",
    12: "TransferHookResult",
    13: "TokenMetadata",
    16: "MetadataPointer",
}

DANGEROUS = {0, 2, 3, 6, 7, 9, 11}

for mint in mints:
    info = rpc.account_info(mint)
    raw = base64.b64decode(info["data"][0] if isinstance(info["data"], list) else info["data"])
    print(f"\n=== {mint[:24]}... ({len(raw)} bytes) ===")

    found = set()
    for offset in range(82, len(raw) - 4):
        t = struct.unpack_from("<H", raw, offset)[0]
        if t in KNOWN:
            length = struct.unpack_from("<H", raw, offset + 2)[0]
            if length > 0 and offset + 4 + length <= len(raw):
                found.add(t)
                print(f"  {KNOWN[t]} (type={t}) len={length} at offset={offset}")

    if not found:
        print("  No known extensions found")
        # Try the proper TLV parse from offset 82
        offset = 82
        while offset + 4 <= len(raw):
            t = struct.unpack_from("<H", raw, offset)[0]
            length = struct.unpack_from("<H", raw, offset + 2)[0]
            if t == 0 and length == 0:
                offset += 4
                continue
            if offset + 4 + length > len(raw):
                break
            found.add(t)
            print(f"  {KNOWN.get(t, f'Unknown({t})')} (type={t}) len={length}")
            offset += 4 + length

    dangerous = found & DANGEROUS
    if dangerous:
        names = [KNOWN[d] for d in dangerous]
        print(f"  ⚠️  DANGEROUS: {names}")
    else:
        print(f"  ✅ Safe: no dangerous extensions")