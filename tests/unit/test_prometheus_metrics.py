"""Real middleware/exposition, bounded labels and truthful collector failures."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from prometheus_client import generate_latest
from prometheus_client.core import GaugeMetricFamily

from goaletto.bot.main import build_dispatcher
from goaletto.bot.metrics import TelegramMetrics, UpdateMetrics, route
from goaletto.config import Settings
from goaletto.infrastructure import metrics_server
from goaletto.infrastructure.metrics import Metrics
from goaletto.infrastructure.metrics_snapshot import report_family


def message(value: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=12345678, type="private"),
        from_user=User(id=12345678, is_bot=False, first_name="PRIVATE_PERSON"),
        text=value,
    )


def sample(metrics: Metrics, name: str, labels: dict[str, str]) -> float | None:
    return metrics.registry.get_sample_value("goaletto_" + name, labels)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/start campaign-secret", ("command", "start")),
        ("/match@PRIVATE_BOT", ("command", "match")),
        ("/random_12345678 secret", ("command", "unknown")),
        ("PRIVATE_COMMENT", ("message", "text_or_media")),
    ],
)
def test_untrusted_text_never_becomes_a_label(value, expected):
    assert route(message(value)) == expected
    callback = CallbackQuery(
        id="SECRET_CALLBACK",
        from_user=message("").from_user,
        chat_instance="SECRET_CHAT",
        data="hx:m:PERSON_UUID:13",
    )
    assert route(callback) == ("callback", "button_hx")
    assert route(callback.model_copy(update={"data": "PRIVATE_12345678:secret"})) == (
        "callback",
        "unknown",
    )


async def test_handler_success_error_and_cancellation_are_distinct_without_payloads():
    metrics = Metrics()
    middleware = UpdateMetrics(metrics)
    await middleware(AsyncMock(return_value=None), message("/start secret"), {})
    for error, outcome in [
        (RuntimeError("PRIVATE_ERROR"), "error"),
        (asyncio.CancelledError(), "cancelled"),
    ]:
        with pytest.raises(type(error)):
            await middleware(AsyncMock(side_effect=error), message("/start secret"), {})
        assert (
            sample(
                metrics, "updates_total", {"kind": "command", "route": "start", "outcome": outcome}
            )
            == 1
        )
    assert sample(metrics, "updates_started_total", {"kind": "command", "route": "start"}) == 3
    assert sample(metrics, "updates_inflight", {"kind": "command"}) == 0
    exported = generate_latest(metrics.registry).decode()
    for secret in ("12345678", "PRIVATE", "secret"):
        assert secret not in exported


async def test_real_dispatcher_counts_access_denial_without_executing_game_handler(monkeypatch):
    metrics = Metrics()
    monkeypatch.setattr("goaletto.bot.main.UpdateMetrics", lambda: UpdateMetrics(metrics))
    bot = Bot("123456:TEST_TOKEN_FOR_METRICS")
    bot.session = AsyncMock()
    dispatcher = build_dispatcher(None, game_enabled=False, allowed_telegram_ids=frozenset({999}))
    try:
        await dispatcher.feed_update(bot, Update(update_id=10, message=message("/start")))
        assert (
            sample(
                metrics, "updates_total", {"kind": "command", "route": "start", "outcome": "denied"}
            )
            == 1
        )
        assert bot.session.call_count == 1
    finally:
        await bot.session.close()


async def test_api_failures_are_observed_and_reraised_without_secret_messages():
    metrics = Metrics()
    middleware = TelegramMetrics(metrics)
    bot = Bot("123456:TEST_TOKEN_FOR_METRICS")
    method = SendMessage(chat_id=12345678, text="PRIVATE_TEXT")
    try:
        for error, outcome in [
            (
                TelegramRetryAfter(method=method, message="PRIVATE_ERROR", retry_after=7),
                "rate_limited",
            ),
            (TelegramNetworkError(method=method, message="PRIVATE_ERROR"), "network_unknown"),
        ]:
            with pytest.raises(type(error)):
                await middleware(AsyncMock(side_effect=error), bot, method)
            assert (
                sample(
                    metrics,
                    "telegram_requests_total",
                    {"method": "sendMessage", "outcome": outcome},
                )
                == 1
            )
        assert sample(metrics, "telegram_retry_after_seconds_sum", {"method": "sendMessage"}) == 7
        assert "PRIVATE" not in generate_latest(metrics.registry).decode()
        assert "12345678" not in generate_latest(metrics.registry).decode()
    finally:
        await bot.session.close()


def test_snapshot_collapses_campaigns_and_does_not_convert_missing_values_to_zero():
    family = report_family(
        "sources",
        {
            "7d": {
                "rows": [
                    {"kind": "known", "campaign_id": "SECRET_ONE", "starts": 2, "eligible": None},
                    {"kind": "known", "campaign_id": "SECRET_TWO", "starts": 3},
                ]
            }
        },
    )
    samples = family.samples
    assert len(samples) == 1 and samples[0].value == 5
    assert samples[0].labels == {"window": "7d", "kind": "known", "measure": "starts"}
    assert "SECRET" not in repr(family)


async def test_exposition_reads_only_cache_and_unknown_routes_are_not_an_api():
    metrics = Metrics()
    family = GaugeMetricFamily("goaletto_cached_example", "cached", value=9)
    metrics.snapshot.families = (family,)
    async with TestServer(metrics_server.metrics_app(metrics)) as server, ClientSession() as client:
        async with client.get(server.make_url("/metrics")) as response:
            body = await response.text()
            assert response.status == 200 and "text/plain" in response.headers["Content-Type"]
            assert "goaletto_cached_example 9.0" in body
        async with client.get(server.make_url("/game")) as response:
            assert response.status == 404
        async with client.post(server.make_url("/metrics")) as response:
            assert response.status == 405


async def test_snapshot_failure_drops_stale_gauges_but_preserves_last_success(monkeypatch):
    metrics = Metrics()
    metrics.snapshot_at.set(100)
    metrics.snapshot.families = (GaugeMetricFamily("goaletto_old", "old", value=9),)
    attempted = asyncio.Event()

    async def unavailable(*args):
        attempted.set()
        raise OSError("PRIVATE_DSN")

    monkeypatch.setattr(metrics_server, "collect_snapshot", unavailable)
    task = asyncio.create_task(metrics_server.snapshots(None, 60, metrics))
    await attempted.wait()
    # Yield to allow the bounded failure handler to finish, not a wall-clock sleep.
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert metrics.snapshot.families == ()
    assert sample(metrics, "db_snapshot_success", {}) == 0
    assert sample(metrics, "db_snapshot_timestamp_seconds", {}) == 100
    assert sample(metrics, "db_snapshot_errors_total", {}) == 1
    assert "PRIVATE_DSN" not in generate_latest(metrics.registry).decode()


async def test_disabled_metrics_does_not_bind_any_listener(monkeypatch):
    monkeypatch.setattr(
        metrics_server.web, "AppRunner", lambda *args, **kwargs: pytest.fail("Unexpected listener")
    )
    async with metrics_server.serve_metrics(Settings(metrics_enabled=False), None, "bot"):
        pass


def test_grafana_queries_only_victoriametrics_and_marks_snapshot_semantics():
    root = Path(__file__).resolve().parents[2]
    files = list((root / "deploy/grafana/dashboards").glob("*.json"))
    assert len(files) == 3
    for path in files:
        dashboard = json.loads(path.read_text())
        for panel in dashboard["panels"]:
            for query in panel.get("targets", []):
                assert query["datasource"] == {"type": "prometheus", "uid": "goaletto-vm"}
                assert "rawSql" not in query
                assert "SELECT " not in query["expr"]
                if "goaletto_analytics_" in query["expr"]:
                    assert "goaletto_db_snapshot_success" in query["expr"]
                    assert "time()-goaletto_db_snapshot_timestamp_seconds" in query["expr"]
    journey = json.loads((root / "deploy/grafana/dashboards/journey.json").read_text())
    assert "не произвольный исторический cohort" in journey["description"].lower()
