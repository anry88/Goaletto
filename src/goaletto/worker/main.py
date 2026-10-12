import asyncio
import logging

from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.jobs import process_one_job

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level)
    database = Database(settings)
    logger.info("Worker started; regulation match jobs enabled, calendar activation is explicit")
    try:
        while True:
            try:
                async with database.transaction() as session:
                    processed = await process_one_job(session)
                if not processed:
                    await asyncio.sleep(settings.worker_poll_seconds)
            except Exception as error:
                # Avoid logging exception text which can contain a credential-bearing DSN.
                logger.error("Worker iteration failed (%s)", type(error).__name__)
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await database.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
