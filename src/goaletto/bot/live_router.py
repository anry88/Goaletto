"""Live command IO after commit; immediate callback acknowledgement."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.application.live_commands import accept_live_command, button_payload, load_live_screen
from goaletto.bot import live_views
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database

router = Router(name="live-controls")


@router.message(Command("match"))
async def match_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = live_views.render(await load_live_screen(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer("Матч недоступен. Откройте /start.")
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("lv:"))
async def live_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    try:
        if query.data != "lv:open":
            action, args, ownership_id, match_id, target, halftime = live_views.parse_callback(
                query.data or ""
            )
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session,
                telegram_id=query.from_user.id,
                callback_id=query.id,
                at=datetime.now(UTC),
                update_id=event_update.update_id,
            )
            screen = await load_live_screen(session, actor, record=False)
            if query.data == "lv:open":
                screen = await load_live_screen(session, actor)
                text, keyboard = live_views.render(screen)
            elif screen.ownership_id != ownership_id or screen.match_id != match_id:
                text, keyboard = live_views.render(
                    screen, notice="Кнопка от другого матча или владения. Команда не принята."
                )
            elif (screen.target, screen.halftime) != (target, halftime) or not screen.open:
                text, keyboard = live_views.render(
                    screen, notice="Окно устарело. Показан текущий матч."
                )
            elif action in {"s", "t", "m", "i"}:
                kind, payload = button_payload(screen, action, args)
                receipt = await accept_live_command(
                    session,
                    actor,
                    match_id=match_id,
                    ownership_id=ownership_id,
                    target=target,
                    halftime=halftime,
                    kind=kind,
                    payload=payload,
                )
                screen = await load_live_screen(session, actor, record=False)
                notice = "Принято. Применится на следующем этапе, если команда остаётся допустимой."
                if receipt.status == "applied":
                    notice = "Команда уже применена."
                text, keyboard = live_views.render(screen, notice=notice)
            else:
                if action == "p" and len(args) == 1 and 0 <= args[0] <= 4:
                    panel, selected = args[0], 0
                elif action == "o":
                    panel, selected = 5, args[0]
                elif action == "u":
                    panel, selected = 6, args[0]
                elif action == "r":
                    panel, selected = 0, 0
                else:
                    raise ValueError("Unknown panel")
                screen = await load_live_screen(session, actor)
                text, keyboard = live_views.render(screen, panel=panel, selected=selected)
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message,
            "Команда не принята: окно устарело, лимит исчерпан или игрок недоступен. "
            "Откройте /match.",
        )
        return
    await edit_response(query.message, text, keyboard)
