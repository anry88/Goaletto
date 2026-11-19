"""Keep command-menu updates independent from receiving Telegram updates."""

import asyncio
import logging
from asyncio import sleep
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import partial

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import User

from goaletto.bot.commands import configure_commands

logger = logging.getLogger(__name__)


async def _retry_telegram[T](
    operation: Callable[[], Awaitable[T]], label: str, *, deadline_seconds: float | None = None
) -> T:
    delay = 2.0
    failed = False
    while True:
        try:
            async with asyncio.timeout(deadline_seconds):
                result = await operation()
        except TelegramRetryAfter as error:
            wait = max(delay, float(error.retry_after))
            reason = type(error).__name__
        except (TelegramNetworkError, TelegramServerError, TimeoutError) as error:
            wait = delay
            reason = type(error).__name__
        else:
            if failed:
                logger.info("Telegram %s recovered", label)
            return result

        if not failed:
            logger.warning("Telegram %s failed (%s), retrying with backoff", label, reason)
        failed = True
        await sleep(wait)
        delay = min(delay * 2, 30.0)


class PollingBot(Bot):
    def __init__(self, token: str, *, default: DefaultBotProperties | None = None) -> None:
        super().__init__(token, default=default)
        self._identity_lock = asyncio.Lock()

    async def me(self) -> User:
        # The dispatcher uses the cached Bot.me() before its own getUpdates
        # retry loop. The menu task shares that same identity lookup.
        async with self._identity_lock:
            return await _retry_telegram(super().me, "getMe", deadline_seconds=12.0)


async def _refresh_menu(
    bot: PollingBot, *, game_enabled: bool, saved_locales: dict[int, str]
) -> None:
    try:
        await bot.me()
        await _retry_telegram(
            partial(
                configure_commands, bot, game_enabled=game_enabled, saved_locales=saved_locales
            ),
            "command menu",
        )
    except TelegramAPIError as error:
        logger.warning("Telegram command menu setup rejected (%s)", type(error).__name__)


async def run_polling(
    bot: PollingBot, dispatcher: Dispatcher, *, game_enabled: bool, saved_locales: dict[int, str]
) -> None:
    menu = asyncio.create_task(
        _refresh_menu(bot, game_enabled=game_enabled, saved_locales=saved_locales)
    )
    try:
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        menu.cancel()
        with suppress(asyncio.CancelledError):
            await menu
