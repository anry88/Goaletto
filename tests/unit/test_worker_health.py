import asyncio
import json

import pytest

from goaletto.diagnostics import health_cli
from goaletto.infrastructure import worker_health


def test_health_failure_is_unhealthy_and_does_not_expose_exception(monkeypatch, capsys):
    async def fail(*args):
        raise ConnectionError("SECRET_CREDENTIALS_MUST_NOT_APPEAR")

    monkeypatch.setattr(health_cli, "check", fail)
    monkeypatch.setattr("sys.argv", ["health"])
    with pytest.raises(SystemExit) as error:
        health_cli.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out) == {
        "healthy": False,
        "check": "unavailable",
        "error_class": "ConnectionError",
    }


async def test_intentional_sender_backoff_observes_wait_without_repeating_transport(monkeypatch):
    clock = [0.0]
    waits, beats = [], []

    async def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    async def beat(self, *, success):
        beats.append(success)

    monkeypatch.setattr(worker_health, "monotonic", lambda: clock[0])
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(worker_health.PulseWriter, "beat", beat)
    writer = worker_health.PulseWriter(None, None, "sender")
    await writer.wait(125)
    assert sum(waits) == 125 and max(waits) == 10
    assert len(beats) == 13 and all(beats)
