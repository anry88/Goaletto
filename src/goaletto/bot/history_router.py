"""History router. Handles hx: results/report/player/seasons/archive screens."""

from datetime import UTC, datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.history import (
    get_club_results,
    get_club_seasons_page,
    get_match_report,
    get_player_matches,
    get_season_archive,
)
from goaletto.bot.club_cards import stale_view
from goaletto.bot.history_callbacks import parse_history_callback
from goaletto.bot.history_cards import (
    archive_view,
    player_history_view,
    report_view,
    results_view,
    seasons_view,
)
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database

router = Router(name="history-view")


@router.callback_query(F.data.startswith("hx:"))
async def history_screen(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    try:
        kind, identity, page = parse_history_callback(query.data or "")
        async with database.transaction() as session:
            text, keyboard = await _render(session, user.id, query.id, kind, identity, page)
        await edit_response(message, text, keyboard)
    except PermissionDenied:
        await edit_response(message, "Нет доступа: история недоступна. Откройте /start.")
    except ValueError:
        text, keyboard = stale_view()
        await edit_response(message, text, keyboard)


async def _render(
    session: AsyncSession,
    user_id: int,
    callback_id: str,
    kind: str,
    identity: UUID | None,
    page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    actor = await actor_for_callback(
        session, telegram_id=user_id, callback_id=callback_id, at=datetime.now(UTC)
    )
    if kind == "results":
        return results_view(await get_club_results(session, actor, page))
    if kind == "match":
        assert identity is not None
        return report_view(await get_match_report(session, actor, identity), page)
    if kind == "player":
        assert identity is not None
        return player_history_view(await get_player_matches(session, actor, identity, page))
    if kind == "seasons":
        data = await get_club_seasons_page(session, actor, page)
        return seasons_view(data.entries, page=data.page, total_pages=data.total_pages)
    if kind == "archive":
        assert identity is not None
        return archive_view(await get_season_archive(session, actor, identity))
    raise ValueError("Unknown history screen")


__all__ = ["router"]
