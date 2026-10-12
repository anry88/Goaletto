"""Local aiogram exception mapping; no Telegram requests."""

from types import SimpleNamespace
from typing import cast

import pytest
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage

from goaletto.application.outbox_delivery import (
    Blocked,
    ChatUnavailable,
    InvalidRequest,
    MessageMissing,
    RateLimited,
    TransportTimeout,
)
from goaletto.infrastructure.telegram_transport import REQUEST_TIMEOUT, AiogramTransport

METHOD = SendMessage(chat_id=123, text="test")


class BotStub:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def send_message(self, *args, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(message_id=42)

    async def edit_message_text(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error


@pytest.mark.parametrize(
    "error,expected",
    [
        (TelegramForbiddenError(METHOD, "bot was blocked by the user"), Blocked),
        (TelegramBadRequest(METHOD, "chat not found"), ChatUnavailable),
        (TelegramBadRequest(METHOD, "can't parse entities"), InvalidRequest),
        (TelegramNetworkError(METHOD, "network failed"), TransportTimeout),
        (TimeoutError(), TransportTimeout),
        (TelegramRetryAfter(METHOD, "flood control", 1800), RateLimited),
    ],
)
async def test_send_maps_actual_aiogram_errors_and_keeps_entire_retry_after(error, expected):
    stub = BotStub(error)
    with pytest.raises(expected) as result:
        await AiogramTransport(cast(Bot, stub)).send_text(123, "safe text")
    if isinstance(result.value, RateLimited):
        assert result.value.retry_after == 1800
    assert stub.calls[0]["request_timeout"] == REQUEST_TIMEOUT
    assert stub.calls[0]["parse_mode"] == "HTML"


async def test_not_modified_edit_succeeds_but_missing_message_is_distinct_from_missing_chat():
    transport = AiogramTransport(
        cast(Bot, BotStub(TelegramBadRequest(METHOD, "message is not modified")))
    )
    await transport.edit_text(123, 42, "same text")
    for text, expected in [
        ("message to edit not found", MessageMissing),
        ("chat not found", ChatUnavailable),
        ("can't parse entities", InvalidRequest),
    ]:
        transport = AiogramTransport(cast(Bot, BotStub(TelegramBadRequest(METHOD, text))))
        with pytest.raises(expected):
            await transport.edit_text(123, 42, "new text")


async def test_successful_send_persists_api_message_id():
    stub = BotStub()
    assert (await AiogramTransport(cast(Bot, stub)).send_text(123, "text")).message_id == 42
