"""Synthetic diagnostics checks: normal states, damaged states, no false alarms.

No database is used here; every snapshot is built in memory. The suite proves:

* an intentionally held calendar raises no overdue/late alarm;
* a normal half-time wait raises no tick-late alarm;
* explicit pause/postpone raises info, never an accident alarm;
* overdue jobs, late ticks, expired leases, unknown outcomes, missing
  results and tick gaps raise precise reason codes with the needed UUIDs;
* truncated coverage suppresses proof-requiring findings instead of
  declaring partial data damage;
* collectors only execute reads and reports never leak secrets.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from goaletto.diagnostics.checks import (
    FINDING_CODES,
    collect_report,
    finding_to_dict,
    render_human,
    report_to_dict,
)
from goaletto.diagnostics.snapshot import (
    AppearanceStock,
    AttemptInfo,
    BoardRow,
    FixtureRow,
    JobRow,
    MatchRow,
    OutboxRow,
    ResultRow,
    SeasonRow,
    Snapshot,
)

NOW = datetime(2026, 10, 17, 18, 0, 0, tzinfo=UTC)
SEASON = uuid4()
FIXTURE = uuid4()
MATCH = uuid4()
OUTBOX = uuid4()


_UNSET: object = object()


def _job(
    *,
    job_id: UUID | None = None,
    kind: str = "match.tick",
    status: str = "pending",
    due_at: datetime | None = None,
    season_id: UUID | None = None,
    fixture_id: UUID | None | object = _UNSET,
    tick: int | None = 1,
    last_error: str | None = None,
    attempts: int = 0,
) -> JobRow:
    resolved_fixture: UUID | None = FIXTURE if fixture_id is _UNSET else fixture_id  # type: ignore[assignment]
    return JobRow(
        id=job_id or uuid4(),
        kind=kind,
        status=status,
        due_at=due_at or NOW,
        attempts=attempts,
        last_error=last_error,
        season_id=season_id or SEASON,
        fixture_id=resolved_fixture,
        tick_number=tick,
    )


def _fixture(
    *,
    fixture_id: UUID | None = None,
    status: str = "scheduled",
    season_id: UUID | None = None,
    kickoff: datetime | None = None,
    started: bool = False,
    finished: bool = False,
) -> FixtureRow:
    kickoff = kickoff or NOW
    return FixtureRow(
        id=fixture_id or uuid4(),
        season_id=season_id or SEASON,
        status=status,
        scheduled_start_at=kickoff,
        lineup_lock_at=kickoff - timedelta(minutes=1),
        actual_start_at=kickoff if started or status in {"live", "paused", "finished"} else None,
        actual_finish_at=(kickoff + timedelta(minutes=33)) if finished else None,
    )


def _match(
    *,
    match_id: UUID | None = None,
    fixture_id: UUID | None = None,
    phase: str = "first_half",
    completed: int = 0,
    next_due: datetime | None = None,
    ticks: tuple[int, ...] = (),
) -> MatchRow:
    return MatchRow(
        id=match_id or uuid4(),
        fixture_id=fixture_id or FIXTURE,
        phase=phase,
        completed_tick=completed,
        next_due_at=next_due,
        tick_numbers=ticks,
        paused_at=NOW if phase == "paused" else None,
    )


def _snapshot(**overrides: object) -> Snapshot:
    base: dict[str, object] = {"now": NOW}
    base.update(overrides)
    return Snapshot(**base)  # type: ignore[arg-type]


def _codes(snapshot: Snapshot, **kwargs: object) -> dict[str, int]:
    report = collect_report(snapshot, **kwargs)  # type: ignore[arg-type]
    counts: dict[str, int] = {}
    for finding in report.findings:
        counts[finding.code] = finding.count
    return counts


def test_supported_kinds_match_runtime() -> None:
    from goaletto.application.match_runtime import RUNTIME_KINDS
    from goaletto.diagnostics.snapshot import SUPPORTED_JOB_KINDS

    assert SUPPORTED_JOB_KINDS == RUNTIME_KINDS


def test_finding_codes_cover_expected_set() -> None:
    assert "JOBS_PENDING_OVERDUE" in FINDING_CODES
    assert "OUTBOX_LEASE_EXPIRED" in FINDING_CODES
    assert "OUTBOX_UNKNOWN_NEEDS_RECONCILIATION" in FINDING_CODES
    assert "MATCH_TICK_LATE" in FINDING_CODES
    assert "FIXTURE_FINISHED_WITHOUT_RESULT" in FINDING_CODES
    assert "APPEARANCE_STOCK_LOW" in FINDING_CODES
    assert "DIAGNOSTICS_INCOMPLETE_TRUNCATED" in FINDING_CODES
    assert FINDING_CODES["JOBS_HELD_AWAITING_ACTIVATION"][0] == "info"
    assert FINDING_CODES["MATCH_HALFTIME_WAIT_NORMAL"][0] == "info"
    assert FINDING_CODES["MATCH_PAUSED_OPERATOR"][0] == "info"


def test_held_calendar_no_false_alarm() -> None:
    season = SeasonRow(id=SEASON, status="draft")
    jobs = tuple(
        _job(kind=kind, status="held", tick=None if kind != "match.tick" else 1)
        for kind in (
            "season.open",
            "match.remind",
            "match.lock_lineup",
            "match.start",
            "match.second_half_start",
        )
    ) + (_job(kind="season.rollover", status="held", fixture_id=None, tick=None),)
    codes = _codes(_snapshot(jobs=jobs, seasons=(season,)))
    assert codes.get("JOBS_HELD_AWAITING_ACTIVATION") == 5
    assert codes.get("JOBS_HELD_ROLLOVER") == 1
    for alarm in (
        "JOBS_PENDING_OVERDUE",
        "MATCH_TICK_LATE",
        "FIXTURE_FINISHED_WITHOUT_RESULT",
        "JOBS_FAILED_INVALID_STATE",
        "JOBS_FAILED_UNSUPPORTED_KIND",
        "OUTBOX_LEASE_EXPIRED",
        "OUTBOX_UNKNOWN_NEEDS_RECONCILIATION",
    ):
        assert alarm not in codes


def test_halftime_normal_no_false_alarm() -> None:
    fixture = _fixture(fixture_id=FIXTURE, status="live", started=True)
    match = _match(
        match_id=MATCH,
        fixture_id=FIXTURE,
        phase="half_time",
        completed=15,
        next_due=NOW + timedelta(seconds=60),
        ticks=tuple(range(1, 16)),
    )
    job = _job(
        kind="match.second_half_start",
        status="pending",
        due_at=NOW + timedelta(seconds=60),
        tick=None,
    )
    codes = _codes(_snapshot(fixtures=(fixture,), matches=(match,), jobs=(job,)))
    assert codes.get("MATCH_HALFTIME_WAIT_NORMAL") == 1
    assert "MATCH_TICK_LATE" not in codes
    assert "JOBS_PENDING_OVERDUE" not in codes


def test_paused_no_false_alarm() -> None:
    fixture = _fixture(fixture_id=FIXTURE, status="paused", started=True)
    match = _match(match_id=MATCH, fixture_id=FIXTURE, phase="paused", completed=10)
    jobs = (
        _job(status="pending", due_at=NOW - timedelta(hours=2)),
        _job(status="pending", due_at=NOW - timedelta(hours=1), tick=11),
    )
    codes = _codes(_snapshot(fixtures=(fixture,), matches=(match,), jobs=jobs))
    assert codes.get("MATCH_PAUSED_OPERATOR") == 1
    assert codes.get("JOBS_DEFERRED_BY_PAUSE") == 2
    assert "JOBS_PENDING_OVERDUE" not in codes
    assert "MATCH_TICK_LATE" not in codes


def test_overdue_jobs_and_tick_late_carry_uuids_and_age() -> None:
    job_id = uuid4()
    job = _job(job_id=job_id, status="pending", due_at=NOW - timedelta(seconds=600), tick=7)
    fixture = _fixture(fixture_id=FIXTURE, status="live", started=True)
    match = _match(
        match_id=MATCH,
        fixture_id=FIXTURE,
        phase="second_half",
        completed=16,
        next_due=NOW - timedelta(seconds=500),
        ticks=tuple(range(1, 17)),
    )
    report = collect_report(_snapshot(jobs=(job,), fixtures=(fixture,), matches=(match,)))
    by_code = {finding.code: finding for finding in report.findings}
    overdue = by_code["JOBS_PENDING_OVERDUE"]
    assert overdue.count == 1
    assert str(job_id) in overdue.job_ids
    assert str(FIXTURE) in overdue.fixture_ids
    assert overdue.max_age_seconds is not None and overdue.max_age_seconds >= 600
    late = by_code["MATCH_TICK_LATE"]
    assert str(FIXTURE) in late.fixture_ids
    assert str(MATCH) in late.match_ids


def test_explicit_threshold_is_respected() -> None:
    job = _job(status="pending", due_at=NOW - timedelta(seconds=60))
    assert "JOBS_PENDING_OVERDUE" not in _codes(_snapshot(jobs=(job,)))
    assert "JOBS_PENDING_OVERDUE" in _codes(_snapshot(jobs=(job,)), late_threshold_seconds=30)
    report = collect_report(_snapshot(jobs=(job,)), late_threshold_seconds=30)
    assert report.late_threshold_seconds == 30


def test_failed_jobs_map_to_distinct_codes() -> None:
    jobs = (
        _job(status="failed", last_error="invalid_state", attempts=1),
        _job(status="failed", last_error="unsupported_job_kind", attempts=1),
        _job(status="failed", last_error="mystery", attempts=3),
    )
    codes = _codes(_snapshot(jobs=jobs))
    assert codes.get("JOBS_FAILED_INVALID_STATE") == 1
    assert codes.get("JOBS_FAILED_UNSUPPORTED_KIND") == 1
    assert codes.get("JOBS_FAILED_OTHER") == 1


def test_expired_leases_flagged_active_lease_spared() -> None:
    expired = OutboxRow(
        id=OUTBOX,
        status="pending",
        due_at=NOW - timedelta(minutes=10),
        attempts=2,
        last_error="unknown_outcome",
        leased_until=NOW - timedelta(seconds=5),
        kind="match_board",
        stream_key="live:v1:x",
    )
    active = OutboxRow(
        id=uuid4(),
        status="pending",
        due_at=NOW - timedelta(minutes=10),
        attempts=1,
        last_error=None,
        leased_until=NOW + timedelta(seconds=60),
        kind="match_board",
        stream_key="live:v1:y",
    )
    attempts = (AttemptInfo(outbox_id=OUTBOX, attempt=2, outcome="unknown"),)
    report = collect_report(_snapshot(outbox=(expired, active), attempts=attempts))
    by_code = {finding.code: finding for finding in report.findings}
    assert str(OUTBOX) in by_code["OUTBOX_LEASE_EXPIRED"].outbox_ids
    assert "unknown" in by_code["OUTBOX_LEASE_EXPIRED"].summary


def test_unknown_exhausted_needs_reconciliation_never_sent() -> None:
    row = OutboxRow(
        id=OUTBOX,
        status="failed",
        due_at=NOW - timedelta(hours=1),
        attempts=8,
        last_error="unknown_outcome",
        leased_until=None,
        kind="match_result",
        stream_key=None,
    )
    report = collect_report(_snapshot(outbox=(row,)))
    by_code = {finding.code: finding for finding in report.findings}
    assert by_code["OUTBOX_UNKNOWN_NEEDS_RECONCILIATION"].count == 1
    assert str(OUTBOX) in by_code["OUTBOX_UNKNOWN_NEEDS_RECONCILIATION"].outbox_ids
    blob = " ".join(
        f"{finding.code} {finding.severity} {finding.summary}" for finding in report.findings
    )
    assert "flip to sent" not in blob or "never flip to sent" in blob
    assert " marked sent" not in blob


def test_outbox_backlog_and_terminal_classes() -> None:
    backlog = OutboxRow(
        id=uuid4(),
        status="pending",
        due_at=NOW - timedelta(hours=1),
        attempts=1,
        last_error="transient",
        leased_until=None,
        kind="fixture_reminder",
        stream_key=None,
    )
    blocked = OutboxRow(
        id=uuid4(),
        status="failed",
        due_at=NOW - timedelta(hours=2),
        attempts=3,
        last_error="blocked",
        leased_until=None,
        kind="ownership_reminder",
        stream_key=None,
    )
    stale = OutboxRow(
        id=uuid4(),
        status="failed",
        due_at=NOW - timedelta(hours=2),
        attempts=1,
        last_error="superseded",
        leased_until=None,
        kind="match_board",
        stream_key="live:v1:z",
    )
    codes = _codes(_snapshot(outbox=(backlog, blocked, stale)))
    assert codes.get("OUTBOX_BACKLOG_OVERDUE") == 1
    assert codes.get("OUTBOX_FAILED_TERMINAL") == 1
    assert "OUTBOX_UNKNOWN_NEEDS_RECONCILIATION" not in codes


def test_finished_without_result_abandoned_is_normal() -> None:
    finished = _fixture(status="finished", finished=True)
    abandoned = _fixture(status="abandoned", finished=True)
    match = _match(
        fixture_id=finished.id, phase="full_time", completed=30, ticks=tuple(range(1, 31))
    )
    codes = _codes(_snapshot(fixtures=(finished, abandoned), matches=(match,)))
    assert codes.get("FIXTURE_FINISHED_WITHOUT_RESULT") == 1
    report = collect_report(_snapshot(fixtures=(finished, abandoned), matches=(match,)))
    target = next(
        finding for finding in report.findings if finding.code == "FIXTURE_FINISHED_WITHOUT_RESULT"
    )
    assert str(finished.id) in target.fixture_ids
    assert str(abandoned.id) not in target.fixture_ids


def test_fulltime_and_tick_mismatches() -> None:
    scheduled = _fixture(status="scheduled")
    fulltime = _match(
        fixture_id=scheduled.id, phase="full_time", completed=30, ticks=tuple(range(1, 31))
    )
    finished = _fixture(status="finished", finished=True)
    halftime = _match(
        fixture_id=finished.id, phase="half_time", completed=15, ticks=tuple(range(1, 16))
    )
    gapped = _fixture(status="live", started=True)
    gap_match = _match(fixture_id=gapped.id, phase="second_half", completed=5, ticks=(1, 2, 4, 5))
    codes = _codes(
        _snapshot(fixtures=(scheduled, finished, gapped), matches=(fulltime, halftime, gap_match))
    )
    assert codes.get("MATCH_FULLTIME_FIXTURE_NOT_FINISHED") == 1
    assert codes.get("FIXTURE_FINISHED_MATCH_NOT_FULLTIME") == 1
    assert codes.get("TICK_GAP_OR_COUNT_MISMATCH") == 1


def test_complete_match_has_no_tick_finding() -> None:
    fixture = _fixture(status="finished", finished=True)
    match = _match(
        fixture_id=fixture.id, phase="full_time", completed=30, ticks=tuple(range(1, 31))
    )
    result = ResultRow(match_id=match.id, fixture_id=fixture.id)
    codes = _codes(_snapshot(fixtures=(fixture,), matches=(match,), results=(result,)))
    assert "TICK_GAP_OR_COUNT_MISMATCH" not in codes
    assert "FIXTURE_FINISHED_WITHOUT_RESULT" not in codes
    assert "FIXTURE_FINISHED_MATCH_NOT_FULLTIME" not in codes


def test_live_without_match_after_kickoff() -> None:
    live = _fixture(status="live", started=True)
    codes = _codes(_snapshot(fixtures=(live,)))
    assert codes.get("FIXTURE_LIVE_WITHOUT_MATCH") == 1
    scheduled = _fixture(status="scheduled")
    assert "FIXTURE_LIVE_WITHOUT_MATCH" not in _codes(_snapshot(fixtures=(scheduled,)))


def test_appearance_stock_threshold_and_aggregates() -> None:
    stocks = (
        AppearanceStock(bank="players", total=50, used=50, free=0),
        AppearanceStock(bank="staff", total=80, used=10, free=70),
    )
    report = collect_report(_snapshot(appearance=stocks), appearance_low_threshold=60)
    by_code = {finding.code: finding for finding in report.findings}
    assert by_code["APPEARANCE_STOCK_LOW"].count == 50
    assert "players" in by_code["APPEARANCE_STOCK_LOW"].summary
    assert report.aggregates["appearance_stock"] == [
        {"bank": "players", "total": 50, "used": 50, "free": 0},
        {"bank": "staff", "total": 80, "used": 10, "free": 70},
    ]
    healthy = collect_report(
        _snapshot(appearance=(AppearanceStock(bank="players", total=1000, used=1000, free=0),)),
        appearance_low_threshold=100,
    )
    assert "APPEARANCE_STOCK_LOW" not in {finding.code for finding in healthy.findings}
    assert healthy.aggregates["appearance_stock"][0]["free"] == 0


def test_truncated_coverage_suppresses_proof_findings() -> None:
    finished = _fixture(status="finished", finished=True)
    snapshot = _snapshot(
        fixtures=(finished,), fixtures_truncated=True, incomplete_scopes=("fixtures",)
    )
    codes = _codes(snapshot)
    assert codes.get("DIAGNOSTICS_INCOMPLETE_TRUNCATED") == 1
    assert "FIXTURE_FINISHED_WITHOUT_RESULT" not in codes
    assert "FIXTURE_LIVE_WITHOUT_MATCH" not in codes


def test_truncated_fixtures_never_false_alarm_unknown_pause() -> None:
    job = _job(status="pending", due_at=NOW - timedelta(hours=3))
    snapshot = _snapshot(jobs=(job,), fixtures_truncated=True, incomplete_scopes=("fixtures",))
    assert "JOBS_PENDING_OVERDUE" not in _codes(snapshot)


def test_held_after_activation_needs_complete_seasons() -> None:
    season = SeasonRow(id=SEASON, status="active")
    job = _job(kind="match.tick", status="held", tick=3)
    assert "JOBS_HELD_AFTER_ACTIVATION" in _codes(_snapshot(jobs=(job,), seasons=(season,)))
    suppressed = _snapshot(
        jobs=(job,), seasons=(season,), seasons_truncated=True, incomplete_scopes=("seasons",)
    )
    assert "JOBS_HELD_AFTER_ACTIVATION" not in _codes(suppressed)


def test_orphan_board_lease() -> None:
    board = BoardRow(
        key="live:v1:a:b",
        fixture_id=uuid4(),
        ownership_id=uuid4(),
        leased_until=NOW + timedelta(seconds=60),
        retry_at=None,
        revision=10,
    )
    assert "OUTBOX_BOARD_ORPHAN_LEASE" in _codes(_snapshot(boards=(board,)))
    message = OutboxRow(
        id=uuid4(),
        status="pending",
        due_at=NOW,
        attempts=1,
        last_error=None,
        leased_until=NOW + timedelta(seconds=60),
        kind="match_board",
        stream_key="live:v1:a:b",
    )
    assert "OUTBOX_BOARD_ORPHAN_LEASE" not in _codes(_snapshot(boards=(board,), outbox=(message,)))


async def test_collectors_only_read() -> None:
    from goaletto.diagnostics.queries import collect_snapshot

    class _EmptyResult:
        def all(self) -> list[tuple[object, ...]]:
            return []

    class _ReadOnlyConnection:
        def __init__(self) -> None:
            self.executes = 0

        async def execute(self, statement: object) -> _EmptyResult:
            self.executes += 1
            return _EmptyResult()

        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"diagnostics must not touch the write path: {name}")

    connection = _ReadOnlyConnection()
    snapshot = await collect_snapshot(connection, now=NOW, limit=10)  # type: ignore[arg-type]
    assert connection.executes >= 8
    assert snapshot.jobs == () and snapshot.outbox == () and snapshot.appearance == ()
    report = collect_report(snapshot)
    assert report.findings == ()
    assert report.aggregates["jobs_total"] == 0


def test_diagnostics_sources_have_no_write_or_secret_paths() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "goaletto" / "diagnostics"
    sources = {path.name: path.read_text() for path in sorted(root.glob("*.py"))}
    assert {"__init__.py", "snapshot.py", "checks.py", "queries.py", "cli.py"} <= set(sources)
    forbidden = (
        "with_for_update",
        "pg_advisory",
        "session.add",
        ".commit(",
        "record_event",
        "GameActionReceipt",
        "goaletto.application.",
        "goaletto.bot.",
        "goaletto.worker.",
        "chat_id",
        "telegram_message_id",
        "send_text",
        "edit_text",
        "get_secret_value",
        "SecretStr",
        "polling",
    )
    for name, text in sources.items():
        lowered = text.lower()
        for token in forbidden:
            assert token.lower() not in lowered, f"{name} must stay read-only: {token}"
    assert "insert(" not in sources["queries.py"]
    assert "update(" not in sources["queries.py"]
    assert "delete(" not in sources["queries.py"]


def test_reports_carry_no_secrets_or_contents() -> None:
    import json

    job = _job(status="failed", last_error="invalid_state", attempts=1)
    outbox = OutboxRow(
        id=OUTBOX,
        status="failed",
        due_at=NOW - timedelta(hours=1),
        attempts=8,
        last_error="unknown_outcome",
        leased_until=None,
        kind="match_result",
        stream_key=None,
    )
    report = collect_report(_snapshot(jobs=(job,), outbox=(outbox,)))
    assert finding_to_dict(report.findings[0])["code"]
    blob = json.dumps(report_to_dict(report))
    for secret in (
        "chat_id",
        "telegram_message_id",
        "payload",
        "message_text",
        "token",
        "dsn",
        "password",
        "traceback",
        "exception",
    ):
        assert secret not in blob.lower()
    assert str(job.id) in blob
    assert str(OUTBOX) in blob

    human = render_human(report)
    for secret in ("chat_id", "telegram_id", "payload", "bot_token", "dsn", "traceback"):
        assert secret not in human.lower()
    assert str(job.id) in human


def test_cli_parser_exposes_explicit_thresholds() -> None:
    from goaletto.diagnostics.checks import Thresholds, describe_codes
    from goaletto.diagnostics.cli import (
        DEFAULT_APPEARANCE_LOW_THRESHOLD,
        DEFAULT_LATE_THRESHOLD_SECONDS,
        build_parser,
    )

    args = build_parser().parse_args([])
    assert args.late_threshold_seconds == DEFAULT_LATE_THRESHOLD_SECONDS == 180.0
    assert args.appearance_low_threshold == DEFAULT_APPEARANCE_LOW_THRESHOLD
    assert args.format == "human"
    for code in FINDING_CODES:
        assert code in describe_codes()
    with pytest.raises(ValueError):
        Thresholds(late_threshold_seconds=-1)
    with pytest.raises(ValueError):
        Thresholds(appearance_low_threshold=-1)
    with pytest.raises(ValueError):
        Thresholds(row_limit=0)


def _worker_job(kind: str, **overrides: object) -> JobRow:
    base: dict[str, object] = {
        "kind": kind,
        "status": "pending",
        "due_at": NOW + timedelta(minutes=10),
        "season_id": None,
        "fixture_id": None,
        "tick": None,
    }
    base.update(overrides)
    return _job(**base)  # type: ignore[arg-type]


def test_worker_kinds_need_no_fixture_id() -> None:
    from goaletto.diagnostics.snapshot import ALL_KNOWN_JOB_KINDS

    for kind in (
        "ownership.remind",
        "ownership.expire",
        "training.apply",
        "finance.weekly",
        "stadium.complete",
        "market.close",
        "market.cancel_player",
        "system.ping",
    ):
        assert kind in ALL_KNOWN_JOB_KINDS
    jobs = tuple(
        _worker_job(kind)
        for kind in (
            "ownership.remind",
            "training.apply",
            "finance.weekly",
            "stadium.complete",
            "market.close",
            "market.cancel_player",
            "system.ping",
        )
    )
    codes = _codes(_snapshot(jobs=jobs))
    assert "FIXTURE_LIVE_WITHOUT_MATCH" not in codes
    assert "MATCH_TICK_LATE" not in codes
    assert "JOBS_PENDING_OVERDUE" not in codes
    assert "JOBS_UNKNOWN_KIND" not in codes


def test_future_held_finance_is_normal() -> None:
    job = _job(
        kind="finance.weekly",
        status="held",
        due_at=NOW + timedelta(days=2),
        season_id=None,
        fixture_id=None,
        tick=None,
    )
    codes = _codes(_snapshot(jobs=(job,)))
    assert codes.get("JOBS_HELD_FUTURE_NORMAL") == 1
    assert "JOBS_HELD_AFTER_ACTIVATION" not in codes
    assert "JOBS_PENDING_OVERDUE" not in codes


def test_unknown_kind_is_warn_not_calendar_damage() -> None:
    job = _job(
        kind="dragon.fire",
        status="pending",
        due_at=NOW + timedelta(minutes=5),
        season_id=None,
        fixture_id=None,
        tick=None,
    )
    report = collect_report(_snapshot(jobs=(job,)))
    by_code = {finding.code: finding for finding in report.findings}
    assert by_code["JOBS_UNKNOWN_KIND"].severity == "warn"
    assert "JOBS_PENDING_OVERDUE" not in by_code
    assert "FIXTURE_LIVE_WITHOUT_MATCH" not in by_code


def _recovery_world(*, late: bool = False, same_timestamps: bool = False) -> Snapshot:
    fixture_id = FIXTURE
    match_id = MATCH
    if same_timestamps:
        moved = NOW + timedelta(seconds=60)
        previous = NOW - timedelta(seconds=60)
    elif late:
        moved = NOW - timedelta(seconds=600)
        previous = NOW - timedelta(seconds=1200)
    else:
        moved = NOW + timedelta(seconds=60)
        previous = NOW - timedelta(seconds=300)
    fixture = FixtureRow(
        id=fixture_id,
        season_id=SEASON,
        status="live",
        scheduled_start_at=NOW - timedelta(minutes=10),
        lineup_lock_at=NOW - timedelta(minutes=11),
        actual_start_at=NOW - timedelta(minutes=10),
        actual_finish_at=None,
    )
    match = _match(
        match_id=match_id,
        fixture_id=fixture_id,
        phase="second_half",
        completed=16,
        next_due=moved,
        ticks=tuple(range(1, 17)),
    )
    from goaletto.diagnostics.snapshot import RecoveryRow

    recovery = RecoveryRow(
        id=uuid4(),
        match_id=match_id,
        fixture_id=fixture_id,
        checkpoint_tick=16,
        state_hash="hash-16",
        previous_due_at=previous,
        next_due_at=moved,
        recovered_at=previous + timedelta(seconds=10),
        reason="worker_lag",
    )
    job = _job(
        kind="match.tick",
        status="pending",
        due_at=moved,
        season_id=SEASON,
        fixture_id=fixture_id,
        tick=17,
    )
    return _snapshot(fixtures=(fixture,), matches=(match,), jobs=(job,), recoveries=(recovery,))


def test_recovered_future_window_is_normal() -> None:
    codes = _codes(_recovery_world())
    assert codes.get("MATCH_RECOVERED_WINDOW_NORMAL") == 1
    assert "MATCH_TICK_LATE" not in codes
    assert "MATCH_RECOVERY_MISMATCH" not in codes
    assert "JOBS_PENDING_OVERDUE" not in codes


def test_recovered_window_missed_again_is_late() -> None:
    codes = _codes(_recovery_world(late=True))
    assert codes.get("MATCH_TICK_LATE") == 1
    assert codes.get("JOBS_PENDING_OVERDUE") == 1
    assert "MATCH_RECOVERED_WINDOW_NORMAL" not in codes


def test_identical_moved_timestamps_are_consistent() -> None:
    codes = _codes(_recovery_world(same_timestamps=True))
    assert codes.get("MATCH_RECOVERED_WINDOW_NORMAL") == 1
    assert "MATCH_RECOVERY_MISMATCH" not in codes


def test_recovery_future_checkpoint_is_mismatch() -> None:
    from goaletto.diagnostics.snapshot import RecoveryRow

    snapshot = _recovery_world()
    bad = RecoveryRow(
        id=uuid4(),
        match_id=MATCH,
        fixture_id=FIXTURE,
        checkpoint_tick=25,
        state_hash="hash-25",
        previous_due_at=NOW - timedelta(seconds=300),
        next_due_at=NOW + timedelta(seconds=60),
        recovered_at=NOW - timedelta(seconds=290),
        reason="worker_lag",
    )
    codes = _codes(
        _snapshot(
            fixtures=snapshot.fixtures,
            matches=snapshot.matches,
            jobs=snapshot.jobs,
            recoveries=(bad,),
        )
    )
    assert codes.get("MATCH_RECOVERY_MISMATCH") == 1


def _market_world(
    *,
    reserve_status: str = "active",
    reserve_amount: int | None = None,
    auction_status: str = "open",
    with_close_job: bool = True,
    past_deadline: bool = False,
) -> Snapshot:
    from goaletto.diagnostics.snapshot import AuctionRow, BidRow, FinanceAccountRow, ReservationRow

    seller = uuid4()
    buyer = uuid4()
    auction_id = uuid4()
    bid_id = uuid4()
    reservation_id = uuid4()
    amount = 150000
    ends = NOW - timedelta(hours=1) if past_deadline else NOW + timedelta(hours=1)
    auction = AuctionRow(
        id=auction_id,
        seller_club_id=seller,
        status=auction_status,
        ends_at=ends,
        highest_bid_id=bid_id if auction_status == "open" else None,
    )
    bid = BidRow(
        id=bid_id,
        auction_id=auction_id,
        buyer_club_id=buyer,
        amount=amount,
        reservation_id=reservation_id,
    )
    reservation = ReservationRow(
        id=reservation_id,
        club_id=buyer,
        amount=reserve_amount or amount,
        status=reserve_status,
    )
    account = FinanceAccountRow(club_id=buyer, cash=600000, reserved=amount, arrears=0)
    bids = (bid,) if auction_status == "open" else ()
    reservations = (reservation,) if auction_status == "open" else ()
    accounts = (account,) if auction_status == "open" else ()
    jobs: tuple[JobRow, ...] = ()
    if with_close_job and auction_status == "open":
        jobs = (
            JobRow(
                id=uuid4(),
                kind="market.close",
                status="pending",
                due_at=ends + timedelta(seconds=60),
                attempts=0,
                last_error=None,
                season_id=None,
                fixture_id=None,
                tick_number=None,
                auction_id=auction_id,
            ),
        )
    return _snapshot(
        jobs=jobs,
        auctions=(auction,),
        bids=bids,
        reservations=reservations,
        finance_accounts=accounts,
    )


def test_open_auction_healthy_has_no_mismatch() -> None:
    codes = _codes(_market_world())
    assert "MARKET_RESERVATION_MISMATCH" not in codes
    assert "MARKET_CLOSE_JOB_MISSING" not in codes
    assert "FINANCE_RESERVATION_MISMATCH" not in codes


def test_sold_and_cancelled_auctions_are_terminal() -> None:
    from goaletto.diagnostics.snapshot import AuctionRow

    sold = AuctionRow(
        id=uuid4(),
        seller_club_id=uuid4(),
        status="sold",
        ends_at=NOW - timedelta(hours=2),
        highest_bid_id=None,
    )
    cancelled = AuctionRow(
        id=uuid4(),
        seller_club_id=uuid4(),
        status="cancelled",
        ends_at=NOW - timedelta(hours=2),
        highest_bid_id=None,
    )
    codes = _codes(_snapshot(auctions=(sold, cancelled)))
    assert "MARKET_RESERVATION_MISMATCH" not in codes
    assert "MARKET_CLOSE_JOB_MISSING" not in codes


def test_wrong_reserve_is_mismatch() -> None:
    codes = _codes(_market_world(reserve_amount=120000))
    assert codes.get("MARKET_RESERVATION_MISMATCH") == 1
    released = _codes(_market_world(reserve_status="released"))
    assert released.get("MARKET_RESERVATION_MISMATCH") == 1


def test_open_auction_without_close_job_is_missing() -> None:
    codes = _codes(_market_world(with_close_job=False))
    assert codes.get("MARKET_CLOSE_JOB_MISSING") == 1


def test_finance_reserve_exceeds_cash() -> None:
    from goaletto.diagnostics.snapshot import FinanceAccountRow

    account = FinanceAccountRow(club_id=uuid4(), cash=50000, reserved=120000, arrears=0)
    codes = _codes(_snapshot(finance_accounts=(account,)))
    assert codes.get("FINANCE_RESERVE_EXCEEDS_CASH") == 1


def test_finance_sum_mismatch_needs_full_scope() -> None:
    from goaletto.diagnostics.snapshot import FinanceAccountRow, ReservationRow

    club = uuid4()
    account = FinanceAccountRow(club_id=club, cash=600000, reserved=100000, arrears=0)
    reservations = (
        ReservationRow(id=uuid4(), club_id=club, amount=100000, status="active"),
        ReservationRow(id=uuid4(), club_id=club, amount=50000, status="active"),
    )
    codes = _codes(_snapshot(finance_accounts=(account,), reservations=reservations))
    assert codes.get("FINANCE_RESERVATION_MISMATCH") == 1
    truncated = _snapshot(
        finance_accounts=(account,),
        reservations=reservations,
        reservations_truncated=True,
        incomplete_scopes=("reservations",),
    )
    truncated_codes = _codes(truncated)
    assert truncated_codes.get("DIAGNOSTICS_INCOMPLETE_TRUNCATED") == 1
    assert "FINANCE_RESERVATION_MISMATCH" not in truncated_codes


def test_market_truncation_suppresses_mismatch() -> None:
    snapshot = _market_world(reserve_amount=120000)
    truncated = _snapshot(
        jobs=snapshot.jobs,
        auctions=snapshot.auctions,
        bids=snapshot.bids,
        reservations=snapshot.reservations,
        finance_accounts=snapshot.finance_accounts,
        bids_truncated=True,
        incomplete_scopes=("bids",),
    )
    codes = _codes(truncated)
    assert codes.get("DIAGNOSTICS_INCOMPLETE_TRUNCATED") == 1
    assert "MARKET_RESERVATION_MISMATCH" not in codes


def test_market_deferred_by_active_match_is_normal() -> None:
    from goaletto.diagnostics.snapshot import AuctionRow, BidRow

    seller = uuid4()
    buyer = uuid4()
    auction_id = uuid4()
    bid_id = uuid4()
    auction = AuctionRow(
        id=auction_id,
        seller_club_id=seller,
        status="open",
        ends_at=NOW - timedelta(minutes=30),
        highest_bid_id=bid_id,
    )
    bid = BidRow(
        id=bid_id, auction_id=auction_id, buyer_club_id=buyer, amount=150000, reservation_id=uuid4()
    )
    fixture = FixtureRow(
        id=FIXTURE,
        season_id=SEASON,
        status="live",
        scheduled_start_at=NOW - timedelta(minutes=20),
        lineup_lock_at=NOW - timedelta(minutes=21),
        actual_start_at=NOW - timedelta(minutes=20),
        actual_finish_at=None,
        home_club_id=seller,
        away_club_id=uuid4(),
    )
    job = JobRow(
        id=uuid4(),
        kind="market.close",
        status="pending",
        due_at=NOW + timedelta(seconds=60),
        attempts=1,
        last_error=None,
        season_id=None,
        fixture_id=None,
        tick_number=None,
        auction_id=auction_id,
    )
    from goaletto.diagnostics.snapshot import ReservationRow

    reservation = ReservationRow(
        id=bid.reservation_id, club_id=buyer, amount=150000, status="active"
    )
    codes = _codes(
        _snapshot(
            fixtures=(fixture,),
            auctions=(auction,),
            bids=(bid,),
            reservations=(reservation,),
            jobs=(job,),
        )
    )
    assert codes.get("MARKET_CLOSE_DEFERRED_BY_MATCH") == 1
    assert "JOBS_PENDING_OVERDUE" not in codes


def test_new_aggregates_and_routing_ids_present() -> None:
    snapshot = _market_world()
    report = collect_report(snapshot)
    assert report.aggregates["auctions_total"] == 1
    assert report.aggregates["bids_total"] == 1
    assert report.aggregates["recoveries_total"] == 0
    blob = report_to_dict(report)
    assert blob["aggregates"]["finance_accounts_total"] == 1


def test_recovery_checks_the_persisted_checkpoint_hash_and_exact_job():
    from dataclasses import replace

    snapshot = _recovery_world()
    bad = replace(snapshot.recoveries[0], checkpoint_hashes=("different-hash",))
    assert "MATCH_RECOVERY_MISMATCH" in _codes(replace(snapshot, recoveries=(bad,)))
    wrong_job = replace(snapshot.jobs[0], tick_number=25)
    assert "MATCH_RECOVERY_MISMATCH" in _codes(replace(snapshot, jobs=(wrong_job,)))


def test_market_deferral_uses_highest_bid_not_historical_bid():
    from dataclasses import replace

    snapshot = _market_world()
    auction = snapshot.auctions[0]
    winner = snapshot.bids[0]
    old_buyer = uuid4()
    old = replace(winner, id=uuid4(), buyer_club_id=old_buyer, reservation_id=uuid4())
    busy = FixtureRow(
        id=FIXTURE,
        season_id=SEASON,
        status="live",
        scheduled_start_at=NOW - timedelta(minutes=20),
        lineup_lock_at=NOW - timedelta(minutes=21),
        actual_start_at=NOW - timedelta(minutes=20),
        actual_finish_at=None,
        home_club_id=old_buyer,
        away_club_id=uuid4(),
    )
    auction = replace(auction, ends_at=NOW - timedelta(minutes=5))
    snapshot = replace(snapshot, auctions=(auction,), bids=(winner, old), fixtures=(busy,))
    assert "MARKET_CLOSE_DEFERRED_BY_MATCH" not in _codes(snapshot)
    assert "MARKET_CLOSE_DEFERRED_BY_MATCH" in _codes(
        replace(snapshot, fixtures=(replace(busy, home_club_id=winner.buyer_club_id),))
    )


def test_season_filtered_market_reports_coverage_and_keeps_reservation_checks() -> None:
    from dataclasses import replace

    snapshot = _market_world(with_close_job=False, reserve_status="released")
    codes = _codes(replace(snapshot, market_schedule_complete=False))
    assert codes.get("MARKET_SCHEDULE_SCOPE_LIMITED") == 1
    assert "MARKET_CLOSE_JOB_MISSING" not in codes
    assert "MARKET_CLOSE_DEFERRED_BY_MATCH" not in codes
    assert codes.get("MARKET_RESERVATION_MISMATCH") == 1
