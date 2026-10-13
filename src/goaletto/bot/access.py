"""Host-configured access gate, before handlers or durable game mutations."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class TelegramAccess(BaseMiddleware):
    def __init__(self, allowed_ids: frozenset[int]) -> None:
        self.allowed_ids = allowed_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not self.allowed_ids:
            return await handler(event, data)
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
            if user is not None and user.id in self.allowed_ids:
                return await handler(event, data)
            text = "Сейчас Goaletto проходит закрытый тест. Доступ пока ограничен."
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                await event.answer(text)
        return None
