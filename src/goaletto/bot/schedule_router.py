"""Calendar commands and stateless, ownership-bound pagination."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.application.schedule import load_schedule
from goaletto.bot.responses import edit_response
from goaletto.bot.result_views import expand_id
from goaletto.bot.schedule_views import schedule_view
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="schedule")


@router.message(Command("calendar", "fixtures"))
async def calendar_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = schedule_view(await load_schedule(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer(tr("Расписание недоступно. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("ca:"))
async def calendar_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session,
                telegram_id=query.from_user.id,
                callback_id=query.id,
                at=datetime.now(UTC),
                update_id=event_update.update_id,
            )
            data = query.data or ""
            if data == "ca:open":
                screen = await load_schedule(session, actor)
            else:
                parts = data.split(":")
                if (
                    len(data.encode()) > 64
                    or len(parts) != 4
                    or parts[1] not in ("list", "cup", "champions")
                    or not parts[3].isascii()
                    or not parts[3].isdigit()
                ):
                    raise ValueError("Invalid schedule callback")
                screen = await load_schedule(
                    session,
                    actor,
                    int(parts[3]),
                    ownership_id=expand_id(parts[2]),
                    competition=None if parts[1] == "list" else parts[1],
                )
            text, keyboard = schedule_view(screen)
    except (PermissionDenied, ValueError):
        await edit_response(query.message, tr("Кнопка расписания устарела. Откройте /calendar."))
        return
    await edit_response(query.message, text, keyboard)
