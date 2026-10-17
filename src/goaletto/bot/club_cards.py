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
from goaletto.domain.names import latin_name
from goaletto.presentation.i18n import LabelMap, tr

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

STATUS_TEXT = LabelMap(
    {
        "active": "в составе",
        "last_season": "⏳ Последний сезон",
        "retired": "🏁 Завершил карьеру",
    }
)

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
    text = tr(
        "🏠 <b>Главное меню</b>\n\nУправляйте составом и тактикой, "
        "следите за матчами и таблицей лиги."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr("🏟 Мой клуб"), callback_data=CLUB_CALLBACK)],
            [InlineKeyboardButton(text=tr("👥 Состав"), callback_data=squad_callback(1))],
            [InlineKeyboardButton(text=tr("📋 Расстановка"), callback_data="ln:open")],
            [
                InlineKeyboardButton(text=tr("⚽ Матч"), callback_data="lv:open"),
                InlineKeyboardButton(text=tr("🗓 Расписание"), callback_data="ca:open"),
            ],
            [
                InlineKeyboardButton(text=tr("🏋️ Тренировки"), callback_data="tr:open"),
                InlineKeyboardButton(text=tr("📈 Финансы"), callback_data="fi:open"),
            ],
            [
                InlineKeyboardButton(text=tr("💰 Рынок"), callback_data="mk:open"),
                InlineKeyboardButton(text=tr("🏟 Стадион"), callback_data="st:open"),
            ],
            [InlineKeyboardButton(text=tr("🎓 Академия"), callback_data="ac:open")],
            [InlineKeyboardButton(text=tr("👥 Персонал"), callback_data="cg:s:0")],
            [InlineKeyboardButton(text=tr("🏆 Таблица"), callback_data="nav:table")],
            [InlineKeyboardButton(text=tr("📜 История"), callback_data=results_callback(1))],
            [InlineKeyboardButton(text=tr("🔔 Напоминания"), callback_data="ntf:open")],
        ]
    )
    return text, keyboard


def club_view(card: ClubCard) -> tuple[str, InlineKeyboardMarkup]:
    text = tr(
        "🏟 <b>{p0}</b> · Лига {p1}\n💰 Бюджет: {p2}\n🏟 Стадион: {p3:,} · 👥 Болельщики: {p4:,}",
        p0=escape(latin_name(card.name)),
        p1=card.tier,
        p2=format_money(card.budget),
        p3=card.stadium_capacity,
        p4=card.supporters,
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr("👥 Состав"), callback_data=squad_callback(1))],
            [InlineKeyboardButton(text=tr("📋 Расстановка"), callback_data="ln:open")],
            [
                InlineKeyboardButton(text=tr("⚽ Матч"), callback_data="lv:open"),
                InlineKeyboardButton(text=tr("🗓 Расписание"), callback_data="ca:open"),
            ],
            [
                InlineKeyboardButton(text=tr("🏋️ Тренировки"), callback_data="tr:open"),
                InlineKeyboardButton(text=tr("📈 Финансы"), callback_data="fi:open"),
            ],
            [
                InlineKeyboardButton(text=tr("💰 Рынок"), callback_data="mk:open"),
                InlineKeyboardButton(text=tr("🏟 Стадион"), callback_data="st:open"),
            ],
            [InlineKeyboardButton(text=tr("🎓 Академия"), callback_data="ac:open")],
            [InlineKeyboardButton(text=tr("👥 Персонал"), callback_data="cg:s:0")],
            [InlineKeyboardButton(text=tr("🏆 Таблица"), callback_data="nav:table")],
            [InlineKeyboardButton(text=tr("📜 История"), callback_data=results_callback(1))],
            [InlineKeyboardButton(text=tr("🗂 Сезоны"), callback_data=seasons_callback(1))],
            [InlineKeyboardButton(text=tr("🏠 Главное меню"), callback_data=MENU_CALLBACK)],
        ]
    )
    return text, keyboard


def _member_line(index: int, name: str, position: str, age: int, form: int, status: str) -> str:
    marker = " ⏳" if status == "last_season" else ""
    return tr(
        "{p0}. <b>{p1}</b> {p2} · {p3} · форма {p4}{p5}",
        p0=index,
        p1=escape(latin_name(name)),
        p2=escape(position),
        p3=age,
        p4=form,
        p5=marker,
    )


