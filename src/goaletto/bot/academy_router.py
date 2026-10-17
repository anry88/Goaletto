"""Academy commands and scoped paid actions; network after database commit."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update

from goaletto.application.academy import load_academy, sign_candidate, upgrade_academy
from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.bot.academy_views import academy_view, parse_academy
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="academy")


@router.message(Command("academy"))
async def academy_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = academy_view(await load_academy(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer(tr("Академия недоступна. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("ac:"))
async def academy_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
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
            if query.data != "ac:open":
                action, owner, slot, season_id = parse_academy(query.data or "")
                if action == "u":
                    await upgrade_academy(session, actor, owner, expected_level=slot)
                    notice = tr("Академия улучшена.")
                    screen = await load_academy(session, actor, record=False)
                elif action == "g" and slot is not None:
                    await sign_candidate(session, actor, owner, slot, season_id=season_id)
                    notice = tr("Юноша подписан.")
                    screen = await load_academy(session, actor, record=False)
                else:
                    screen = await load_academy(session, actor)
                if screen.ownership_id != owner:
                    raise PermissionDenied("Academy button belongs to an earlier owner")
            else:
                screen = await load_academy(session, actor)
            text, keyboard = academy_view(screen, notice)
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message,
            tr(
                "Действие не выполнено: кнопка устарела, нет свободных денег, лимит "
                "подписаний или состав заполнен. Откройте /academy."
            ),
        )
        return
    await edit_response(query.message, text, keyboard)
