"""Pure lineup editor presenters and compact callbacks. No database or network calls."""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.application.lineup import LineupData
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.lineup import (
    ATT_POSITIONS,
    DEF_POSITIONS,
    FORMATION_SHAPES,
    FORMATION_SLOTS,
    MID_POSITIONS,
)
from goaletto.domain.match_types import Mentality, Tactic
from goaletto.domain.names import latin_name
from goaletto.presentation.i18n import LabelMap, tr
from goaletto.presentation.person_names import person_name

TACTIC_LABELS = LabelMap(
    {
        Tactic.NORMAL: "Обычная",
        Tactic.PRESSING: "⚡ Прессинг",
        Tactic.COUNTER: "🏃 Контратаки",
        Tactic.WINGS: "↗️ Игра по флангам",
        Tactic.DEFENCE: "🛡 Оборона",
        Tactic.LONG_BALLS: "⬆️ Длинные мячи",
    }
)

MENTALITY_LABELS = LabelMap(
    {
        Mentality.ULTRA_ATTACK: "🔥 Ультраатака",
        Mentality.ATTACK: "⚔️ Атака",
        Mentality.BALANCED: "⚖️ Сбалансированный",
        Mentality.DEFENCE: "🛡 Защита",
        Mentality.ULTRA_DEFENCE: "🧱 Ультразащита",
    }
)

PANELS = frozenset({"main", "formation", "tactic", "mentality", "swap", "captain", "bench"})


def _version_text(value: int) -> str:
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise ValueError("Editor revision must be a positive integer")
    return str(value)


def _badges(person_id: UUID, slot: str, data: LineupData) -> str:
    marks = ""
    if data.captain_id == person_id:
        marks += " ©"
    member = next(
        (m for m in (*data.starters, *data.bench, *data.reserves) if m.person_id == person_id), None
    )
    if member is not None and member.last_season:
        marks += " 🔶"
    if member is not None and member.injured:
        marks += " 🤕"
    if member is not None and slot != member.position:
        marks += " ↔85%" if slot in member.alternatives else " ⚠️60%"
    return marks


def render_main(data: LineupData, notice: str | None = None) -> str:
    lines = [f"📋 <b>{escape(latin_name(data.club_name))}</b> · {escape(data.formation)}"]
    try:
        tactic = TACTIC_LABELS[Tactic(data.tactic)]
    except ValueError:
        tactic = data.tactic
    try:
        mentality = MENTALITY_LABELS[Mentality(data.mentality)]
    except ValueError:
        mentality = data.mentality
    lines.append(tr("Тактика: {p0} · Настрой: {p1}", p0=escape(tactic), p1=escape(mentality)))
    captain = next((m for m in data.starters if m.person_id == data.captain_id), None)
    lines.append(
        tr(
            "Капитан: {p0}",
            p0=escape(person_name(captain)) if captain else tr("— не выбран"),
        )
    )
    if data.lock_at is not None:
        stamp = data.lock_at.strftime("%d.%m %H:%M UTC")
        lines.append(
            tr("🔒 Состав заблокирован перед матчем.")
            if data.locked
            else tr("Блокировка: {p0}.", p0=stamp)
        )
    lines.append("")
    lines.append(tr("<b>Основа:</b>"))
    for index, member in enumerate(data.starters, start=1):
        lines.append(
            f"{index}. {escape(person_name(member))} ({escape(member.slot_position)})"
            f"{_badges(member.person_id, member.slot_position, data)}"
            + (
                f" · {'/'.join((member.position, *member.alternatives))}"
                if member.alternatives
                else ""
            )
            + f" · ⚡ {member.energy_bps / 100:.0f}%"
        )
    lines.append("")
    lines.append(tr("<b>Запас:</b>"))
    for index, member in enumerate(data.bench, start=12):
        lines.append(
            f"{index}. {escape(person_name(member))} "
            f"({escape('/'.join((member.position, *member.alternatives)))})"
            f"{_badges(member.person_id, member.position, data)}"
            f" · ⚡ {member.energy_bps / 100:.0f}%"
        )
    lines.append("")
    if data.reserves:
        lines.extend(("", tr("<b>Вне заявки:</b>")))
        for member in data.reserves:
            lines.append(
                f"{escape(person_name(member))} "
                f"({escape('/'.join((member.position, *member.alternatives)))})"
                f"{_badges(member.person_id, member.position, data)}"
                f" · ⚡ {member.energy_bps / 100:.0f}%"
            )
    lines.append(
        tr(
            "Заявка: {selected}/{roster} игроков",
            selected=len(data.starters) + len(data.bench),
            roster=len(data.starters) + len(data.bench) + len(data.reserves),
        )
    )
    lines.append(
        tr("🔶 — последний сезон. ↔85% — дополнительная позиция. ⚠️60% — чужая. 🤕 — травма.")
    )
    if notice:
        lines.append("")
        lines.append(escape(notice))
    return "\n".join(lines)


