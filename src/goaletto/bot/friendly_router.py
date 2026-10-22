"""Friendly challenge commands and callbacks; commit before showing results."""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from aiogram import F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ForceReply, Message, Update
from aiogram.types import InlineKeyboardMarkup as Keyboard
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.club_view import actor_for_callback
from goaletto.application.friendly import (
    begin_opponent_search,
    challenge_days,
    challenge_opponents,
    invite_friendly,
    load_friendly_screen,
    respond_friendly,
    schedule_friendly,
)
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    actor_for,
    owned_club,
    user_effect,
)
from goaletto.bot.friendly_views import (
    challenge_dates_view,
    challenge_opponents_view,
    friendly_view,
    parse_day,
    parse_invite,
    parse_opponent,
)
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import FriendlySearch, Manager, OwnershipPeriod
from goaletto.presentation.i18n import tr

router = Router(name="friendly")


async def _challenge(
    session: AsyncSession, actor: Actor, day: date, club_id: UUID
) -> tuple[str, Keyboard]:
    owned = await session.scalar(
        select(OwnershipPeriod.id).where(
            OwnershipPeriod.club_id == club_id,
            OwnershipPeriod.ended_at.is_(None),
            OwnershipPeriod.expires_at > actor.at,
        )
    )
    if owned is None:
        await schedule_friendly(session, actor, club_id, day)
        notice = tr("Матч назначен. Смотрите /calendar.")
    else:
        await invite_friendly(session, actor, club_id, day)
        notice = tr("Вызов отправлен. Ответ появится здесь же.")
    return friendly_view(await load_friendly_screen(session, actor, record=False), notice=notice)


@router.message(Command("friendly"))
async def friendly_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = friendly_view(await load_friendly_screen(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer(tr("Товарищеские матчи недоступны. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("fr:"))
async def friendly_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    search_owner = None
    try:
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session,
                telegram_id=query.from_user.id,
                callback_id=query.id,
                at=datetime.now(UTC),
                update_id=event_update.update_id,
            )
            data = query.data or ""
            if data == "fr:open":
                text, keyboard = friendly_view(await load_friendly_screen(session, actor))
            elif data == "fr:c":
                text, keyboard = challenge_dates_view(await challenge_days(session, actor))
            elif data.startswith("fr:d:"):
                day = parse_day(data)
                search_owner = await begin_opponent_search(session, actor, day)
                text = tr("Ответьте на сообщение поиска названием команды или его частью.")
                keyboard = None
            elif data.startswith("fr:o:"):
                day, club_id = parse_opponent(data)
                text, keyboard = await _challenge(session, actor, day, club_id)
            elif data.startswith("fr:a:"):
                row = await respond_friendly(session, actor, parse_invite(data, "a"), accept=True)
                notice = (
                    tr("Матч назначен. Смотрите /calendar.")
                    if row.status == "accepted"
                    else tr("Вызов уже разобран. Откройте /friendly.")
                )
                text, keyboard = friendly_view(
                    await load_friendly_screen(session, actor, record=False), notice
                )
            elif data.startswith(("fr:r:", "fr:x:")):
                row = await respond_friendly(
                    session, actor, parse_invite(data, data.split(":")[1]), accept=False
                )
                notice = (
                    tr("Вызов отклонён.")
                    if row.status == "declined"
                    else tr("Вызов отозван.")
                    if row.status == "cancelled"
                    else tr("Вызов уже разобран. Откройте /friendly.")
                )
                text, keyboard = friendly_view(
                    await load_friendly_screen(session, actor, record=False), notice
                )
            else:
                raise ValueError("Invalid friendly callback")
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message,
            tr("Кнопка устарела: день занят, вызов разобран или клуб сменил владельца."),
        )
        return
    await edit_response(query.message, text, keyboard)
    if search_owner is not None:
        prompt = await query.message.answer(
            tr(
                "🔎 Соперник на {day} UTC. Ответьте названием команды или его частью.",
                day=day.strftime("%d.%m.%Y"),
            ),
            reply_markup=ForceReply(selective=True),
        )
        async with database.transaction() as session:
            search = await session.get(FriendlySearch, search_owner, with_for_update=True)
            if search and search.selected_at == actor.at and search.assigned_day == day:
                search.prompt_message_id = prompt.message_id


@router.message(F.text, F.reply_to_message.from_user.is_bot, ~F.text.startswith("/"))
async def opponent_search_message(
    message: Message, database: Database, event_update: Update
) -> Any:
    if message.from_user is None or message.reply_to_message is None:
        return UNHANDLED
    try:
        async with database.transaction() as session:
            row = await session.scalar(
                select(FriendlySearch)
                .join(OwnershipPeriod)
                .join(Manager)
                .where(
                    Manager.telegram_id == message.from_user.id,
                    OwnershipPeriod.ended_at.is_(None),
                    OwnershipPeriod.expires_at > datetime.now(UTC),
                    FriendlySearch.prompt_message_id == message.reply_to_message.message_id,
                )
            )
            if row is None:
                return UNHANDLED
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            owner, club = await owned_club(session, actor)
            await user_effect(
                session,
                actor,
                "friendly_opened",
                Surface.CALENDAR,
                ownership=owner,
                club_id=club.id,
            )
            text, keyboard = challenge_opponents_view(
                row.assigned_day,
                await challenge_opponents(session, actor, row.assigned_day, message.text or ""),
            )
    except (PermissionDenied, ValueError):
        await message.answer(tr("Поиск устарел. Выберите дату заново в /friendly."))
        return None
    await message.answer(text, reply_markup=keyboard)
    return None
