"""Notification settings router. Commits state before any Telegram I/O.

Game state lives in PostgreSQL; callbacks carry only the explicit desired
value plus the ownership they were rendered for. The application layer
rechecks manager and ownership under lock. No FSM game state, no network or
wall-clock reads inside the transaction.
"""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, Update
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.application.notification_preferences import get_settings, set_reminders_enabled
from goaletto.bot.responses import edit_response
from goaletto.bot.settings_views import parse_settings_callback, settings_view, stale_settings_view
from goaletto.infrastructure.database import Database

router = Router(name="notification-settings")


async def build_settings_response(
    session: AsyncSession, telegram_id: int, callback_id: str, at: datetime
) -> tuple[str, InlineKeyboardMarkup]:
    actor = await actor_for_callback(
        session, telegram_id=telegram_id, callback_id=callback_id, at=at
    )
    return settings_view(await get_settings(session, actor))


@router.message(Command("settings"))
async def settings_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None or message.chat.type != "private":
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = settings_view(await get_settings(session, actor))
    except PermissionDenied:
        await message.answer("Сначала откройте /start, затем /settings.")
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("ntf:"))
async def settings_callback(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    try:
        action, ownership_id = parse_settings_callback(query.data or "")
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session, telegram_id=user.id, callback_id=query.id, at=datetime.now(UTC)
            )
            if action == "open":
                text, keyboard = settings_view(await get_settings(session, actor))
            elif action in {"on", "off"} and ownership_id is not None:
                await set_reminders_enabled(
                    session,
                    actor,
                    enabled=(action == "on"),
                    ownership_id=ownership_id,
                )
                text, keyboard = settings_view(await get_settings(session, actor))
            else:
                raise ValueError("Stale settings callback")
    except (PermissionDenied, ValueError):
        text, keyboard = stale_settings_view()
        await edit_response(message, text, keyboard)
        return
    await edit_response(message, text, keyboard)
