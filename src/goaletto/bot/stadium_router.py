"""Stadium commands and scoped paid quotes; network after database commit."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.application.stadium import load_stadium, order_expansion
from goaletto.bot.responses import edit_response
from goaletto.bot.stadium_views import parse_stadium, stadium_view
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="stadium")


@router.message(Command("stadium"))
async def stadium_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = stadium_view(await load_stadium(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer(tr("Стадион недоступен. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("st:"))
async def stadium_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
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
            notice = None
            if query.data != "st:open":
                action, owner, capacity = parse_stadium(query.data or "")
                if action == "u" and capacity is not None:
                    await order_expansion(session, actor, owner, capacity)
                    notice = tr("Расширение заказано.")
                    screen = await load_stadium(session, actor, record=False)
                else:
                    screen = await load_stadium(session, actor)
                if screen.ownership_id != owner:
                    raise PermissionDenied("Stadium button belongs to an earlier owner")
            else:
                screen = await load_stadium(session, actor)
            text, keyboard = stadium_view(screen, notice)
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message,
            tr(
                "Расширение не заказано: кнопка устарела, уже идёт стройка или недостат"
                "очно средств. Откройте /stadium."
            ),
        )
        return
    await edit_response(query.message, text, keyboard)
