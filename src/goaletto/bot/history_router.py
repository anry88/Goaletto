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
    get_club_seasons,
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

router = Router(name="history-view")


@router.callback_query(F.data.startswith("hx:"))
async def history_screen(query: CallbackQuery, session: AsyncSession) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    try:
        kind, identity, page = parse_history_callback(query.data or "")
        text, keyboard = await _render(session, user.id, query.id, kind, identity, page)
        await message.edit_text(text, reply_markup=keyboard)
    except PermissionDenied:
        await message.edit_text("Нет доступа: история недоступна. Откройте /start.")
    except ValueError:
        text, keyboard = stale_view()
        await message.edit_text(text, reply_markup=keyboard)


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
        return seasons_view(await get_club_seasons(session, actor))
    if kind == "archive":
        assert identity is not None
        return archive_view(await get_season_archive(session, actor, identity))
    raise ValueError("Unknown history screen")


__all__ = ["router"]
