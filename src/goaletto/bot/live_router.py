"""Live command IO after commit; immediate callback acknowledgement."""

from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from uuid import uuid5

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, Update

from goaletto.application.acquisition import telegram_operation
from goaletto.application.club_view import CALLBACK_NAMESPACE, actor_for_callback
from goaletto.application.feedback import offer_feedback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.application.live_commands import (
    NoFixture,
    accept_live_command,
    button_payload,
    load_live_screen,
)
from goaletto.bot import live_views
from goaletto.bot.responses import edit_response
from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.request_timing import observe_request
from goaletto.presentation.i18n import tr

router = Router(name="live-controls")


@router.message(Command("match"))
async def match_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    render: Callable[[], tuple[str, InlineKeyboardMarkup]]
    async with observe_request(
        database, telegram_operation(event_update.update_id), "match_open"
    ) as timing:
        try:
            async with timing.transaction() as session:
                actor = await actor_for(
                    session, message.from_user.id, event_update.update_id, datetime.now(UTC)
                )
                screen = await load_live_screen(session, actor)
                timing.bind(screen.fixture_id)
                feedback_available = Settings().feedback_enabled and await offer_feedback(
                    session, actor, actor.at
                )
                render = partial(live_views.render, screen, feedback_available=feedback_available)
        except NoFixture:
            render = live_views.no_fixture_view
        except (PermissionDenied, ValueError):
            await timing.send(lambda: message.answer(tr("Матч недоступен. Откройте /start.")))
            return
        text, keyboard = timing.render(render)
        await timing.send(lambda: message.answer(text, reply_markup=keyboard))


@router.callback_query(F.data.startswith("lv:"))
async def live_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    message = query.message
    operation = uuid5(CALLBACK_NAMESPACE, f"goaletto:callback:v1:{query.id}")
    render: Callable[[], tuple[str, InlineKeyboardMarkup]]
    async with observe_request(database, operation, "match_callback") as timing:
        try:
            if query.data != "lv:open":
                action, args, ownership_id, match_id, target, halftime = live_views.parse_callback(
                    query.data or ""
                )
            async with timing.transaction() as session:
                actor = await actor_for_callback(
                    session,
                    telegram_id=query.from_user.id,
                    callback_id=query.id,
                    at=datetime.now(UTC),
                    update_id=event_update.update_id,
                )
                screen = await load_live_screen(session, actor, record=False)
                timing.bind(screen.fixture_id)
                if query.data == "lv:open":
                    screen = await load_live_screen(session, actor)
                    render = partial(live_views.render, screen)
                elif screen.ownership_id != ownership_id or screen.match_id != match_id:
                    render = partial(
                        live_views.render,
                        screen,
                        notice=tr("Кнопка от другого матча или владения. Команда не принята."),
                    )
                elif (screen.target, screen.halftime) != (target, halftime) or not screen.open:
                    render = partial(
                        live_views.render,
                        screen,
                        notice=tr("Команда не принята. Показан текущий матч."),
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
                    timing.bind(screen.fixture_id, receipt.id)
                    screen = await load_live_screen(session, actor, record=False)
                    notice = tr("Команда принята.")
                    if receipt.status == "applied":
                        notice = tr("Команда уже применена.")
                    render = partial(live_views.render, screen, notice=notice)
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
                    render = partial(live_views.render, screen, panel=panel, selected=selected)
        except NoFixture:
            render = live_views.no_fixture_view
        except (PermissionDenied, ValueError):

            async def send_error() -> None:
                await edit_response(
                    message,
                    tr(
                        "Команда не может быть применена: лимит исчерпан или "
                        "действие сейчас недоступно. Откройте /match."
                    ),
                )

            await timing.send(send_error)
            return
        text, keyboard = timing.render(render)
        await timing.send(lambda: edit_response(message, text, keyboard))
