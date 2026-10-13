"""Club home router. Handles vc:c and vc:menu. Old callbacks are ignored."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_view import actor_for_callback, get_owned_club
from goaletto.application.game_actions import PermissionDenied
from goaletto.bot.club_cards import (
    CLUB_CALLBACK,
    MENU_CALLBACK,
    club_view,
    menu_view,
    stale_view,
)
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="club-view")


async def build_club_response(
    session: AsyncSession, telegram_id: int, callback_id: str, at: datetime
) -> tuple[str, InlineKeyboardMarkup]:
    actor = await actor_for_callback(
        session, telegram_id=telegram_id, callback_id=callback_id, at=at
    )
    card = await get_owned_club(session, actor)
    return club_view(card)


@router.callback_query(F.data == CLUB_CALLBACK)
async def club_home(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    try:
        async with database.transaction() as session:
            text, keyboard = await build_club_response(
                session, user.id, query.id, datetime.now(UTC)
            )
        await edit_response(message, text, keyboard)
    except PermissionDenied:
        await edit_response(
            message, tr("Нет доступа: клуб больше не принадлежит вам. Откройте /start.")
        )
    except ValueError:
        text, keyboard = stale_view()
        await edit_response(message, text, keyboard)


@router.callback_query(F.data == MENU_CALLBACK)
async def main_menu(query: CallbackQuery) -> None:
    await query.answer()
    message = query.message
    if not isinstance(message, Message):
        return
    text, keyboard = menu_view()
    await edit_response(message, text, keyboard)


@router.callback_query(
    lambda q: (
        type(q.data) is str
        and q.data.startswith("vc:")
        and q.data not in {CLUB_CALLBACK, MENU_CALLBACK}
        and not q.data.startswith("vc:s:")
        and not q.data.startswith("vc:p:")
    )
)
async def club_fallback(query: CallbackQuery, database: Database) -> None:
    """Unknown vc: values land here: never apply an action, show fresh state."""
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    try:
        async with database.transaction() as session:
            text, keyboard = await build_club_response(
                session, user.id, query.id, datetime.now(UTC)
            )
    except (PermissionDenied, ValueError):
        text, keyboard = stale_view()
    await edit_response(message, text, keyboard)
