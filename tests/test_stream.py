import json

from websocket import WebSocketTimeoutException

from backrunner.stream import (
    PUMP_AMM_PROGRAM_ID,
    ProcessedLogStream,
    normalize_transaction,
    parse_log_notification,
)


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


class _FakeSocket:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def recv(self) -> str:
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return str(value)


def test_poll_event_returns_none_on_timeout_and_then_returns_venue_event() -> None:
    stream = ProcessedLogStream("https://example.invalid")
    notification = json.dumps(
        {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 44},
                    "value": {
                        "signature": "venue-signature",
                        "err": None,
                        "logs": [f"Program {PUMP_AMM_PROGRAM_ID} invoke [1]"],
                    },
                }
            },
        }
    )
    stream.sockets = [  # type: ignore[list-item]
        _FakeSocket([WebSocketTimeoutException(), notification])
    ]

    assert stream.poll_event() is None
    event = stream.poll_event()
    assert event is not None
    assert event.signature == "venue-signature"
