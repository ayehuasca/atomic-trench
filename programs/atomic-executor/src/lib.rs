#![forbid(unsafe_code)]

//! Atomic two-leg Pump AMM / Meteora DLMM executor.
//!
//! The caller supplies two already-built DEX instructions. This program runs
//! the first leg, reads the actual intermediate-token delta, patches that
//! exact amount into the second leg, runs it, then rejects the whole
//! transaction unless inventory is zero and the WSOL profit floor is met.

use solana_program::{
    account_info::AccountInfo,
    clock::Clock,
    entrypoint::ProgramResult,
    instruction::{AccountMeta, Instruction},
    msg,
    program::invoke,
    program_error::ProgramError,
    pubkey,
    pubkey::Pubkey,
    sysvar::Sysvar,
};

#[cfg(not(feature = "no-entrypoint"))]
solana_program::entrypoint!(process_instruction);

const MAGIC: &[u8; 4] = b"WABR";
const VERSION: u8 = 1;
const HEADER_LEN: usize = 33;
const FLAG_SIGNER: u8 = 1;
const FLAG_WRITABLE: u8 = 2;

const PUMP_AMM_PROGRAM: Pubkey = pubkey!("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
const METEORA_DLMM_PROGRAM: Pubkey = pubkey!("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo");
const TOKEN_PROGRAM: Pubkey = pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
const TOKEN_2022_PROGRAM: Pubkey = pubkey!("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
const NATIVE_MINT: Pubkey = pubkey!("So11111111111111111111111111111111111111112");
const PUMP_BUY_DISCRIMINATOR: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
const PUMP_SELL_DISCRIMINATOR: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const METEORA_SWAP2_DISCRIMINATOR: [u8; 8] = [65, 75, 63, 76, 235, 91, 91, 136];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u32)]
pub enum ExecutorError {
    InvalidInstruction = 1,
    InvalidAmountOffset = 2,
    InvalidProgramPair = 3,
    InvalidTokenAccount = 4,
    InvalidAuthority = 5,
    ExistingInventory = 6,
    NoIntermediateOutput = 7,
    ResidualInventory = 8,
    ProfitFloorNotMet = 9,
    Expired = 10,
    PrivilegeEscalation = 11,
    UnexpectedUserTokenAccount = 12,
    ArithmeticOverflow = 13,
}

impl From<ExecutorError> for ProgramError {
    fn from(value: ExecutorError) -> Self {
        ProgramError::Custom(value as u32)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AccountRef {
    index: u8,
    flags: u8,
}

struct ExecutorInstruction<'a> {
    first_program_index: u8,
    second_program_index: u8,
    quote_index: u8,
    intermediate_index: u8,
    second_amount_offset: usize,
    minimum_profit: u64,
    valid_until_slot: u64,
    first_refs: Vec<AccountRef>,
    second_refs: Vec<AccountRef>,
    first_data: &'a [u8],
    second_data: &'a [u8],
}

pub fn patch_u64_le(data: &mut [u8], offset: usize, value: u64) -> Result<(), ExecutorError> {
    let end = offset
        .checked_add(8)
        .ok_or(ExecutorError::InvalidAmountOffset)?;
    let target = data
        .get_mut(offset..end)
        .ok_or(ExecutorError::InvalidAmountOffset)?;
    target.copy_from_slice(&value.to_le_bytes());
    Ok(())
}

fn take_u16(data: &[u8], offset: usize) -> Result<u16, ExecutorError> {
    let bytes: [u8; 2] = data
        .get(offset..offset + 2)
        .ok_or(ExecutorError::InvalidInstruction)?
        .try_into()
        .map_err(|_| ExecutorError::InvalidInstruction)?;
    Ok(u16::from_le_bytes(bytes))
}

fn take_u64(data: &[u8], offset: usize) -> Result<u64, ExecutorError> {
    let bytes: [u8; 8] = data
        .get(offset..offset + 8)
        .ok_or(ExecutorError::InvalidInstruction)?
        .try_into()
        .map_err(|_| ExecutorError::InvalidInstruction)?;
    Ok(u64::from_le_bytes(bytes))
}

fn parse_instruction(data: &[u8]) -> Result<ExecutorInstruction<'_>, ExecutorError> {
    if data.len() < HEADER_LEN || data.get(0..4) != Some(MAGIC) || data[4] != VERSION {
        return Err(ExecutorError::InvalidInstruction);
    }
    let first_count = data[9] as usize;
    let second_count = data[10] as usize;
    let second_amount_offset = take_u16(data, 11)? as usize;
    let minimum_profit = take_u64(data, 13)?;
    let valid_until_slot = take_u64(data, 21)?;
    let first_data_len = take_u16(data, 29)? as usize;
    let second_data_len = take_u16(data, 31)? as usize;

