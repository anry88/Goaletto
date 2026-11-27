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
from goaletto.domain.nationalities import country_label
from goaletto.presentation.club_status import club_label
from goaletto.presentation.i18n import LabelMap, locale, tr
from goaletto.presentation.person_names import person_button_name, person_name
from goaletto.presentation.progress import position_percent
from goaletto.presentation.squad import position_warning, squad_legend

MENU_CALLBACK = "vc:menu"
CLUB_CALLBACK = "vc:c"

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
    if type(value) is not int:
        raise ValueError("Money must be an integer")
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
            [InlineKeyboardButton(text=tr("🤝 Товарищеские матчи"), callback_data="fr:open")],
            [InlineKeyboardButton(text=tr("🎓 Академия"), callback_data="ac:open")],
            [InlineKeyboardButton(text=tr("👥 Персонал"), callback_data="cg:s:0")],
            [
                InlineKeyboardButton(text=tr("🏆 Лига"), callback_data="nav:table"),
                InlineKeyboardButton(text=tr("🏆 Кубки"), callback_data="ca:cups"),
            ],
            [InlineKeyboardButton(text=tr("📜 История"), callback_data=results_callback(1))],
            [InlineKeyboardButton(text=tr("⚙️ Настройки"), callback_data="ntf:open")],
        ]
    )
    return text, keyboard


def club_view(card: ClubCard) -> tuple[str, InlineKeyboardMarkup]:
    text = tr(
        "🏟 <b>{p0}</b> · Лига {p1}\n💰 Бюджет: {p2}\n🏟 Стадион: {p3:,} · 👥 Болельщики: {p4:,}",
        p0=escape(club_label(latin_name(card.name), card.vip)),
        p1=card.tier,
        p2=format_money(card.budget),
        p3=card.stadium_capacity,
        p4=card.supporters,
    )
    if card.country_code:
        text += "\n" + tr(
            "Страна клуба: {country}", country=country_label(card.country_code, locale())
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
            [
                InlineKeyboardButton(text=tr("🏆 Лига"), callback_data="nav:table"),
                InlineKeyboardButton(text=tr("🏆 Кубки"), callback_data="ca:cups"),
            ],
            [InlineKeyboardButton(text=tr("📜 История"), callback_data=results_callback(1))],
            [InlineKeyboardButton(text=tr("🗂 Сезоны"), callback_data=seasons_callback(1))],
            [InlineKeyboardButton(text=tr("⚙️ Настройки"), callback_data="ntf:open")],
            [InlineKeyboardButton(text=tr("🏠 Главное меню"), callback_data=MENU_CALLBACK)],
        ]
    )
    return text, keyboard


