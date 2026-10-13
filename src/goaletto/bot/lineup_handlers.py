"""Lineup editor router. Game state lives in PostgreSQL; callbacks carry revision.

Handlers use short database transactions and send/edit only after commit.
Mutation callbacks carry ownership/revision and use the real Telegram update ID.
"""

from datetime import UTC, datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, Update
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import Actor, PermissionDenied, actor_for
from goaletto.application.lineup import (
    LineupData,
    LineupInput,
    SaveResult,
    load_lineup,
    open_lineup,
    save_lineup,
)
from goaletto.bot import lineup_views as views
from goaletto.bot.responses import edit_response
from goaletto.bot.result_views import expand_id
from goaletto.domain.lineup import (
    LineupInvalid,
    SlotCandidate,
    StarterPick,
    reassign_slots,
)
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="lineup")


async def build_open_response(
    session: AsyncSession, telegram_id: int, callback_id: str, at: datetime
) -> tuple[str, InlineKeyboardMarkup]:
    actor = await actor_for_callback(
        session, telegram_id=telegram_id, callback_id=callback_id, at=at
    )
    data = await open_lineup(session, actor)
    return views.render_main(data), views.main_keyboard(data)


async def build_callback_response(
    session: AsyncSession,
    telegram_id: int,
    callback_id: str,
    callback_data: str,
    at: datetime,
    *,
    update_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    actor = await actor_for_callback(
        session, telegram_id=telegram_id, callback_id=callback_id, at=at, update_id=update_id
    )
    action, args = views.parse_callback(callback_data)
    data = await load_lineup(session, actor)
    version = args[-1] if args else str(data.version)
    if args and expand_id(args[-2]) != data.ownership_id:
        return views.render_main(
            data, tr("Кнопка от прежнего владения: состав не изменён.")
        ), views.main_keyboard(data)
    if str(data.version) != version:
        return (
            views.render_main(data, tr("Экран устарел — показана актуальная версия.")),
            views.main_keyboard(data),
        )
    result = await _dispatch(session, actor, data, action, args)
    if result is not None:
        data = await load_lineup(session, actor)
    if action == "panel":
        return views.render_main(data), _panel_keyboard(args[0], data)
    if action == "sel":
        return views.render_main(data), views.swap_keyboard(data, int(args[0]))
    return views.render_main(data), views.main_keyboard(data)


@router.message(Command("lineup"))
async def lineup_command(message: Message, database: Database, event_update: Update) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, telegram_id=user.id, update_id=event_update.update_id, at=datetime.now(UTC)
            )
            data = await open_lineup(session, actor)
    except PermissionDenied:
        await message.answer(tr("Клуб недоступен: нет действующего владения."))
        return
    except (LineupInvalid, ValueError):
        await message.answer(tr("Состав недоступен. Откройте /start или попробуйте /lineup позже."))
        return
    await message.answer(views.render_main(data), reply_markup=views.main_keyboard(data))


@router.callback_query(F.data == "ln:open")
async def lineup_open(query: CallbackQuery, database: Database, event_update: Update) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    try:
        async with database.transaction() as session:
            text, keyboard = await build_open_response(
                session, user.id, query.id, datetime.now(UTC)
            )
    except PermissionDenied:
        await edit_response(message, tr("Нет действующего владения клубом. Откройте /start."))
        return
    except (LineupInvalid, ValueError):
        await edit_response(message, tr("Состав недоступен. Откройте /lineup заново."))
        return
    await edit_response(message, text, keyboard)


@router.callback_query(F.data.startswith("ln:"))
async def lineup_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    try:
        async with database.transaction() as session:
            text, keyboard = await build_callback_response(
                session,
                user.id,
                query.id,
                query.data or "",
                datetime.now(UTC),
                update_id=event_update.update_id,
            )
    except PermissionDenied:
        await edit_response(message, tr("Нет действующего владения клубом. Откройте /start."))
        return
    except (LineupInvalid, ValueError):
        await edit_response(
            message,
            tr(
                "Состав не изменён: экран устарел, игрок недоступен или "
                "состав уже заблокирован. Откройте /lineup заново."
            ),
        )
        return
    await edit_response(message, text, keyboard)