    let refs_len = first_count
        .checked_add(second_count)
        .and_then(|value| value.checked_mul(2))
        .ok_or(ExecutorError::InvalidInstruction)?;
    let first_data_start = HEADER_LEN
        .checked_add(refs_len)
        .ok_or(ExecutorError::InvalidInstruction)?;
    let second_data_start = first_data_start
        .checked_add(first_data_len)
        .ok_or(ExecutorError::InvalidInstruction)?;
    let end = second_data_start
        .checked_add(second_data_len)
        .ok_or(ExecutorError::InvalidInstruction)?;
    if end != data.len()
        || second_amount_offset
            .checked_add(8)
            .is_none_or(|v| v > second_data_len)
    {
        return Err(ExecutorError::InvalidInstruction);
    }

    let parse_refs = |start: usize, count: usize| -> Result<Vec<AccountRef>, ExecutorError> {
        (0..count)
            .map(|position| {
                let offset = start + position * 2;
                let flags = data[offset + 1];
                if flags & !(FLAG_SIGNER | FLAG_WRITABLE) != 0 {
                    return Err(ExecutorError::InvalidInstruction);
                }
                Ok(AccountRef {
                    index: data[offset],
                    flags,
                })
            })
            .collect()
    };
    let first_refs = parse_refs(HEADER_LEN, first_count)?;
    let second_refs = parse_refs(HEADER_LEN + first_count * 2, second_count)?;

    Ok(ExecutorInstruction {
        first_program_index: data[5],
        second_program_index: data[6],
        quote_index: data[7],
        intermediate_index: data[8],
        second_amount_offset,
        minimum_profit,
        valid_until_slot,
        first_refs,
        second_refs,
        first_data: &data[first_data_start..second_data_start],
        second_data: &data[second_data_start..end],
    })
}

fn is_token_program(owner: &Pubkey) -> bool {
    owner == &TOKEN_PROGRAM || owner == &TOKEN_2022_PROGRAM
}

fn validate_quote_mint(mint: &Pubkey) -> Result<(), ExecutorError> {
    if mint == &NATIVE_MINT {
        Ok(())
    } else {
        Err(ExecutorError::InvalidTokenAccount)
    }
}

fn token_state(account: &AccountInfo<'_>) -> Result<(Pubkey, Pubkey, u64), ExecutorError> {
    if !is_token_program(account.owner) {
        return Err(ExecutorError::InvalidTokenAccount);
    }
    let data = account
        .try_borrow_data()
        .map_err(|_| ExecutorError::InvalidTokenAccount)?;
    if data.len() < 72 {
        return Err(ExecutorError::InvalidTokenAccount);
    }
    let mint = Pubkey::try_from(&data[0..32]).map_err(|_| ExecutorError::InvalidTokenAccount)?;
    let authority =
        Pubkey::try_from(&data[32..64]).map_err(|_| ExecutorError::InvalidTokenAccount)?;
    let amount = u64::from_le_bytes(
        data[64..72]
            .try_into()
            .map_err(|_| ExecutorError::InvalidTokenAccount)?,
    );
    Ok((mint, authority, amount))
}

fn validate_pump(data: &[u8], discriminator: &[u8; 8], dynamic: bool) -> Result<(), ExecutorError> {
    if data.len() != 24 || data.get(0..8) != Some(discriminator) {
        return Err(ExecutorError::InvalidInstruction);
    }
    let amount = take_u64(data, 8)?;
    let limit = take_u64(data, 16)?;
    if (dynamic && (amount != 1 || limit != 1)) || (!dynamic && (amount == 0 || limit == 0)) {
        return Err(ExecutorError::InvalidInstruction);
    }
    Ok(())
}

