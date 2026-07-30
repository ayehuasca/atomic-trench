"""
Creator scoring and filtering for pump.fun token sniping.

Scores creators based on on-chain history: graduation rate, launch frequency,
wallet age, and dump behavior. Filters out serial ruggers and fresh wallets.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

import base58
import requests

from backrunner.providers import SolanaRpc

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
CREATE_DISCRIMINATOR = bytes([24, 30, 200, 40, 5, 28, 7, 119])
CREATE_V2_DISCRIMINATOR = bytes([214, 144, 76, 236, 95, 139, 49, 180])


@dataclass
class CreatorProfile:
    address: str
    wallet_age_days: float
    total_creates: int
    total_graduates: int
    graduation_rate: float
    tokens_last_7d: int
    tokens_last_24h: int
    median_time_between_launches_hours: float
    score: float  # 0.0 (worst) to 1.0 (best)
    tags: list[str] = field(default_factory=list)
    last_scored: float = 0.0  # timestamp


@dataclass
class SnipeDecision:
    mint: str
    creator: str
    profile: CreatorProfile | None
    approved: bool
    reason: str


class CreatorScorer:
    """Scores pump.fun creators by their on-chain history.

    Uses a JSON file for persistent caching across restarts.
    """

    # Scoring thresholds
    MIN_WALLET_AGE_MINUTES = 60
    MIN_GRADUATION_RATE = 0.001
    MAX_TOKENS_PER_DAY = 20
    MAX_TOKENS_TOTAL = 500
    CACHE_TTL_SECONDS = 86400  # 24h cache TTL

    def __init__(
        self,
        rpc: SolanaRpc,
        scorer: Any | None = None,
        *,
        cache_path: str | None = None,
        max_sigs_to_scan: int = 200,
    ):
        self.rpc = rpc
        self.scorer = scorer
        self.max_sigs_to_scan = max_sigs_to_scan
        self.cache_path = cache_path
        self._memory_cache: dict[str, tuple[CreatorProfile, float]] = {}

        # Load persistent cache from disk
        if cache_path:
            import pathlib
            p = pathlib.Path(cache_path)
            if p.exists():
                try:
                    import json
                    data = json.loads(p.read_text())
                    now = time.time()
                    for addr, profile_dict in data.items():
                        age = now - profile_dict.get("last_scored", 0)
                        if age < self.CACHE_TTL_SECONDS:
                            self._memory_cache[addr] = (CreatorProfile(**profile_dict), now)
                except Exception:
                    pass

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            import json, pathlib
            p = pathlib.Path(self.cache_path)
            data = {}
            for addr, (profile, _) in self._memory_cache.items():
                data[addr] = {
                    "address": profile.address,
                    "wallet_age_days": profile.wallet_age_days,
                    "total_creates": profile.total_creates,
                    "total_graduates": profile.total_graduates,
                    "graduation_rate": profile.graduation_rate,
                    "tokens_last_7d": profile.tokens_last_7d,
                    "tokens_last_24h": profile.tokens_last_24h,
                    "median_time_between_launches_hours": profile.median_time_between_launches_hours,
                    "score": profile.score,
                    "tags": profile.tags,
                    "last_scored": time.time(),
                }
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def score(self, creator: str, mint: str) -> SnipeDecision:
        """Score a creator and return a snipe decision."""
        now = time.time()

        # Check memory cache
        if creator in self._memory_cache:
            profile, cached_at = self._memory_cache[creator]
            if now - cached_at < self.CACHE_TTL_SECONDS:
                return self._decide(profile, mint)

        # Fetch and score
        try:
            profile = self._fetch_profile(creator)
        except Exception as e:
            return SnipeDecision(
                mint=mint, creator=creator, profile=None,
                approved=False, reason=f"scoring_error: {e}",
            )

        profile.last_scored = now
        self._memory_cache[creator] = (profile, now)
        self._save_cache()
        return self._decide(profile, mint)

    def _decide(self, profile: CreatorProfile, mint: str) -> SnipeDecision:
        """Apply hard filters and return a decision."""
        reasons = []

        # Wallet age
        if profile.wallet_age_days < self.MIN_WALLET_AGE_MINUTES / 60 / 24:
            reasons.append("wallet_too_fresh")

        # Graduation rate
        if profile.total_creates > 10 and profile.graduation_rate < self.MIN_GRADUATION_RATE:
            reasons.append(f"low_graduation_rate_{profile.graduation_rate:.4f}")

        # Launch frequency
        if profile.tokens_last_24h > self.MAX_TOKENS_PER_DAY:
            reasons.append(f"too_many_tokens_24h_{profile.tokens_last_24h}")

        if profile.total_creates > self.MAX_TOKENS_TOTAL:
            reasons.append(f"too_many_tokens_total_{profile.total_creates}")

        approved = len(reasons) == 0
        return SnipeDecision(
            mint=mint, creator=profile.address, profile=profile,
            approved=approved, reason="; ".join(reasons) if reasons else "ok",
        )

    def _fetch_profile(self, creator: str) -> CreatorProfile:
        """Fetch and analyze a creator's on-chain history."""
        # Get signatures
        data = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [creator, {"limit": min(self.max_sigs_to_scan, 1000)}],
        }).encode()
        resp = requests.post(self.rpc.url, data=data, headers={"Content-Type": "application/json"}, timeout=30)
        sigs = resp.json().get("result", [])

        if not sigs:
            # No history — fresh wallet
            return CreatorProfile(
                address=creator, wallet_age_days=0, total_creates=0,
                total_graduates=0, graduation_rate=0.0,
                tokens_last_7d=0, tokens_last_24h=0,
                median_time_between_launches_hours=0, score=0.3,
                tags=["fresh_wallet"],
            )

        # Wallet age from earliest signature
        now = time.time()
        earliest_time = sigs[-1].get("blockTime", 0)
        wallet_age_days = (now - earliest_time) / 86400 if earliest_time else 0

        # Scan signatures for create events
        create_times: list[float] = []
        graduate_count = 0
        total_creates = 0
        tokens_last_7d = 0
        tokens_last_24h = 0

        for s in sigs:
            sig = s["signature"]
            block_time = s.get("blockTime", 0)

            # Fetch transaction
            tx_data = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            }).encode()
            try:
                tx_resp = requests.post(self.rpc.url, data=tx_data, headers={"Content-Type": "application/json"}, timeout=15)
                tx = tx_resp.json().get("result")
                if not tx:
                    continue
            except Exception:
                continue

            meta = tx.get("meta", {})
            if meta.get("err"):
                continue

            msg = tx.get("transaction", {}).get("message", {})
            instructions = msg.get("instructions", [])

            for ix in instructions:
                prog = str(ix.get("programId", ix.get("program", "")))
                if prog != PUMP_PROGRAM:
                    continue
                ix_data_raw = ix.get("data") or ix.get("data")
                if not ix_data_raw:
                    continue
                try:
                    ix_data = bytes.fromhex(ix_data_raw) if isinstance(ix_data_raw, str) and all(c in "0123456789abcdefABCDEF" for c in ix_data_raw) else None
                    if ix_data is None:
                        ix_data = base58.b58decode(ix_data_raw) if len(ix_data_raw) == 88 else bytes.fromhex(ix_data_raw)
                except Exception:
                    try:
                        import base64
                        ix_data = base64.b64decode(ix_data_raw)
                    except Exception:
                        continue

                d = ix_data[:8]
                if d == CREATE_DISCRIMINATOR or d == CREATE_V2_DISCRIMINATOR:
                    total_creates += 1
                    if block_time:
                        create_times.append(block_time)
                        # Check if within last 7/24 hours
                        age_hours = (now - block_time) / 3600
                        if age_hours <= 168:  # 7 days
                            tokens_last_7d += 1
                        if age_hours <= 24:
                            tokens_last_24h += 1

                    # Check for graduation: look for "complete" or "migrate" in instruction or logs
                    logs = [str(l) for l in (meta.get("logMessages") or meta.get("logs") or [])]
                    has_graduate = any("complete" in l.lower() or "migrate" in l.lower() for l in logs)
                    if has_graduate:
                        graduate_count += 1
                    else:
                        # Also check if this is a later tx (not the create itself)
                        # A quick check: does this tx have a "withdraw" instruction?
                        for ix2 in instructions:
                            p2 = str(ix2.get("programId", ix2.get("program", "")))
                            if p2 == PUMP_PROGRAM:
                                ix2_data_raw = ix2.get("data") or ix2.get("data")
                                if ix2_data_raw and len(ix2_data_raw) >= 8:
                                    try:
                                        d2 = bytes.fromhex(ix2_data_raw[:16]) if isinstance(ix2_data_raw, str) else None
                                        if d2 and d2 != CREATE_DISCRIMINATOR and d2 != CREATE_V2_DISCRIMINATOR:
                                            # Non-create instruction = either buy/sell or complete
                                            has_graduate = True
                                            break
                                    except Exception:
                                        pass

        # Compute stats
        graduation_rate = graduate_count / max(total_creates, 1)
        median_time = 0
        if len(create_times) >= 2:
            sorted_times = sorted(create_times)
            gaps = [sorted_times[i+1] - sorted_times[i] for i in range(len(sorted_times)-1)]
            gaps.sort()
            median_gap = gaps[len(gaps)//2] / 3600  # in hours
            median_time = median_gap

        # Compute score (0-1)
        score = 0.5  # baseline
        tags = []

        if wallet_age_days > 30:
            score += 0.2
            tags.append("aged_wallet")
        elif wallet_age_days < 1:
            score -= 0.2
            tags.append("fresh_wallet")

        if graduation_rate > 0.01:  # 1% — great
            score += 0.2
            tags.append("high_graduation")
        elif graduation_rate > 0.001:  # 0.1% — decent
            score += 0.1
            tags.append("moderate_graduation")

        if total_creates > 100:
            score -= 0.1
            tags.append("high_volume_creator")
        if tokens_last_24h > 10:
            score -= 0.1
            tags.append("high_frequency")

        if median_time > 24:  # >1 day between launches
            score += 0.1
            tags.append("measured_pace")

        score = max(0.0, min(1.0, score))

        return CreatorProfile(
            address=creator, wallet_age_days=wallet_age_days,
            total_creates=total_creates, total_graduates=graduate_count,
            graduation_rate=graduation_rate,
            tokens_last_7d=tokens_last_7d, tokens_last_24h=tokens_last_24h,
            median_time_between_launches_hours=median_time,
            score=score, tags=tags,
        )