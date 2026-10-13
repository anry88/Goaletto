"""Access rejects commands/callbacks before game handlers and database use."""

from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from pydantic import ValidationError

from goaletto.bot.main import build_dispatcher
from goaletto.config import Settings


class Telegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(
            message_id=9,
            date=datetime.now(UTC),
            chat=Chat(id=method.chat_id, type="private"),
            text=method.text,
        )

    async def stream_content(self, *args, **kwargs):
        yield b""


@pytest.mark.parametrize("game_enabled", [False, True])
@pytest.mark.parametrize("callback", [False, True])
async def test_denied_updates_never_reach_database(game_enabled, callback):
    # A sentinel database has no transaction method: calling a handler would fail.
    dispatcher = build_dispatcher(
        object(), game_enabled=game_enabled, allowed_telegram_ids=frozenset({1001, 1002})
    )
    transport = Telegram()
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    user = User(id=9999, is_bot=False, first_name="Denied")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        from_user=user,
        chat=Chat(id=user.id, type="private"),
        text="/start",
    )
    if callback:
        update = Update(
            update_id=1,
            callback_query=CallbackQuery(
                id="denied",
                from_user=user,
                chat_instance="fake",
                message=message,
                data="en:o:1" if game_enabled else "preview:squad",
            ),
        )
    else:
        update = Update(update_id=1, message=message)
    await dispatcher.feed_update(bot, update)
    assert len(transport.calls) == 1
    method = transport.calls[0]
    assert isinstance(method, AnswerCallbackQuery if callback else SendMessage)
    assert "закрытый тест" in (method.text or "")
    if callback:
        assert method.show_alert is True
    await bot.session.close()


@pytest.mark.parametrize("allowed_ids", [frozenset(), frozenset({1001, 1002})])
@pytest.mark.parametrize("user_id", [1001, 1002])
async def test_both_approved_accounts_reach_router(allowed_ids, user_id):
    dispatcher = build_dispatcher(None, game_enabled=False, allowed_telegram_ids=allowed_ids)
    transport = Telegram()
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    await dispatcher.feed_update(
        bot,
        Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                from_user=User(id=user_id, is_bot=False, first_name="Tester"),
                chat=Chat(id=user_id, type="private"),
                text="/start",
            ),
        ),
    )
    assert len(transport.calls) == 1
    assert "предварительный интерфейс" in transport.calls[0].text
    await bot.session.close()


def test_access_config_requires_positive_ids(monkeypatch):
    monkeypatch.setenv("GOALETTO_ALLOWED_TELEGRAM_IDS", "[1001,1002]")
    assert Settings(_env_file=None).allowed_telegram_ids == frozenset({1001, 1002})
    monkeypatch.setenv("GOALETTO_ALLOWED_TELEGRAM_IDS", "[0]")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
