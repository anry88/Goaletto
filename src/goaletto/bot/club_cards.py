"""Pure club/squad/player presenters, keyboards and compact callbacks.

No database, network or wall-clock access. Dynamic names are HTML-escaped.
Callbacks carry only short action/page/person references; the club is always
resolved from the current ownership by the application layer, never from the
callback. Legacy callbacks (preview:/table:/report:/game:) are ignored here
so old buttons stay safe.
"""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.application.club_view import ClubCard, PlayerCard, SquadPage
from goaletto.bot.history_callbacks import (
    player_history_callback,
    results_callback,
    seasons_callback,
)
from goaletto.bot.portrait_cache import PORTRAIT_UNAVAILABLE_TEXT
from goaletto.bot.result_views import compact_id, expand_id

MENU_CALLBACK = "vc:menu"
CLUB_CALLBACK = "vc:c"
PAGE_SIZE = 5

SKILL_LABELS = (
    ("goalkeeping", "Вратарское мастерство"),
    ("defending", "Защита"),
    ("playmaking", "Плеймейкинг"),
    ("winger", "Фланговая игра"),
    ("attacking", "Атака"),
    ("set_pieces", "Стандарты"),
    ("stamina", "Выносливость"),
    ("pace", "Скорость"),
    ("leadership", "Лидерство"),
    ("loyalty", "Лояльность"),
)

STATUS_TEXT = {
    "active": "в составе",
    "last_season": "⏳ Последний сезон",
    "retired": "🏁 Завершил карьеру",
}

RETIRED_NOTE = (
    "Недоступен для состава, тренировок, трансферов и следующих матчей. "
    "Карточка сохранена в истории."
)
LAST_SEASON_NOTE = "Завершит карьеру после текущего сезона. Трансфер недоступен."


def format_money(value: int) -> str:
    if type(value) is not int or value < 0:
        raise ValueError("Money must be a nonnegative integer")
    return f"{value:,}".replace(",", " ")


def club_callback() -> str:
    return CLUB_CALLBACK


def menu_callback() -> str:
    return MENU_CALLBACK


def squad_callback(page: int) -> str:
    if type(page) is not int or not 1 <= page <= 20:
        raise ValueError("Invalid squad page")
    value = f"vc:s:{page}"
    assert len(value.encode()) <= 64
    return value


def player_callback(person_id: UUID, page: int = 1) -> str:
    if not isinstance(person_id, UUID):
        raise ValueError("Invalid player identity")
    if type(page) is not int or not 1 <= page <= 20:
        raise ValueError("Invalid return page")
    value = f"vc:p:{compact_id(person_id)}:{page}"
    assert len(value.encode()) <= 64
    return value


def parse_view_callback(value: str) -> tuple[str, UUID | None, int]:
    """Return (kind, person_id, page) for our own prefix; raise on anything else."""
    if type(value) is not str:
        raise ValueError("Invalid view callback")
    if value == MENU_CALLBACK:
        return ("menu", None, 1)
    if value == CLUB_CALLBACK:
        return ("club", None, 1)
    parts = value.split(":")
    if len(parts) == 3 and parts[0] == "vc" and parts[1] == "s" and parts[2].isdigit():
        page = int(parts[2])
        if 1 <= page <= 20:
            return ("squad", None, page)
    if (
        len(parts) == 4
        and parts[0] == "vc"
        and parts[1] == "p"
        and parts[3].isdigit()
        and 1 <= int(parts[3]) <= 20
    ):
        return ("player", expand_id(parts[2]), int(parts[3]))
    raise ValueError("Invalid view callback")


def is_view_callback(value: str | None) -> bool:
    return type(value) is str and value.startswith("vc:")


def menu_view() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🏠 <b>Главное меню</b>\n\n"
        "Управляйте составом и тактикой, следите за матчами и таблицей лиги."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏟 Мой клуб", callback_data=CLUB_CALLBACK)],
            [InlineKeyboardButton(text="👥 Состав", callback_data=squad_callback(1))],
            [InlineKeyboardButton(text="📋 Расстановка", callback_data="ln:open")],
            [InlineKeyboardButton(text="⚽ Матч", callback_data="lv:open")],
            [InlineKeyboardButton(text="🏋️ Тренировки", callback_data="tr:open")],
            [InlineKeyboardButton(text="🏆 Таблица", callback_data="nav:table")],
            [InlineKeyboardButton(text="📜 История", callback_data=results_callback(1))],
            [InlineKeyboardButton(text="🔔 Напоминания", callback_data="ntf:open")],
        ]
    )
    return text, keyboard