def squad_view(page: SquadPage) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        tr("👥 <b>{club}</b> · Состав", club=escape(latin_name(page.club.name))),
        tr("Всего: {p0}", p0=page.total_members),
    ]
    if page.formation is not None:
        lines.append(tr("Схема: {formation}", formation=escape(page.formation)))
    buttons: list[list[InlineKeyboardButton]] = []
    previous_group = ""
    indices = {member.person_id: index for index, member in enumerate(page.members, start=1)}
    for member in page.members:
        index = indices[member.person_id]
        group = (
            tr("<b>Основа:</b>")
            if member.slot is not None and member.slot <= 11
            else tr("<b>Запас:</b>")
            if member.slot is not None
            else tr("<b>Вне заявки:</b>")
        )
        if group != previous_group:
            lines.extend(("", group))
            previous_group = group
        declared = (member.position, *member.alternatives)
        position = member.slot_position or "/".join(declared)
        name = person_name(member)
        if len(name) > 46:
            name = name[:45] + "…"
        line = f"{index}. {escape(name)} ({escape(position)})"
        if member.slot_position is not None:
            warning = position_warning(member.slot_position, declared)
            if warning:
                line += escape(warning)
            elif member.alternatives:
                line += " · " + escape("/".join(declared))
        if member.status == "last_season":
            line += " 🔶"
        if member.injured:
            line += " 🤕"
        if member.slot_position is not None:
            value = dict(member.position_experience).get(member.slot_position, 0)
            line += tr(" · опыт {value}%", value=position_percent(value))
        line += f" · {member.age}" + tr(
            " · форма {form}/10 · ⚡ {energy}%",
            form=member.form,
            energy=f"{member.energy_bps / 100:.0f}",
        )
        lines.append(line)
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{index}. {person_button_name(member)}"[:60],
                    callback_data=player_callback(member.person_id),
                )
            ]
        )
    if not page.members:
        lines.append(tr("Состав пока пуст."))
    if page.discipline:
        lines.append("")
        competitions = {"league": tr("Лига"), "cup": tr("Кубок"), "champions": tr("Кубок лиги")}
        for entry in page.discipline:
            marks = []
            if entry.cautions:
                marks.append("🟨1/2 " + " ".join(f"#{indices[pid]}" for pid in entry.cautions))
            if entry.suspensions:
                marks.append(
                    "⛔ "
                    + " ".join(
                        f"#{indices[pid]}×{games}" if games > 1 else f"#{indices[pid]}"
                        for pid, games in entry.suspensions
                    )
                )
            lines.append(f"{competitions[entry.competition]} · " + " · ".join(marks))
    lines.extend(("", *squad_legend()))
    buttons.append([InlineKeyboardButton(text=tr("🏟 Клуб"), callback_data=CLUB_CALLBACK)])
    buttons.append([InlineKeyboardButton(text=tr("🏠 Главное меню"), callback_data=MENU_CALLBACK)])
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
    if type(card.form) is not int or not 1 <= card.form <= 10:
        raise ValueError("Invalid player form")
    status = STATUS_TEXT.get(card.status, card.status)
    lines = [
        tr(
            "👤 <b>{p0}</b> · {p1} · возраст {p2}",
            p0=escape(person_name(card)),
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
        injury_label = {
            "light": "🤕 Лёгкая травма до {date} UTC",
            "moderate": "🤕 Травма средней тяжести до {date} UTC",
            "severe": "🤕 Тяжёлая травма до {date} UTC",
        }.get(card.injury_severity or "", "🤕 Травмирован до {date} UTC")
        lines.append(
            tr(injury_label, date=card.injured_until.strftime("%d.%m %H:%M"))
            if card.injured_until
            else tr("🤕 Травмирован — недоступен для выбора.")
        )
    if card.alternative_positions:
        lines.append(tr("Также: {p0}", p0=escape("/".join(card.alternative_positions))))
    lines.append("")
    skill_text = "\n".join(
        f"{tr(label)}: {value}" for (_, label), value in zip(SKILL_LABELS, skills, strict=True)
    )
    lines.append(tr("<b>Навыки (1–20)</b>\n{p0}", p0=skill_text))
    lines.append(
        tr(
            "Форма: {p0}/10",
            p0=card.form,
        )
    )
    visible_positions = dict.fromkeys(
        (card.position, *card.alternative_positions, *card.played_positions)
    )
    experience = " · ".join(
        f"{position} {position_percent(dict(card.position_experience).get(position, 0))}%"
        for position in visible_positions
    )
    lines.append(tr("Опыт: {positions}", positions=experience or "—"))
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
    if card.ownership_id is not None and card.club_id is not None and card.status == "active":
        from goaletto.bot.result_views import compact_id

        person_key, owner_key = compact_id(card.person_id), compact_id(card.ownership_id)
        keyboard.inline_keyboard.insert(
            0,
            [
                InlineKeyboardButton(
                    text=tr("💰 Продать"), callback_data=f"mk:q:{person_key}:{owner_key}"
                ),
                InlineKeyboardButton(
                    text=tr("Расторгнуть контракт"), callback_data=f"pr:q:{person_key}:{owner_key}"
                ),
            ],
        )
    return "\n".join(lines), keyboard


def stale_view() -> tuple[str, InlineKeyboardMarkup]:
    text, keyboard = menu_view()
    return (tr("Кнопка устарела — показан актуальный экран.\n\n") + text, keyboard)
