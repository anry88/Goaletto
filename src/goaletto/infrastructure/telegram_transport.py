"""Bounded aiogram transport; errors never expose response text or bot credentials."""

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from goaletto.application.outbox_delivery import (
    Blocked,
    ChatUnavailable,
    InvalidRequest,
    MessageMissing,
    RateLimited,
    SendResult,
    TransportTimeout,
    UnknownOutcome,
)

REQUEST_TIMEOUT = 25


class _NotModified(Exception):
    pass


def _classify_error(error: Exception, *, editing: bool = False) -> Exception:
    if isinstance(error, TelegramRetryAfter):
        return RateLimited(float(error.retry_after))
    if isinstance(error, TelegramForbiddenError):
        text = error.message.lower()
        if "blocked" in text or "deactivated" in text:
            return Blocked()
        return ChatUnavailable()
    if isinstance(error, TelegramBadRequest):
        text = error.message.lower()
        if "chat not found" in text or "chat_id is empty" in text:
            return ChatUnavailable()
        if editing and "message is not modified" in text:
            return _NotModified()
        if editing and ("message to edit not found" in text or "message can't be edited" in text):
            return MessageMissing()
        return InvalidRequest()
    if isinstance(error, (TelegramNetworkError, TimeoutError)):
        return TransportTimeout()
    return UnknownOutcome()


class AiogramTransport:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_text(self, chat_id: int, text: str) -> SendResult:
        try:
            sent = await self._bot.send_message(
                chat_id, text, parse_mode=ParseMode.HTML, request_timeout=REQUEST_TIMEOUT
            )
        except Exception as error:
            raise _classify_error(error) from error
        if sent.message_id <= 0:
            raise UnknownOutcome()
        return SendResult(sent.message_id)

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            await self._bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode=ParseMode.HTML,
                request_timeout=REQUEST_TIMEOUT,
            )
        except Exception as error:
            mapped = _classify_error(error, editing=True)
            if isinstance(mapped, _NotModified):
                return
            raise mapped from error
