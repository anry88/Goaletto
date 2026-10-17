"""Execute collector with recorded SQL, never with a substitute database."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import JourneyEvent, Origin, Reason, Surface
from goaletto.infrastructure.analytics import (
    CoverageNotStarted,
    cleanup_events,
    event_envelope,
    exclude_manager,
    pseudonym_for,
    record_event,
    start_coverage,
)
from goaletto.infrastructure.models import (
    AnalyticsCoverage,
    AnalyticsExclusion,
    AnalyticsManager,
    JourneyEventRow,
    JourneySession,
    Manager,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)


class Recorder:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.statements = []
        self.added = []
        self.flushes = 0

    def in_transaction(self):
        return True

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.answers.pop(0)

    async def execute(self, statement):
        self.statements.append(statement)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1

    def sql(self):
        return [str(s.compile(dialect=postgresql.dialect())) for s in self.statements]


def setup():
    manager = Manager(id=uuid4(), telegram_id=123, is_internal=False, first_game_start_at=None)
    mapping = AnalyticsManager(id=uuid4(), manager_id=manager.id)
    coverage = AnalyticsCoverage(version=1, started_at=NOW, retention_days=180)
    event = JourneyEvent(
        event_id=uuid4(),
        name="manager_started",
        occurred_at=NOW,
        recorded_at=NOW,
        origin=Origin.USER,
        operation_id=uuid4(),
        effect_number=0,
        surface=Surface.ONBOARDING,
        analytics_manager_id=mapping.id,
        session_id=uuid4(),
    )
    return manager, mapping, coverage, event


async def test_success_allocates_persisted_session_and_first_start_without_committing():
    manager, mapping, coverage, event = setup()
    recorder = Recorder(coverage, mapping, manager, None, None, None, None, event.event_id)
    assert await record_event(cast(AsyncSession, recorder), event)
    assert manager.first_game_start_at == NOW
    assert len(recorder.added) == recorder.flushes == 1
    assert recorder.added[0].first_action_at == NOW
    sql = recorder.sql()
    assert any("FOR UPDATE" in s and "managers" in s for s in sql)
    assert "ON CONFLICT (version, operation_id, effect_number) DO NOTHING" in sql[-1]
    params = recorder.statements[-1].compile().params
    assert params["session_id"] == recorder.added[0].id
    assert params["envelope"]["session_id"] == str(recorder.added[0].id)
    assert "telegram_id" not in params["envelope"]


async def test_duplicate_effect_does_not_extend_session_and_changed_effect_is_rejected():
    manager, mapping, coverage, event = setup()
    saved = JourneyEventRow(envelope=event_envelope(event))
    recorder = Recorder(coverage, mapping, manager, None, saved)
    assert not await record_event(
        cast(AsyncSession, recorder),
        replace(event, event_id=uuid4(), recorded_at=NOW + timedelta(minutes=10)),
    )
    assert recorder.added == []
    changed = replace(event, surface=Surface.HELP)
    with pytest.raises(ValueError, match="different content"):
        await record_event(
            cast(AsyncSession, Recorder(coverage, mapping, manager, None, saved)), changed
        )


@pytest.mark.parametrize("gap,new", [(1799, False), (1800, True)])
async def test_only_successful_actions_join_a_session_within_thirty_minutes(gap, new):
    manager, mapping, coverage, event = setup()
    previous = JourneySession(
        id=uuid4(), analytics_manager_id=mapping.id, first_action_at=NOW, last_action_at=NOW
    )
    at = NOW + timedelta(seconds=gap)
    event = replace(
        event, name="next_action_opened", target=Surface.SQUAD, occurred_at=at, recorded_at=at
    )
    recorder = Recorder(coverage, mapping, manager, None, None, previous, event.event_id)
    assert await record_event(cast(AsyncSession, recorder), event)
    assert bool(recorder.added) == new
    assert previous.last_action_at == (NOW if new else at)


async def test_rejected_action_never_creates_or_extends_a_session():
    manager, mapping, coverage, event = setup()
    event = replace(event, name="action_rejected", session_id=None, reason=Reason.STALE_ACTION)
    recorder = Recorder(coverage, mapping, manager, None, None, None, event.event_id)
    assert await record_event(cast(AsyncSession, recorder), event)
    assert recorder.added == []
    assert recorder.statements[-1].compile().params["session_id"] is None
    assert not recorder.statements[-1].compile().params["user_activity"]


async def test_background_event_never_reads_or_touches_a_user_session():
    _, _, coverage, _ = setup()
    event = JourneyEvent(
        event_id=uuid4(),
        name="fixture_finished",
        occurred_at=NOW,
        recorded_at=NOW,
        origin=Origin.SYSTEM,
        operation_id=uuid4(),
        effect_number=0,
        surface=Surface.MATCH,
        fixture_id=uuid4(),
    )
    recorder = Recorder(coverage, None, event.event_id)
    assert await record_event(cast(AsyncSession, recorder), event)
    assert all("journey_sessions" not in s for s in recorder.sql())
    assert recorder.statements[-1].compile().params["session_id"] is None


async def test_cleanup_does_not_restart_a_retention_cohort_on_new_start():
    manager, mapping, coverage, event = setup()
    manager.first_game_start_at = NOW
    recorder = Recorder(coverage, mapping, manager, None, None, None)
    assert not await record_event(cast(AsyncSession, recorder), event)
    assert recorder.added == []


async def test_disabled_coverage_and_internal_registry_prevent_product_collection():
    manager, mapping, coverage, event = setup()
    with pytest.raises(CoverageNotStarted):
        await record_event(cast(AsyncSession, Recorder(None)), event)
    for kind in ("internal", "test", "operator", "opt_out"):
        excluded = AnalyticsExclusion(manager_id=manager.id, kind=kind, excluded_at=NOW)
        recorder = Recorder(coverage, mapping, manager, excluded)
        assert not await record_event(cast(AsyncSession, recorder), event)
        assert recorder.added == []
    assert not await record_event(cast(AsyncSession, Recorder(coverage, None)), event)


async def test_mapping_exclusion_and_erasure_use_same_manager_lock_and_keep_tombstone():
    manager, _, _, _ = setup()
    recorder = Recorder(manager, None, uuid4())
    assert await pseudonym_for(cast(AsyncSession, recorder), manager.id, NOW)
    assert "FOR UPDATE" in recorder.sql()[0]
    assert "ON CONFLICT (manager_id) DO NOTHING" in recorder.sql()[2]
    recorder = Recorder(manager)
    await exclude_manager(cast(AsyncSession, recorder), manager.id, "opt_out", NOW)
    assert "FOR UPDATE" in recorder.sql()[0]
    assert "INSERT INTO analytics_exclusions" in recorder.sql()[1]
    assert "DELETE FROM analytics_managers" in recorder.sql()[2]
    assert not manager.is_internal


async def test_cleanup_uses_configured_recording_age_and_does_not_remove_live_sessions():
    _, _, coverage, _ = setup()
    coverage.retention_days = 90
    recorder = Recorder(coverage)
    await cleanup_events(cast(AsyncSession, recorder), NOW)
    assert recorder.statements[1].compile().params["recorded_at_1"] == NOW - timedelta(days=90)
    assert "NOT (EXISTS" in recorder.sql()[-1]
    recorder = Recorder()
    await start_coverage(cast(AsyncSession, recorder), NOW)
    assert "ON CONFLICT (version) DO NOTHING" in recorder.sql()[0]


async def test_historical_bot_replay_does_not_invent_earlier_analytics_coverage():
    _, _, coverage, original = setup()
    event = replace(
        original,
        name="fixture_finished",
        origin=Origin.SYSTEM,
        analytics_manager_id=None,
        session_id=None,
        fixture_id=uuid4(),
        occurred_at=NOW - timedelta(days=1),
        recorded_at=NOW,
    )
    recorder = Recorder(coverage)
    with pytest.raises(ValueError, match="precedes declared"):
        await record_event(cast(AsyncSession, recorder), event)
    recorder = Recorder(coverage)
    recorder.info = {"world_replay_recorded_at": NOW}
    assert not await record_event(cast(AsyncSession, recorder), event)
    assert not recorder.added
