"""Durable optional club rename, with explicit preview/confirmation."""

from datetime import UTC, datetime
from html import escape

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from sqlalchemy import select

from goaletto.application.club_rename import RenameScreen, action, screen, submit
from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.bot.responses import edit_response
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.club_names import InvalidClubName
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import ClubNameChoice, Manager, OwnershipPeriod
from goaletto.presentation.i18n import tr

router = Router(name="club-rename")
RULES = (
    "От 2 до 30 символов: латинские буквы, цифры, пробел, дефис, апостроф и точка. "
    "Без запрещённых слов."
)


def view(state: RenameScreen) -> tuple[str, InlineKeyboardMarkup]:
    key = compact_id(state.ownership_id)
    token = compact_id(state.draft_id) if state.draft_id else "-"
    buttons = []
    if state.state == "done":
        text = tr("Название сохранено: <b>{name}</b>.", name=escape(state.name))
        if state.allowed:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=tr("Переименовать клуб"), callback_data=f"rn:i:{key}:{token}"
                    )
                ]
            )
        buttons.append([InlineKeyboardButton(text=tr("⚙️ Настройки"), callback_data="ntf:open")])
    elif not state.allowed:
        text = tr("Название клуба: <b>{name}</b>.", name=escape(state.name))
        text += "\n" + (
            tr("Одна попытка сохранена. Использовать её можно только в межсезонье.")
            if state.credit
            else tr(
                "Свободных попыток нет. Купить переименование клуба "
                "или подключить VIP можно в настройках."
            )
        )
        buttons.append([InlineKeyboardButton(text=tr("⚙️ Настройки"), callback_data="ntf:open")])
    elif state.state == "preview":
        text = tr(
            "Новое название: <b>{name}</b>\nПодтвердить переименование? "
            "Будет использована одна попытка.",
            name=escape(state.candidate or ""),
        )
        buttons.append(
            [InlineKeyboardButton(text=tr("Подтвердить"), callback_data=f"rn:c:{key}:{token}")]
        )
        buttons.append(
            [InlineKeyboardButton(text=tr("Ввести другое"), callback_data=f"rn:i:{key}:{token}")]
        )
    elif state.state == "input":
        text = tr("Пришлите новое название клуба одним сообщением.") + "\n" + tr(RULES)
    else:
        text = tr(
            "Ваш клуб: <b>{name}</b>\nХотите переименовать его? В запасе одна попытка. "
            "После отказа следующая возможность будет в межсезонье.",
            name=escape(state.name),
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=tr("Переименовать клуб"), callback_data=f"rn:i:{key}:{token}"
                )
            ]
        )
    if state.allowed:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=tr("Оставить текущее название"), callback_data=f"rn:k:{key}:{token}"
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text=tr("🏟 Мой клуб"), callback_data="vc:c")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("rename"))
async def rename_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = view(await screen(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer(tr("Нет доступа: клуб больше не принадлежит вам. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("rn:"))
async def rename_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
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
            parts = (query.data or "").split(":")
            if parts == ["rn", "open"]:
                state = await screen(session, actor)
            elif len(parts) == 4 and parts[1] in {"i", "k", "c"}:
                state = await action(
                    session,
                    actor,
                    expand_id(parts[2]),
                    {"i": "input", "k": "keep", "c": "confirm"}[parts[1]],
                    None if parts[3] == "-" else expand_id(parts[3]),
                )
            else:
                raise ValueError("Invalid rename callback")
            text, keyboard = view(state)
    except (PermissionDenied, ValueError):
        await edit_response(query.message, tr("Кнопка устарела. Откройте /rename заново."))
        return
    await edit_response(query.message, text, keyboard)


@router.message(F.text & ~F.text.startswith("/"))
async def rename_input(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        raise SkipHandler
    async with database.transaction() as session:
        waiting = await session.scalar(
            select(ClubNameChoice.ownership_id)
            .join(OwnershipPeriod, OwnershipPeriod.id == ClubNameChoice.ownership_id)
            .join(Manager, Manager.id == OwnershipPeriod.manager_id)
            .where(
                Manager.telegram_id == message.from_user.id,
                OwnershipPeriod.ended_at.is_(None),
                ClubNameChoice.state == "input",
            )
        )
    if waiting is None:
        raise SkipHandler
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = view(await submit(session, actor, message.text or ""))
    except (PermissionDenied, ValueError) as error:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            try:
                state = await screen(session, actor)
            except (PermissionDenied, ValueError):
                raise SkipHandler from error
            text, keyboard = view(state)
        reason = (
            tr(
                "Название содержит недопустимые символы или слова. "
                "Попробуйте ещё раз либо оставьте текущее."
            )
            if isinstance(error, InvalidClubName)
            else tr("Название не изменено. Введите другое либо оставьте текущее.")
        )
        text = reason + "\n\n" + text
    await message.answer(text, reply_markup=keyboard)
