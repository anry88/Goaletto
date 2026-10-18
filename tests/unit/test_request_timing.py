"""Operational storage failure must not block a gameplay response or leak secrets."""

from contextlib import asynccontextmanager
from uuid import uuid4

from goaletto.infrastructure.request_timing import observe_request


async def test_failed_observation_is_disabled_for_request_without_blocking_send(caplog):
    class Broken:
        calls = 0

        @asynccontextmanager
        async def transaction(self):
            self.calls += 1
            raise RuntimeError("postgresql://hidden:secret@private.invalid/db")
            yield

    database = Broken()
    delivered = []

    async def send():
        delivered.append(True)
        return 1

    async with observe_request(database, uuid4(), "match_open") as timing:
        assert await timing.send(send) == 1
    assert delivered == [True] and database.calls == 1
    assert "RuntimeError" in caplog.text and "secret" not in caplog.text
