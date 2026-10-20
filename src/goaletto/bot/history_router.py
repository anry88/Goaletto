"""History router. Handles hx: results/report/player/seasons/archive screens."""

from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from uuid import UUID, uuid5

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_view import CALLBACK_NAMESPACE, actor_for_callback
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.history import (
    get_club_results,
    get_club_seasons_page,
    get_match_report,
    get_player_matches,
    get_season_archive,
)
from goaletto.bot.club_cards import stale_view
from goaletto.bot.history_callbacks import (
    parse_context_report_callback,
    parse_history_callback,
    parse_report_callback,
)
from goaletto.bot.history_cards import (
    archive_view,
    player_history_view,
    report_view,
    results_view,
    seasons_view,
)
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.request_timing import observe_request
from goaletto.presentation.i18n import tr

router = Router(name="history-view")


@router.callback_query(F.data.startswith("hx:"))
async def history_screen(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    operation = uuid5(CALLBACK_NAMESPACE, f"goaletto:callback:v1:{query.id}")
    async with observe_request(database, operation, "history") as timing:
        try:
            kind, identity, page = parse_history_callback(query.data or "")
            async with timing.transaction() as session:
                render = await _load(session, user.id, query.id, kind, identity, page)
                if kind == "match" and identity is not None:
                    timing.bind(identity)
        except PermissionDenied:
            await timing.send(
                lambda: edit_response(
                    message, tr("Нет доступа: история недоступна. Откройте /start.")
                )
            )
            return
        except ValueError:
            render = stale_view
        text, keyboard = timing.render(render)
        await timing.send(lambda: edit_response(message, text, keyboard))


async def _render(
    session: AsyncSession,
    user_id: int,
    callback_id: str,
    kind: str,
    identity: UUID | None,
    page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    return (await _load(session, user_id, callback_id, kind, identity, page))()


async def _load(
    session: AsyncSession,
    user_id: int,
    callback_id: str,
    kind: str,
    identity: UUID | None,
    page: int,
) -> Callable[[], tuple[str, InlineKeyboardMarkup]]:
    actor = await actor_for_callback(
        session, telegram_id=user_id, callback_id=callback_id, at=datetime.now(UTC)
    )
    if kind == "results":
        return partial(results_view, await get_club_results(session, actor, page))
    if kind == "season_results":
        assert identity is not None
        return partial(
            results_view,
            await get_club_results(session, actor, page, season_id=identity),
            season_id=identity,
        )
    if kind == "match":
        assert identity is not None
        return partial(report_view, await get_match_report(session, actor, identity), page)
    if kind == "player":
        assert identity is not None
        return partial(
            player_history_view, await get_player_matches(session, actor, identity, page)
        )
    if kind == "seasons":
        data = await get_club_seasons_page(session, actor, page)
        return partial(seasons_view, data.entries, page=data.page, total_pages=data.total_pages)
    if kind == "archive":
        assert identity is not None
        return partial(archive_view, await get_season_archive(session, actor, identity), page)
    raise ValueError("Unknown history screen")


__all__ = ["router"]


@router.callback_query(F.data.startswith("hr:"))
@router.callback_query(F.data.startswith("ht:"))
async def report_page(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    message = query.message
    operation = uuid5(CALLBACK_NAMESPACE, f"goaletto:callback:v1:{query.id}")
    async with observe_request(database, operation, "history") as timing:
        try:
            data = query.data or ""
            origin = None
            if data.startswith("ht:"):
                fixture_id, section, page, origin = parse_context_report_callback(data)
                return_page = origin.page
            else:
                fixture_id, section, page, return_page = parse_report_callback(data)
            async with timing.transaction() as session:
                actor = await actor_for_callback(
                    session,
                    telegram_id=query.from_user.id,
                    callback_id=query.id,
                    at=datetime.now(UTC),
                )
                report = await get_match_report(session, actor, fixture_id)
                timing.bind(fixture_id)
            text, keyboard = timing.render(
                report_view, report, return_page, section=section, page=page, origin=origin
            )
        except (PermissionDenied, ValueError):
            await timing.send(
                lambda: edit_response(
                    message, tr("Отчёт недоступен или кнопка устарела. Откройте /history.")
                )
            )
            return
        await timing.send(lambda: edit_response(message, text, keyboard))
