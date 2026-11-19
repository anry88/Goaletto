"""Explicit game entry. Commit state before showing its result in Telegram."""

from datetime import UTC, datetime
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update

from goaletto.application.analytics import Reason, Surface
from goaletto.application.club_country import screen as country_screen
from goaletto.application.club_rename import screen as rename_screen
from goaletto.application.club_view import actor_for_callback, get_owned_club
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.onboarding import (
    ClubOffer,
    ClubUnavailable,
    begin_game,
    claim_club,
    offers,
    preview_club,
)
from goaletto.bot.club_cards import club_view, format_money
from goaletto.bot.club_kit import show_club
from goaletto.bot.country_router import view as country_view
from goaletto.bot.onboarding_measurement import observe, recovery_keyboard
from goaletto.bot.rename_router import view as rename_view
from goaletto.bot.responses import edit_response
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.names import latin_name
from goaletto.infrastructure.database import Database
from goaletto.presentation.channels import channel_links
from goaletto.presentation.i18n import tr

router = Router(name="game-entry")


def offer_view(items: tuple[ClubOffer, ...], page: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    lines = [tr("⚽ <b>Goaletto</b>\nВыберите свободный клуб лиги 5:")]
    lines.append(tr("Название клуба можно будет изменить позже."))
    buttons = []
    for item in items:
        lines.append(
            tr(
                "🏟 {p0} · Бюджет {p1}",
                p0=escape(latin_name(item.name)),
                p1=format_money(item.budget),
            )
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=latin_name(item.name)[:60],
                    callback_data=f"en:p:{compact_id(item.id)}:{page}",
                )
            ]
        )
    if not items:
        lines.append(tr("Свободные клубы временно недоступны. Обновите список позже."))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text=tr("← Назад"), callback_data=f"en:o:{page - 1}"))
    if len(items) == 5:
        nav.append(InlineKeyboardButton(text=tr("Далее →"), callback_data=f"en:o:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text=tr("Обновить"), callback_data=f"en:o:{page}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def _edit_entry(message: Message, text: str, keyboard: InlineKeyboardMarkup) -> None:
    # Keep new entry screens as text. Old callbacks can still refer to a
    # message the previous version converted to a photo, with stale metadata.
    try:
        if not message.photo:
            try:
                await edit_response(message, text, keyboard)
                return
            except TelegramBadRequest as error:
                if "there is no text in the message to edit" not in error.message.lower():
                    raise
        await message.edit_caption(caption=text, reply_markup=keyboard)
    except TelegramBadRequest as error:
        if "message is not modified" not in error.message.lower():
            raise


@router.message(CommandStart())
async def game_start(
    message: Message,
    database: Database,
    event_update: Update,
    command: CommandObject,
    internal_telegram_ids: frozenset[int] = frozenset(),
) -> None:
    if message.from_user is None or message.chat.type != "private":
        return
    card = None
    async with database.transaction() as session:
        entry = await begin_game(
            session,
            telegram_id=message.from_user.id,
            update_id=event_update.update_id,
            payload=command.args,
            at=datetime.now(UTC),
            internal=message.from_user.id in internal_telegram_ids,
        )
        if entry.club_id is not None:
            card = await get_owned_club(session, entry.actor, record=False)
            text, keyboard = club_view(card)
        else:
            text, keyboard = offer_view(entry.offers)
            if entry.claim_blocked_until is not None:
                text = tr(
                    "После банкротства новый клуб можно получить с {date} UTC.",
                    date=entry.claim_blocked_until.strftime("%d.%m.%Y %H:%M"),
                )
    text += "\n\n" + channel_links()
    if card is not None:
        await show_club(message, database, card, edit=False, view=(text, keyboard))
    else:
        await message.answer(text, reply_markup=keyboard)
        if entry.offers:
            await observe(
                database,
                message.from_user.id,
                event_update.update_id,
                Surface.ONBOARDING,
                offer=True,
            )


@router.callback_query(F.data.startswith("en:"))
async def entry_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
    await query.answer()
    message = query.message
    if not isinstance(message, Message) or message.chat.type != "private":
        return
    shown_offers: tuple[ClubOffer, ...] = ()
    try:
        parts = (query.data or "").split(":")
        if len(parts) not in {3, 4}:
            raise ValueError("Stale entry callback")
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session,
                telegram_id=query.from_user.id,
                callback_id=query.id,
                at=datetime.now(UTC),
                update_id=event_update.update_id,
            )
            try:
                card = await get_owned_club(session, actor, record=False)
            except PermissionDenied:
                card = None
            if card is not None:
                # A second callback can arrive while the claim response is in
                # flight. Route it using committed ownership, not the old menu.
                text, keyboard = (
                    country_view(await country_screen(session, actor, record=False), mode="r")
                    if card.country_code is None
                    else club_view(card)
                )
            elif parts[1] == "o" and len(parts) == 3:
                if not parts[2].isascii() or not parts[2].isdigit():
                    raise ValueError("Invalid page")
                page = int(parts[2])
                shown_offers = await offers(session, page)
                text, keyboard = offer_view(shown_offers, page)
            elif parts[1] == "p" and len(parts) == 4:
                club_id = expand_id(parts[2])
                if (
                    not parts[3].isascii()
                    or not parts[3].isdigit()
                    or not 1 <= int(parts[3]) <= 10000
                ):
                    raise ValueError("Invalid page")
                item = await preview_club(session, actor, club_id)
                lines = [
                    tr("🏟 <b>{p0}</b> · Лига 5", p0=escape(latin_name(item.name))),
                    tr("Стартовый бюджет: {p0}", p0=format_money(item.budget)),
                    tr("При получении клуба будет создан новый состав."),
                    tr("Название клуба можно будет изменить позже."),
                ]
                text = "\n".join(lines)
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=tr("Получить клуб"),
                                callback_data=f"en:c:{compact_id(club_id)}",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=tr("← К списку"), callback_data=f"en:o:{parts[3]}"
                            )
                        ],
                    ]
                )
            elif parts[1] == "c" and len(parts) == 3:
                await claim_club(session, actor, expand_id(parts[2]))
                country = await country_screen(session, actor, record=False)
                text, keyboard = (
                    country_view(country, mode="r")
                    if country.country_code is None
                    else rename_view(await rename_screen(session, actor))
                )
            else:
                raise ValueError("Stale entry callback")
    except (PermissionDenied, ClubUnavailable, ValueError) as error:
        reason = (
            Reason.PERMISSION_DENIED
            if isinstance(error, PermissionDenied)
            else Reason.CLUB_UNAVAILABLE
            if isinstance(error, ClubUnavailable)
            else Reason.INVALID_INPUT
        )
        await observe(
            database, query.from_user.id, event_update.update_id, Surface.ONBOARDING, reason
        )
        await _edit_entry(
            message,
            tr("Клуб или кнопка больше недоступны. Откройте /start заново."),
            recovery_keyboard(),
        )
        await observe(database, query.from_user.id, event_update.update_id, Surface.ONBOARDING)
        return
    await _edit_entry(message, text, keyboard)
    if shown_offers:
        await observe(
            database, query.from_user.id, event_update.update_id, Surface.ONBOARDING, offer=True
        )


@router.message(Command("help"))
async def game_help(message: Message) -> None:
    await message.answer(
        tr(
            "/start — клуб или выбор клуба\n"
            "/squad — состав\n"
            "/lineup — расстановка\n"
            "/match — матч и live-команды\n/calendar — расписание матчей\n"
            "/table — таблица\n"
            "/history — результаты\n"
            "/train — тренировки\n"
            "/finance — финансы клуба\n"
            "/stadium — стадион и посещаемость\n"
            "/market — аукционы и продажа игроков\n"
            "/freeagents — свободные футболисты\n"
            "/staff — персонал и найм\n"
            "/feedback — добровольный отзыв\n"
            "/settings — настройки клуба\n"
            "/help — помощь"
        )
        + "\n\n"
        + channel_links()
    )
