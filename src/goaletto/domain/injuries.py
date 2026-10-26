"""Deterministic match injury severity; independent of the engine's RNG stream."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID

LEGACY_POLICY = "match-injury-v1"
POLICY = "match-injury-v2"
DAYS = 7


@dataclass(frozen=True)
class Injury:
    policy: str
    severity: str | None
    days: int


def match_injury(match_id: UUID, seed: int, person_id: UUID, *, policy: str = POLICY) -> Injury:
    if policy == LEGACY_POLICY:
        return Injury(LEGACY_POLICY, None, DAYS)
    if policy != POLICY:
        raise ValueError("Unknown match injury policy")
    digest = sha256(f"{POLICY}:{match_id}:{seed}:{person_id}".encode("ascii")).digest()
    roll = int.from_bytes(digest[:8]) % 10000
    severity, low, high = (
        ("light", 1, 3) if roll < 6500 else ("moderate", 4, 7) if roll < 9500 else ("severe", 8, 14)
    )
    days = low + int.from_bytes(digest[8:16]) % (high - low + 1)
    return Injury(POLICY, severity, days)


def unavailable_until(started_at: datetime, days: int = DAYS) -> datetime:
    if (
        started_at.tzinfo is None
        or started_at.utcoffset() != timedelta(0)
        or type(days) is not int
        or not 1 <= days <= 14
    ):
        raise ValueError("Invalid match injury terms")
    return started_at + timedelta(days=days)
