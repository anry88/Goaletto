import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from goaletto.bot.handlers import router
from goaletto.config import Settings


async def run() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level)
    token = settings.bot_token.get_secret_value()
    if not token:
        raise SystemExit("Set GOALETTO_BOT_TOKEN in .env before starting the bot")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    try:
        # One polling instance per token. Existing webhook migration is a deployment step.
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
