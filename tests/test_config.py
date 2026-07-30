from pathlib import Path

from backrunner.config import load_config


def test_live_mode_loads_when_enabled(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("dry_run: false\nrpc_url: https://example.invalid\n")
    config = load_config(path)
    assert config.dry_run is False


def test_dry_run_config_loads_without_private_key(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "dry_run: true\n"
        "rpc_url: https://solana-rpc.publicnode.com\n"
        "minimum_buy_usd: 300\n"
        "maximum_transaction_gap: 3\n"
    )

    config = load_config(path)

    assert config.dry_run is True
    assert config.minimum_buy_usd == 300
    assert config.maximum_transaction_gap == 3
    assert config.failed_attempt_reserve_lamports == 815_123