def squad_view(page: SquadPage) -> tuple[str, InlineKeyboardMarkup]:
    if page.total_pages < 1 or not 1 <= page.page <= page.total_pages:
        raise ValueError("Invalid squad page view")
    lines = [
        tr(
            "👥 <b>{p0}</b> · Состав {p1}/{p2}",
            p0=escape(latin_name(page.club.name)),
            p1=page.page,
            p2=page.total_pages,
        ),
        tr("Всего: {p0}", p0=page.total_members),
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for offset, member in enumerate(page.members, start=(page.page - 1) * PAGE_SIZE + 1):
        lines.append(
            _member_line(
                offset,
                latin_name(member.name),
                member.position,
                member.age,
                member.form,
                member.status,
            )
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{offset}. {latin_name(member.name)}"[:60],
                    callback_data=player_callback(member.person_id, page.page),
                )
            ]
        )
    if not page.members:
        lines.append(tr("Состав пока пуст."))
    nav: list[InlineKeyboardButton] = []
    if page.page > 1:
        nav.append(
            InlineKeyboardButton(text=tr("← Назад"), callback_data=squad_callback(page.page - 1))
        )
    if page.page < page.total_pages:
        nav.append(
            InlineKeyboardButton(text=tr("Вперёд →"), callback_data=squad_callback(page.page + 1))
        )
    if nav:
        buttons.append(nav)
    buttons.append(
        [InlineKeyboardButton(text=tr("🏟 Клуб"), callback_data=CLUB_CALLBACK)],
    )
    buttons.append(
        [InlineKeyboardButton(text=tr("🏠 Главное меню"), callback_data=MENU_CALLBACK)],
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
        tr(
            "👤 <b>{p0}</b> · {p1} · возраст {p2}",
            p0=escape(latin_name(card.name)),
            p1=escape(card.position),
            p2=card.age,
        ),
        tr("Статус: {p0}", p0=status),
    ]
    if card.status == "last_season":
        lines.append(tr(LAST_SEASON_NOTE))
    if card.status == "retired":
        lines.append(tr(RETIRED_NOTE))
    if card.injured:
        lines.append(tr("🤕 Травмирован — недоступен для выбора."))
    if card.alternative_positions:
        lines.append(tr("Также: {p0}", p0=escape("/".join(card.alternative_positions))))
    lines.append("")
    skill_text = "\n".join(
        f"{tr(label)}: {value}" for (_, label), value in zip(SKILL_LABELS, skills, strict=True)
    )
    lines.append(tr("<b>Навыки (1–20)</b>\n{p0}", p0=skill_text))
    lines.append(
        tr(
            "Форма: {p0}/8 · Опыт: {p1} · Мораль: {p2}",
            p0=card.form,
            p1=card.experience,
            p2=card.morale,
        )
    )
    if card.status != "retired":
        lines.append(tr("⚡ Энергия: {energy}%", energy=f"{card.energy_bps / 100:.1f}"))
        if card.energy_bps < 10000 and card.energy_full_at is not None:
            lines.append(
                tr(
                    "До 100% при текущем штабе: {time}",
                    time=card.energy_full_at.strftime("%d.%m %H:%M UTC"),
                )
            )
    if card.club_name is not None:
        lines.append(tr("Клуб: {p0}", p0=escape(latin_name(card.club_name))))
    if not portrait_ready:
        lines.append("")
        lines.append(tr(PORTRAIT_UNAVAILABLE_TEXT))
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr("← Назад к составу"), callback_data=squad_callback(return_page)
                )
            ],
            [
                InlineKeyboardButton(
                    text=tr("📜 История игрока"),
                    callback_data=player_history_callback(card.person_id, 1),
                )
            ],
            [InlineKeyboardButton(text=tr("🏠 Главное меню"), callback_data=MENU_CALLBACK)],
        ]
    )
    return "\n".join(lines), keyboard


def stale_view() -> tuple[str, InlineKeyboardMarkup]:
    text, keyboard = menu_view()
    return (tr("Кнопка устарела — показан актуальный экран.\n\n") + text, keyboard)
