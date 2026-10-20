"""Private metrics-only HTTP listener with asynchronous cached DB collection."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic, perf_counter, time

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.pool import QueuePool

from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.metrics import METRICS, Metrics
from goaletto.infrastructure.metrics_snapshot import collect_snapshot

logger = logging.getLogger(__name__)


def metrics_app(metrics: Metrics = METRICS) -> web.Application:
    async def expose(request: web.Request) -> web.Response:
        return web.Response(
            body=generate_latest(metrics.registry),
            headers={"Content-Type": CONTENT_TYPE_LATEST, "Cache-Control": "no-store"},
        )

    app = web.Application(client_max_size=1024)
    app.router.add_get("/metrics", expose)
    return app


async def snapshots(database: Database, interval: float, metrics: Metrics = METRICS) -> None:
    while True:
        started = perf_counter()
        try:
            async with asyncio.timeout(10):
                families = await collect_snapshot(database, datetime.now(UTC))
            metrics.snapshot.families = families
            metrics.snapshot_ok.set(1)
            metrics.snapshot_at.set(time())
        except Exception as error:
            metrics.snapshot.families = ()
            metrics.snapshot_ok.set(0)
            metrics.snapshot_errors.inc()
            logger.warning("Metrics snapshot unavailable (%s)", type(error).__name__)
        finally:
            metrics.snapshot_seconds.observe(perf_counter() - started)
        await asyncio.sleep(interval)


async def event_loop_probe(database: Database | None, metrics: Metrics = METRICS) -> None:
    while True:
        due = monotonic() + 5
        await asyncio.sleep(5)
        lag = max(0, monotonic() - due)
        metrics.lag.set(lag)
        metrics.lag_seconds.observe(lag)
        metrics.tasks.set(len(asyncio.all_tasks()))
        if database is not None and isinstance(database.engine.pool, QueuePool):
            pool = database.engine.pool
            metrics.pool.labels("checked_out").set(pool.checkedout())
            metrics.pool.labels("idle").set(pool.checkedin())
            metrics.pool.labels("size").set(pool.size())


@asynccontextmanager
async def serve_metrics(
    settings: Settings, database: Database | None, role: str
) -> AsyncIterator[None]:
    if not settings.metrics_enabled:
        yield
        return
    runner = web.AppRunner(metrics_app(), access_log=None)
    tasks: list[asyncio.Task[None]] = []
    try:
        await runner.setup()
        await web.TCPSite(runner, settings.metrics_host, settings.metrics_port).start()
        METRICS.role.labels(role).set(1)
        tasks.append(asyncio.create_task(event_loop_probe(database)))
        if role == "worker" and database is not None:
            tasks.append(
                asyncio.create_task(snapshots(database, settings.metrics_snapshot_seconds))
            )
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()
