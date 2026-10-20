from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.acquisition import (
    TouchKind,
    accept_start,
    campaign_link,
    classify_start,
    create_campaign,
    telegram_operation,
)
from goaletto.infrastructure.acquisition_queries import source_funnel, validate_funnel_row
from goaletto.infrastructure.models import (
    AcquisitionCampaign,
    AcquisitionFirstTouch,
    AcquisitionTouch,
    AnalyticsCoverage,
    Manager,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)


class Recorder:
    def __init__(self):
        self.manager = Manager(
            id=uuid4(), telegram_id=123, is_internal=False, first_game_start_at=None
        )
        self.analytics_id = uuid4()
        self.campaign = AcquisitionCampaign(
            id=uuid4(), code="c_opaque", source="community", placement="launch"
        )
        self.campaigns = {self.campaign.code: self.campaign}
        self.touch = None
        self.first = None
        self.added = []
        self.events = []
        self.statements = []

    def in_transaction(self):
        return True

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        if statement.table.name == "acquisition_campaigns":
            values = statement.compile().params
            self.campaigns.setdefault(values["code"], AcquisitionCampaign(**values))

    async def scalar(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        if entity is AcquisitionCampaign:
            row = self.campaigns.get(statement.compile().params["code_1"])
            return row.id if row else None
        return self.manager

    async def get(self, model, key):
        if model is AcquisitionTouch:
            return self.touch if self.touch is not None and self.touch.operation_id == key else None
        return self.first

    def add(self, value):
        self.added.append(value)
        if isinstance(value, AcquisitionTouch):
            self.touch = value
        if isinstance(value, AcquisitionFirstTouch):
            self.first = value

    async def flush(self):
        pass


@pytest.fixture(autouse=True)
def capture(monkeypatch):
    async def pseudonym(session, manager_id, at):
        return session.analytics_id

    async def event(session, event):
        session.events.append(event)
        if event.name == "manager_started":
            session.manager.first_game_start_at = event.occurred_at
        return True

    monkeypatch.setattr("goaletto.application.acquisition.pseudonym_for", pseudonym)
    monkeypatch.setattr("goaletto.application.acquisition.record_event", event)


@pytest.mark.parametrize(
    "payload,kind",
    [
        (None, "direct"),
        ("", "direct"),
        ("missing_code", "known"),
        ("ai_match_prediction", "known"),
        ("x" * 64, "known"),
        ("raw private text /start", "unknown"),
        ("x" * 65, "unknown"),
        ("c_opaque", "known"),
        ("C_opaque", "known"),
    ],
)
async def test_first_touch_classification_before_claim_has_no_raw_payload(payload, kind):
    recorder = Recorder()
    result = await accept_start(
        cast(AsyncSession, recorder), telegram_id=123, update_id=1, payload=payload, at=NOW
    )
    assert result.kind.value == recorder.first.kind == kind and result.created_touch
    assert recorder.first.operation_id == telegram_operation(1)
    assert recorder.manager.first_game_start_at == NOW
    assert (
        recorder.events[0].name == "manager_started"
        and recorder.events[-1].name == "start_accepted"
    )
    assert all(e.club_id is None and e.ownership_id is None for e in recorder.events)
    assert not hasattr(recorder.touch, "payload") and not hasattr(recorder.first, "code")
    if kind == "known":
        assert result.campaign_id == recorder.campaigns[payload].id
        assert any(e.name == "campaign_touched" for e in recorder.events)


async def test_retry_is_not_a_new_touch_session_or_manager_and_returning_start_keeps_first_touch():
    recorder = Recorder()
    first = await accept_start(
        cast(AsyncSession, recorder), telegram_id=123, update_id=1, payload=None, at=NOW
    )
    original = recorder.first
    counts = len(recorder.added), len(recorder.events)
    retry = await accept_start(
        cast(AsyncSession, recorder),
        telegram_id=123,
        update_id=1,
        payload="c_opaque",
        at=NOW + timedelta(minutes=1),
    )
    assert not retry.created_touch and retry.kind == TouchKind.DIRECT
    assert counts == (len(recorder.added), len(recorder.events))
    returned = await accept_start(
        cast(AsyncSession, recorder),
        telegram_id=123,
        update_id=2,
        payload="c_opaque",
        at=NOW + timedelta(days=3),
    )
    assert returned.campaign_id == recorder.campaign.id and recorder.first is original
    assert sum(e.name == "manager_started" for e in recorder.events) == 1
    assert first.manager_id == returned.manager_id


async def test_deleted_first_touch_after_retention_is_never_recreated_as_a_new_cohort():
    recorder = Recorder()
    recorder.manager.first_game_start_at = NOW - timedelta(days=200)
    await accept_start(
        cast(AsyncSession, recorder), telegram_id=123, update_id=1, payload="c_opaque", at=NOW
    )
    assert recorder.first is None and all(e.name != "manager_started" for e in recorder.events)


async def test_internal_or_opt_out_accounts_have_no_product_attribution():
    recorder = Recorder()
    recorder.analytics_id = None
    result = await accept_start(
        cast(AsyncSession, recorder), telegram_id=123, update_id=1, payload="c_opaque", at=NOW
    )
    assert result.analytics_manager_id is None and recorder.added == recorder.events == []


async def test_opaque_campaign_registry_and_link_limits():
    recorder = Recorder()
    row = await create_campaign(
        cast(AsyncSession, recorder), "community", "launch", date(2026, 10, 12), NOW
    )
    assert 1 <= len(row.code) <= 64 and campaign_link(row.code).startswith(
        "https://t.me/GoalettoBot?start=c_"
    )
    for invalid in ("x" * 65, "a b", "https://secret", "кириллица"):
        with pytest.raises(ValueError):
            campaign_link(invalid)
    with pytest.raises(ValueError):
        await create_campaign(
            cast(AsyncSession, recorder), "free_source", "launch", date(2026, 10, 12), NOW
        )
    assert await classify_start(cast(AsyncSession, recorder), " ", at=NOW) == (
        TouchKind.UNKNOWN,
        None,
    )


async def test_automatic_source_keeps_exact_code_and_reuses_existing_metadata():
    recorder = Recorder()
    session = cast(AsyncSession, recorder)
    kind, source_id = await classify_start(session, "AI_match-prediction", at=NOW)
    row = recorder.campaigns["AI_match-prediction"]
    assert kind == TouchKind.KNOWN and row.id == source_id
    assert (row.source, row.placement, row.published_on) == (
        "other",
        "ai_match-prediction",
        NOW.date(),
    )
    assert await classify_start(session, row.code, at=NOW + timedelta(days=1)) == (kind, source_id)
    assert row.created_at == NOW
    assert await classify_start(session, "c_opaque", at=NOW) == (
        TouchKind.KNOWN,
        recorder.campaign.id,
    )
    assert recorder.campaign.source == "community" and recorder.campaign.placement == "launch"


def aggregate():
    return {
        "kind": "direct",
        "campaign_id": None,
        "source": None,
        "placement": None,
        "published_on": None,
        "starts": 10,
        "eligible": 7,
        "waiting": 3,
        "claimed": 4,
        "ready": 3,
    }


def test_source_funnel_rejects_inconsistent_denominators_and_private_fields():
    validate_funnel_row(aggregate())
    for changes in (
        {"ready": 8},
        {"claimed": 8},
        {"waiting": 4},
        {"starts": True},
        {"telegram_id": 123},
        {"kind": "known"},
    ):
        with pytest.raises(ValueError):
            validate_funnel_row({**aggregate(), **changes})


async def test_report_clips_requested_window_to_actual_coverage_and_retained_data():
    class ReportRecorder:
        def __init__(self):
            self.params = None

        async def scalar(self, statement):
            return AnalyticsCoverage(
                version=1, started_at=NOW - timedelta(days=400), retention_days=90
            )

        async def execute(self, statement, params):
            self.params = params
            return self

        def mappings(self):
            return [aggregate()]

    recorder = ReportRecorder()
    report = await source_funnel(cast(AsyncSession, recorder), NOW - timedelta(days=365), NOW)
    assert report.coverage_limited and report.available_from == NOW - timedelta(days=90)
    assert report.requested_from == NOW - timedelta(days=365)
    assert recorder.params == {"cohort_from": report.available_from, "report_at": NOW}
    assert report.rows[0]["waiting"] == 3


async def test_report_rejects_an_invalid_window_before_sql():
    recorder = Recorder()
    with pytest.raises(ValueError):
        await source_funnel(cast(AsyncSession, recorder), NOW + timedelta(days=1), NOW)
    assert recorder.statements == []
