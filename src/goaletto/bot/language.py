"""Short database read before handling; locale context is scoped to one update."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import Manager
from goaletto.presentation.i18n import using_locale


class TelegramLanguage(BaseMiddleware):
    def __init__(self, database: Database | None) -> None:
        self.database = database

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        value = "ru"
        if (
            self.database is not None
            and isinstance(event, (Message, CallbackQuery))
            and event.from_user
        ):
            async with self.database.transaction() as session:
                saved = await session.scalar(
                    select(Manager.locale).where(Manager.telegram_id == event.from_user.id)
                )
                value = saved if saved in {"ru", "en"} else "ru"
        with using_locale(value):
            return await handler(event, data)
