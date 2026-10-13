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

#: Worker job kinds handled by ``infrastructure.jobs.process_one_job`` outside
#: the calendar scope. Kept separate from ``SUPPORTED_JOB_KINDS`` because
#: calendar checks (fixture linkage, activation, tick lateness) do not apply:
#: these jobs carry club/plan/auction payloads and never a fixture reference.
#: Mirrors the ``RUNTIME_KINDS | {...}`` branch in ``infrastructure/jobs.py``.
WORKER_JOB_KINDS = frozenset(
    {
        "ownership.remind",
        "ownership.expire",
        "training.apply",
        "finance.weekly",
        "stadium.complete",
        "market.close",
        "market.cancel_player",
    }
)

#: Trivial liveness jobs with an empty payload; never carry season/fixture refs.
SYSTEM_JOB_KINDS = frozenset({"system.ping"})

#: Every kind the worker claims (calendar + worker + system + rollover).
#: Unknown kinds are reported as ``JOBS_UNKNOWN_KIND`` instead of being
#: mistaken for calendar damage.
ALL_KNOWN_JOB_KINDS = frozenset(
    SUPPORTED_JOB_KINDS | WORKER_JOB_KINDS | SYSTEM_JOB_KINDS | {"season.rollover"}
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
    # Worker routing refs extracted from the job payload (never a fixture).
    # Absence of fixture_id on these kinds is normal, not match damage.
    club_id: UUID | None = None
    auction_id: UUID | None = None
    plan_id: UUID | None = None
    expansion_id: UUID | None = None
    person_id: UUID | None = None
    ownership_id: UUID | None = None


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
    home_club_id: UUID | None = None
    away_club_id: UUID | None = None


@dataclass(frozen=True, kw_only=True)
class MatchRow:
    id: UUID
    fixture_id: UUID
    phase: str
    completed_tick: int
    next_due_at: datetime | None
    tick_numbers: tuple[int, ...]
    paused_at: datetime | None
    paused_seconds: int = 0


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
class RecoveryRow:
    """Bounded projection of one ``match_recoveries`` row.

    ``fixture_id`` is resolved via the recovery match at collection time and
    is ``None`` for orphan rows (provable mismatch, never silently dropped).
    """

    id: UUID
    match_id: UUID
    fixture_id: UUID | None
    checkpoint_tick: int
    state_hash: str
    previous_due_at: datetime
    next_due_at: datetime
    recovered_at: datetime
    reason: str


@dataclass(frozen=True, kw_only=True)
class FinanceAccountRow:
    """Cash comes from ``clubs.budget``; reserved/arrears from the account."""

    club_id: UUID
    cash: int
    reserved: int
    arrears: int


@dataclass(frozen=True, kw_only=True)
class ReservationRow:
    id: UUID
    club_id: UUID
    amount: int
    status: str


@dataclass(frozen=True, kw_only=True)
class AuctionRow:
    id: UUID
    seller_club_id: UUID
    status: str
    ends_at: datetime
    highest_bid_id: UUID | None


@dataclass(frozen=True, kw_only=True)
class BidRow:
    id: UUID
    auction_id: UUID
    buyer_club_id: UUID
    amount: int
    reservation_id: UUID


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
    recoveries: tuple[RecoveryRow, ...] = ()
    recoveries_truncated: bool = False
    finance_accounts: tuple[FinanceAccountRow, ...] = ()
    finance_truncated: bool = False
    reservations: tuple[ReservationRow, ...] = ()
    reservations_truncated: bool = False
    auctions: tuple[AuctionRow, ...] = ()
    auctions_truncated: bool = False
    bids: tuple[BidRow, ...] = ()
    bids_truncated: bool = False
    incomplete_scopes: tuple[str, ...] = field(default_factory=tuple)
