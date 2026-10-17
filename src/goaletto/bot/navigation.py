"""Command fallbacks and authorized current/archived league table pagination."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_view import actor_for_callback, get_owned_club, get_squad_page
from goaletto.application.game_actions import Actor, PermissionDenied, actor_for, owned_club
from goaletto.application.history import get_club_results, get_season_archive
from goaletto.bot.club_cards import club_view, squad_view
from goaletto.bot.club_kit import show_club
from goaletto.bot.history_cards import results_view
from goaletto.bot.responses import edit_response
from goaletto.bot.result_views import expand_id, parse_table_callback, table_view
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import Season, SeasonClub
from goaletto.presentation.i18n import tr

router = Router(name="navigation")


async def current_table(session: AsyncSession, actor: Actor) -> tuple[str, InlineKeyboardMarkup]:
    _, club = await owned_club(session, actor)
    membership = await session.scalar(
        select(SeasonClub)
        .join(Season, Season.id == SeasonClub.season_id)
        .where(SeasonClub.club_id == club.id)
        .order_by(Season.starts_at.desc(), Season.id)
        .limit(1)
    )
    if membership is None:
        raise ValueError("Club has no season")
    archive = await get_season_archive(session, actor, membership.season_id)
    return table_view(archive.table, archive.season_id, archive.league_id, archive.tier)


@router.message(Command("club", "squad", "history", "table"))
async def game_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    action = (message.text or "").split()[0].split("@")[0]
    card = None
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            if action == "/squad":
                text, keyboard = squad_view(await get_squad_page(session, actor, 1))
            elif action == "/history":
                text, keyboard = results_view(await get_club_results(session, actor, 1))
            elif action == "/table":
                text, keyboard = await current_table(session, actor)
            else:
                card = await get_owned_club(session, actor)
                text, keyboard = club_view(card)
    except (PermissionDenied, ValueError):
        await message.answer(tr("Раздел недоступен. Откройте /start."))
        return
    if card is not None:
        await show_club(message, database, card, edit=False, view=(text, keyboard))
    else:
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "nav:table")
@router.callback_query(F.data.startswith("table:"))
async def league_screen(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session, telegram_id=query.from_user.id, callback_id=query.id, at=datetime.now(UTC)
            )
            if query.data == "nav:table":
                text, keyboard = await current_table(session, actor)
            else:
                season_id, league_id, page = parse_table_callback(query.data or "")
                archive = await get_season_archive(session, actor, season_id)
                if archive.league_id != league_id:
                    raise PermissionDenied("Foreign league")
                text, keyboard = table_view(archive.table, season_id, league_id, archive.tier, page)
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message, tr("Таблица недоступна или кнопка устарела. Откройте /table.")
        )
        return
    await edit_response(query.message, text, keyboard)


@router.callback_query(F.data.startswith("report:"))
async def old_report(query: CallbackQuery, database: Database) -> None:
    # Preserve old result buttons without granting a different authorization path.
    from goaletto.bot.history_router import _render

    await query.answer()
    if not isinstance(query.message, Message):
        return
    try:
        fixture_id = expand_id((query.data or "").split(":")[1])
        async with database.transaction() as session:
            text, keyboard = await _render(
                session, query.from_user.id, query.id, "match", fixture_id, 1
            )
    except (IndexError, ValueError, PermissionDenied):
        await edit_response(query.message, tr("Отчёт недоступен. Откройте /history."))
        return
    await edit_response(query.message, text, keyboard)
