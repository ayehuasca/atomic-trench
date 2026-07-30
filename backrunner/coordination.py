"""Cross-process attempt locking and durable no-submit shadow evidence."""

import json
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self


class ActiveAttemptError(RuntimeError):
    """Another process already owns the single-attempt lock."""


class DuplicateCandidateError(RuntimeError):
    """A trigger signature already exists in durable evidence."""


class AttemptLock:
    """Fail-closed filesystem lock that can only be released by its owner."""

    def __init__(self, path: Path, *, candidate_id: str) -> None:
        self.path = path
        self.candidate_id = candidate_id
        self._token: str | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        payload = {
            "candidate_id": self.candidate_id,
            "pid": os.getpid(),
            "acquired_at": datetime.now(UTC).isoformat(),
            "owner_token": token,
        }
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ActiveAttemptError(f"active attempt lock exists: {self.path}") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            self.path.unlink(missing_ok=True)
            raise
        self._token = token
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._token is None:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("attempt lock became unreadable; refusing unsafe release") from error
        if payload.get("owner_token") != self._token:
            raise RuntimeError("attempt lock ownership changed; refusing unsafe release")
        self.path.unlink()
        self._token = None


@dataclass(frozen=True)
class ShadowEvidenceRecord:
    signature: str
    observed_at: str
    simulation_slot: int
    simulation_succeeded: bool
    wallet_net_lamports: int
    rejection_reasons: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ShadowEvidenceRecord":
        return cls(
            signature=str(payload["signature"]),
            observed_at=str(payload["observed_at"]),
            simulation_slot=int(payload["simulation_slot"]),
            simulation_succeeded=bool(payload["simulation_succeeded"]),
            wallet_net_lamports=int(payload["wallet_net_lamports"]),
            rejection_reasons=tuple(str(value) for value in payload["rejection_reasons"]),
        )


@dataclass(frozen=True)
class ShadowEvidenceSummary:
    started_at: str | None
    runtime_hours: float
    candidate_count: int
    succeeded_count: int
    wallet_net_lamports: int

    def ready(self, *, minimum_hours: float, minimum_candidates: int) -> bool:
        return (
            self.runtime_hours >= minimum_hours
            and self.candidate_count >= minimum_candidates
            and self.wallet_net_lamports > 0
        )


class ShadowEvidence:
    """Atomically persisted, signature-deduplicated shadow campaign evidence."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = path.with_suffix(path.suffix + ".lock")

    def _load_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": 1,
                "runtime_seconds": 0.0,
                "last_heartbeat_at": None,
                "records": [],
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload["records"], list):
                raise TypeError("records must be a list")
            float(payload["runtime_seconds"])
            last_heartbeat = payload["last_heartbeat_at"]
            if last_heartbeat is not None:
                datetime.fromisoformat(str(last_heartbeat))
            return payload
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"shadow evidence is unreadable: {self.path}") from exc

    @staticmethod
    def _records(payload: dict[str, Any]) -> list[ShadowEvidenceRecord]:
        return [ShadowEvidenceRecord.from_dict(value) for value in payload["records"]]

    def _save_payload(self, payload: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def contains(self, signature: str) -> bool:
        return any(
            record.signature == signature
            for record in self._records(self._load_payload())
        )

    def record(self, record: ShadowEvidenceRecord) -> None:
        if not record.signature:
            raise ValueError("shadow evidence signature cannot be empty")
        with AttemptLock(self._write_lock, candidate_id="shadow-evidence-write"):
            payload = self._load_payload()
            records = self._records(payload)
            if any(existing.signature == record.signature for existing in records):
                raise DuplicateCandidateError(
                    f"candidate signature already recorded: {record.signature}"
                )
            records.append(record)
            payload["records"] = [asdict(value) for value in records]
            self._save_payload(payload)

    def heartbeat(
        self,
        *,
        at: datetime | None = None,
        maximum_gap_seconds: float = 120.0,
    ) -> None:
        current = at or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("heartbeat timestamp must include a timezone")
        if maximum_gap_seconds <= 0:
            raise ValueError("maximum heartbeat gap must be positive")
        with AttemptLock(self._write_lock, candidate_id="shadow-evidence-heartbeat"):
            payload = self._load_payload()
            previous_raw = payload["last_heartbeat_at"]
            if previous_raw is not None:
                previous = datetime.fromisoformat(str(previous_raw))
                elapsed = (current - previous).total_seconds()
                if elapsed < 0:
                    raise ValueError("heartbeat timestamp moved backwards")
                if elapsed <= maximum_gap_seconds:
                    payload["runtime_seconds"] = float(payload["runtime_seconds"]) + elapsed
            payload["last_heartbeat_at"] = current.isoformat()
            self._save_payload(payload)

    def summary(self) -> ShadowEvidenceSummary:
        payload = self._load_payload()
        records = self._records(payload)
        started_at: str | None = None
        if records:
            timestamps = [datetime.fromisoformat(record.observed_at) for record in records]
            if any(value.tzinfo is None for value in timestamps):
                raise RuntimeError("shadow evidence timestamps must include a timezone")
            started_at = min(timestamps).isoformat()
        return ShadowEvidenceSummary(
            started_at=started_at,
            runtime_hours=round(float(payload["runtime_seconds"]) / 3600, 6),
            candidate_count=len(records),
            succeeded_count=sum(record.simulation_succeeded for record in records),
            wallet_net_lamports=sum(record.wallet_net_lamports for record in records),
        )
