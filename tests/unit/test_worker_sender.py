"""Worker wiring with fake tasks, database and Bot; no polling/network/DB."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from goaletto.config import Settings
from goaletto.worker import main as worker


@pytest.mark.parametrize("enabled", [False, True])
async def test_worker_runs_sender_independently_and_closes_resources(monkeypatch, enabled):
    settings = Settings(_env_file=None, outbox_enabled=enabled, bot_token="stub")
    started_jobs, started_sender = asyncio.Event(), asyncio.Event()
    stopped = []

    class DatabaseStub:
        def __init__(self, settings):
            pass

        @asynccontextmanager
        async def transaction(self):
            yield None

        async def close(self):
            stopped.append("database")

    class BotStub:
        def __init__(self, token):
            assert token == "stub"
            self.session = SimpleNamespace(close=self.close, middleware=lambda observer: observer)

        async def close(self):
            stopped.append("bot")

    async def jobs(*args):
        started_jobs.set()
        await asyncio.Event().wait()

    async def sender(*args):
        started_sender.set()
        await asyncio.Event().wait()

    async def configure(*args):
        pass

    async def initialize(*args):
        return uuid4()

    monkeypatch.setattr(worker, "configure_feedback", configure)
    monkeypatch.setattr(worker, "initialize_health", initialize)
    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "Database", DatabaseStub)
    monkeypatch.setattr(worker, "Bot", BotStub)
    monkeypatch.setattr(worker, "run_jobs", jobs)
    monkeypatch.setattr(worker, "run_sender", sender)
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(started_jobs.wait(), timeout=1)
        if enabled:
            await asyncio.wait_for(started_sender.wait(), timeout=1)
        else:
            assert not started_sender.is_set()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped == (["bot", "database"] if enabled else ["database"])


async def test_sender_enabled_without_token_fails_before_opening_database(monkeypatch):
    settings = Settings(_env_file=None, outbox_enabled=True, bot_token="")
    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "Database", lambda _: pytest.fail("Database must not open"))
    with pytest.raises(SystemExit, match="GOALETTO_BOT_TOKEN"):
        await worker.run()


async def test_startup_failure_closes_bot_and_database(monkeypatch):
    settings = Settings(_env_file=None, outbox_enabled=True, bot_token="stub")
    stopped = []

    class DatabaseStub:
        def __init__(self, settings):
            pass

        async def close(self):
            stopped.append("database")

    class BotStub:
        def __init__(self, token):
            self.session = SimpleNamespace(close=self.close, middleware=lambda observer: observer)

        async def close(self):
            stopped.append("bot")

    async def fail(*args):
        raise ConnectionError("startup failed")

    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "Database", DatabaseStub)
    monkeypatch.setattr(worker, "Bot", BotStub)
    monkeypatch.setattr(worker, "prepare_worker", fail)
    with pytest.raises(ConnectionError):
        await worker.run()
    assert stopped == ["bot", "database"]
