"""Prometheus gauges agree with exact SQL cohorts on the one Goaletto DB."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from sqlalchemy import select

from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.metrics import METRICS
from goaletto.infrastructure.metrics_snapshot import collect_snapshot
from goaletto.infrastructure.models import AnalyticsCoverage, Job
from goaletto.worker import main as worker
from tests.integration.test_analytics_reports_pg import COVERAGE_AT, START, T, _manager, _start

pytestmark = pytest.mark.integration


async def test_snapshot_hand_count_windows_maturity_internal_and_queues(sessions):
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
        for index, at in enumerate(
            (START, T - timedelta(hours=1), START - timedelta(days=2), START)
        ):
            _, mapping = await _manager(session, 10001 + index, internal=index == 3)
            await _start(session, mapping, at)
        session.add(
            Job(
                id=uuid4(),
                kind="system.ping",
                status="pending",
                due_at=T - timedelta(seconds=12),
                payload={},
                attempts=0,
                dedupe_key="metrics-past",
            )
        )
        session.add(
            Job(
                id=uuid4(),
                kind="system.ping",
                status="pending",
                due_at=T + timedelta(days=1),
                payload={},
                attempts=0,
                dedupe_key="metrics-future",
            )
        )
    database = Database(Settings())
    try:
        families = await collect_snapshot(database, T)
    finally:
        await database.close()
    samples = {
        (s.name, tuple(sorted(s.labels.items()))): s.value for f in families for s in f.samples
    }

    def value(name, **labels):
        return samples[("goaletto_" + name, tuple(sorted(labels.items())))]

    assert value("analytics_entry", window="1d", measure="starts") == 2
    assert value("analytics_entry", window="1d", measure="eligible") == 1
    assert value("analytics_entry", window="1d", measure="waiting") == 1
    assert value("analytics_entry", window="7d", measure="starts") == 3
    assert value("analytics_entry", window="7d", measure="eligible") == 2
    assert value("queue_items", queue="jobs", status="pending", measure="items") == 2
    assert value("queue_items", queue="jobs", status="pending", measure="due") == 1
    assert value("queue_items", queue="jobs", status="pending", measure="oldest_due_seconds") == 12
    assert value("queue_items", queue="jobs", status="failed", measure="items") == 0
    registry = CollectorRegistry()

    class Cached:
        def collect(self):
            return iter(families)

    registry.register(Cached())
    exported = generate_latest(registry).decode()
    assert "10001" not in exported and "analytics_manager_id" not in exported
    async with sessions() as session:
        assert len((await session.scalars(select(Job))).all()) == 2


async def test_job_counter_does_not_claim_a_commit_when_outer_transaction_rolls_back(sessions):
    job_id = uuid4()
    async with sessions.begin() as session:
        session.add(
            Job(
                id=job_id,
                kind="system.ping",
                status="pending",
                due_at=T,
                payload={},
                attempts=0,
                dedupe_key="metrics-rollback",
            )
        )
    observed = asyncio.Event()

    class FailedCommit:
        @asynccontextmanager
        async def transaction(self):
            async with sessions.begin() as session:
                yield session
                observed.set()
                raise ConnectionError("SECRET_COMMIT_ERROR")

    labels = {"kind": "system.ping", "outcome": "committed"}
    before = METRICS.registry.get_sample_value("goaletto_jobs_total", labels) or 0
    task = asyncio.create_task(worker.run_jobs(FailedCommit(), 0.1))
    await asyncio.wait_for(observed.wait(), timeout=5)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (METRICS.registry.get_sample_value("goaletto_jobs_total", labels) or 0) == before
    async with sessions() as session:
        job = await session.get(Job, job_id)
        assert job.status == "pending" and job.attempts == 0
