import asyncio
import logging
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

from aiogram import Bot

from goaletto.application.feedback import FeedbackSettings, schedule_comment_cleanup
from goaletto.application.feedback_automation import configure as configure_feedback
from goaletto.bot.metrics import TelegramMetrics
from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.metrics import METRICS
from goaletto.infrastructure.metrics_server import serve_metrics
from goaletto.infrastructure.metrics_snapshot import JOB_KINDS, bounded
from goaletto.infrastructure.outbox_runner import process_one_delivery
from goaletto.infrastructure.telegram_transport import AiogramTransport
from goaletto.infrastructure.worker_health import PulseWriter, initialize_health

logger = logging.getLogger(__name__)


async def run_jobs(database: Database, poll_seconds: float, instance: UUID | None = None) -> None:
    heartbeat = PulseWriter(database, instance, "jobs")
    while True:
        started = perf_counter()
        try:
            async with database.transaction() as session:
                processed = await process_one_job(session)
                observed = session.info.get("metrics_job")
            METRICS.loops.labels("jobs", "work" if observed else "idle").inc()
            if observed:
                kind, outcome, lateness = observed
                kind = bounded(kind, JOB_KINDS)
                METRICS.jobs.labels(kind, outcome).inc()
                if kind != "world.catch_up":
                    METRICS.job_lateness.labels(kind).observe(lateness)
            METRICS.loop_seconds.labels("jobs").observe(perf_counter() - started)
            await heartbeat.beat(success=True)
            if not processed:
                await asyncio.sleep(poll_seconds)
        except Exception as error:
            METRICS.loops.labels("jobs", "error").inc()
            METRICS.loop_seconds.labels("jobs").observe(perf_counter() - started)
            # Exception text can contain a credential-bearing DSN.
            logger.error("Worker iteration failed (%s)", type(error).__name__)
            await heartbeat.beat(success=False)
            await asyncio.sleep(poll_seconds)


async def run_sender(
    database: Database, bot: Bot, poll_seconds: float, instance: UUID | None = None
) -> None:
    heartbeat = PulseWriter(database, instance, "sender")
    transport = AiogramTransport(bot)
    while True:
        started = perf_counter()
        try:
            step = await process_one_delivery(database, transport)
            METRICS.loops.labels("sender", "work" if step.processed else "idle").inc()
            METRICS.loop_seconds.labels("sender").observe(perf_counter() - started)
            if step.outcome is not None:
                METRICS.deliveries.labels(step.outcome).inc()
            await heartbeat.beat(success=True)
            if step.retry_after is not None:
                await heartbeat.wait(max(poll_seconds, step.retry_after))
            elif not step.processed:
                await asyncio.sleep(poll_seconds)
        except Exception as error:
            METRICS.loops.labels("sender", "error").inc()
            METRICS.loop_seconds.labels("sender").observe(perf_counter() - started)
            # Persisted leases recover after failure; logs never print API tokens/errors.
            logger.error("Sender iteration failed (%s)", type(error).__name__)
            await heartbeat.beat(success=False)
            await asyncio.sleep(poll_seconds)


async def run_refunds(database: Database, bot: Bot, poll_seconds: float) -> None:
    from goaletto.infrastructure.star_refunds import process_refund

    while True:
        try:
            processed = await process_refund(database, bot)
            if not processed:
                await asyncio.sleep(poll_seconds)
        except Exception as error:
            logger.error("Stars refund iteration failed (%s)", type(error).__name__)
            await asyncio.sleep(poll_seconds)


async def prepare_worker(database: Database, settings: Settings) -> UUID:
    if settings.feedback_comment_retention_days is not None:
        async with database.transaction() as session:
            await schedule_comment_cleanup(
                session,
                at=datetime.now(UTC),
                retention_days=settings.feedback_comment_retention_days,
            )
    async with database.transaction() as session:
        await configure_feedback(
            session,
            datetime.now(UTC),
            FeedbackSettings(
                settings.feedback_enabled and settings.feedback_comment_retention_days is not None,
                settings.feedback_comment_retention_days,
                settings.feedback_cooldown_days,
            ),
            settings.game_enabled and settings.feedback_automatic_enabled,
        )
    if settings.game_enabled:
        from goaletto.application.runtime_reconciliation import request_reconciliation
        from goaletto.application.world_capacity import request_reserve

        async with database.transaction() as session:
            await request_reserve(session, datetime.now(UTC))
            await request_reconciliation(session, datetime.now(UTC))
    return await initialize_health(database, settings)


async def run() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level)
    bot: Bot | None = None
    if settings.outbox_enabled:
        token = settings.bot_token.get_secret_value()
        if not token:
            raise SystemExit("Set GOALETTO_BOT_TOKEN before enabling GOALETTO_OUTBOX_ENABLED")
        bot = Bot(token)  # Sends only; polling remains in the bot process.
        bot.session.middleware(TelegramMetrics())
    database = Database(settings)
    try:
        async with serve_metrics(settings, database, "worker"):
            instance = await prepare_worker(database, settings)
            logger.info("Worker started; existing match calendar activation remains explicit")
            logger.info("Outbox sender %s", "enabled" if bot else "disabled")
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(run_jobs(database, settings.worker_poll_seconds, instance))
                if bot is not None:
                    if settings.payments_enabled:
                        tasks.create_task(run_refunds(database, bot, settings.worker_poll_seconds))
                    tasks.create_task(
                        run_sender(database, bot, settings.worker_poll_seconds, instance)
                    )
    finally:
        try:
            if bot is not None:
                await bot.session.close()
        finally:
            await database.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
