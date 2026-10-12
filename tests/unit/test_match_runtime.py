"""Application transitions with supplied ORM objects/recorded SQL; no database."""

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4, uuid5

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.match_runtime import (
    NotReady,
    activate_season,
    domain_command,
    due_for,
    handle_calendar_job,
    job_refs,
    pause_match,
    postpone_fixture,
    resume_match,
)
from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.match_codec import restore_state, state_document, state_hash
from goaletto.domain.match_types import EnginePolicy, Mentality
from goaletto.infrastructure.models import (
    AcceptedCommand,
    Club,
    Fixture,
    Job,
    Manager,
    Match,
    MatchStage,
    MatchTick,
    OwnershipPeriod,
    Season,
)

NOW = datetime(2026, 10, 17, 18, tzinfo=UTC)


class Rows:
    def __init__(self, rows=()):
        self.rows = rows

    def all(self):
        return self.rows


class Recorder:
    def __init__(self, fixture, match):
        self.fixture = fixture
        self.match = match
        self.statements = []
        self.added = []
        self.events = []
        self.commands = []
        self.flush_frames = []
        self.owners = []
        self.season = Season(id=fixture.season_id, status="scheduled")

    def in_transaction(self):
        return True

    async def scalar(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        return self.fixture if entity is Fixture else self.match if entity is Match else self.season

    async def scalars(self, statement):
        self.statements.append(statement)
        return Rows(self.commands)

    async def execute(self, statement):
        self.statements.append(statement)
        return Rows(self.owners)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_frames.append((list(self.added), [c.status for c in self.commands]))


def setup():
    state = demo_match(7)
    fixture = Fixture(
        id=uuid5(state.match_id, "fixture"),
        season_id=uuid4(),
        home_club_id=state.home.snapshot.club_id,
        away_club_id=state.away.snapshot.club_id,
        scheduled_start_at=NOW,
        lineup_lock_at=NOW - timedelta(minutes=1),
        status="scheduled",
        actual_start_at=None,
        actual_finish_at=None,
    )
    match = Match(
        id=state.match_id,
        fixture_id=fixture.id,
        seed=state.seed,
        engine_version=state.engine_version,
        state_version=1,
        state=state_document(state),
        starting_snapshot=state_document(state),
        completed_tick=0,
        phase="first_half",
        home_goals=0,
        away_goals=0,
        next_due_at=NOW,
        paused_at=None,
        paused_seconds=0,
    )
    return state, fixture, match, Recorder(fixture, match)


def job(fixture, kind, tick=None):
    return Job(
        id=uuid4(),
        dedupe_key="test-job",
        kind=kind,
        status="pending",
        attempts=0,
        payload={
            "version": 1,
            "calendar_version": "league-calendar-v1",
            "season_id": str(fixture.season_id),
            "fixture_id": str(fixture.id),
            "tick_number": tick,
        },
        due_at=NOW,
    )


@pytest.fixture(autouse=True)
def capture_events(monkeypatch):
    async def capture(session, event):
        session.events.append(event)
        return True

    monkeypatch.setattr("goaletto.application.match_runtime.record_event", capture)

    async def finalized(session, fixture_id, at):
        assert session.match.phase == "full_time" and session.fixture.status == "finished"
        assert any(isinstance(t, MatchTick) and t.tick_number == 30 for t in session.added)
        return True

    monkeypatch.setattr("goaletto.application.match_runtime.finalize_result", finalized)


async def run(recorder, kind, tick=None, at=NOW):
    await handle_calendar_job(cast(AsyncSession, recorder), job(recorder.fixture, kind, tick), at)


async def test_entire_regulation_runtime_retries_and_restarts_match_offline_engine():
    initial, fixture, match, recorder = setup()
    await run(recorder, "match.start")
    assert fixture.actual_start_at == NOW and fixture.status == "live"
    for tick in range(1, 31):
        if tick == 16:
            assert match.phase == "half_time"
            await run(recorder, "match.second_half_start", at=NOW + timedelta(minutes=18))
            counts = len(recorder.added), len(recorder.events)
            await run(recorder, "match.second_half_start", at=NOW + timedelta(minutes=18))
            assert counts == (len(recorder.added), len(recorder.events))
        at = due_for(match, fixture, tick)
        if tick == 17:
            # The only durable state supplied after this checkpoint is the saved JSON.
            match.state = state_document(restore_state(match.state))
        await run(recorder, "match.tick", tick, at=at)
        counts = len(recorder.added), len(recorder.events)
        await run(recorder, "match.tick", tick, at=at + timedelta(seconds=10))
        assert counts == (len(recorder.added), len(recorder.events))
    end, _ = simulate_match(initial)
    assert state_hash(restore_state(match.state)) == state_hash(end)
    assert fixture.actual_finish_at == NOW + timedelta(minutes=33)
    assert fixture.status == "finished" and match.next_due_at is None
    ticks = [t for t in recorder.added if isinstance(t, MatchTick)]
    assert len(ticks) == 30
    assert [(ticks[i - 1].tick_number, ticks[i - 1].due_at) for i in (15, 16, 30)] == [
        (15, NOW + timedelta(minutes=15)),
        (16, NOW + timedelta(minutes=19)),
        (30, NOW + timedelta(minutes=33)),
    ]
    assert all(e.origin.value == "system" and e.session_id is None for e in recorder.events)
    assert Counter(e.name for e in recorder.events)["match_tick_committed"] == 30
    assert sum(isinstance(t, MatchStage) and t.stage == "finished" for t in recorder.added) == 1


async def test_late_start_rebases_once_but_late_ticks_do_not_drift():
    _, fixture, match, recorder = setup()
    await run(recorder, "match.start", at=NOW + timedelta(minutes=5))
    assert fixture.actual_start_at == NOW + timedelta(minutes=5)
    update = next(s for s in recorder.statements if getattr(s, "is_update", False))
    assert "jobs.due_at +" in str(update.compile(dialect=postgresql.dialect()))
    assert update.compile().params["due_at_1"] == timedelta(minutes=5)
    await run(recorder, "match.tick", 1, at=NOW + timedelta(minutes=8))
    assert match.next_due_at == NOW + timedelta(minutes=7)
    receipt = next(t for t in recorder.added if isinstance(t, MatchTick))
    assert receipt.due_at == NOW + timedelta(minutes=6)
    assert receipt.committed_at == NOW + timedelta(minutes=8)


async def test_lock_pins_crowd_and_start_never_reads_changed_stadium(monkeypatch):
    state, fixture, _, recorder = setup()
    recorder.match = None
    recorder.season.status = "active"
    recorder.season.seed = 0
    home = Club(id=fixture.home_club_id, name="Home", stadium_capacity=5000, supporters=8000)
    away = Club(id=fixture.away_club_id, name="Away", stadium_capacity=5000, supporters=2500)

    async def scalars(statement):
        recorder.statements.append(statement)
        return Rows((home, away))

    async def get(model, key):
        return recorder.season

    async def snapshot(session, club, at):
        return state.home.snapshot if club.id == home.id else state.away.snapshot

    recorder.scalars = scalars
    recorder.get = get
    monkeypatch.setattr("goaletto.application.match_runtime.team_snapshot", snapshot)
    await run(recorder, "match.lock_lineup", at=fixture.lineup_lock_at)
    locked = next(t for t in recorder.added if isinstance(t, Match))
    recorder.match = locked
    assert restore_state(locked.state).crowd.home_fans == 5000
    assert restore_state(locked.state).crowd.stadium_capacity == 5000
    home.stadium_capacity = 20000
    home.supporters = 20000
    await run(recorder, "match.start")
    assert restore_state(locked.state).crowd.home_fans == 5000
    assert locked.starting_snapshot == locked.state


async def test_future_or_out_of_order_tick_defers_without_changing_checkpoint():
    _, _, match, recorder = setup()
    await run(recorder, "match.start")
    before = match.state
    for tick, at in ((1, NOW + timedelta(seconds=59)), (2, NOW + timedelta(minutes=2))):
        with pytest.raises(NotReady):
            await run(recorder, "match.tick", tick, at=at)
        assert match.state == before and match.completed_tick == 0


async def test_halftime_commands_have_no_fk_to_uncommitted_tick16():
    state, _, match, recorder = setup()
    await run(recorder, "match.start")
    for tick in range(1, 16):
        await run(recorder, "match.tick", tick, at=NOW + timedelta(minutes=tick))
    command = AcceptedCommand(
        id=uuid4(),
        club_id=state.home.snapshot.club_id,
        target_tick_number=16,
        payload_version=1,
        kind="mentality",
        payload={"mentality": "attack"},
        accepted_at=NOW + timedelta(minutes=17),
        status="accepted",
        telegram_update_id=1,
        effect_number=0,
    )
    recorder.commands = [command]
    await run(recorder, "match.second_half_start", at=NOW + timedelta(minutes=18))
    assert command.status == "applied" and command.applied_tick_number is None
    assert match.completed_tick == 15
    assert all(t.tick_number <= 15 for t in recorder.added if isinstance(t, MatchTick))


async def test_abandoned_fixture_keeps_actual_finish_and_field_score_without_standings():
    state, fixture, match, recorder = setup()
    state = replace(
        state, policy=EnginePolicy(yellow_bps=0, direct_red_bps=10000, injury_bps=0, foul_bps=0)
    )
    match.state = state_document(state)
    await run(recorder, "match.start")
    for tick in range(1, 31):
        await run(recorder, "match.tick", tick, at=due_for(match, fixture, tick))
        if fixture.status == "abandoned":
            break
    assert fixture.actual_finish_at == due_for(match, fixture, match.completed_tick)
    assert match.phase == "abandoned" and match.completed_tick < 30
    last = next(t for t in reversed(recorder.added) if isinstance(t, MatchTick))
    assert last.tick_number * 3 - 2 <= last.game_minute <= last.tick_number * 3
    assert all(
        getattr(s, "table", None) is None or s.table.name != "standings"
        for s in recorder.statements
    )
    assert recorder.events[-1].name == "fixture_abandoned"


async def test_postponement_moves_future_jobs_before_lock_and_never_resets_a_locked_match():
    _, fixture, _, recorder = setup()
    recorder.match = None
    new = NOW + timedelta(days=1)
    await postpone_fixture(cast(AsyncSession, recorder), fixture.id, new, NOW - timedelta(hours=1))
    assert fixture.scheduled_start_at == new and fixture.lineup_lock_at == new - timedelta(
        minutes=1
    )
    assert recorder.statements[-1].compile().params["due_at_1"] == timedelta(days=1)
    recorder.match = object()
    with pytest.raises(ValueError, match="unlocked"):
        await postpone_fixture(
            cast(AsyncSession, recorder), fixture.id, new + timedelta(days=1), NOW
        )


async def test_pause_resume_preserves_original_snapshot_and_adds_only_explicit_pause():
    _, fixture, match, recorder = setup()
    await run(recorder, "match.start")
    await run(recorder, "match.tick", 1, at=NOW + timedelta(minutes=1))
    before = match.state
    await pause_match(cast(AsyncSession, recorder), fixture.id, NOW + timedelta(seconds=90))
    assert fixture.status == match.phase == "paused"
    with pytest.raises(NotReady):
        await run(recorder, "match.tick", 2, at=NOW + timedelta(minutes=2))
    await resume_match(cast(AsyncSession, recorder), fixture.id, NOW + timedelta(seconds=390))
    assert match.paused_seconds == 300 and match.state == before
    assert due_for(match, fixture, 2) == NOW + timedelta(minutes=7)
    counts = len(recorder.statements)
    await resume_match(cast(AsyncSession, recorder), fixture.id, NOW + timedelta(minutes=10))
    assert len(recorder.statements) == counts + 2 and match.paused_seconds == 300


async def test_command_application_inserts_tick_before_foreign_key_and_correlates_event():
    state, fixture, _, recorder = setup()
    await run(recorder, "match.start")
    command = AcceptedCommand(
        id=uuid4(),
        club_id=state.home.snapshot.club_id,
        target_tick_number=1,
        payload_version=1,
        kind="mentality",
        payload={"mentality": "attack"},
        accepted_at=NOW + timedelta(seconds=30),
        status="accepted",
        telegram_update_id=1,
        effect_number=0,
    )
    recorder.commands = [command]
    await run(recorder, "match.tick", 1, at=NOW + timedelta(minutes=1))
    assert command.status == "applied" and command.applied_tick_number == 1
    tick_flush = next(
        frame for frame in recorder.flush_frames if any(isinstance(x, MatchTick) for x in frame[0])
    )
    assert tick_flush[1] == ["accepted"]
    applied = next(e for e in recorder.events if e.name == "match_command_applied")
    assert applied.command_id == command.id and applied.fixture_id == fixture.id
    assert restore_state(recorder.match.state).home.mentality == Mentality.ATTACK


async def test_command_after_deadline_is_cancelled_before_domain_effect():
    state, _, _, recorder = setup()
    await run(recorder, "match.start")
    command = AcceptedCommand(
        id=uuid4(),
        club_id=state.home.snapshot.club_id,
        target_tick_number=1,
        payload_version=1,
        kind="mentality",
        payload={"mentality": "attack"},
        accepted_at=NOW + timedelta(seconds=60),
        status="accepted",
        telegram_update_id=1,
        effect_number=0,
    )
    recorder.commands = [command]
    await run(recorder, "match.tick", 1, at=NOW + timedelta(minutes=2))
    assert command.status == "cancelled"
    assert restore_state(recorder.match.state).home.mentality == Mentality.BALANCED


async def test_notifications_use_outbox_and_never_record_delivery_success():
    _, fixture, _, recorder = setup()
    manager = Manager(id=uuid4(), telegram_id=123)
    owner = OwnershipPeriod(id=uuid4(), manager_id=manager.id, club_id=fixture.home_club_id)
    recorder.owners = [(owner, manager)]
    await run(recorder, "match.start")
    insert = next(s for s in recorder.statements if getattr(s, "is_insert", False))
    params = insert.compile().params
    assert params["chat_id"] == 123 and params["payload"]["kind"] == "match_board"
    assert "ON CONFLICT (dedupe_key) DO NOTHING" in str(
        insert.compile(dialect=postgresql.dialect())
    )
    assert all(e.name != "result_delivered" for e in recorder.events)


async def test_activation_releases_only_supported_jobs_and_not_rollover_or_notifications(
    monkeypatch,
):
    _, fixture, _, recorder = setup()

    async def coverage(*args):
        pass

    monkeypatch.setattr("goaletto.application.match_runtime.start_coverage", coverage)
    await activate_season(cast(AsyncSession, recorder), fixture.season_id, NOW)
    statement = recorder.statements[-1]
    params = statement.compile().params
    assert params["status_1"] == "held" and params["status"] == "pending"
    assert "season.rollover" not in params["kind_1"]


def test_closed_job_and_command_payloads_reject_unknown_versions_or_fields():
    state, fixture, _, _ = setup()
    work = job(fixture, "match.tick", 1)
    for key, value in (("version", True), ("tick_number", 0), ("calendar_version", "v99")):
        original = work.payload
        work.payload = {**original, key: value}
        with pytest.raises(ValueError):
            job_refs(work)
        work.payload = original
    command = AcceptedCommand(
        id=uuid4(),
        club_id=state.home.snapshot.club_id,
        target_tick_number=1,
        payload_version=1,
        kind="mentality",
        payload={"mentality": "attack", "raw_callback": "private"},
    )
    with pytest.raises(ValueError):
        domain_command(command, state, halftime=False)
