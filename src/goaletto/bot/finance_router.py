"""Owned finance screen, committed before Telegram output."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update

from goaletto.application.club_view import actor_for_callback
from goaletto.application.finances import load_finances
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.bot.finance_views import finance_view, parse_page
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="finances")


@router.message(Command("finance", "finances"))
async def finance_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = finance_view(await load_finances(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer(tr("Финансы недоступны. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("fi:"))
async def finance_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
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
            ownership_id, page = (
                (None, 0) if query.data == "fi:open" else parse_page(query.data or "")
            )
            text, keyboard = finance_view(await load_finances(session, actor, page, ownership_id))
    except (PermissionDenied, ValueError):
        await edit_response(query.message, tr("Кнопка устарела. Откройте /finance."))
        return
    await edit_response(query.message, text, keyboard)
