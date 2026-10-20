"""Host-configured access gate, before handlers or durable game mutations."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from goaletto.presentation.i18n import tr


class TelegramAccess(BaseMiddleware):
    def __init__(self, allowed_ids: frozenset[int], admin_chat: int = 0) -> None:
        self.allowed_ids = allowed_ids
        self.admin_chat = admin_chat

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            text = event.text or ""
            command = text.split(" ", 1)[0].split("@")[0]
            if (
                event.successful_payment
                or event.refunded_payment
                or command in {"/paysupport", "/answer"}
            ):
                return await handler(event, data)
            if event.chat.id == self.admin_chat and command in {"/refund", "/reject", "/ask"}:
                return await handler(event, data)
        if not self.allowed_ids:
            return await handler(event, data)
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
            if user is not None and user.id in self.allowed_ids:
                return await handler(event, data)
            text = tr("Сейчас Goaletto проходит закрытый тест. Доступ пока ограничен.")
            data["metrics_access_denied"] = True
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                await event.answer(text)
        return None
