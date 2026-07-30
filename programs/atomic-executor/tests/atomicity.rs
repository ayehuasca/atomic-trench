use solana_program::{
    account_info::{next_account_info, AccountInfo},
    entrypoint::ProgramResult,
    program::{invoke, invoke_signed},
    program_error::ProgramError,
    program_pack::Pack,
    pubkey,
    pubkey::Pubkey,
};
use solana_program_test::{processor, ProgramTest, ProgramTestContext};
use solana_sdk::{
    account::Account,
    instruction::{AccountMeta, Instruction},
    signature::Signer,
    transaction::Transaction,
};
use spl_token::state::Account as TokenAccount;
use spl_token::native::NATIVE_MINT;
use wallet_a_atomic_executor::process_instruction;

const EXECUTOR: Pubkey = Pubkey::new_from_array([7_u8; 32]);
const PUMP: Pubkey = pubkey!("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
const METEORA: Pubkey = pubkey!("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo");
const TOKEN: Pubkey = pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");

struct Fixture {
    context: ProgramTestContext,
    quote: Pubkey,
    intermediate: Pubkey,
    accounts: Vec<AccountMeta>,
}

fn token_account(mint: Pubkey, authority: Pubkey, amount: u64) -> Account {
    let mut data = vec![0_u8; TokenAccount::LEN];
    TokenAccount::pack(
        TokenAccount {
            mint,
            owner: authority,
            amount,
            state: spl_token::state::AccountState::Initialized,
            ..TokenAccount::default()
        },
        &mut data,
    )
    .unwrap();
    Account {
        lamports: 2_039_280,
        data,
        owner: TOKEN,
        executable: false,
        rent_epoch: 0,
    }
}

fn read_amount(account: &Account) -> u64 {
    TokenAccount::unpack(&account.data).unwrap().amount
}

fn transfer<'a>(
    token_program: &AccountInfo<'a>,
    source: &AccountInfo<'a>,
    destination: &AccountInfo<'a>,
    authority: &AccountInfo<'a>,
    amount: u64,
    signer_seeds: Option<&[&[u8]]>,
) -> ProgramResult {
    let instruction = spl_token::instruction::transfer(
        token_program.key,
        source.key,
        destination.key,
        authority.key,
        &[],
        amount,
    )?;
    let infos = [
        source.clone(),
        destination.clone(),
        authority.clone(),
        token_program.clone(),
    ];
    if let Some(seeds) = signer_seeds {
        invoke_signed(&instruction, &infos, &[seeds])
    } else {
        invoke(&instruction, &infos)
    }
}

