from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4, uuid5

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.match_runtime import NotReady
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.models import Job

NOW = datetime(2026, 10, 17, 18, tzinfo=UTC)


class Recorder:
    def __init__(self, job):
        self.job = job
        self.added = []
        self.statements = []
        self.nested = 0

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.job

    def add(self, value):
        self.added.append(value)

    @asynccontextmanager
    async def begin_nested(self):
        self.nested += 1
        yield


@pytest.mark.parametrize("outcome", ["success", "deferred", "invalid"])
async def test_dispatch_records_attempt_and_uses_savepoint_without_external_delivery(
    monkeypatch, outcome
):
    fixture_id = uuid4()
    job = Job(
        id=uuid4(),
        kind="match.tick",
        status="pending",
        attempts=0,
        due_at=NOW,
        payload={
            "version": 1,
            "calendar_version": "four-day-calendar-v1",
            "season_id": str(uuid4()),
            "fixture_id": str(fixture_id),
            "tick_number": 1,
        },
    )
    recorder = Recorder(job)

    async def handle(*args):
        if outcome == "deferred":
            raise NotReady("predecessor not committed")
        if outcome == "invalid":
            raise ValueError("private unsafe exception text")

    monkeypatch.setattr("goaletto.infrastructure.jobs.handle_calendar_job", handle)
    processed = await process_one_job(cast(AsyncSession, recorder), at=NOW)
    assert recorder.nested == job.attempts == len(recorder.added) == 1
    attempt = recorder.added[0]
    assert attempt.failure_class in {None, "not_ready", "invalid_state"}
    assert (
        attempt.outcome
        == {"success": "committed", "deferred": "deferred", "invalid": "failed"}[outcome]
    )
    assert processed == (outcome != "deferred")
    assert job.status == {"success": "done", "deferred": "pending", "invalid": "failed"}[outcome]
    assert job.due_at == NOW  # Waiting for a predecessor must never move the match clock.
    assert attempt.finished_at >= attempt.started_at
    if outcome == "success":
        assert attempt.operation_id == uuid5(fixture_id, "runtime:v1:tick:1")
    sql = str(recorder.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql and "fixtures.status" in sql
    assert "CAST(fixtures.id AS VARCHAR)" in sql


async def test_unknown_job_is_quarantined_with_bounded_error_code():
    job = Job(id=uuid4(), kind="unknown", status="pending", attempts=0, due_at=NOW, payload={})
    recorder = Recorder(job)
    assert await process_one_job(cast(AsyncSession, recorder), at=NOW)
    assert job.status == "failed" and job.last_error == "unsupported_job_kind"
    assert recorder.nested == 0 and recorder.added[0].failure_class == "unsupported_job_kind"
