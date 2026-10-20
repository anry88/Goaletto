"""Progress after committed loop iterations; separate, bounded monitoring writes."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update

from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.metrics import METRICS
from goaletto.infrastructure.models import WorkerHeartbeat

logger = logging.getLogger(__name__)


async def initialize_health(database: Database, settings: Settings) -> UUID:
    instance = uuid4()
    now = datetime.now(UTC)
    async with database.transaction() as session:
        await session.execute(
            delete(WorkerHeartbeat).where(WorkerHeartbeat.observed_at < now - timedelta(days=7))
        )
        for component in ("jobs", "sender") if settings.outbox_enabled else ("jobs",):
            session.add(
                WorkerHeartbeat(
                    instance_id=instance,
                    component=component,
                    started_at=now,
                    observed_at=now,
                    outcome="starting",
                )
            )
    # Own container/process identity: another healthy replica cannot hide this one's failure.
    path = settings.worker_health_file
    temporary = path.with_name(f"{path.name}.{instance}.tmp")
    try:
        temporary.write_text(str(instance), encoding="ascii")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return instance


class PulseWriter:
    def __init__(self, database: Database, instance: UUID | None, component: str) -> None:
        self.database, self.instance, self.component = database, instance, component
        self.next_due = 0.0

    async def beat(self, *, success: bool) -> None:
        METRICS.pulse(self.component, success, datetime.now(UTC).timestamp())
        if self.instance is None or (success and monotonic() < self.next_due):
            return
        self.next_due = monotonic() + 10
        now = datetime.now(UTC)
        try:
            async with asyncio.timeout(1):
                async with self.database.transaction() as session:
                    values: dict[str, object] = {
                        "observed_at": now,
                        "outcome": "ok" if success else "error",
                    }
                    if success:
                        values["succeeded_at"] = now
                    await session.execute(
                        update(WorkerHeartbeat)
                        .where(
                            WorkerHeartbeat.instance_id == self.instance,
                            WorkerHeartbeat.component == self.component,
                        )
                        .values(**values)
                    )
        except Exception as error:
            METRICS.heartbeat_errors.labels(self.component).inc()
            # Monitoring failure must not cancel an already committed game operation.
            logger.warning("Worker heartbeat unavailable (%s)", type(error).__name__)

    async def wait(self, seconds: float) -> None:
        """Stay observable during an intentional Telegram backoff, without retrying IO."""
        deadline = monotonic() + seconds
        while (remaining := deadline - monotonic()) > 0:
            await asyncio.sleep(min(remaining, 10))
            await self.beat(success=True)


async def status(
    database: Database,
    instance: UUID,
    *,
    sender: bool,
    now: datetime | None = None,
    max_age: float = 120,
) -> dict[str, object]:
    async with database.read_transaction() as session:
        rows = (
            await session.scalars(
                select(WorkerHeartbeat).where(WorkerHeartbeat.instance_id == instance)
            )
        ).all()
    by_component = {row.component: row for row in rows}
    now = now or datetime.now(UTC)
    components = {}
    for name in ("jobs", "sender") if sender else ("jobs",):
        row = by_component.get(name)
        state = "missing"
        age = None
        observation_age = None
        if row is not None:
            age = (now - row.succeeded_at).total_seconds() if row.succeeded_at else None
            observation_age = (now - row.observed_at).total_seconds()
            state = row.outcome
            if state == "ok" and age is None:
                state = "unknown"
            if row.observed_at > now or (age is not None and age < 0):
                state = "invalid_clock"
            elif observation_age > max_age or (state == "ok" and age is not None and age > max_age):
                state = "stale"
        components[name] = {
            "status": state,
            "success_age_seconds": age,
            "observation_age_seconds": observation_age,
        }
    return {
        "database": "ok",
        "healthy": all(c["status"] == "ok" for c in components.values()),
        "components": components,
    }
