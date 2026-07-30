"""
Multi-region Jito bundle sender.

Fires the same bundle to all Jito block-engine regions in parallel
for maximum coverage (hedges leader location).
"""

import random

import aiohttp

JITO_TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
]

JITO_ENDPOINTS = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://tokyo.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://slc.mainnet.block-engine.jito.wtf/api/v1/bundles",
]

# Jito tip account set supported across all regions
TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
]


def random_tip_account() -> str:
    return random.choice(TIP_ACCOUNTS)


class JitoSender:
    """Multi-region Jito bundle sender.

    Sends each bundle to all Jito regions in parallel.
    Tracks the fastest accepting region for metrics.
    """

    def __init__(self):
        self.last_accepted_region: str | None = None

    async def send_bundle(
        self,
        encoded_txs: list[str],
        *,
        encoding: str = "base64",
    ) -> list[tuple[str, dict]]:
        """Send encoded transactions as a bundle to all regions.

        Args:
            encoded_txs: List of base64 or base58 encoded transactions.
            encoding: 'base64' or 'base58'.

        Returns:
            List of (region_url, response_dict) tuples.
        """
        async with aiohttp.ClientSession() as session:
            tasks = [self._post(session, url, encoded_txs, encoding) for url in JITO_ENDPOINTS]
            results = await asyncio.gather(*tasks)

        # Track first accepted
        for url, res in results:
            result_val = res.get("result") if isinstance(res, dict) else res
            if isinstance(result_val, str):
                self.last_accepted_region = url
                break

        return results

    async def _post(
        self,
        session: aiohttp.ClientSession,
        url: str,
        encoded_txs: list[str],
        encoding: str,
    ) -> tuple[str, dict]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [encoded_txs, {"encoding": encoding}] if encoding == "base64" else [encoded_txs],
        }
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                data = await resp.json()
                return url, data
        except Exception as e:
            return url, {"error": str(e)}