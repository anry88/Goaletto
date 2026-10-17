"""Friendly challenge commands and callbacks; commit before showing results."""

from datetime import UTC, date, datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update
from aiogram.types import InlineKeyboardMarkup as Keyboard
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_view import actor_for_callback
from goaletto.application.friendly import (
    challenge_days,
    challenge_opponents,
    invite_friendly,
    load_friendly_screen,
    respond_friendly,
    schedule_friendly,
)
from goaletto.application.game_actions import Actor, PermissionDenied, actor_for
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
from goaletto.infrastructure.models import OwnershipPeriod
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
        notice = tr("Матч с бот-клубом назначен. Смотрите /calendar.")
    else:
        await invite_friendly(session, actor, club_id, day)
        notice = tr("Вызов отправлен. Ответ появится здесь же.")
    return friendly_view(await load_friendly_screen(session, actor), notice=notice)


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
                text, keyboard = challenge_opponents_view(
                    day, await challenge_opponents(session, actor, day)
                )
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
                text, keyboard = friendly_view(await load_friendly_screen(session, actor), notice)
            elif data.startswith("fr:r:"):
                await respond_friendly(session, actor, parse_invite(data, "r"), accept=False)
                text, keyboard = friendly_view(
                    await load_friendly_screen(session, actor), tr("Вызов отклонён.")
                )
            elif data.startswith("fr:x:"):
                await respond_friendly(session, actor, parse_invite(data, "x"), accept=False)
                text, keyboard = friendly_view(
                    await load_friendly_screen(session, actor), tr("Вызов отозван.")
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
