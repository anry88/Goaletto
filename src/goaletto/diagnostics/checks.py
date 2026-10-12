"""Pure diagnostic checks over a read-only snapshot.

Every check is a total function of :class:`Snapshot` plus explicit numeric
thresholds. No database access, no clock reads, no network calls.

Severity levels:

* ``info`` — an intentionally inactive calendar, a normal halftime wait, an
  explicit operator pause/postpone, or another expected state. Never an
  accident on its own.
* ``warn`` — needs operator attention but the game can continue (backlog,
  low appearance stock, truncated coverage).
* ``alarm`` — probable damage or stuck runtime work that needs triage.

A truncated (bounded) query is never presented as proof of damage: findings
that require full coverage (missing results, tick gaps, orphan board leases,
held-after-activation) are suppressed while any of their input scopes is
truncated, and ``DIAGNOSTICS_INCOMPLETE_TRUNCATED`` is reported instead.
Existential findings (an observed overdue job, an observed expired lease)
remain valid because the observed row itself is complete evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from goaletto.diagnostics.snapshot import OUTBOX_MAX_ATTEMPTS, SUPPORTED_JOB_KINDS, Snapshot

#: Hard cap on UUID samples per finding, keeping reports bounded and readable.
MAX_SAMPLE_IDS = 20

SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_ALARM = "alarm"

# -- reason codes -----------------------------------------------------------

JOBS_HELD_AWAITING_ACTIVATION = "JOBS_HELD_AWAITING_ACTIVATION"
JOBS_HELD_ROLLOVER = "JOBS_HELD_ROLLOVER"
JOBS_HELD_AFTER_ACTIVATION = "JOBS_HELD_AFTER_ACTIVATION"
JOBS_PENDING_OVERDUE = "JOBS_PENDING_OVERDUE"
JOBS_FAILED_INVALID_STATE = "JOBS_FAILED_INVALID_STATE"
JOBS_FAILED_UNSUPPORTED_KIND = "JOBS_FAILED_UNSUPPORTED_KIND"
JOBS_FAILED_OTHER = "JOBS_FAILED_OTHER"
JOBS_DEFERRED_BY_PAUSE = "JOBS_DEFERRED_BY_PAUSE"
MATCH_TICK_LATE = "MATCH_TICK_LATE"
MATCH_HALFTIME_WAIT_NORMAL = "MATCH_HALFTIME_WAIT_NORMAL"
MATCH_PAUSED_OPERATOR = "MATCH_PAUSED_OPERATOR"
FIXTURE_LIVE_WITHOUT_MATCH = "FIXTURE_LIVE_WITHOUT_MATCH"
OUTBOX_LEASE_EXPIRED = "OUTBOX_LEASE_EXPIRED"
OUTBOX_BACKLOG_OVERDUE = "OUTBOX_BACKLOG_OVERDUE"
OUTBOX_UNKNOWN_NEEDS_RECONCILIATION = "OUTBOX_UNKNOWN_NEEDS_RECONCILIATION"
OUTBOX_FAILED_TERMINAL = "OUTBOX_FAILED_TERMINAL"
OUTBOX_BOARD_ORPHAN_LEASE = "OUTBOX_BOARD_ORPHAN_LEASE"
FIXTURE_FINISHED_WITHOUT_RESULT = "FIXTURE_FINISHED_WITHOUT_RESULT"
MATCH_FULLTIME_FIXTURE_NOT_FINISHED = "MATCH_FULLTIME_FIXTURE_NOT_FINISHED"
FIXTURE_FINISHED_MATCH_NOT_FULLTIME = "FIXTURE_FINISHED_MATCH_NOT_FULLTIME"
TICK_GAP_OR_COUNT_MISMATCH = "TICK_GAP_OR_COUNT_MISMATCH"
APPEARANCE_STOCK_LOW = "APPEARANCE_STOCK_LOW"
DIAGNOSTICS_INCOMPLETE_TRUNCATED = "DIAGNOSTICS_INCOMPLETE_TRUNCATED"

#: Every code the package can emit, with severity and a one-line meaning.
FINDING_CODES: dict[str, tuple[str, str]] = {
    JOBS_HELD_AWAITING_ACTIVATION: (
        SEVERITY_INFO,
        "Held calendar jobs for seasons that were never activated; expected before --apply.",
    ),
    JOBS_HELD_ROLLOVER: (
        SEVERITY_INFO,
        "Held season.rollover jobs; rollover stays held until its own implementation.",
    ),
    JOBS_HELD_AFTER_ACTIVATION: (
        SEVERITY_WARN,
        "Supported held jobs remain for an active season; activation looks incomplete.",
    ),
    JOBS_PENDING_OVERDUE: (
        SEVERITY_ALARM,
        "Pending jobs past due_at plus the explicit lateness threshold.",
    ),
    JOBS_FAILED_INVALID_STATE: (
        SEVERITY_ALARM,
        "Jobs terminally failed with invalid_state; a handler rejected their content.",
    ),
    JOBS_FAILED_UNSUPPORTED_KIND: (
        SEVERITY_ALARM,
        "Jobs terminally failed with unsupported_job_kind; no handler claims the kind.",
    ),
    JOBS_FAILED_OTHER: (SEVERITY_ALARM, "Jobs terminally failed with another error class."),
    JOBS_DEFERRED_BY_PAUSE: (
        SEVERITY_INFO,
        "Pending jobs belonging to explicitly paused fixtures; the worker skips them.",
    ),
    MATCH_TICK_LATE: (
        SEVERITY_ALARM,
        "Live matches whose next due moment is past the explicit lateness threshold.",
    ),
    MATCH_HALFTIME_WAIT_NORMAL: (
        SEVERITY_INFO,
        "Matches in half_time whose second-half transition is not yet late; normal break.",
    ),
    MATCH_PAUSED_OPERATOR: (
        SEVERITY_INFO,
        "Fixtures/matches in explicit operator pause; not a runtime accident.",
    ),
    FIXTURE_LIVE_WITHOUT_MATCH: (
        SEVERITY_ALARM,
        "Live fixtures with a committed kickoff but no match row after lineup lock.",
    ),
    OUTBOX_LEASE_EXPIRED: (
        SEVERITY_ALARM,
        "Pending outbox rows whose delivery lease expired; need sweeper/reclaim triage.",
    ),
    OUTBOX_BACKLOG_OVERDUE: (
        SEVERITY_WARN,
        "Pending outbox rows past due_at plus the explicit lateness threshold.",
    ),
    OUTBOX_UNKNOWN_NEEDS_RECONCILIATION: (
        SEVERITY_ALARM,
        "Exhausted outbox rows with unknown outcome; reconcile manually, never mark sent.",
    ),
    OUTBOX_FAILED_TERMINAL: (
        SEVERITY_WARN,
        "Terminally failed outbox rows with a known outcome class (blocked, invalid, ...).",
    ),
    OUTBOX_BOARD_ORPHAN_LEASE: (
        SEVERITY_WARN,
        "Live-board stream leases blocking delivery with no pending message on the stream.",
    ),
    FIXTURE_FINISHED_WITHOUT_RESULT: (
        SEVERITY_ALARM,
        "Fixtures marked finished with no immutable MatchResult receipt.",
    ),
    MATCH_FULLTIME_FIXTURE_NOT_FINISHED: (
        SEVERITY_ALARM,
        "Matches at full_time whose fixture is not marked finished.",
    ),
    FIXTURE_FINISHED_MATCH_NOT_FULLTIME: (
        SEVERITY_ALARM,
        "Finished fixtures whose match projection is not full_time.",
    ),
    TICK_GAP_OR_COUNT_MISMATCH: (
        SEVERITY_ALARM,
        "Match completed_tick disagrees with the stored tick sequence.",
    ),
    APPEARANCE_STOCK_LOW: (
        SEVERITY_WARN,
        "Free unique appearance identities below the explicit low-stock threshold.",
    ),
    DIAGNOSTICS_INCOMPLETE_TRUNCATED: (
        SEVERITY_WARN,
        "One or more bounded queries hit their limit; coverage-gated findings suppressed.",
    ),
}

#: Outbox failure classes that are normal business dedup, not damage.
OUTBOX_NORMAL_FAILURES = frozenset({"skip_stale", "skip_unavailable", "superseded"})

_NON_TERMINAL_PHASES = frozenset({"first_half", "half_time", "second_half"})


@dataclass(frozen=True, kw_only=True)
class Finding:
    code: str
    severity: str
    summary: str
    count: int
    job_ids: tuple[str, ...] = ()
    fixture_ids: tuple[str, ...] = ()
    match_ids: tuple[str, ...] = ()
    outbox_ids: tuple[str, ...] = ()
    season_ids: tuple[str, ...] = ()
    max_age_seconds: float | None = None


@dataclass(frozen=True, kw_only=True)
class Report:
    now: datetime
    late_threshold_seconds: float
    appearance_low_threshold: int
    row_limit: int
    aggregates: dict[str, Any]
    findings: tuple[Finding, ...] = ()
    truncated: bool = False
    incomplete_scopes: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Thresholds:
    late_threshold_seconds: float = 180.0
    appearance_low_threshold: int = 100
    row_limit: int = 2000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.late_threshold_seconds, (int, float))
            or not math.isfinite(self.late_threshold_seconds)
            or self.late_threshold_seconds < 0
        ):
            raise ValueError("late_threshold_seconds must be a finite number >= 0")
        if type(self.appearance_low_threshold) is not int or self.appearance_low_threshold < 0:
            raise ValueError("appearance_low_threshold must be an int >= 0")
        if type(self.row_limit) is not int or self.row_limit < 1:
            raise ValueError("row_limit must be a positive int")


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")


def _sample_ids(values: tuple[UUID, ...]) -> tuple[str, ...]:
    return tuple(str(value) for value in values[:MAX_SAMPLE_IDS])


def _age_seconds(now: datetime, due_at: datetime) -> float:
    return (now - due_at).total_seconds()


def collect_report(
    snapshot: Snapshot,
    *,
    late_threshold_seconds: float = 180.0,
    appearance_low_threshold: int = 100,
    row_limit: int = 2000,
) -> Report:
    """Evaluate every diagnostic check over ``snapshot`` with explicit thresholds."""
    thresholds = Thresholds(
        late_threshold_seconds=late_threshold_seconds,
        appearance_low_threshold=appearance_low_threshold,
        row_limit=row_limit,
    )
    _require_utc(snapshot.now, "snapshot.now")
    findings: list[Finding] = []
    findings.extend(_check_incomplete(snapshot))
    findings.extend(_check_jobs_held(snapshot))
    findings.extend(_check_jobs_failed(snapshot))
    findings.extend(_check_jobs_overdue(snapshot, thresholds.late_threshold_seconds))
    findings.extend(_check_jobs_deferred_by_pause(snapshot))
    findings.extend(_check_matches(snapshot, thresholds.late_threshold_seconds))
    findings.extend(_check_outbox(snapshot, thresholds.late_threshold_seconds))
    findings.extend(_check_appearance(snapshot, thresholds.appearance_low_threshold))
    aggregates = _aggregates(snapshot, thresholds.late_threshold_seconds)
    truncated = bool(snapshot.incomplete_scopes)
    return Report(
        now=snapshot.now,
        late_threshold_seconds=thresholds.late_threshold_seconds,
        appearance_low_threshold=thresholds.appearance_low_threshold,
        row_limit=thresholds.row_limit,
        aggregates=aggregates,
        findings=tuple(findings),
        truncated=truncated,
        incomplete_scopes=snapshot.incomplete_scopes,
    )


def _check_incomplete(snapshot: Snapshot) -> list[Finding]:
    if not snapshot.incomplete_scopes:
        return []
    scopes = ", ".join(sorted(snapshot.incomplete_scopes))
    return [
        Finding(
            code=DIAGNOSTICS_INCOMPLETE_TRUNCATED,
            severity=SEVERITY_WARN,
            summary=(
                "Bounded queries hit their limit "
                f"({scopes}); coverage-gated findings are suppressed and "
                "nothing missing from the unseen tail is declared damage."
            ),
            count=len(snapshot.incomplete_scopes),
        )
    ]


def _check_jobs_held(snapshot: Snapshot) -> list[Finding]:
    held = [job for job in snapshot.jobs if job.status == "held"]
    if not held:
        return []
    rollover = [job for job in held if job.kind == "season.rollover"]
    rest = [job for job in held if job.kind != "season.rollover"]
    status_by_season = {season.id: season.status for season in snapshot.seasons}
    awaiting: list[UUID] = []
    after_activation: list[UUID] = []
    after_seasons: list[UUID] = []
    for job in rest:
        season_status = status_by_season.get(job.season_id) if job.season_id else None
        if season_status == "active" and job.kind in SUPPORTED_JOB_KINDS:
            after_activation.append(job.id)
            if job.season_id is not None:
                after_seasons.append(job.season_id)
        else:
            awaiting.append(job.id)
    findings: list[Finding] = []
    if awaiting:
        findings.append(
            Finding(
                code=JOBS_HELD_AWAITING_ACTIVATION,
                severity=SEVERITY_INFO,
                summary=(
                    f"{len(awaiting)} held jobs awaiting explicit season activation; "
                    "an unactivated calendar is intended, not an accident."
                ),
                count=len(awaiting),
                job_ids=_sample_ids(tuple(awaiting)),
            )
        )
    if rollover:
        findings.append(
            Finding(
                code=JOBS_HELD_ROLLOVER,
                severity=SEVERITY_INFO,
                summary=(
                    f"{len(rollover)} held season.rollover jobs; rollover stays held until "
                    "its own implementation lands."
                ),
                count=len(rollover),
                job_ids=_sample_ids(tuple(job.id for job in rollover)),
            )
        )
    # Held supported jobs for an active season need full season coverage to
    # prove; a truncated season scope suppresses this finding outright.
    if after_activation and not snapshot.seasons_truncated:
        findings.append(
            Finding(
                code=JOBS_HELD_AFTER_ACTIVATION,
                severity=SEVERITY_WARN,
                summary=(
                    f"{len(after_activation)} supported held jobs remain for active seasons; "
                    "activation looks incomplete."
                ),
                count=len(after_activation),
                job_ids=_sample_ids(tuple(after_activation)),
                season_ids=_sample_ids(tuple(dict.fromkeys(after_seasons))),
            )
        )
    return findings


def _check_jobs_failed(snapshot: Snapshot) -> list[Finding]:
    invalid: list[UUID] = []
    unsupported: list[UUID] = []
    other: list[UUID] = []
    for job in snapshot.jobs:
        if job.status != "failed":
            continue
        if job.last_error == "unsupported_job_kind":
            unsupported.append(job.id)
        elif job.last_error == "invalid_state":
            invalid.append(job.id)
        else:
            other.append(job.id)
    findings: list[Finding] = []
    if invalid:
        findings.append(
            Finding(
                code=JOBS_FAILED_INVALID_STATE,
                severity=SEVERITY_ALARM,
                summary=(
                    f"{len(invalid)} jobs terminally failed with invalid_state; "
                    "a handler rejected their content."
                ),
                count=len(invalid),
                job_ids=_sample_ids(tuple(invalid)),
            )
        )
    if unsupported:
        findings.append(
            Finding(
                code=JOBS_FAILED_UNSUPPORTED_KIND,
                severity=SEVERITY_ALARM,
                summary=(
                    f"{len(unsupported)} jobs terminally failed with unsupported_job_kind; "
                    "no handler claims the kind."
                ),
                count=len(unsupported),
                job_ids=_sample_ids(tuple(unsupported)),
            )
        )
    if other:
        findings.append(
            Finding(
                code=JOBS_FAILED_OTHER,
                severity=SEVERITY_ALARM,
                summary=f"{len(other)} jobs terminally failed with another error class.",
                count=len(other),
                job_ids=_sample_ids(tuple(other)),
            )
        )
    return findings


def _paused_fixture_ids(snapshot: Snapshot) -> frozenset[UUID]:
    return frozenset(fixture.id for fixture in snapshot.fixtures if fixture.status == "paused")


def _check_jobs_overdue(snapshot: Snapshot, late_threshold: float) -> list[Finding]:
    paused = _paused_fixture_ids(snapshot)
    overdue_jobs: list[UUID] = []
    overdue_fixtures: list[UUID] = []
    worst = 0.0
    for job in snapshot.jobs:
        if job.status != "pending":
            continue
        if job.fixture_id is not None and job.fixture_id in paused:
            continue  # Explicit pause explains deferral; reported separately as info.
        if _age_seconds(snapshot.now, job.due_at) <= late_threshold:
            continue
        if job.fixture_id is not None and snapshot.fixtures_truncated:
            known = {fixture.id for fixture in snapshot.fixtures}
            if job.fixture_id not in known and job.fixture_id not in paused:
                # Cannot exclude an unseen pause; never false-alarm on partial coverage.
                continue
        overdue_jobs.append(job.id)
        if job.fixture_id is not None:
            overdue_fixtures.append(job.fixture_id)
        worst = max(worst, _age_seconds(snapshot.now, job.due_at))
    if not overdue_jobs:
        return []
    return [
        Finding(
            code=JOBS_PENDING_OVERDUE,
            severity=SEVERITY_ALARM,
            summary=(
                f"{len(overdue_jobs)} pending jobs overdue beyond {late_threshold:g}s; "
                "oldest age quoted, paused fixtures excluded."
            ),
            count=len(overdue_jobs),
            job_ids=_sample_ids(tuple(overdue_jobs)),
            fixture_ids=_sample_ids(tuple(dict.fromkeys(overdue_fixtures))),
            max_age_seconds=worst,
        )
    ]


def _check_jobs_deferred_by_pause(snapshot: Snapshot) -> list[Finding]:
    paused = _paused_fixture_ids(snapshot)
    if not paused:
        return []
    deferred = [
        job for job in snapshot.jobs if job.status == "pending" and job.fixture_id in paused
    ]
    if not deferred:
        return []
    return [
        Finding(
            code=JOBS_DEFERRED_BY_PAUSE,
            severity=SEVERITY_INFO,
            summary=(
                f"{len(deferred)} pending jobs belong to explicitly paused fixtures; "
                "the worker skips them until resume."
            ),
            count=len(deferred),
            job_ids=_sample_ids(tuple(job.id for job in deferred)),
            fixture_ids=_sample_ids(tuple(dict.fromkeys(paused))),
        )
    ]


def _check_matches(snapshot: Snapshot, late_threshold: float) -> list[Finding]:
    findings: list[Finding] = []
    fixture_by_id = {fixture.id: fixture for fixture in snapshot.fixtures}
    match_by_fixture = {match.fixture_id: match for match in snapshot.matches}
    result_fixtures = {result.fixture_id for result in snapshot.results}
    coverage_complete = not (
        snapshot.fixtures_truncated
        or snapshot.matches_truncated
        or snapshot.ticks_truncated
        or snapshot.results_truncated
    )

    paused_fixtures: list[UUID] = []
    halftime_normal: list[UUID] = []
    halftime_matches: list[UUID] = []
    late_fixtures: list[UUID] = []
    late_matches: list[UUID] = []
    live_without_match: list[UUID] = []
    worst = 0.0

    for fixture in snapshot.fixtures:
        match = match_by_fixture.get(fixture.id)
        if fixture.status == "paused" or (match is not None and match.phase == "paused"):
            paused_fixtures.append(fixture.id)
            continue
        if fixture.status != "live":
            continue
        if match is None:
            if fixture.actual_start_at is not None and coverage_complete:
                live_without_match.append(fixture.id)
            continue
        if match.phase == "half_time":
            if match.next_due_at is not None and (
                _age_seconds(snapshot.now, match.next_due_at) > late_threshold
            ):
                late_fixtures.append(fixture.id)
                late_matches.append(match.id)
                worst = max(worst, _age_seconds(snapshot.now, match.next_due_at))
            else:
                halftime_normal.append(fixture.id)
                halftime_matches.append(match.id)
            continue
        if match.phase in _NON_TERMINAL_PHASES:
            if match.next_due_at is None:
                late_fixtures.append(fixture.id)
                late_matches.append(match.id)
            elif _age_seconds(snapshot.now, match.next_due_at) > late_threshold:
                late_fixtures.append(fixture.id)
                late_matches.append(match.id)
                worst = max(worst, _age_seconds(snapshot.now, match.next_due_at))

    if paused_fixtures:
        findings.append(
            Finding(
                code=MATCH_PAUSED_OPERATOR,
                severity=SEVERITY_INFO,
                summary=(
                    f"{len(paused_fixtures)} fixtures explicitly paused by an operator; "
                    "pause preserves the decision window and is not an accident."
                ),
                count=len(paused_fixtures),
                fixture_ids=_sample_ids(tuple(paused_fixtures)),
            )
        )
    if halftime_normal:
        findings.append(
            Finding(
                code=MATCH_HALFTIME_WAIT_NORMAL,
                severity=SEVERITY_INFO,
                summary=(
                    f"{len(halftime_normal)} matches in a normal half-time break; "
                    "second-half transition not yet late."
                ),
                count=len(halftime_normal),
                fixture_ids=_sample_ids(tuple(halftime_normal)),
                match_ids=_sample_ids(tuple(halftime_matches)),
            )
        )
    if late_fixtures:
        findings.append(
            Finding(
                code=MATCH_TICK_LATE,
                severity=SEVERITY_ALARM,
                summary=(
                    f"{len(late_fixtures)} live matches past their next due moment "
                    f"by more than {late_threshold:g}s."
                ),
                count=len(late_fixtures),
                fixture_ids=_sample_ids(tuple(late_fixtures)),
                match_ids=_sample_ids(tuple(late_matches)),
                max_age_seconds=worst if worst else None,
            )
        )
    if live_without_match:
        findings.append(
            Finding(
                code=FIXTURE_LIVE_WITHOUT_MATCH,
                severity=SEVERITY_ALARM,
                summary=(
                    f"{len(live_without_match)} live fixtures with a committed kickoff "
                    "but no match row after lineup lock."
                ),
                count=len(live_without_match),
                fixture_ids=_sample_ids(tuple(live_without_match)),
            )
        )

    if coverage_complete:
        finished_without_result = [
            fixture.id
            for fixture in snapshot.fixtures
            if fixture.status == "finished" and fixture.id not in result_fixtures
        ]
        if finished_without_result:
            findings.append(
                Finding(
                    code=FIXTURE_FINISHED_WITHOUT_RESULT,
                    severity=SEVERITY_ALARM,
                    summary=(
                        f"{len(finished_without_result)} finished fixtures have no "
                        "immutable MatchResult receipt."
                    ),
                    count=len(finished_without_result),
                    fixture_ids=_sample_ids(tuple(finished_without_result)),
                )
            )
        fulltime_mismatch = [
            match.id
            for fixture_id, match in match_by_fixture.items()
            if match.phase == "full_time"
            and (
                fixture_by_id.get(fixture_id) is None
                or fixture_by_id[fixture_id].status != "finished"
            )
        ]
        if fulltime_mismatch:
            findings.append(
                Finding(
                    code=MATCH_FULLTIME_FIXTURE_NOT_FINISHED,
                    severity=SEVERITY_ALARM,
                    summary=(
                        f"{len(fulltime_mismatch)} matches project full_time while "
                        "their fixture is not finished."
                    ),
                    count=len(fulltime_mismatch),
                    match_ids=_sample_ids(tuple(fulltime_mismatch)),
                )
            )
        finished_not_fulltime = [
            fixture.id
            for fixture in snapshot.fixtures
            if fixture.status == "finished"
            and (
                fixture.id not in match_by_fixture
                or match_by_fixture[fixture.id].phase != "full_time"
            )
        ]
        if finished_not_fulltime:
            findings.append(
                Finding(
                    code=FIXTURE_FINISHED_MATCH_NOT_FULLTIME,
                    severity=SEVERITY_ALARM,
                    summary=(
                        f"{len(finished_not_fulltime)} finished fixtures lack a "
                        "full_time match projection."
                    ),
                    count=len(finished_not_fulltime),
                    fixture_ids=_sample_ids(tuple(finished_not_fulltime)),
                )
            )
        gap_matches = [
            match.id
            for match in snapshot.matches
            if _tick_sequence_broken(match.completed_tick, match.tick_numbers)
        ]
        if gap_matches:
            findings.append(
                Finding(
                    code=TICK_GAP_OR_COUNT_MISMATCH,
                    severity=SEVERITY_ALARM,
                    summary=(
                        f"{len(gap_matches)} matches disagree between completed_tick "
                        "and the stored tick sequence."
                    ),
                    count=len(gap_matches),
                    match_ids=_sample_ids(tuple(gap_matches)),
                )
            )
    return findings


def _tick_sequence_broken(completed_tick: int, tick_numbers: tuple[int, ...]) -> bool:
    unique = sorted(set(tick_numbers))
    if completed_tick < 0 or completed_tick > 30:
        return True
    if len(unique) != len(tick_numbers):
        return True
    return unique != list(range(1, completed_tick + 1))


def _check_outbox(snapshot: Snapshot, late_threshold: float) -> list[Finding]:
    findings: list[Finding] = []
    latest_outcome = {attempt.outbox_id: attempt.outcome for attempt in snapshot.attempts}

    expired: list[UUID] = []
    expired_unknown = 0
    for row in snapshot.outbox:
        if row.status != "pending" or row.leased_until is None:
            continue
        if row.leased_until <= snapshot.now:
            expired.append(row.id)
            if latest_outcome.get(row.id) == "unknown":
                expired_unknown += 1
    if expired:
        summary = (
            f"{len(expired)} pending outbox rows hold expired delivery leases; "
            "reclaim or sweep before any resend."
        )
        if expired_unknown:
            summary += (
                f" {expired_unknown} of them have an unknown latest attempt and "
                "need manual reconciliation first."
            )
        findings.append(
            Finding(
                code=OUTBOX_LEASE_EXPIRED,
                severity=SEVERITY_ALARM,
                summary=summary,
                count=len(expired),
                outbox_ids=_sample_ids(tuple(expired)),
            )
        )

    backlog: list[UUID] = []
    worst = 0.0
    for row in snapshot.outbox:
        if row.status != "pending":
            continue
        if row.leased_until is not None and row.leased_until > snapshot.now:
            continue  # Actively leased to a sender; not backlog.
        if _age_seconds(snapshot.now, row.due_at) <= late_threshold:
            continue
        backlog.append(row.id)
        worst = max(worst, _age_seconds(snapshot.now, row.due_at))
    if backlog:
        findings.append(
            Finding(
                code=OUTBOX_BACKLOG_OVERDUE,
                severity=SEVERITY_WARN,
                summary=(
                    f"{len(backlog)} pending outbox rows overdue beyond "
                    f"{late_threshold:g}s without an active lease."
                ),
                count=len(backlog),
                outbox_ids=_sample_ids(tuple(backlog)),
                max_age_seconds=worst,
            )
        )

    unknown: list[UUID] = []
    terminal: list[UUID] = []
    for row in snapshot.outbox:
        if row.status != "failed":
            continue
        if row.last_error in OUTBOX_NORMAL_FAILURES or row.last_error is None:
            continue  # Stale/superseded dedup is normal sender hygiene.
        if row.last_error == "unknown_outcome" or row.attempts >= OUTBOX_MAX_ATTEMPTS:
            if row.last_error == "unknown_outcome":
                unknown.append(row.id)
            else:
                terminal.append(row.id)
        else:
            terminal.append(row.id)
    if unknown:
        findings.append(
            Finding(
                code=OUTBOX_UNKNOWN_NEEDS_RECONCILIATION,
                severity=SEVERITY_ALARM,
                summary=(
                    f"{len(unknown)} outbox rows exhausted with unknown outcome; "
                    "reconcile with Telegram state manually, never flip to sent."
                ),
                count=len(unknown),
                outbox_ids=_sample_ids(tuple(unknown)),
            )
        )
    if terminal:
        findings.append(
            Finding(
                code=OUTBOX_FAILED_TERMINAL,
                severity=SEVERITY_WARN,
                summary=(
                    f"{len(terminal)} outbox rows terminally failed with a known "
                    "outcome class; see runbook before any retry."
                ),
                count=len(terminal),
                outbox_ids=_sample_ids(tuple(terminal)),
            )
        )

    if not snapshot.boards_truncated and not snapshot.outbox_truncated:
        pending_streams = {row.stream_key for row in snapshot.outbox if row.status == "pending"} - {
            None
        }
        orphaned: list[str] = []
        for board in snapshot.boards:
            board_lease_active = (
                board.leased_until is not None and board.leased_until > snapshot.now
            )
            retry_active = board.retry_at is not None and board.retry_at > snapshot.now
            lease_active = board_lease_active or retry_active
            if lease_active and board.key not in pending_streams:
                orphaned.append(board.key)
        if orphaned:
            findings.append(
                Finding(
                    code=OUTBOX_BOARD_ORPHAN_LEASE,
                    severity=SEVERITY_WARN,
                    summary=(
                        f"{len(orphaned)} live-board stream leases block delivery with "
                        "no pending message on the stream."
                    ),
                    count=len(orphaned),
                )
            )
    return findings


def _check_appearance(snapshot: Snapshot, low_threshold: int) -> list[Finding]:
    findings: list[Finding] = []
    for stock in snapshot.appearance:
        if stock.free < low_threshold:
            findings.append(
                Finding(
                    code=APPEARANCE_STOCK_LOW,
                    severity=SEVERITY_WARN,
                    summary=(
                        f"Appearance bank {stock.bank!r} has {stock.free} free unique "
                        f"identities of {stock.total} (used {stock.used}); below the "
                        f"explicit threshold {low_threshold}. Publish more faces, "
                        "never reuse identities."
                    ),
                    count=stock.free,
                )
            )
    return findings


def _aggregates(snapshot: Snapshot, late_threshold: float) -> dict[str, Any]:
    jobs_by_status: dict[str, int] = {}
    jobs_by_kind: dict[str, int] = {}
    for job in snapshot.jobs:
        jobs_by_status[job.status] = jobs_by_status.get(job.status, 0) + 1
        key = f"{job.kind}:{job.status}"
        jobs_by_kind[key] = jobs_by_kind.get(key, 0) + 1
    pending_overdue = sum(
        1
        for job in snapshot.jobs
        if job.status == "pending" and _age_seconds(snapshot.now, job.due_at) > late_threshold
    )
    oldest_job_age: float | None = None
    for job in snapshot.jobs:
        if job.status == "pending":
            age = _age_seconds(snapshot.now, job.due_at)
            if age > 0 and (oldest_job_age is None or age > oldest_job_age):
                oldest_job_age = age
    outbox_by_status: dict[str, int] = {}
    for row in snapshot.outbox:
        outbox_by_status[row.status] = outbox_by_status.get(row.status, 0) + 1
    fixtures_by_status: dict[str, int] = {}
    for fixture in snapshot.fixtures:
        fixtures_by_status[fixture.status] = fixtures_by_status.get(fixture.status, 0) + 1
    matches_by_phase: dict[str, int] = {}
    for match in snapshot.matches:
        matches_by_phase[match.phase] = matches_by_phase.get(match.phase, 0) + 1
    return {
        "late_threshold_seconds": late_threshold,
        "jobs_total": len(snapshot.jobs),
        "jobs_by_status": dict(sorted(jobs_by_status.items())),
        "jobs_by_kind_status": dict(sorted(jobs_by_kind.items())),
        "jobs_pending_overdue": pending_overdue,
        "jobs_oldest_pending_age_seconds": oldest_job_age,
        "jobs_failed": sum(1 for job in snapshot.jobs if job.status == "failed"),
        "outbox_total": len(snapshot.outbox),
        "outbox_by_status": dict(sorted(outbox_by_status.items())),
        "boards_total": len(snapshot.boards),
        "fixtures_total": len(snapshot.fixtures),
        "fixtures_by_status": dict(sorted(fixtures_by_status.items())),
        "matches_total": len(snapshot.matches),
        "matches_by_phase": dict(sorted(matches_by_phase.items())),
        "results_total": len(snapshot.results),
        "appearance_stock": [
            {"bank": stock.bank, "total": stock.total, "used": stock.used, "free": stock.free}
            for stock in sorted(snapshot.appearance, key=lambda item: item.bank)
        ],
        "seasons_total": len(snapshot.seasons),
    }


def finding_to_dict(finding: Finding) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": finding.code,
        "severity": finding.severity,
        "summary": finding.summary,
        "count": finding.count,
    }
    if finding.job_ids:
        payload["job_ids"] = list(finding.job_ids)
    if finding.fixture_ids:
        payload["fixture_ids"] = list(finding.fixture_ids)
    if finding.match_ids:
        payload["match_ids"] = list(finding.match_ids)
    if finding.outbox_ids:
        payload["outbox_ids"] = list(finding.outbox_ids)
    if finding.season_ids:
        payload["season_ids"] = list(finding.season_ids)
    if finding.max_age_seconds is not None:
        payload["max_age_seconds"] = finding.max_age_seconds
    return payload


def report_to_dict(report: Report) -> dict[str, Any]:
    return {
        "now": report.now.isoformat(),
        "late_threshold_seconds": report.late_threshold_seconds,
        "appearance_low_threshold": report.appearance_low_threshold,
        "row_limit": report.row_limit,
        "truncated": report.truncated,
        "incomplete_scopes": list(report.incomplete_scopes),
        "aggregates": report.aggregates,
        "findings": [finding_to_dict(finding) for finding in report.findings],
    }


def render_human(report: Report) -> str:
    lines = [
        "Goaletto runtime diagnostics (read-only)",
        f"now: {report.now.isoformat()}",
        f"late threshold: {report.late_threshold_seconds:g}s",
        f"appearance low threshold: {report.appearance_low_threshold}",
        f"row limit per scope: {report.row_limit}",
        f"coverage truncated: {'yes' if report.truncated else 'no'}",
        "",
        "Aggregates:",
    ]
    aggregates = report.aggregates
    lines.append(
        f"  jobs: total={aggregates['jobs_total']} by_status={aggregates['jobs_by_status']}"
    )
    lines.append(
        f"  jobs pending overdue>{report.late_threshold_seconds:g}s: "
        f"{aggregates['jobs_pending_overdue']} "
        f"(oldest pending age: {aggregates['jobs_oldest_pending_age_seconds']}s)"
    )
    lines.append(
        f"  outbox: total={aggregates['outbox_total']} "
        f"by_status={aggregates['outbox_by_status']} boards={aggregates['boards_total']}"
    )
    lines.append(
        f"  fixtures: total={aggregates['fixtures_total']} "
        f"by_status={aggregates['fixtures_by_status']}"
    )
    lines.append(
        f"  matches: total={aggregates['matches_total']} "
        f"by_phase={aggregates['matches_by_phase']} results={aggregates['results_total']}"
    )
    lines.append(f"  appearance stock: {aggregates['appearance_stock']}")
    lines.append("")
    if not report.findings:
        lines.append("Findings: none. No damage proven on the observed coverage.")
        return "\n".join(lines) + "\n"
    lines.append(f"Findings ({len(report.findings)}):")
    for finding in sorted(report.findings, key=lambda item: (item.severity, item.code)):
        lines.append(f"  [{finding.severity}] {finding.code} x{finding.count}")
        lines.append(f"    {finding.summary}")
        if finding.max_age_seconds is not None:
            lines.append(f"    max age: {finding.max_age_seconds:.0f}s")
        for label, ids in (
            ("jobs", finding.job_ids),
            ("fixtures", finding.fixture_ids),
            ("matches", finding.match_ids),
            ("outbox", finding.outbox_ids),
            ("seasons", finding.season_ids),
        ):
            if ids:
                shown = ", ".join(ids[:MAX_SAMPLE_IDS])
                lines.append(f"    {label}: {shown}")
    lines.append("")
    lines.append("Mutating recovery is out of scope here; see docs/runtime-diagnostics-runbook.md.")
    return "\n".join(lines) + "\n"


def describe_codes() -> str:
    lines = ["Diagnostic finding codes (finite set):", ""]
    for code in sorted(FINDING_CODES):
        severity, meaning = FINDING_CODES[code]
        lines.append(f"  [{severity}] {code}: {meaning}")
    return "\n".join(lines) + "\n"


__all__ = [
    "APPEARANCE_STOCK_LOW",
    "DIAGNOSTICS_INCOMPLETE_TRUNCATED",
    "FINDING_CODES",
    "FIXTURE_FINISHED_MATCH_NOT_FULLTIME",
    "FIXTURE_FINISHED_WITHOUT_RESULT",
    "FIXTURE_LIVE_WITHOUT_MATCH",
    "JOBS_DEFERRED_BY_PAUSE",
    "JOBS_FAILED_INVALID_STATE",
    "JOBS_FAILED_OTHER",
    "JOBS_FAILED_UNSUPPORTED_KIND",
    "JOBS_HELD_AFTER_ACTIVATION",
    "JOBS_HELD_AWAITING_ACTIVATION",
    "JOBS_HELD_ROLLOVER",
    "JOBS_PENDING_OVERDUE",
    "MATCH_FULLTIME_FIXTURE_NOT_FINISHED",
    "MATCH_HALFTIME_WAIT_NORMAL",
    "MATCH_PAUSED_OPERATOR",
    "MATCH_TICK_LATE",
    "OUTBOX_BACKLOG_OVERDUE",
    "OUTBOX_BOARD_ORPHAN_LEASE",
    "OUTBOX_FAILED_TERMINAL",
    "OUTBOX_LEASE_EXPIRED",
    "OUTBOX_UNKNOWN_NEEDS_RECONCILIATION",
    "TICK_GAP_OR_COUNT_MISMATCH",
    "Finding",
    "Report",
    "Thresholds",
    "collect_report",
    "describe_codes",
    "finding_to_dict",
    "render_human",
    "report_to_dict",
]