fn validate_meteora(data: &[u8], dynamic: bool) -> Result<(), ExecutorError> {
    if data.len() != 28
        || data.get(0..8) != Some(&METEORA_SWAP2_DISCRIMINATOR)
        || take_u64(data, 16)? != 0
        || data.get(24..28) != Some(&0_u32.to_le_bytes())
    {
        return Err(ExecutorError::InvalidInstruction);
    }
    let amount = take_u64(data, 8)?;
    if (dynamic && amount != 1) || (!dynamic && amount == 0) {
        return Err(ExecutorError::InvalidInstruction);
    }
    Ok(())
}

fn validate_route_abis(
    first_program: &Pubkey,
    first_data: &[u8],
    second_program: &Pubkey,
    second_data: &[u8],
    second_amount_offset: usize,
) -> Result<(), ExecutorError> {
    if second_amount_offset != 8 {
        return Err(ExecutorError::InvalidAmountOffset);
    }
    if first_program == &PUMP_AMM_PROGRAM && second_program == &METEORA_DLMM_PROGRAM {
        validate_pump(first_data, &PUMP_BUY_DISCRIMINATOR, false)?;
        validate_meteora(second_data, true)
    } else if first_program == &METEORA_DLMM_PROGRAM && second_program == &PUMP_AMM_PROGRAM {
        validate_meteora(first_data, false)?;
        validate_pump(second_data, &PUMP_SELL_DISCRIMINATOR, true)
    } else {
        Err(ExecutorError::InvalidProgramPair)
    }
}

fn validate_program_pair(first: &Pubkey, second: &Pubkey) -> Result<(), ExecutorError> {
    let valid = (*first == PUMP_AMM_PROGRAM && *second == METEORA_DLMM_PROGRAM)
        || (*first == METEORA_DLMM_PROGRAM && *second == PUMP_AMM_PROGRAM);
    if valid {
        Ok(())
    } else {
        Err(ExecutorError::InvalidProgramPair)
    }
}

fn invoke_leg<'a>(
    program: &AccountInfo<'a>,
    refs: &[AccountRef],
    data: Vec<u8>,
    accounts: &[AccountInfo<'a>],
) -> Result<(), ProgramError> {
    let mut metas = Vec::with_capacity(refs.len());
    let mut infos = Vec::with_capacity(refs.len() + 1);
    for reference in refs {
        let account = accounts
            .get(reference.index as usize)
            .ok_or(ExecutorError::InvalidInstruction)?;
        let signer = reference.flags & FLAG_SIGNER != 0;
        let writable = reference.flags & FLAG_WRITABLE != 0;
        if (signer && !account.is_signer) || (writable && !account.is_writable) {
            return Err(ExecutorError::PrivilegeEscalation.into());
        }
        let meta = if writable {
            AccountMeta::new(*account.key, signer)
        } else {
            AccountMeta::new_readonly(*account.key, signer)
        };
        metas.push(meta);
        infos.push(account.clone());
    }
    infos.push(program.clone());
    invoke(
        &Instruction {
            program_id: *program.key,
            accounts: metas,
            data,
        },
        &infos,
    )
}

