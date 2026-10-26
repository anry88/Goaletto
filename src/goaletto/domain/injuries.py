"""Explicit seven-day recovery for actual new match injuries; no I/O or clock."""

from datetime import datetime, timedelta

POLICY = "match-injury-v1"
DAYS = 7


def unavailable_until(started_at: datetime, days: int = DAYS) -> datetime:
    if (
        started_at.tzinfo is None
        or started_at.utcoffset() != timedelta(0)
        or type(days) is not int
        or days != DAYS
    ):
        raise ValueError("Invalid match injury terms")
    return started_at + timedelta(days=days)
