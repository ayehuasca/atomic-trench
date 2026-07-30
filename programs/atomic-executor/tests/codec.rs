use wallet_a_atomic_executor::{patch_u64_le, ExecutorError};

#[test]
fn patches_actual_first_leg_delta_into_second_swap_data() {
    let mut instruction = vec![9_u8; 24];

    patch_u64_le(&mut instruction, 8, 42_500).unwrap();

    assert_eq!(&instruction[8..16], &42_500_u64.to_le_bytes());
}

#[test]
fn rejects_amount_offset_outside_second_instruction() {
    let mut instruction = vec![0_u8; 12];

    let error = patch_u64_le(&mut instruction, 8, 1).unwrap_err();

    assert_eq!(error, ExecutorError::InvalidAmountOffset);
}