fn mock_dex(program_id: &Pubkey, accounts: &[AccountInfo<'_>], data: &[u8]) -> ProgramResult {
    let mut iterator = accounts.iter();
    let user = next_account_info(&mut iterator)?;
    let user_input = next_account_info(&mut iterator)?;
    let user_output = next_account_info(&mut iterator)?;
    let pool_input = next_account_info(&mut iterator)?;
    let pool_output = next_account_info(&mut iterator)?;
    let token_program = next_account_info(&mut iterator)?;
    let pool_authority = next_account_info(&mut iterator)?;

    let (input_amount, output_amount) = if program_id == &METEORA {
        let input = u64::from_le_bytes(
            data[8..16]
                .try_into()
                .map_err(|_| ProgramError::InvalidInstructionData)?,
        );
        (input, 400)
    } else if program_id == &PUMP {
        let input = u64::from_le_bytes(
            data[8..16]
                .try_into()
                .map_err(|_| ProgramError::InvalidInstructionData)?,
        );
        (input, 120)
    } else {
        return Err(ProgramError::IncorrectProgramId);
    };

    transfer(
        token_program,
        user_input,
        pool_input,
        user,
        input_amount,
        None,
    )?;
    let (expected_authority, bump) = Pubkey::find_program_address(&[b"vault"], program_id);
    if pool_authority.key != &expected_authority {
        return Err(ProgramError::InvalidSeeds);
    }
    let bump_seed = [bump];
    transfer(
        token_program,
        pool_output,
        user_output,
        pool_authority,
        output_amount,
        Some(&[b"vault", &bump_seed]),
    )
}

fn account_ref(index: u8, signer: bool, writable: bool) -> [u8; 2] {
    [index, u8::from(signer) | (u8::from(writable) << 1)]
}

fn executor_data(
    minimum_profit: u64,
    first_input: u64,
    _first_output: u64,
    _second_output: u64,
    valid_until_slot: u64,
    require_quote_signer: bool,
) -> Vec<u8> {
    let first_refs = [
        account_ref(0, true, false),
        account_ref(1, require_quote_signer, true),
        account_ref(2, false, true),
        account_ref(5, false, true),
        account_ref(6, false, true),
        account_ref(7, false, false),
        account_ref(8, false, false),
    ];
    let second_refs = [
        account_ref(0, true, false),
        account_ref(2, false, true),
        account_ref(1, false, true),
        account_ref(9, false, true),
        account_ref(10, false, true),
        account_ref(7, false, false),
        account_ref(11, false, false),
    ];
    let mut first_data = vec![65, 75, 63, 76, 235, 91, 91, 136];
    first_data.extend_from_slice(&first_input.to_le_bytes());
    first_data.extend_from_slice(&0_u64.to_le_bytes());
    // Vec<SliceAccountFlag>: u32 length=2, then (u8 accountsType, u8 length=0) x2
    first_data.extend_from_slice(&2_u32.to_le_bytes());
    first_data.push(0); // transferHookX
    first_data.push(0); // length=0
    first_data.push(1); // transferHookY
    first_data.push(0); // length=0
    let mut second_data = vec![51, 230, 133, 164, 1, 127, 131, 173];
    second_data.extend_from_slice(&1_u64.to_le_bytes());
    second_data.extend_from_slice(&1_u64.to_le_bytes());

    let mut data = Vec::new();
    data.extend_from_slice(b"WABR");
    data.push(1);
    data.extend_from_slice(&[3, 4, 1, 2]);
    data.push(first_refs.len() as u8);
    data.push(second_refs.len() as u8);
    data.extend_from_slice(&8_u16.to_le_bytes());
    data.extend_from_slice(&minimum_profit.to_le_bytes());
    data.extend_from_slice(&valid_until_slot.to_le_bytes());
    data.extend_from_slice(&(first_data.len() as u16).to_le_bytes());
    data.extend_from_slice(&(second_data.len() as u16).to_le_bytes());
    for reference in first_refs.into_iter().chain(second_refs) {
        data.extend_from_slice(&reference);
    }
    data.extend_from_slice(&first_data);
    data.extend_from_slice(&second_data);
    data
}

async fn fixture() -> Fixture {
    let quote = Pubkey::new_unique();
    let intermediate = Pubkey::new_unique();
    let quote_mint = NATIVE_MINT;
    let intermediate_mint = Pubkey::new_unique();
    let meteora_pool_quote = Pubkey::new_unique();
    let meteora_pool_intermediate = Pubkey::new_unique();
    let pump_pool_intermediate = Pubkey::new_unique();
    let pump_pool_quote = Pubkey::new_unique();
    let (meteora_authority, _) = Pubkey::find_program_address(&[b"vault"], &METEORA);
    let (pump_authority, _) = Pubkey::find_program_address(&[b"vault"], &PUMP);

    let mut test = ProgramTest::new("executor", EXECUTOR, processor!(process_instruction));
    test.add_program("mock_meteora", METEORA, processor!(mock_dex));
    test.add_program("mock_pump", PUMP, processor!(mock_dex));
    test.add_program(
        "spl_token",
        TOKEN,
        processor!(spl_token::processor::Processor::process),
    );
    let mut context = test.start_with_context().await;
    let user = context.payer.pubkey();

    let accounts_to_add = [
        (quote, token_account(quote_mint, user, 1_000)),
        (intermediate, token_account(intermediate_mint, user, 0)),
        (
            meteora_pool_quote,
            token_account(quote_mint, meteora_authority, 0),
        ),
        (
            meteora_pool_intermediate,
            token_account(intermediate_mint, meteora_authority, 10_000),
        ),
        (
            pump_pool_intermediate,
            token_account(intermediate_mint, pump_authority, 0),
        ),
        (
            pump_pool_quote,
            token_account(quote_mint, pump_authority, 10_000),
        ),
    ];
    for (address, account) in accounts_to_add {
        context.set_account(&address, &account.into());
    }

    Fixture {
        context,
        quote,
        intermediate,
        accounts: vec![
            AccountMeta::new_readonly(user, true),
            AccountMeta::new(quote, false),
            AccountMeta::new(intermediate, false),
            AccountMeta::new_readonly(METEORA, false),
            AccountMeta::new_readonly(PUMP, false),
            AccountMeta::new(meteora_pool_quote, false),
            AccountMeta::new(meteora_pool_intermediate, false),
            AccountMeta::new_readonly(TOKEN, false),
            AccountMeta::new_readonly(meteora_authority, false),
            AccountMeta::new(pump_pool_intermediate, false),
            AccountMeta::new(pump_pool_quote, false),
            AccountMeta::new_readonly(pump_authority, false),
        ],
    }
}

async fn run(
    fixture: &mut Fixture,
    minimum_profit: u64,
) -> Result<(), solana_banks_client::BanksClientError> {
    run_values(fixture, minimum_profit, 100, 400, 120).await
}

async fn run_values(
    fixture: &mut Fixture,
    minimum_profit: u64,
    first_input: u64,
    first_output: u64,
    second_output: u64,
) -> Result<(), solana_banks_client::BanksClientError> {
    let data = executor_data(
        minimum_profit,
        first_input,
        first_output,
        second_output,
        u64::MAX,
        false,
    );
    run_encoded(fixture, data).await
}

async fn run_encoded(
    fixture: &mut Fixture,
    data: Vec<u8>,
) -> Result<(), solana_banks_client::BanksClientError> {
    let instruction = Instruction {
        program_id: EXECUTOR,
        accounts: fixture.accounts.clone(),
        data,
    };
    let transaction = Transaction::new_signed_with_payer(
        &[instruction],
        Some(&fixture.context.payer.pubkey()),
        &[&fixture.context.payer],
        fixture.context.last_blockhash,
    );
    fixture
        .context
        .banks_client
        .process_transaction(transaction)
        .await
}

async fn balances(fixture: &mut Fixture) -> (u64, u64) {
    let quote = fixture
        .context
        .banks_client
        .get_account(fixture.quote)
        .await
        .unwrap()
        .unwrap();
    let intermediate = fixture
        .context
        .banks_client
        .get_account(fixture.intermediate)
        .await
        .unwrap()
        .unwrap();
    (read_amount(&quote), read_amount(&intermediate))
}

#[tokio::test]
async fn executes_runtime_sized_second_leg_and_clears_inventory() {
    let mut fixture = fixture().await;

    run(&mut fixture, 10).await.unwrap();

    let quote = fixture
        .context
        .banks_client
        .get_account(fixture.quote)
        .await
        .unwrap()
        .unwrap();
    let intermediate = fixture
        .context
        .banks_client
        .get_account(fixture.intermediate)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(read_amount(&quote), 1_020);
    assert_eq!(read_amount(&intermediate), 0);
}

#[tokio::test]
async fn profit_guard_failure_rolls_back_both_swaps() {
    let mut fixture = fixture().await;

    assert!(run(&mut fixture, 30).await.is_err());

    let quote = fixture
        .context
        .banks_client
        .get_account(fixture.quote)
        .await
        .unwrap()
        .unwrap();
    let intermediate = fixture
        .context
        .banks_client
        .get_account(fixture.intermediate)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(read_amount(&quote), 1_000);
    assert_eq!(read_amount(&intermediate), 0);
}

#[tokio::test]
async fn first_leg_failure_changes_no_token_balance() {
    let mut fixture = fixture().await;

    assert!(run_values(&mut fixture, 0, 2_000, 400, 120).await.is_err());

    let quote = fixture
        .context
        .banks_client
        .get_account(fixture.quote)
        .await
        .unwrap()
        .unwrap();
    let intermediate = fixture
        .context
        .banks_client
        .get_account(fixture.intermediate)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(read_amount(&quote), 1_000);
    assert_eq!(read_amount(&intermediate), 0);
}

#[tokio::test]
async fn second_leg_failure_rolls_back_first_leg() {
    let mut fixture = fixture().await;
    let pump_quote = fixture.accounts[10].pubkey;
    let mut account = fixture
        .context
        .banks_client
        .get_account(pump_quote)
        .await
        .unwrap()
        .unwrap();
    let mut token = TokenAccount::unpack(&account.data).unwrap();
    token.amount = 0;
    TokenAccount::pack(token, &mut account.data).unwrap();
    fixture.context.set_account(&pump_quote, &account.into());

    assert!(run_values(&mut fixture, 0, 100, 400, 20_000).await.is_err());

    let quote = fixture
        .context
        .banks_client
        .get_account(fixture.quote)
        .await
        .unwrap()
        .unwrap();
    let intermediate = fixture
        .context
        .banks_client
        .get_account(fixture.intermediate)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(read_amount(&quote), 1_000);
    assert_eq!(read_amount(&intermediate), 0);
}

#[tokio::test]
async fn expired_opportunity_is_rejected_before_swaps() {
    let mut fixture = fixture().await;
    let data = executor_data(0, 100, 400, 120, 0, false);

    assert!(run_encoded(&mut fixture, data).await.is_err());
    assert_eq!(balances(&mut fixture).await, (1_000, 0));
}

#[tokio::test]
async fn signer_privilege_escalation_is_rejected_before_swaps() {
    let mut fixture = fixture().await;
    let data = executor_data(0, 100, 400, 120, u64::MAX, true);

    assert!(run_encoded(&mut fixture, data).await.is_err());
    assert_eq!(balances(&mut fixture).await, (1_000, 0));
}