def club_view(card: ClubCard) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"🏟 <b>{escape(card.name)}</b> · Лига {card.tier}\n"
        f"💰 Бюджет: {format_money(card.budget)}\n"
        f"🏟 Стадион: {card.stadium_capacity:,} · 👥 Болельщики: {card.supporters:,}\n"
        f"🎽 Форма: {escape(card.kit_primary)}/{escape(card.kit_secondary)} "
        f"(v{card.kit_version})"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Состав", callback_data=squad_callback(1))],
            [InlineKeyboardButton(text="📋 Расстановка", callback_data="ln:open")],
            [InlineKeyboardButton(text="⚽ Матч", callback_data="lv:open")],
            [InlineKeyboardButton(text="🏋️ Тренировки", callback_data="tr:open")],
            [InlineKeyboardButton(text="🏆 Таблица", callback_data="nav:table")],
            [InlineKeyboardButton(text="📜 История", callback_data=results_callback(1))],
            [InlineKeyboardButton(text="🗂 Сезоны", callback_data=seasons_callback(1))],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=MENU_CALLBACK)],
        ]
    )
    return text, keyboard


def _member_line(index: int, name: str, position: str, age: int, form: int, status: str) -> str:
    marker = " ⏳" if status == "last_season" else ""
    return f"{index}. <b>{escape(name)}</b> {escape(position)} · {age} · форма {form}{marker}"


def squad_view(page: SquadPage) -> tuple[str, InlineKeyboardMarkup]:
    if page.total_pages < 1 or not 1 <= page.page <= page.total_pages:
        raise ValueError("Invalid squad page view")
    lines = [
        f"👥 <b>{escape(page.club.name)}</b> · Состав {page.page}/{page.total_pages}",
        f"Всего: {page.total_members}",
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for offset, member in enumerate(page.members, start=(page.page - 1) * PAGE_SIZE + 1):
        lines.append(
            _member_line(
                offset, member.name, member.position, member.age, member.form, member.status
            )
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{offset}. {member.name}"[:60],
                    callback_data=player_callback(member.person_id, page.page),
                )
            ]
        )
    if not page.members:
        lines.append("Состав пока пуст.")
    nav: list[InlineKeyboardButton] = []
    if page.page > 1:
        nav.append(
            InlineKeyboardButton(text="← Назад", callback_data=squad_callback(page.page - 1))
        )
    if page.page < page.total_pages:
        nav.append(
            InlineKeyboardButton(text="Вперёд →", callback_data=squad_callback(page.page + 1))
        )
    if nav:
        buttons.append(nav)
    buttons.append(
        [InlineKeyboardButton(text="🏟 Клуб", callback_data=CLUB_CALLBACK)],
    )
    buttons.append(
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data=MENU_CALLBACK)],
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def player_view(
    card: PlayerCard, return_page: int = 1, *, portrait_ready: bool = True
) -> tuple[str, InlineKeyboardMarkup]:
    if type(return_page) is not int or not 1 <= return_page <= 20:
        raise ValueError("Invalid return page")
    skills = (
        card.goalkeeping,
        card.defending,
        card.playmaking,
        card.winger,
        card.attacking,
        card.set_pieces,
        card.stamina,
        card.pace,
        card.leadership,
        card.loyalty,
    )
    if any(type(v) is not int or not 1 <= v <= 20 for v in skills):
        raise ValueError("Invalid player skills")
    if type(card.form) is not int or not 1 <= card.form <= 8:
        raise ValueError("Invalid player form")
    status = STATUS_TEXT.get(card.status, card.status)
    lines = [
        f"👤 <b>{escape(card.name)}</b> · {escape(card.position)} · возраст {card.age}",
        f"Статус: {status}",
    ]
    if card.status == "last_season":
        lines.append(LAST_SEASON_NOTE)
    if card.status == "retired":
        lines.append(RETIRED_NOTE)
    if card.injured:
        lines.append("🤕 Травмирован — недоступен для выбора.")
    if card.alternative_positions:
        lines.append(f"Также: {escape('/'.join(card.alternative_positions))}")
    lines.append("")
    skill_text = "\n".join(
        f"{label}: {value}" for (_, label), value in zip(SKILL_LABELS, skills, strict=True)
    )
    lines.append(f"<b>Навыки (1–20)</b>\n{skill_text}")
    lines.append(f"Форма: {card.form}/8 · Опыт: {card.experience} · Мораль: {card.morale}")
    if card.club_name is not None:
        lines.append(f"Клуб: {escape(card.club_name)}")
    if not portrait_ready:
        lines.append("")
        lines.append(PORTRAIT_UNAVAILABLE_TEXT)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← Назад к составу", callback_data=squad_callback(return_page)
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 История игрока",
                    callback_data=player_history_callback(card.person_id, 1),
                )
            ],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=MENU_CALLBACK)],
        ]
    )
    return "\n".join(lines), keyboard


def stale_view() -> tuple[str, InlineKeyboardMarkup]:
    text, keyboard = menu_view()
    return ("Кнопка устарела — показан актуальный экран.\n\n" + text, keyboard)
