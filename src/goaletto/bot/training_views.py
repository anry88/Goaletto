"""Stateless daily training controls. Choices pin ownership and the UTC date."""

from datetime import UTC, datetime
from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.training import TrainingDate, TrainingScreen
from goaletto.application.training_reports import TrainingReport
from goaletto.bot.candidate_views import ROLES
from goaletto.bot.club_cards import SKILL_LABELS
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.kickoff import local_datetime
from goaletto.domain.names import latin_name
from goaletto.domain.training import SPECIALISTS, Focus, Intensity
from goaletto.infrastructure.models import TrainingPlan
from goaletto.presentation.i18n import LabelMap, tr
from goaletto.presentation.progress import experience_line, percent, progress_line

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
        Focus.DEFENDING: "DC, DL, DR, DM · защита",
        Focus.PLAYMAKING: "DM, MC, AMC · плеймейкинг",
        Focus.WINGER: "ML, MR, DL, DR, AML, AMR · фланговая игра",
        Focus.ATTACKING: "FC, AMC, AML, AMR · атака",
        Focus.SET_PIECES: "выбранный исполнитель · стандарты",
        Focus.PHYSICAL: "вся команда · выносливость и скорость",
        Focus.TEAM: "вся команда · профильный навык, опыт и командная подготовка",
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
        tr("Время тренировки: {time}", time=escape(local_datetime(screen.due_at, screen.zone))),
        "",
    ]
    calendar_rows = []
    if screen.dates and (focus is None or screen.blocked):
        lines.append(tr("Ближайшие даты (UTC):"))
        for item in screen.dates:
            mark = "⚽" if item.reason else "✅" if item.selected else "○"
            lines.append(f"{mark} {item.day:%d.%m} — " + date_description(item))
        for index in range(0, len(screen.dates), 2):
            calendar_rows.append(
                [
                    _button(
                        ("⚽ " if item.reason else "✅ " if item.selected else "○ ")
                        + item.day.strftime("%d.%m"),
                        date_callback(screen.ownership_id, item.day),
                    )
                    for item in screen.dates[index : index + 2]
                ]
            )
        lines.append("")
    if screen.blocked:
        lines.append(
            date_description(screen.block_reason)
            if screen.block_reason
            else tr("На выбранную дату у клуба матч. Выберите свободный UTC-день.")
        )
        rows = calendar_rows
        if screen.last_result:
            rows.append(
                [
                    _button(
                        tr("📋 Последний результат"), f"tr:r:{compact_id(screen.last_result.id)}:1"
                    )
                ]
            )
        rows.append([_button(tr("🏟 Клуб"), "vc:c")])
        return "\n".join(lines), Keyboard(inline_keyboard=rows)
    if screen.staff_changed:
        lines.append(tr("Штаб изменился. Выберите тренировку заново, чтобы она состоялась."))
    if screen.plan:
        plan = screen.plan
        if plan.status == "cancelled":
            lines.append(cancellation_reason(plan))
        lines.append(
            (tr("Сохранено: ") if plan.status == "scheduled" else tr("План дня: "))
            + FOCUS_LABELS[Focus(plan.focus)]
            + " · "
            + INTENSITY_LABELS[Intensity(plan.intensity)].lower()
        )
    else:
        lines.append(tr("На эту дату тренировка ещё не выбрана."))
    if notice:
        lines.append(escape(notice))
    if focus is not None and focus not in screen.available:
        required = ROLES[SPECIALISTS[focus]]
        notice = tr(
            "Для этого занятия нужен {role}. Наймите специалиста в /staff "
            "или выберите общую подготовку.",
            role=required,
        )
        lines.append(notice)
        focus = None
    if focus is None:
        rows = [
            [_button(label, choice_callback("f", screen, value, Intensity.MEDIUM))]
            for value, label in FOCUS_LABELS.items()
            if value in screen.available
        ]
        lines.append(tr("Выберите фокус тренировки."))
        for value in Focus:
            if value not in screen.available:
                lines.append(
                    tr(
                        "🔒 {focus} — нужен {role}",
                        focus=FOCUS_LABELS[value],
                        role=ROLES[SPECIALISTS[value]],
                    )
                )
        rows = calendar_rows + rows
        rows.append([_button(tr("👥 Персонал"), "cg:s:0")])
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
        rows.append(
            [
                _button(
                    tr("← Выбрать другой фокус"),
                    date_callback(screen.ownership_id, screen.week_start),
                )
            ]
        )
    if screen.plan and screen.plan.status == "scheduled":
        rows.append(
            [
                _button(
                    tr("Отменить тренировку"),
                    f"tr:x:{compact_id(screen.plan.id)}:{compact_id(screen.ownership_id)}",
                )
            ]
        )
    if screen.last_result:
        rows.append(
            [_button(tr("📋 Последний результат"), f"tr:r:{compact_id(screen.last_result.id)}:1")]
        )
    lines.append(tr("Выбор можно менять до времени тренировки. Без выбора тренировки нет."))
    rows.append([_button(tr("🏟 Клуб"), "vc:c"), _button(tr("🏠 Главное меню"), "vc:menu")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def training_report(
    plan: TrainingPlan | TrainingReport, club_name: str, page: int = 1
) -> tuple[str, Keyboard]:
    if plan.status == "cancelled":
        return cancellation_reason(plan), Keyboard(
            inline_keyboard=[
                [_button(tr("← Тренировки"), "tr:open"), _button(tr("👥 Персонал"), "cg:s:0")]
            ]
        )
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
        tr("Прогресс до следующего балла: 0–100."),
        "",
    ]
    labels = LabelMap(dict(SKILL_LABELS))
    for row in plan.results[(page - 1) * 5 : page * 5]:
        heading = "<b>" + escape(latin_name(row["name"])) + "</b>"
        if row.get("position"):
            heading += " · " + escape(row["position"])
        lines.append(heading)
        if "skill_progress" in row:
            for key, progress in row["skill_progress"].items():
                lines.append(
                    progress_line(
                        labels[key],
                        progress["progress_after"],
                        progress["gain_bps"],
                        progress["before"],
                        progress["after"],
                    )
                )
        else:
            # Old immutable receipts do not contain historical fractions or positions.
            for key, change in row["changes"].items():
                lines.append(f"{labels[key]}: {change['before']} → {change['after']}")
            if row.get("gain_bps", 0) > 0:
                lines.append(tr("Рост навыков: +{gain}% балла", gain=percent(row["gain_bps"])))
            elif not row["changes"]:
                lines.append(tr("Навыки не изменились."))
        if row["form_before"] != row["form_after"]:
            lines.append(tr("Форма: {p0} → {p1}", p0=row["form_before"], p1=row["form_after"]))
        if row.get("position_experience"):
            for position, values in row["position_experience"].items():
                gain = values["after"] - values["before"]
                if gain:
                    lines.append(
                        experience_line(
                            tr("Опыт {position}", position=position),
                            values["before"],
                            values["after"],
                            gain,
                        )
                    )
        elif row.get("experience_progress"):
            values = row["experience_progress"]
            if values["gain"]:
                lines.append(
                    experience_line(tr("Опыт"), values["before"], values["after"], values["gain"])
                )
        elif row["experience_before"] != row["experience_after"]:
            lines.append(
                tr("Опыт: {p0} → {p1}", p0=row["experience_before"], p1=row["experience_after"])
            )
        if row.get("morale_before") != row.get("morale_after"):
            lines.append(
                tr(
                    "Мораль: {before} → {after}",
                    before=row["morale_before"],
                    after=row["morale_after"],
                )
            )
        if row.get("leadership_progress") and row.get("leadership_gain", 0):
            lines.append(
                progress_line(
                    tr("Лидерство"),
                    row["leadership_progress"]["after"],
                    row["leadership_gain"],
                    row["leadership_before"],
                    row["leadership_after"],
                )
            )
        elif row.get("leadership_before") != row.get("leadership_after"):
            lines.append(
                tr(
                    "Лидерство: {before} → {after}",
                    before=row["leadership_before"],
                    after=row["leadership_after"],
                )
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


def cancellation_reason(plan: TrainingPlan | TrainingReport) -> str:
    if plan.results and plan.results[0].get("reason") == "manager_cancelled":
        return tr("Тренировка отменена менеджером.")
    reason = str(plan.results[0].get("reason", "")) if plan.results else ""
    return {
        "staff_changed": tr(
            "Тренировка не состоялась: состав или квалификация штаба изменились. "
            "Выберите новое занятие в свободный день; общая подготовка доступна всегда."
        ),
        "match_day": tr("Тренировка не состоялась: в этот день у клуба матч."),
        "ownership_changed": tr("Тренировка отменена после смены или окончания владения клубом."),
        "policy_updated": tr(
            "Прежний план отменён. Выберите новое занятие в следующий свободный день."
        ),
    }.get(reason, tr("Тренировка отменена. Выберите новое занятие в свободный день."))


def date_callback(owner_id: UUID, day: datetime) -> str:
    return f"tr:d:{compact_id(owner_id)}:{day:%Y%m%d}"


def parse_date(value: str) -> tuple[UUID, datetime]:
    parts = value.split(":")
    if (
        len(parts) != 4
        or parts[:2] != ["tr", "d"]
        or len(value.encode()) > 64
        or len(parts[3]) != 8
        or not parts[3].isascii()
        or not parts[3].isdigit()
    ):
        raise ValueError("Invalid training date button")
    day = datetime.strptime(parts[3], "%Y%m%d").replace(tzinfo=UTC)
    if day.strftime("%Y%m%d") != parts[3]:
        raise ValueError("Invalid training date")
    return expand_id(parts[2]), day


def date_description(item: TrainingDate | None) -> str:
    if item and item.reason == "time_passed":
        return tr("Время тренировки уже прошло. Выберите другую дату.")
    if item is None:
        return ""
    if item.reason == "calendar_pending":
        return tr("Календарь следующего сезона ещё не определён.")
    if item.reason:
        competition = {
            "league": tr("Лига"),
            "cup": tr("Кубок"),
            "champions": tr("Кубки чемпионов"),
            "friendly": tr("Товарищеский матч"),
        }.get(item.competition or "", tr("Матч"))
        if item.reason == "conditional":
            return tr(
                "Возможный матч: {competition}. Дата зарезервирована.", competition=competition
            )
        return competition + (" · " + escape(latin_name(item.opponent)) if item.opponent else "")
    return tr("Тренировка выбрана") if item.selected else tr("Свободно")
