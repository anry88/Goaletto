"""Optional, confirmed club country selection; all durable state stays in PostgreSQL."""

from datetime import UTC, datetime
from html import escape
from unicodedata import combining, normalize

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, Update

from goaletto.application.club_country import CountryScreen, choose, screen
from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.bot.market_views import button
from goaletto.bot.responses import edit_response
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.nationalities import COUNTRIES, COUNTRY_LABELS, country_label
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import locale, tr

router = Router(name="club-country")
PAGE_SIZE = 8


def searchable(value: str) -> str:
    return (
        "".join(c for c in normalize("NFKD", value.casefold()) if not combining(c))
        .replace("ё", "е")
        .strip()
    )


def view(
    state: CountryScreen,
    *,
    mode: str = "s",
    page: int | None = None,
    query: str = "",
    preview: str | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    key = compact_id(state.ownership_id)
    rows = []
    lines = [tr("🌍 <b>Страна клуба</b> · {club}", club=escape(state.club_name))]
    if state.country_code:
        lines += [
            country_label(state.country_code, locale()),
            tr("Страна выбрана. Новые выпуски академии будут из этой страны."),
        ]
    elif preview:
        if preview not in COUNTRIES:
            raise ValueError("Unknown country")
        lines += [
            country_label(preview, locale()),
            tr("Подтвердить страну клуба? После подтверждения изменить её нельзя."),
        ]
        rows += [
            [button(tr("Подтвердить"), f"ct:c:{key}:{mode}:{preview}")],
            [button(tr("Выбрать другую"), f"ct:p:{key}:{mode}:0")],
        ]
    else:
        lines += [
            tr("Выберите страну для будущих выпусков академии. Текущий состав сохранится."),
            tr("Поиск по названию или коду: /country Украина или /country UA"),
        ]
        if query:
            needle = searchable(query[:80])
            matches = [
                c
                for c in COUNTRIES
                if needle and any(needle in searchable(v) for v in (c, *COUNTRY_LABELS[c]))
            ]
            matches.sort(
                key=lambda c: (searchable(c) != needle, COUNTRY_LABELS[c][locale() == "en"])
            )
            options = matches[:PAGE_SIZE]
            if not options:
                lines.append(
                    tr(
                        "Страна не найдена. Попробуйте русское/английское название "
                        "или двухбуквенный код."
                    )
                )
        elif page is None:
            options = list(
                ("RU", "UA", "BY", "RS", "GB", "BR")
                if locale() == "ru"
                else ("GB", "US", "CA", "AU", "FR", "BR")
            )
        else:
            ordered = sorted(COUNTRIES, key=lambda c: COUNTRY_LABELS[c][locale() == "en"])
            pages = (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE
            page = max(0, min(page, pages - 1))
            lines.append(tr("Страница {page}/{pages}", page=page + 1, pages=pages))
            options = ordered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
            nav = []
            if page:
                nav.append(button("◀️", f"ct:p:{key}:{mode}:{page - 1}"))
            if page + 1 < pages:
                nav.append(button("▶️", f"ct:p:{key}:{mode}:{page + 1}"))
            if nav:
                rows.append(nav)
        rows = [
            [button(country_label(code, locale()), f"ct:v:{key}:{mode}:{code}")] for code in options
        ] + rows
        if page is None:
            rows.append([button(tr("Все страны"), f"ct:p:{key}:{mode}:0")])
    back = "rn:open" if mode == "r" else "ntf:open"
    rows.append([button(tr("Позже") if not state.country_code else tr("Назад"), back)])
    return "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("country"))
async def country_command(
    message: Message, database: Database, event_update: Update, command: CommandObject
) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = view(await screen(session, actor), query=command.args or "")
    except (PermissionDenied, ValueError):
        await message.answer(tr("Раздел недоступен. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("ct:"))
async def country_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
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
            if data == "ct:open":
                text, keyboard = view(await screen(session, actor))
            else:
                parts = data.split(":")
                if (
                    len(data.encode()) > 64
                    or len(parts) != 5
                    or parts[1] not in ("p", "v", "c")
                    or parts[3] not in ("s", "r")
                ):
                    raise ValueError("Invalid country callback")
                action, owner, mode, value = parts[1], expand_id(parts[2]), parts[3], parts[4]
                state = await screen(session, actor, owner, record=False)
                if action == "p":
                    if not value.isascii() or not value.isdigit() or len(value) > 3:
                        raise ValueError("Invalid country page")
                    state = await screen(session, actor, owner)
                    text, keyboard = view(state, mode=mode, page=int(value))
                elif action == "v":
                    state = await screen(session, actor, owner)
                    text, keyboard = view(state, mode=mode, preview=value)
                else:
                    state = await choose(session, actor, owner, value)
                    if mode == "r":
                        from goaletto.application.club_rename import screen as rename_screen
                        from goaletto.bot.rename_router import view as rename_view

                        text, keyboard = rename_view(await rename_screen(session, actor))
                        text = (
                            tr("Страна клуба: {country}", country=country_label(value, locale()))
                            + "\n\n"
                            + text
                        )
                    else:
                        text, keyboard = view(state)
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message, tr("Страна или кнопка больше недоступна. Откройте /country заново.")
        )
        return
    await edit_response(query.message, text, keyboard)
