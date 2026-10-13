"""Squad list router. Handles vc:s:<page>. Paginated, 5 players per page."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_view import actor_for_callback, get_squad_page
from goaletto.application.game_actions import PermissionDenied
from goaletto.bot.club_cards import parse_view_callback, squad_view, stale_view
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="squad-view")


async def build_squad_response(
    session: AsyncSession, telegram_id: int, callback_id: str, at: datetime, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    actor = await actor_for_callback(
        session, telegram_id=telegram_id, callback_id=callback_id, at=at
    )
    data = await get_squad_page(session, actor, page)
    return squad_view(data)


@router.callback_query(F.data.startswith("vc:s:"))
async def squad_page(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    try:
        kind, _, page = parse_view_callback(query.data or "")
        if kind != "squad":
            raise ValueError("Not a squad callback")
        async with database.transaction() as session:
            text, keyboard = await build_squad_response(
                session, user.id, query.id, datetime.now(UTC), page
            )
        await edit_response(message, text, keyboard)
    except PermissionDenied:
        await edit_response(
            message, tr("Нет доступа: клуб больше не принадлежит вам. Откройте /start.")
        )
    except ValueError:
        text, keyboard = stale_view()
        await edit_response(message, text, keyboard)
