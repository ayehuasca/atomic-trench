from backrunner.detector import detect_large_buys

MINT = "TokenMint111111111111111111111111111111111"
BUYER = "Buyer1111111111111111111111111111111111111"


def test_detects_confirmed_large_buy_and_preserves_transaction_order() -> None:
    block = {
        "blockTime": 1_785_328_545,
        "transactions": [
            {
                "meta": {
                    "err": None,
                    "preBalances": [10_000_000_000, 2_039_280],
                    "postBalances": [5_000_000_000, 2_039_280],
                    "preTokenBalances": [
                        {
                            "accountIndex": 1,
                            "mint": MINT,
                            "owner": BUYER,
                            "uiTokenAmount": {"amount": "0", "decimals": 6},
                        }
                    ],
                    "postTokenBalances": [
                        {
                            "accountIndex": 1,
                            "mint": MINT,
                            "owner": BUYER,
                            "uiTokenAmount": {"amount": "250000000", "decimals": 6},
                        }
                    ],
                },
                "transaction": {
                    "signatures": ["victim-signature"],
                    "accountKeys": [BUYER, "buyer-token-account"],
                },
            }
        ],
    }

    events = detect_large_buys(
        block=block,
        slot=435_948_397,
        trending_mints={MINT},
        sol_price_usd=73.385944,
        minimum_buy_usd=300,
    )

    assert len(events) == 1
    assert events[0].transaction_index == 0
    assert events[0].signature == "victim-signature"
    assert events[0].mint == MINT
    assert events[0].buy_sol == 5.0
