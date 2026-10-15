"""Scouting callbacks commit paid commands before Telegram delivery."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.application.scouting import (
    cancel_search,
    decline_finding,
    finding_for,
    load_scout,
    sign_finding,
    start_search,
)
from goaletto.bot import scouting_views as views
from goaletto.bot.responses import edit_response
from goaletto.bot.result_views import expand_id
from goaletto.domain.finances import scout_mission_fee
from goaletto.domain.market import decode_price
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="scouting")


@router.message(Command("scout"))
async def scout_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = views.view(await load_scout(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer(tr("Раздел недоступен. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("ss:"))
async def scout_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
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
            raw = query.data or ""
            if len(raw.encode()) > 64:
                raise ValueError("Invalid callback")
            p = raw.split(":")
            screen = await load_scout(session, actor, record=False)
            if p == ["ss", "open"]:
                text, keyboard = views.view(screen)
            else:
                if len(p) < 3 or expand_id(p[2]) != screen.ownership_id:
                    raise PermissionDenied("Old ownership")
                action = p[1]
                if action == "n" and len(p) == 3:
                    text, keyboard = views.positions(screen.ownership_id)
                elif action == "p" and len(p) == 4:
                    text, keyboard = views.durations(screen, p[3])
                elif action in {"q", "b"} and len(p) == 7:
                    scout, position, days, fee = (
                        expand_id(p[3]),
                        p[4],
                        int(p[5]),
                        decode_price(p[6]),
                    )
                    if (
                        screen.scout is None
                        or scout != screen.scout.id
                        or fee != scout_mission_fee(screen.skill, days)
                    ):
                        raise ValueError("Search quote changed")
                    if action == "q":
                        text, keyboard = views.search_quote(
                            screen.ownership_id, scout, position, days, fee, raw
                        )
                    else:
                        await start_search(
                            session, actor, screen.ownership_id, scout, position, days, fee
                        )
                        text, keyboard = views.view(await load_scout(session, actor, record=False))
                elif action in {"r", "c", "k"} and len(p) == 4:
                    mission = expand_id(p[3])
                    if action == "r":
                        text, keyboard = views.report(screen, mission)
                    elif action == "c":
                        if not any(
                            m.id == mission and m.status == "active" for m in screen.missions
                        ):
                            raise ValueError("Search unavailable")
                        text, keyboard = views.cancel_quote(screen.ownership_id, mission)
                    else:
                        await cancel_search(session, actor, screen.ownership_id, mission)
                        text, keyboard = views.view(await load_scout(session, actor, record=False))
                elif action in {"f", "s", "d", "e"} and len(p) == (5 if action == "s" else 4):
                    person = expand_id(p[3])
                    if action == "s":
                        await sign_finding(
                            session, actor, screen.ownership_id, person, decode_price(p[4])
                        )
                        text, keyboard = views.view(
                            await load_scout(session, actor, record=False),
                            tr("Контракт подписан. Проверьте состав и расходы клуба."),
                        )
                    elif action == "e":
                        await decline_finding(session, actor, screen.ownership_id, person)
                        text, keyboard = views.view(await load_scout(session, actor, record=False))
                    elif action == "d":
                        await finding_for(session, actor, screen.ownership_id, person)
                        from aiogram.types import InlineKeyboardMarkup

                        from goaletto.bot.market_views import button

                        text = tr("Отказаться от кандидата? Новый игрок взамен не появится.")
                        keyboard = InlineKeyboardMarkup(
                            inline_keyboard=[
                                [button(tr("Отказаться"), raw.replace("ss:d:", "ss:e:", 1))],
                                [button(tr("Назад"), raw.replace("ss:d:", "ss:f:", 1))],
                            ]
                        )
                    else:
                        text, keyboard = views.finding_view(
                            await finding_for(session, actor, screen.ownership_id, person),
                            screen.ownership_id,
                        )
                else:
                    raise ValueError("Invalid scout callback")
            if len(p) > 1 and p[1] not in {"b", "k", "s", "e"}:
                await load_scout(session, actor)
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message,
            tr(
                "Действие недоступно: кнопка устарела, поиск занят, не хватает денег/места "
                "или закрыто трансферное окно. Откройте /scout."
            ),
        )
        return
    await edit_response(query.message, text, keyboard)