def _button(text: str, callback: str) -> InlineKeyboardButton:
    if len(callback.encode()) > 64:
        raise ValueError("Callback exceeds the Telegram limit")
    return InlineKeyboardButton(text=text, callback_data=callback)


def main_keyboard(data: LineupData) -> InlineKeyboardMarkup:
    version = compact_id(data.ownership_id) + ":" + _version_text(data.version)
    if data.locked:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [_button(tr("🔄 Обновить"), f"ln:refresh:{version}")],
                [_button(tr("🏟 Клуб"), "vc:c")],
            ]
        )
    rows = [
        [
            _button(tr("🔄 Замена"), f"ln:panel:swap:{version}"),
            _button(tr("Скамейка"), f"ln:panel:bench:{version}"),
        ],
        [
            _button(tr("Схема"), f"ln:panel:formation:{version}"),
            _button(tr("Тактика"), f"ln:panel:tactic:{version}"),
        ],
        [
            _button(tr("Настрой"), f"ln:panel:mentality:{version}"),
            _button(tr("Капитан"), f"ln:panel:captain:{version}"),
        ],
        [_button(tr("🔄 Обновить"), f"ln:refresh:{version}")],
    ]
    rows.append([_button(tr("🏟 Клуб"), "vc:c")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def formation_keyboard(data: LineupData) -> InlineKeyboardMarkup:
    version = compact_id(data.ownership_id) + ":" + _version_text(data.version)
    rows = [
        [_button(name, f"ln:f:{name}:{version}")]
        for name in sorted(FORMATION_SHAPES)
        if name != data.formation
    ]
    rows.append([_button(tr("← Назад"), f"ln:panel:main:{version}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def formation_view(data: LineupData) -> str:
    lines = [
        tr("📋 <b>Выбор схемы</b>"),
        tr("Линии ниже показывают места, для которых нужны игроки."),
    ]
    for name, slots in sorted(FORMATION_SLOTS.items()):
        lines.append("\n<b>" + name + (" ✓" if name == data.formation else "") + "</b>")
        for label, allowed in (
            (tr("Защитники"), DEF_POSITIONS),
            (tr("Полузащита"), MID_POSITIONS),
            (tr("Атакующие"), ATT_POSITIONS),
        ):
            lines.append(label + ": " + " · ".join(p for p in slots if p in allowed))
        lines.append(tr("Есть место DM.") if "DM" in slots else tr("Отдельного места DM нет."))
    lines.extend(
        (
            "",
            tr("<b>Позиции</b>"),
            tr("GK — вратарь; DL/DC/DR — левый/центральный/правый защитник."),
            tr("DM — опорный; MC — центральный; ML/MR — левый/правый полузащитник."),
            tr("AMC — атакующий полузащитник; AML/AMR — левый/правый вингер; FC — нападающий."),
            tr(
                "В каждой схеме нужен один GK. Дополнительная позиция даёт 85% навыков, "
                "незнакомая полевая — 60%."
            ),
        )
    )
    return "\n".join(lines)


def tactic_keyboard(data: LineupData) -> InlineKeyboardMarkup:
    version = compact_id(data.ownership_id) + ":" + _version_text(data.version)
    rows = [
        [_button(label, f"ln:t:{tactic.value}:{version}")]
        for tactic, label in TACTIC_LABELS.items()
        if tactic.value != data.tactic
        and (not data.allowed_tactics or tactic.value in data.allowed_tactics)
    ]
    rows.append([_button(tr("← Назад"), f"ln:panel:main:{version}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mentality_keyboard(data: LineupData) -> InlineKeyboardMarkup:
    version = compact_id(data.ownership_id) + ":" + _version_text(data.version)
    rows = [
        [_button(label, f"ln:mo:{mentality.value}:{version}")]
        for mentality, label in MENTALITY_LABELS.items()
        if mentality.value != data.mentality
    ]
    rows.append([_button(tr("← Назад"), f"ln:panel:main:{version}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def swap_keyboard(data: LineupData, slot: int) -> InlineKeyboardMarkup:
    version = compact_id(data.ownership_id) + ":" + _version_text(data.version)
    if not 1 <= slot <= len(data.starters):
        raise ValueError("Invalid starter slot")
    rows = [
        [_button(tr("← Основа"), f"ln:panel:main:{version}")],
    ]
    rows.extend(
        [
            _button(
                f"⇄ {person_name(member)}",
                f"ln:s:{slot}:{compact_id(member.person_id)}:{version}",
            )
        ]
        for member in (*data.bench, *data.reserves)
    )
    rows.append([_button(tr("← Назад"), f"ln:panel:swap:{version}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bench_slots_keyboard(data: LineupData) -> InlineKeyboardMarkup:
    version = compact_id(data.ownership_id) + ":" + _version_text(data.version)
    rows = [
        [
            _button(
                f"{index}. {person_name(member)}",
                f"ln:bsel:{index}:{version}",
            )
        ]
        for index, member in enumerate(data.bench, 12)
    ]
    if len(data.bench) < 9 and data.reserves:
        rows.append([_button(tr("Добавить в запас"), f"ln:bsel:{12 + len(data.bench)}:{version}")])
    rows.append([_button(tr("← Назад"), f"ln:panel:main:{version}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bench_choices_keyboard(data: LineupData, slot: int) -> InlineKeyboardMarkup:
    if not 12 <= slot <= min(20, 12 + len(data.bench)):
        raise ValueError("Invalid bench slot")
    version = compact_id(data.ownership_id) + ":" + _version_text(data.version)
    rows = [
        [
            _button(
                person_name(m),
                f"ln:b:{slot}:{compact_id(m.person_id)}:{version}",
            )
        ]
        for m in data.reserves
    ]
    if slot < 12 + len(data.bench):
        rows.append([_button(tr("Оставить вне заявки"), f"ln:br:{slot}:{version}")])
    rows.append([_button(tr("← Назад"), f"ln:panel:bench:{version}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def starter_keyboard(data: LineupData) -> InlineKeyboardMarkup:
    version = compact_id(data.ownership_id) + ":" + _version_text(data.version)
    rows = [
        [
            _button(
                f"{index}. {person_name(member)}",
                f"ln:sel:{index}:{version}",
            )
        ]
        for index, member in enumerate(data.starters, start=1)
    ]
    rows.append([_button(tr("← Назад"), f"ln:panel:main:{version}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def captain_keyboard(data: LineupData) -> InlineKeyboardMarkup:
    version = compact_id(data.ownership_id) + ":" + _version_text(data.version)
    rows = [
        [
            _button(
                f"{'© ' if member.person_id == data.captain_id else ''}{person_name(member)}",
                f"ln:cap:{compact_id(member.person_id)}:{version}",
            )
        ]
        for member in data.starters
    ]
    rows.append([_button(tr("← Назад"), f"ln:panel:main:{version}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_callback(value: str) -> tuple[str, tuple[str, ...]]:
    """Mutations carry canonical ownership scope and bounded revision."""
    if not isinstance(value, str) or len(value.encode()) > 64:
        raise ValueError("Invalid lineup callback")
    parts = value.split(":")
    if len(parts) < 2 or parts[0] != "ln":
        raise ValueError("Not a lineup callback")
    action, args = parts[1], tuple(parts[2:])
    if action == "open" and not args:
        return action, args
    if len(args) < 2 or not args[-1].isascii() or not args[-1].isdigit():
        raise ValueError("Stale lineup callback")
    _version_text(int(args[-1]))
    if str(int(args[-1])) != args[-1]:
        raise ValueError("Invalid revision encoding")
    expand_id(args[-2])
    values = args[:-2]
    if action == "refresh" and not values:
        return action, args
    if action == "panel" and len(values) == 1 and values[0] in PANELS:
        return action, args
    if action == "f" and len(values) == 1 and values[0] in FORMATION_SHAPES:
        return action, args
    if action == "t" and len(values) == 1:
        Tactic(values[0])
        return action, args
    if action == "mo" and len(values) == 1:
        Mentality(values[0])
        return action, args
    if action == "sel" and len(values) == 1 and values[0].isdigit() and 1 <= int(values[0]) <= 11:
        return action, args
    if action == "s" and len(values) == 2 and values[0].isdigit() and 1 <= int(values[0]) <= 11:
        expand_id(values[1])
        return "sw", args
    if (
        action in {"bsel", "br"}
        and len(values) == 1
        and values[0].isascii()
        and values[0].isdigit()
        and 12 <= int(values[0]) <= 20
    ):
        return action, args
    if (
        action == "b"
        and len(values) == 2
        and values[0].isascii()
        and values[0].isdigit()
        and 12 <= int(values[0]) <= 20
    ):
        expand_id(values[1])
        return "bs", args
    if action == "cap" and len(values) == 1:
        expand_id(values[0])
        return action, args
    raise ValueError("Invalid lineup callback")
