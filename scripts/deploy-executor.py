#!/usr/bin/env python3
"""Deploy Atomic Trench executor to Solana mainnet via BpfUpgradeableLoader."""

import base64
import hashlib
import json
import struct
import time
from pathlib import Path

import requests
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import (
    CreateAccountParams,
    create_account,
)
from solders.transaction import Transaction

RPC = "https://solana-rpc.publicnode.com"
BPF_LOADER = Pubkey.from_string("BPFLoaderUpgradeab1e11111111111111111111111")
SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")

KEYPAIR_PATH = Path(".keys/atomic-trench-deploy.json")
SBF_PATH = Path("programs/atomic-executor/target/deploy/wallet_a_atomic_executor.so")
PROG_ID_PATH = Path(".keys/program-id.txt")


def rpc(method: str, params: list) -> dict:
    resp = requests.post(RPC, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params
    }, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"RPC error: {data['error']}")
    return data["result"]


def get_blockhash() -> tuple[str, int]:
    result = rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
    return result["value"]["blockhash"], result["value"]["lastValidBlockHeight"]


def confirm_tx(sig: str, max_wait: int = 120) -> bool:
    for _ in range(max_wait):
        status = rpc("getSignatureStatuses", [[sig]])
        val = status.get("value", [None])[0]
        if val and val.get("confirmationStatus") in ("confirmed", "finalized"):
            if val.get("err"):
                raise RuntimeError(f"tx failed: {val['err']}")
            return True
        time.sleep(1)
    return False


def send_and_confirm(ixs: list, payer: Keypair, *additional_signers: Keypair) -> str:
    max_attempts = 3
    for attempt in range(max_attempts):
        bh, _ = get_blockhash()
        tx = Transaction.new_signed_with_payer(
            ixs, payer.pubkey(), [payer, *additional_signers], Hash.from_string(bh)
        )
        sig = str(tx.signatures[0])
        
        try:
            rpc("sendTransaction", [
                base64.b64encode(bytes(tx)).decode(),
                {"encoding": "base64", "maxRetries": 3, "skipPreflight": True}
            ])
            
            if not confirm_tx(sig):
                if attempt < max_attempts - 1:
                    print(f"  retry {attempt + 1}...")
                    continue
                raise RuntimeError(f"tx not confirmed: {sig}")
            print(f"  OK: {sig[:16]}...")
            return sig
        except Exception as e:
            if attempt < max_attempts - 1:
                print(f"  retry {attempt + 1}: {e}")
                time.sleep(1)
            else:
                raise


def main():
    with open(KEYPAIR_PATH) as f:
        deploy_kp = Keypair.from_bytes(bytes(json.load(f)))
    
    program_id = deploy_kp.pubkey()
    elf = SBF_PATH.read_bytes()
    elf_size = len(elf)
    
    sha256 = hashlib.sha256(elf).hexdigest()
    print(f"Program ID: {program_id}")
    print(f"SBF size: {elf_size} bytes")
    print(f"SHA-256: {sha256}")

    bal = rpc("getBalance", [str(program_id), {"commitment": "confirmed"}])["value"]
    print(f"Balance: {bal / 1e9:.6f} SOL")

    acct = rpc("getAccountInfo", [str(program_id), {"commitment": "confirmed"}]).get("value")
    if acct and acct.get("executable"):
        print("Already deployed!")
        PROG_ID_PATH.write_text(str(program_id))
        return

    if bal < 0.25e9:
        print(f"Need >= 0.25 SOL, have {bal / 1e9:.6f}")
        return

    # === BPF Upgradeable Loader Deploy ===
    # Step 1: Create buffer account
    buffer_kp = Keypair()
    buffer_space = elf_size + 8
    min_rent = rpc("getMinimumBalanceForRentExemption", [buffer_space])
    
    create_ix = create_account(CreateAccountParams(
        from_pubkey=program_id,
        to_pubkey=buffer_kp.pubkey(),
        lamports=min_rent,
        space=buffer_space,
        owner=BPF_LOADER,
    ))
    print(f"Creating buffer ({min_rent / 1e9:.6f} SOL)...")
    send_and_confirm([create_ix], deploy_kp, buffer_kp)
    
    # Initialize buffer (BpfUpgradeableLoader instruction 0)
    init_ix = Instruction(
        program_id=BPF_LOADER,
        accounts=[
            AccountMeta(buffer_kp.pubkey(), False, True),
            AccountMeta(program_id, True, False),
        ],
        data=struct.pack("<I", 0),  # InitializeBuffer (u32 LE)
    )
    send_and_confirm([init_ix], deploy_kp)
    
    # Step 2: Write chunks (BpfUpgradeableLoader instruction 1)
    CHUNK_SIZE = 512
    chunks = [elf[i:i+CHUNK_SIZE] for i in range(0, elf_size, CHUNK_SIZE)]
    print(f"Writing {len(chunks)} chunks...")
    
    write_ixs = []
    for i, chunk in enumerate(chunks):
        offset = i * CHUNK_SIZE
        write_data = bytes([1])  # Write (u8 tag 1)
        write_data += struct.pack("<I", offset)
        write_data += struct.pack("<I", len(chunk))
        write_data += chunk
        
        write_ixs.append(Instruction(
            program_id=BPF_LOADER,
            accounts=[
                AccountMeta(buffer_kp.pubkey(), False, True),
                AccountMeta(program_id, True, False),
            ],
            data=write_data,
        ))
    
    # Batches of 1 (packet limit: 2 writes = 1258 > 1232 bytes)
    for i in range(0, len(write_ixs), 1):
        batch = write_ixs[i:i+1]
        send_and_confirm(batch, deploy_kp)
        pct = min(100, (i + len(batch)) * 100 // len(write_ixs))
        print(f"  {i + len(batch)}/{len(write_ixs)} ({pct}%)", flush=True)
    
    # Step 3: Deploy from buffer
    # Derive program data PDA
    prog_data = Pubkey.find_program_address([bytes(program_id)], BPF_LOADER)[0]
    print(f"Program data PDA: {prog_data}")
    
    deploy_data = bytes([2])  # DeployWithMaxDataLen (u8 tag 2)
    deploy_data += struct.pack("<Q", elf_size)
    
    deploy_ix = Instruction(
        program_id=BPF_LOADER,
        accounts=[
            AccountMeta(prog_data, False, True),
            AccountMeta(buffer_kp.pubkey(), False, True),
            AccountMeta(program_id, False, True),
            AccountMeta(program_id, True, False),
            AccountMeta(SYSTEM_PROGRAM, False, False),
            AccountMeta(program_id, True, False),
        ],
        data=deploy_data,
    )
    
    print("Deploying from buffer...")
    send_and_confirm([deploy_ix], deploy_kp)
    
    # Verify
    acct = rpc("getAccountInfo", [str(program_id), {"commitment": "confirmed"}]).get("value")
    if acct and acct.get("executable"):
        print("\n✅ SUCCESS! Program deployed.")
        print(f"Program ID: {program_id}")
        PROG_ID_PATH.write_text(str(program_id))
    else:
        print("\n❌ Deployment may have failed.")
        print(f"Program ID: {program_id}")


if __name__ == "__main__":
    main()
