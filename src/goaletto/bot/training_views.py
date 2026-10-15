"""Stateless daily training controls. Choices pin ownership and the UTC date."""

from datetime import UTC, datetime
from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.training import TrainingScreen
from goaletto.bot.club_cards import SKILL_LABELS
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.names import latin_name
from goaletto.domain.training import Focus, Intensity
from goaletto.infrastructure.models import TrainingPlan
from goaletto.presentation.i18n import LabelMap, tr

FOCUS_LABELS = LabelMap(
    {
        Focus.GOALKEEPING: "🧤 Вратарская",
        Focus.DEFENDING: "🛡 Оборонительная",
        Focus.PLAYMAKING: "🎯 Плеймейкинг",
        Focus.WINGER: "↗️ Фланговая",
        Focus.ATTACKING: "⚽ Атакующая",
        Focus.SET_PIECES: "🚩 Стандарты",
        Focus.PHYSICAL: "🏃 Физическая",
        Focus.TEAM: "👥 Общекомандная",
    }
)
INTENSITY_LABELS = LabelMap(
    {Intensity.LOW: "Низкая", Intensity.MEDIUM: "Средняя", Intensity.HIGH: "Высокая"}
)
TARGETS = LabelMap(
    {
        Focus.GOALKEEPING: "вратари · вратарское мастерство",
        Focus.DEFENDING: "DC, DL, DR · защита",
        Focus.PLAYMAKING: "MC · плеймейкинг",
        Focus.WINGER: "ML, MR, DL, DR · фланговая игра",
        Focus.ATTACKING: "FC, AMC, AML, AMR · атака",
        Focus.SET_PIECES: "выбранный исполнитель · стандарты",
        Focus.PHYSICAL: "вся команда · выносливость и скорость",
        Focus.TEAM: "вся команда · опыт",
    }
)


def choice_callback(
    action: str,
    screen: TrainingScreen,
    focus: Focus,
    intensity: Intensity,
    executor_id: UUID | None = None,
) -> str:
    value = (
        f"tr:{action}:{list(Focus).index(focus)}:{list(Intensity).index(intensity)}:"
        f"{compact_id(screen.ownership_id)}:{screen.week_start.strftime('%Y%m%d')}:"
        f"{compact_id(executor_id) if executor_id else '-'}"
    )
    if action not in {"f", "i", "p", "s"} or len(value.encode()) > 64:
        raise ValueError("Invalid training callback")
    return value


def parse_choice(value: str) -> tuple[str, UUID, datetime, Focus, Intensity, UUID | None]:
    parts = value.split(":")
    if (
        len(parts) != 7
        or parts[0] != "tr"
        or parts[1] not in {"f", "i", "p", "s"}
        or len(value.encode()) > 64
    ):
        raise ValueError("Invalid training callback")
    if not all(p.isascii() and p.isdigit() for p in (parts[2], parts[3], parts[5])):
        raise ValueError("Invalid training argument")
    fi, ii = int(parts[2]), int(parts[3])
    if (
        str(fi) != parts[2]
        or str(ii) != parts[3]
        or not 0 <= fi < len(Focus)
        or not 0 <= ii < len(Intensity)
    ):
        raise ValueError("Invalid training selection")
    week = datetime.strptime(parts[5], "%Y%m%d").replace(tzinfo=UTC)
    if week.strftime("%Y%m%d") != parts[5]:
        raise ValueError("Invalid training date")
    return (
        parts[1],
        expand_id(parts[4]),
        week,
        list(Focus)[fi],
        list(Intensity)[ii],
        expand_id(parts[6]) if parts[6] != "-" else None,
    )


def _button(text: str, data: str) -> Button:
    if len(data.encode()) > 64:
        raise ValueError("Training callback too long")
    return Button(text=text[:60], callback_data=data)