def _panel_keyboard(panel: str, data: LineupData) -> InlineKeyboardMarkup:
    if panel == "formation":
        return views.formation_keyboard(data)
    if panel == "tactic":
        return views.tactic_keyboard(data)
    if panel == "mentality":
        return views.mentality_keyboard(data)
    if panel == "swap":
        return views.starter_keyboard(data)
    if panel == "captain":
        return views.captain_keyboard(data)
    return views.main_keyboard(data)


async def _dispatch(
    session: AsyncSession, actor: Actor, data: LineupData, action: str, args: tuple[str, ...]
) -> SaveResult | None:
    if action in {"open", "refresh", "panel", "sel"}:
        return None
    if action == "f":
        candidates = tuple(
            SlotCandidate(m.person_id, m.position, m.alternatives) for m in data.starters
        )
        starters = reassign_slots(candidates, args[0])
        new_captain: UUID = data.captain_id or starters[0].person_id
        return await save_lineup(
            session,
            actor,
            LineupInput(
                starters,
                tuple(m.person_id for m in data.bench),
                args[0],
                new_captain,
                data.tactic,
                data.mentality,
                data.version,
            ),
        )
    if action == "t":
        return await save_lineup(
            session,
            actor,
            LineupInput(
                _current_picks(data),
                tuple(m.person_id for m in data.bench),
                data.formation,
                _require_captain(data),
                args[0],
                data.mentality,
                data.version,
            ),
        )
    if action == "mo":
        return await save_lineup(
            session,
            actor,
            LineupInput(
                _current_picks(data),
                tuple(m.person_id for m in data.bench),
                data.formation,
                _require_captain(data),
                data.tactic,
                args[0],
                data.version,
            ),
        )
    if action == "sw":
        slot = int(args[0])
        if not 1 <= slot <= len(data.starters):
            raise LineupInvalid("count", "Unknown starter slot")
        incoming = expand_id(args[1])
        outgoing = data.starters[slot - 1]
        bench_ids = tuple(m.person_id for m in data.bench)
        if incoming not in bench_ids:
            raise LineupInvalid("foreign", "Swap needs a bench player")
        starters = tuple(
            StarterPick(
                incoming if m.person_id == outgoing.person_id else m.person_id,
                outgoing.slot_position if m.person_id == outgoing.person_id else m.slot_position,
            )
            for m in data.starters
        )
        bench = tuple(outgoing.person_id if p == incoming else p for p in bench_ids)
        swapped_captain = data.captain_id
        if swapped_captain is None or swapped_captain == outgoing.person_id:
            swapped_captain = incoming
        return await save_lineup(
            session,
            actor,
            LineupInput(
                starters,
                bench,
                data.formation,
                swapped_captain,
                data.tactic,
                data.mentality,
                data.version,
            ),
        )
    if action == "cap":
        person_id = expand_id(args[0])
        if person_id not in {m.person_id for m in data.starters}:
            raise LineupInvalid("captain", "The captain must be one of the eleven starters")
        return await save_lineup(
            session,
            actor,
            LineupInput(
                _current_picks(data),
                tuple(m.person_id for m in data.bench),
                data.formation,
                person_id,
                data.tactic,
                data.mentality,
                data.version,
            ),
        )
    raise ValueError("Unknown lineup action")


def _current_picks(data: LineupData) -> tuple[StarterPick, ...]:
    return tuple(StarterPick(m.person_id, m.slot_position) for m in data.starters)


def _require_captain(data: LineupData) -> UUID:
    if data.captain_id is None:
        raise LineupInvalid("captain", "Choose a captain first")
    return data.captain_id


__all__ = ["build_callback_response", "build_open_response", "router"]
