"""Durable worker progress on the single application DB, no Telegram calls."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, text

from goaletto.config import Settings
from goaletto.diagnostics.health_cli import check
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import WorkerHeartbeat
from goaletto.infrastructure.worker_health import PulseWriter, initialize_health, status
from goaletto.worker import main as worker

pytestmark = pytest.mark.integration


async def test_health_requires_both_loops_of_this_boot_and_marks_errors_stale(sessions, tmp_path):
    database = Database(Settings())
    settings = Settings(outbox_enabled=True, worker_health_file=tmp_path / "instance")
    try:
        instance = await initialize_health(database, settings)
        assert UUID(settings.worker_health_file.read_text()) == instance
        assert not (await status(database, instance, sender=True))["healthy"]
        await PulseWriter(database, instance, "jobs").beat(success=True)
        assert (await status(database, instance, sender=False))["healthy"]
        assert not (await status(database, instance, sender=True))["healthy"]
        await PulseWriter(database, instance, "sender").beat(success=True)
        assert (await status(database, instance, sender=True))["healthy"]
        # A sender error must not be hidden by a successful jobs loop.
        await PulseWriter(database, instance, "sender").beat(success=False)
        failed = await status(database, instance, sender=True)
        assert not failed["healthy"] and failed["components"]["sender"]["status"] == "error"
        stale = await status(
            database, instance, sender=False, now=datetime.now(UTC) + timedelta(seconds=121)
        )
        assert not stale["healthy"] and stale["components"]["jobs"]["status"] == "stale"
        # Restart cannot borrow a previous process's fresh progress.
        replacement = await initialize_health(database, settings)
        assert (
            replacement != instance
            and not (await status(database, replacement, sender=False))["healthy"]
        )
        assert not (await check(settings, 120))["healthy"]
        async with sessions.begin() as session:
            assert await session.scalar(select(func.count()).select_from(WorkerHeartbeat)) == 4
    finally:
        await database.close()


async def test_worker_loop_pulses_after_commit_and_db_failure_is_not_success(
    sessions, tmp_path, monkeypatch
):
    database = Database(Settings())
    settings = Settings(worker_health_file=tmp_path / "instance")
    instance = await initialize_health(database, settings)
    stepped = asyncio.Event()
    pulsed = asyncio.Event()
    original_beat = PulseWriter.beat

    async def watched_beat(self, *, success):
        await original_beat(self, success=success)
        pulsed.set()

    monkeypatch.setattr(PulseWriter, "beat", watched_beat)

    async def idle(session):
        stepped.set()
        return False

    monkeypatch.setattr(worker, "process_one_job", idle)
    task = asyncio.create_task(worker.run_jobs(database, 0.1, instance))
    try:
        await asyncio.wait_for(stepped.wait(), timeout=2)
        await asyncio.wait_for(pulsed.wait(), timeout=3)
        assert (await status(database, instance, sender=False))["healthy"]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    class FailedCommit:
        @asynccontextmanager
        async def transaction(self):
            async with database.transaction() as session:
                yield session
                raise ConnectionError("SECRET must not be stored")

    # The iteration failure rolls back; heartbeat writer uses the same failed
    # DB source and cannot invent successful progress after a rejected commit.
    failed = asyncio.create_task(worker.run_jobs(FailedCommit(), 0.1, instance))
    try:
        await asyncio.sleep(0.15)
    finally:
        failed.cancel()
        with pytest.raises(asyncio.CancelledError):
            await failed
    async with sessions.begin() as session:
        row = await session.get(WorkerHeartbeat, (instance, "jobs"))
        assert row.outcome == "ok"  # Previous committed observation; never overwritten by failure.
    await database.close()


async def test_worker_recovers_after_its_own_pg_connection_is_terminated(
    sessions, tmp_path, monkeypatch
):
    database = Database(Settings())
    settings = Settings(worker_health_file=tmp_path / "instance")
    instance = await initialize_health(database, settings)
    captured, release, failed, recovered = (asyncio.Event() for _ in range(4))
    pid = None
    first = True
    original_beat = PulseWriter.beat

    class ObservedDatabase:
        @asynccontextmanager
        async def transaction(self):
            nonlocal first, pid
            async with database.transaction() as session:
                if first:
                    first = False
                    pid = await session.scalar(text("SELECT pg_backend_pid()"))
                    captured.set()
                    await release.wait()
                yield session

    async def watch(self, *, success):
        await original_beat(self, success=success)
        if not success:
            failed.set()
        elif failed.is_set() and (await status(database, instance, sender=False))["healthy"]:
            recovered.set()

    monkeypatch.setattr(PulseWriter, "beat", watch)
    task = asyncio.create_task(worker.run_jobs(ObservedDatabase(), 0.1, instance))
    try:
        await asyncio.wait_for(captured.wait(), timeout=3)
        async with sessions.begin() as session:
            # Only the backend captured from this test's own worker transaction.
            assert await session.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        release.set()
        await asyncio.wait_for(failed.wait(), timeout=3)
        assert not (await status(database, instance, sender=False))["healthy"]
        await asyncio.wait_for(recovered.wait(), timeout=15)
        assert (await status(database, instance, sender=False))["healthy"]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await database.close()