def training_view(
    screen: TrainingScreen,
    *,
    focus: Focus | None = None,
    intensity: Intensity | None = None,
    executor_id: UUID | None = None,
    notice: str | None = None,
) -> tuple[str, Keyboard]:
    lines = [
        tr("🏋️ <b>{p0}</b> · Тренировки", p0=escape(latin_name(screen.club_name))),
        tr("Дата тренировки: {p0}", p0=screen.week_start.strftime("%d.%m.%Y")),
        tr("Результат: ") + screen.due_at.strftime("%d.%m.%Y %H:%M UTC"),
        "",
    ]
    if screen.blocked:
        return tr("Сегодня у клуба матч. Выберите тренировку в свободный UTC-день."), Keyboard(
            inline_keyboard=[[Button(text=tr("🏟 Клуб"), callback_data="vc:c")]]
        )
    if screen.plan:
        plan = screen.plan
        lines.append(
            tr("Сохранено: ")
            + FOCUS_LABELS[Focus(plan.focus)]
            + " · "
            + INTENSITY_LABELS[Intensity(plan.intensity)].lower()
        )
    else:
        lines.append(tr("На сегодня тренировка ещё не выбрана."))
    if notice:
        lines.append(escape(notice))
    if focus is None:
        rows = [
            [_button(label, choice_callback("f", screen, value, Intensity.MEDIUM))]
            for value, label in FOCUS_LABELS.items()
        ]
        lines.append(tr("Выберите фокус тренировки."))
    else:
        intensity = intensity or Intensity.MEDIUM
        lines.extend(
            (
                "",
                tr("Выбор: ") + FOCUS_LABELS[focus],
                tr("Кто тренируется: ") + TARGETS[focus],
                tr("Интенсивность: ") + INTENSITY_LABELS[intensity].lower(),
                tr("Травмированные пропускают тренировку."),
            )
        )
        if intensity == Intensity.HIGH:
            lines.append(tr("⚠️ Высокая интенсивность ускоряет прогресс, но повышает риск травмы."))
        rows = [
            [
                _button(label, choice_callback("i", screen, focus, value, executor_id))
                for value, label in INTENSITY_LABELS.items()
            ]
        ]
        selected = next((m for m in screen.members if m.id == executor_id), None)
        if focus == Focus.SET_PIECES and selected is None:
            lines.append(tr("Выберите исполнителя стандартов:"))
            rows += [
                [
                    _button(
                        latin_name(m.name) + " · " + m.position,
                        choice_callback("p", screen, focus, intensity, m.id),
                    )
                ]
                for m in screen.members
            ]
        else:
            if selected:
                lines.append(tr("Исполнитель: ") + escape(latin_name(selected.name)))
            rows.append(
                [
                    _button(
                        tr("✅ Сохранить тренировку"),
                        choice_callback("s", screen, focus, intensity, executor_id),
                    )
                ]
            )
        rows.append([_button(tr("← Выбрать другой фокус"), "tr:open")])
    if screen.last_result:
        rows.append(
            [_button(tr("📋 Последний результат"), f"tr:r:{compact_id(screen.last_result.id)}:1")]
        )
    lines.append(tr("Выбор можно менять до конца UTC-дня. Без выбора тренировки нет."))
    rows.append([_button(tr("🏟 Клуб"), "vc:c"), _button(tr("🏠 Главное меню"), "vc:menu")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def training_report(plan: TrainingPlan, club_name: str, page: int = 1) -> tuple[str, Keyboard]:
    pages = max(1, (len(plan.results) + 4) // 5)
    if type(page) is not int or not 1 <= page <= pages:
        raise ValueError("Invalid training report page")
    lines = [
        tr("🏋️ <b>{p0}</b> · Результат тренировки", p0=escape(latin_name(club_name))),
        plan.week_start.strftime("%d.%m.%Y UTC"),
        FOCUS_LABELS[Focus(plan.focus)]
        + " · "
        + INTENSITY_LABELS[Intensity(plan.intensity)].lower(),
        tr("Игроки · {p0}/{p1}", p0=page, p1=pages),
        "",
    ]
    labels = LabelMap(dict(SKILL_LABELS))
    for row in plan.results[(page - 1) * 5 : page * 5]:
        lines.append("<b>" + escape(latin_name(row["name"])) + "</b>")
        for key, change in row["changes"].items():
            lines.append(f"{labels[key]}: {change['before']} → {change['after']}")
        if not row["changes"]:
            lines.append(
                tr("Прогресс накоплен; навыки ещё не выросли на целый балл.")
                if row["progressed"]
                else tr("Навыки не изменились.")
            )
        if row["form_before"] != row["form_after"]:
            lines.append(tr("Форма: {p0} → {p1}", p0=row["form_before"], p1=row["form_after"]))
        if row["experience_before"] != row["experience_after"]:
            lines.append(
                tr("Опыт: {p0} → {p1}", p0=row["experience_before"], p1=row["experience_after"])
            )
        if row["injured"]:
            lines.append(tr("🤕 Травма на тренировке · восстановление 7 дней."))
        lines.append("")
    if not plan.results:
        lines.append(tr("Доступных игроков для выбранного фокуса не было."))
    rows = []
    nav = []
    if page > 1:
        nav.append(_button(tr("← Предыдущая"), f"tr:r:{compact_id(plan.id)}:{page - 1}"))
    if page < pages:
        nav.append(_button(tr("Следующая →"), f"tr:r:{compact_id(plan.id)}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_button(tr("← Тренировки"), "tr:open"), _button(tr("🏟 Клуб"), "vc:c")])
    return "\n".join(lines).rstrip(), Keyboard(inline_keyboard=rows)
