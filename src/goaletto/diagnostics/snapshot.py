"""Snapshot rows for read-only diagnostics.

Every row carries routing UUIDs and bounded status classes only. The snapshot
never contains Telegram chat IDs, message IDs, full payloads, message text,
tokens, DSNs or exception text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

#: Calendar job kinds handled by ``application.match_runtime``. Mirrors
#: ``RUNTIME_KINDS`` without importing application code into diagnostics; the
#: unit suite asserts the two sets stay equal.
SUPPORTED_JOB_KINDS = frozenset(
    {
        "season.open",
        "match.remind",
        "match.lock_lineup",
        "match.start",
        "match.tick",
        "match.second_half_start",
        "match.finalize_result",
    }
)

#: Outbox attempts after which a row is terminally failed by the sender.
OUTBOX_MAX_ATTEMPTS = 8


@dataclass(frozen=True, kw_only=True)
class JobRow:
    id: UUID
    kind: str
    status: str
    due_at: datetime
    attempts: int
    last_error: str | None
    season_id: UUID | None
    fixture_id: UUID | None
    tick_number: int | None


@dataclass(frozen=True, kw_only=True)
class OutboxRow:
    id: UUID
    status: str
    due_at: datetime
    attempts: int
    last_error: str | None
    leased_until: datetime | None
    kind: str | None
    stream_key: str | None


@dataclass(frozen=True, kw_only=True)
class BoardRow:
    key: str
    fixture_id: UUID | None
    ownership_id: UUID | None
    leased_until: datetime | None
    retry_at: datetime | None
    revision: int


@dataclass(frozen=True, kw_only=True)
class AttemptInfo:
    """Latest delivery attempt per outbox row, used for unknown-outcome triage."""

    outbox_id: UUID
    attempt: int
    outcome: str


@dataclass(frozen=True, kw_only=True)
class FixtureRow:
    id: UUID
    season_id: UUID
    status: str
    scheduled_start_at: datetime
    lineup_lock_at: datetime
    actual_start_at: datetime | None
    actual_finish_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class MatchRow:
    id: UUID
    fixture_id: UUID
    phase: str
    completed_tick: int
    next_due_at: datetime | None
    tick_numbers: tuple[int, ...]
    paused_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class ResultRow:
    match_id: UUID
    fixture_id: UUID


@dataclass(frozen=True, kw_only=True)
class SeasonRow:
    id: UUID
    status: str


@dataclass(frozen=True, kw_only=True)
class AppearanceStock:
    bank: str
    total: int
    used: int
    free: int


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    """One bounded, read-only view of runtime state at ``now``."""

    now: datetime
    jobs: tuple[JobRow, ...] = ()
    jobs_truncated: bool = False
    outbox: tuple[OutboxRow, ...] = ()
    outbox_truncated: bool = False
    boards: tuple[BoardRow, ...] = ()
    boards_truncated: bool = False
    attempts: tuple[AttemptInfo, ...] = ()
    attempts_truncated: bool = False
    fixtures: tuple[FixtureRow, ...] = ()
    fixtures_truncated: bool = False
    matches: tuple[MatchRow, ...] = ()
    matches_truncated: bool = False
    ticks_truncated: bool = False
    results: tuple[ResultRow, ...] = ()
    results_truncated: bool = False
    seasons: tuple[SeasonRow, ...] = ()
    seasons_truncated: bool = False
    appearance: tuple[AppearanceStock, ...] = ()
    incomplete_scopes: tuple[str, ...] = field(default_factory=tuple)
