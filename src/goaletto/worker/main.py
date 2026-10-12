import asyncio
import logging

from aiogram import Bot

from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.outbox_runner import process_one_delivery
from goaletto.infrastructure.telegram_transport import AiogramTransport

logger = logging.getLogger(__name__)


async def run_jobs(database: Database, poll_seconds: float) -> None:
    while True:
        try:
            async with database.transaction() as session:
                processed = await process_one_job(session)
            if not processed:
                await asyncio.sleep(poll_seconds)
        except Exception as error:
            # Exception text can contain a credential-bearing DSN.
            logger.error("Worker iteration failed (%s)", type(error).__name__)
            await asyncio.sleep(poll_seconds)


async def run_sender(database: Database, bot: Bot, poll_seconds: float) -> None:
    transport = AiogramTransport(bot)
    while True:
        try:
            step = await process_one_delivery(database, transport)
            if step.retry_after is not None:
                await asyncio.sleep(max(poll_seconds, step.retry_after))
            elif not step.processed:
                await asyncio.sleep(poll_seconds)
        except Exception as error:
            # Persisted leases recover after failure; logs never print API tokens/errors.
            logger.error("Sender iteration failed (%s)", type(error).__name__)
            await asyncio.sleep(poll_seconds)


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
    logger.info("Worker started; match calendar activation remains explicit")
    logger.info("Outbox sender %s", "enabled" if bot else "disabled")
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(run_jobs(database, settings.worker_poll_seconds))
            if bot is not None:
                tasks.create_task(run_sender(database, bot, settings.worker_poll_seconds))
    finally:
        if bot is not None:
            await bot.session.close()
        await database.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
