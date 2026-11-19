import asyncio
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramRetryAfter, TelegramServerError, TelegramUnauthorizedError
from aiogram.methods import GetMe, SetMyCommands
from aiogram.types import User

from goaletto.bot import startup

TOKEN = "123456:FAKE_LOCAL_TEST_TOKEN"


async def test_polling_identity_retries_and_shares_cached_account(monkeypatch):
    identity = User(id=123456, is_bot=True, first_name="Goaletto")
    get_me = AsyncMock(
        side_effect=[
            TelegramServerError(GetMe(), "Bad Gateway"),
            TelegramRetryAfter(GetMe(), "Rate limited", 17),
            identity,
        ]
    )
    wait = AsyncMock()
    monkeypatch.setattr(Bot, "get_me", get_me)
    monkeypatch.setattr(startup, "sleep", wait)
    async with startup.PollingBot(TOKEN).context() as bot:
        polling, menu = await asyncio.gather(bot.me(), bot.me())

    assert polling is menu is identity
    assert get_me.await_count == 3
    assert [call.args for call in wait.await_args_list] == [(2.0,), (17.0,)]


async def test_permanent_identity_error_is_not_retried(monkeypatch):
    get_me = AsyncMock(side_effect=TelegramUnauthorizedError(GetMe(), "Unauthorized"))
    monkeypatch.setattr(Bot, "get_me", get_me)
    async with startup.PollingBot(TOKEN).context() as bot:
        with pytest.raises(TelegramUnauthorizedError):
            await bot.me()
    assert get_me.await_count == 1


async def test_polling_starts_while_menu_update_fails_and_cancels_retry_on_shutdown(monkeypatch):
    identity = User(id=123456, is_bot=True, first_name="Goaletto")
    retrying = asyncio.Event()
    retry_cancelled = asyncio.Event()

    async def pause(_):
        retrying.set()
        try:
            await asyncio.Event().wait()
        finally:
            retry_cancelled.set()

    async def start_polling(self, bot, *, close_bot_session):
        assert not close_bot_session and await bot.me() == identity
        await asyncio.wait_for(retrying.wait(), timeout=1)
        assert not retry_cancelled.is_set()

    get_me = AsyncMock(return_value=identity)
    update = AsyncMock(side_effect=TelegramServerError(SetMyCommands(commands=[]), "Bad Gateway"))
    monkeypatch.setattr(Bot, "get_me", get_me)
    monkeypatch.setattr(Dispatcher, "start_polling", start_polling)
    monkeypatch.setattr(startup, "configure_commands", update)
    monkeypatch.setattr(startup, "sleep", pause)
    async with startup.PollingBot(TOKEN).context() as bot:
        await startup.run_polling(bot, Dispatcher(), game_enabled=True, saved_locales={})

    assert retry_cancelled.is_set()
    assert get_me.await_count == update.await_count == 1
