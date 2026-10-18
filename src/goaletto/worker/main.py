import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from aiogram import Bot

from goaletto.application.feedback import FeedbackSettings, schedule_comment_cleanup
from goaletto.application.feedback_automation import configure as configure_feedback
from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.outbox_runner import process_one_delivery
from goaletto.infrastructure.telegram_transport import AiogramTransport
from goaletto.infrastructure.worker_health import PulseWriter, initialize_health

logger = logging.getLogger(__name__)


async def run_jobs(database: Database, poll_seconds: float, instance: UUID | None = None) -> None:
    heartbeat = PulseWriter(database, instance, "jobs")
    while True:
        try:
            async with database.transaction() as session:
                processed = await process_one_job(session)
            await heartbeat.beat(success=True)
            if not processed:
                await asyncio.sleep(poll_seconds)
        except Exception as error:
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
        try:
            step = await process_one_delivery(database, transport)
            await heartbeat.beat(success=True)
            if step.retry_after is not None:
                await heartbeat.wait(max(poll_seconds, step.retry_after))
            elif not step.processed:
                await asyncio.sleep(poll_seconds)
        except Exception as error:
            # Persisted leases recover after failure; logs never print API tokens/errors.
            logger.error("Sender iteration failed (%s)", type(error).__name__)
            await heartbeat.beat(success=False)
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
    database = Database(settings)
    try:
        instance = await prepare_worker(database, settings)
        logger.info("Worker started; existing match calendar activation remains explicit")
        logger.info("Outbox sender %s", "enabled" if bot else "disabled")
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(run_jobs(database, settings.worker_poll_seconds, instance))
            if bot is not None:
                tasks.create_task(run_sender(database, bot, settings.worker_poll_seconds, instance))
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