pub fn process_instruction(
    _program_id: &Pubkey,
    accounts: &[AccountInfo<'_>],
    instruction_data: &[u8],
) -> ProgramResult {
    let config = parse_instruction(instruction_data)?;
    if config.quote_index != 1 || config.intermediate_index != 2 {
        return Err(ExecutorError::InvalidInstruction.into());
    }
    let user = accounts.first().ok_or(ExecutorError::InvalidInstruction)?;
    let quote = accounts.get(1).ok_or(ExecutorError::InvalidInstruction)?;
    let intermediate = accounts.get(2).ok_or(ExecutorError::InvalidInstruction)?;
    if !user.is_signer {
        return Err(ExecutorError::InvalidAuthority.into());
    }

    let first_program = accounts
        .get(config.first_program_index as usize)
        .ok_or(ExecutorError::InvalidInstruction)?;
    let second_program = accounts
        .get(config.second_program_index as usize)
        .ok_or(ExecutorError::InvalidInstruction)?;
    if !first_program.executable || !second_program.executable {
        return Err(ExecutorError::InvalidProgramPair.into());
    }
    validate_program_pair(first_program.key, second_program.key)?;
    validate_route_abis(
        first_program.key,
        config.first_data,
        second_program.key,
        config.second_data,
        config.second_amount_offset,
    )?;

    let (quote_mint, quote_authority, quote_start) = token_state(quote)?;
    validate_quote_mint(&quote_mint)?;
    let (intermediate_mint, intermediate_authority, intermediate_start) =
        token_state(intermediate)?;
    if quote_authority != *user.key || intermediate_authority != *user.key {
        return Err(ExecutorError::InvalidAuthority.into());
    }
    if quote_mint == intermediate_mint {
        return Err(ExecutorError::InvalidTokenAccount.into());
    }
    if intermediate_start != 0 {
        return Err(ExecutorError::ExistingInventory.into());
    }
    for (index, account) in accounts.iter().enumerate().skip(3) {
        if index == config.first_program_index as usize
            || index == config.second_program_index as usize
        {
            continue;
        }
        if is_token_program(account.owner) {
            if let Ok((_, authority, _)) = token_state(account) {
                if authority == *user.key {
                    return Err(ExecutorError::UnexpectedUserTokenAccount.into());
                }
            }
        }
    }

    if Clock::get()?.slot > config.valid_until_slot {
        return Err(ExecutorError::Expired.into());
    }

    invoke_leg(
        first_program,
        &config.first_refs,
        config.first_data.to_vec(),
        accounts,
    )?;
    let (_, _, intermediate_after_first) = token_state(intermediate)?;
    let actual_intermediate = intermediate_after_first
        .checked_sub(intermediate_start)
        .filter(|amount| *amount > 0)
        .ok_or(ExecutorError::NoIntermediateOutput)?;

    let mut second_data = config.second_data.to_vec();
    patch_u64_le(
        &mut second_data,
        config.second_amount_offset,
        actual_intermediate,
    )?;
    invoke_leg(second_program, &config.second_refs, second_data, accounts)?;

    let (_, _, intermediate_final) = token_state(intermediate)?;
    if intermediate_final != intermediate_start {
        return Err(ExecutorError::ResidualInventory.into());
    }
    let (_, _, quote_final) = token_state(quote)?;
    let required_quote = quote_start
        .checked_add(config.minimum_profit)
        .ok_or(ExecutorError::ArithmeticOverflow)?;
    if quote_final < required_quote {
        msg!(
            "profit floor not met: start={}, final={}, required={}",
            quote_start,
            quote_final,
            required_quote
        );
        return Err(ExecutorError::ProfitFloorNotMet.into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn swap2(amount: u64) -> Vec<u8> {
        let mut data = vec![65, 75, 63, 76, 235, 91, 91, 136];
        data.extend_from_slice(&amount.to_le_bytes());
        data.extend_from_slice(&0_u64.to_le_bytes());
        data.extend_from_slice(&0_u32.to_le_bytes());
        data
    }

    fn pump_sell() -> Vec<u8> {
        let mut data = vec![51, 230, 133, 164, 1, 127, 131, 173];
        data.extend_from_slice(&1_u64.to_le_bytes());
        data.extend_from_slice(&1_u64.to_le_bytes());
        data
    }

    #[test]
    fn route_abi_accepts_only_pinned_dynamic_offset() {
        assert_eq!(
            validate_route_abis(
                &METEORA_DLMM_PROGRAM,
                &swap2(10),
                &PUMP_AMM_PROGRAM,
                &pump_sell(),
                8,
            ),
            Ok(())
        );
        assert_eq!(
            validate_route_abis(
                &METEORA_DLMM_PROGRAM,
                &swap2(10),
                &PUMP_AMM_PROGRAM,
                &pump_sell(),
                9,
            ),
            Err(ExecutorError::InvalidAmountOffset)
        );
    }

    #[test]
    fn route_abi_rejects_discriminator_drift() {
        let mut second = pump_sell();
        second[0] ^= 0xff;
        assert_eq!(
            validate_route_abis(
                &METEORA_DLMM_PROGRAM,
                &swap2(10),
                &PUMP_AMM_PROGRAM,
                &second,
                8,
            ),
            Err(ExecutorError::InvalidInstruction)
        );
    }

    #[test]
    fn token_2022_accounts_are_accepted() {
        assert!(is_token_program(&TOKEN_2022_PROGRAM));
    }

    #[test]
    fn quote_mint_must_be_native_wsol() {
        assert_eq!(validate_quote_mint(&NATIVE_MINT), Ok(()));
        assert_eq!(
            validate_quote_mint(&Pubkey::new_unique()),
            Err(ExecutorError::InvalidTokenAccount)
        );
    }
}
