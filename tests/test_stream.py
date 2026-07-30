from backrunner.stream import normalize_transaction, parse_log_notification


def test_processed_log_notification_is_parsed() -> None:
    payload = {
        "jsonrpc": "2.0",
        "method": "logsNotification",
        "params": {
            "subscription": 7,
            "result": {
                "context": {"slot": 123},
                "value": {"signature": "sig", "err": None, "logs": ["Program log"]},
            },
        },
    }

    event = parse_log_notification(payload)

    assert event is not None
    assert event.signature == "sig"
    assert event.slot == 123
    assert event.succeeded is True


def test_transaction_response_is_normalized_for_large_buy_detector() -> None:
    payload = {
        "blockTime": 10,
        "meta": {"err": None, "preBalances": [1], "postBalances": [1]},
        "transaction": {
            "signatures": ["sig"],
            "message": {"accountKeys": [{"pubkey": "user"}]},
        },
    }

    block = normalize_transaction(payload)

    assert block["blockTime"] == 10
    assert block["transactions"][0]["transaction"]["accountKeys"] == [
        {"pubkey": "user"}
    ]
