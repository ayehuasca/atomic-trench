"""Read-only GMGN and Solana RPC providers."""

import time
import uuid
from dataclasses import dataclass
from typing import Any, ClassVar

import requests

WSOL = "So11111111111111111111111111111111111111112"


@dataclass(frozen=True)
class SimulationResult:
    context_slot: int
    error: Any
    logs: tuple[str, ...]
    units_consumed: int
    fee_lamports: int
    account_lamports: tuple[int | None, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.error is None


def parse_trending_mints(payload: dict[str, Any]) -> set[str]:
    if int(payload.get("code", -1)) != 0:
        raise ValueError(f"GMGN rank error: {payload.get('message') or payload.get('reason')}")
    return {
        str(row["address"])
        for row in payload.get("data", {}).get("rank", [])
        if row.get("address")
    }


def parse_sol_price(payload: dict[str, Any]) -> float:
    token = payload.get("data", payload)
    if "price" not in token:
        raise ValueError(f"GMGN token error: {payload.get('message') or payload.get('reason')}")
    value = float(token["price"]["price"])
    if value <= 0:
        raise ValueError("SOL price must be positive")
    return value


class GmgnProvider:
    _PUBLIC_HEADERS: ClassVar[dict[str, str]] = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://gmgn.ai/?chain=sol",
        "Origin": "https://gmgn.ai",
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        fallback_sol_price_usd: float,
        session: Any | None = None,
    ) -> None:
        if fallback_sol_price_usd <= 0:
            raise ValueError("fallback SOL price must be positive")
        self.api_key = api_key
        self.fallback_sol_price_usd = fallback_sol_price_usd
        self.session = session or requests.Session()

    def _get(
        self,
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        response = self.session.get(url, params=params, headers=headers, timeout=45)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("GMGN returned a non-object response")
        if int(payload.get("code", -1)) != 0:
            raise ValueError(
                f"GMGN API error: {payload.get('message') or payload.get('reason')}"
            )
        return payload

    def trending_mints(self) -> set[str]:
        mints: set[str] = set()
        for interval in ("5m", "1h", "6h", "24h"):
            if self.api_key is None:
                try:
                    payload = self._get(
                        f"https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/{interval}",
                        params={"orderby": "volume", "direction": "desc", "limit": 100},
                        headers=self._PUBLIC_HEADERS,
                    )
                except requests.RequestException:
                    # Public GMGN endpoint may be rate-limited or blocked.
                    # Return empty set to keep the observer running.
                    continue
            else:
                payload = self._get(
                    "https://openapi.gmgn.ai/v1/market/rank",
                    params={
                        "chain": "sol",
                        "interval": interval,
                        "order_by": "volume",
                        "direction": "desc",
                        "limit": 100,
                        "timestamp": int(time.time()),
                        "client_id": str(uuid.uuid4()),
                    },
                    headers={
                        "X-APIKEY": self.api_key,
                        "Content-Type": "application/json",
                        "User-Agent": "atomic-trench/0.1.0",
                    },
                )
            mints.update(parse_trending_mints(payload))
        return mints

    def sol_price_usd(self) -> float:
        if self.api_key is None:
            return self.fallback_sol_price_usd
        payload = self._get(
            "https://openapi.gmgn.ai/v1/token/info",
            params={
                "chain": "sol",
                "address": WSOL,
                "timestamp": int(time.time()),
                "client_id": str(uuid.uuid4()),
            },
            headers={
                "X-APIKEY": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "atomic-trench/0.1.0",
            },
        )
        return parse_sol_price(payload)


class SolanaRpc:
    def __init__(
        self, url: str, commitment: str = "finalized", session: Any | None = None
    ) -> None:
        self.url = url
        self.commitment = commitment
        self.session = session or requests.Session()

    def call(self, method: str, params: list[Any]) -> Any:
        response = self.session.post(
            self.url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(f"RPC {method} failed: {payload['error']}")
        return payload.get("result")

    def latest_slot(self) -> int:
        return int(self.call("getSlot", [{"commitment": self.commitment}]))

    def balance(self, address: str, *, min_context_slot: int | None = None) -> int:
        config: dict[str, Any] = {"commitment": "confirmed"}
        if min_context_slot is not None:
            config["minContextSlot"] = min_context_slot
        result = self.call("getBalance", [address, config])
        return int(result["value"])

    def transaction(self, signature: str, *, commitment: str = "confirmed") -> dict[str, Any] | None:
        result = self.call(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "json",
                    "commitment": commitment,
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        return result if isinstance(result, dict) else None

    def block_accounts(self, slot: int) -> dict[str, Any]:
        result = self.call(
            "getBlock",
            [
                slot,
                {
                    "commitment": self.commitment,
                    "transactionDetails": "accounts",
                    "rewards": False,
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        if result is None:
            raise RuntimeError(f"block {slot} is unavailable")
        return result

    def simulate_transaction(
        self,
        transaction_base64: str,
        *,
        min_context_slot: int | None = None,
        replace_recent_blockhash: bool = True,
        return_accounts: tuple[str, ...] = (),
    ) -> SimulationResult:
        config: dict[str, Any] = {
            "encoding": "base64",
            "commitment": "confirmed",
            "replaceRecentBlockhash": replace_recent_blockhash,
            "sigVerify": False,
            "innerInstructions": True,
        }
        if min_context_slot is not None:
            config["minContextSlot"] = min_context_slot
        if return_accounts:
            config["accounts"] = {
                "addresses": list(return_accounts),
                "encoding": "base64",
            }
        result = self.call("simulateTransaction", [transaction_base64, config])
        value = result["value"]
        account_lamports: tuple[int | None, ...] = ()
        if return_accounts:
            raw_accounts = value.get("accounts")
            if not isinstance(raw_accounts, list) or len(raw_accounts) != len(return_accounts):
                raise RuntimeError("simulation did not return every requested account")
            account_lamports = tuple(
                None if account is None else int(account["lamports"])
                for account in raw_accounts
            )
        return SimulationResult(
            context_slot=int(result["context"]["slot"]),
            error=value.get("err"),
            logs=tuple(str(line) for line in value.get("logs") or []),
            units_consumed=int(value.get("unitsConsumed") or 0),
            fee_lamports=int(value.get("fee") or 0),
            account_lamports=account_lamports,
        )
