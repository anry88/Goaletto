import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from goaletto.bot.access import TelegramAccess
from goaletto.bot.commands import configure_commands
from goaletto.config import Settings
from goaletto.infrastructure.database import Database


def fresh_router(source: Router) -> Router:
    """Clone registrations so app/test dispatchers never share attached routers."""
    result = Router(name=source.name)
    for event_name, observer in source.observers.items():
        for handler in observer.handlers:
            result.observers[event_name].register(
                handler.callback,
                *(flt.callback for flt in handler.filters or ()),
                flags=dict(handler.flags),
            )
    return result


def build_dispatcher(
    database: Database | None,
    *,
    game_enabled: bool,
    allowed_telegram_ids: frozenset[int] = frozenset(),
    internal_telegram_ids: frozenset[int] = frozenset(),
) -> Dispatcher:
    dispatcher = Dispatcher(database=database, internal_telegram_ids=internal_telegram_ids)
    access = TelegramAccess(allowed_telegram_ids)
    dispatcher.message.outer_middleware(access)
    dispatcher.callback_query.outer_middleware(access)
    if not game_enabled:
        from goaletto.bot.handlers import router

        dispatcher.include_router(fresh_router(router))
        return dispatcher
    if database is None:
        raise ValueError("Game dispatcher requires a database")
    from goaletto.bot import (
        club_router,
        entry_router,
        history_router,
        lineup_handlers,
        live_router,
        navigation,
        player_router,
        settings_router,
        squad_router,
        training_router,
    )

    root = Router(name="game")
    # All club information/actions are served in a private chat with the bot.
    root.message.filter(F.chat.type == "private")
    root.callback_query.filter(F.message.chat.type == "private")
    for module in (
        entry_router,
        navigation,
        lineup_handlers,
        live_router,
        squad_router,
        training_router,
        player_router,
        settings_router,
        history_router,
        club_router,
    ):
        root.include_router(fresh_router(module.router))
    dispatcher.include_router(root)
    return dispatcher


async def run() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level)
    token = settings.bot_token.get_secret_value()
    if not token:
        raise SystemExit("Set GOALETTO_BOT_TOKEN in .env before starting the bot")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    database = Database(settings) if settings.game_enabled else None
    dispatcher = build_dispatcher(
        database,
        game_enabled=settings.game_enabled,
        allowed_telegram_ids=settings.allowed_telegram_ids,
        internal_telegram_ids=settings.internal_telegram_ids,
    )
    try:
        await configure_commands(bot, game_enabled=settings.game_enabled)
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        await bot.session.close()
        if database is not None:
            await database.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
