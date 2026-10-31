"""Compact localized schedule and tournament rounds; UTC phases, local kickoffs."""

from html import escape

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.schedule import ScheduleScreen
from goaletto.bot.result_views import compact_id
from goaletto.bot.training_views import FOCUS_LABELS, INTENSITY_LABELS
from goaletto.domain.kickoff import local_datetime
from goaletto.domain.names import latin_name
from goaletto.domain.training import Focus, Intensity
from goaletto.presentation.i18n import LabelMap, tr

PHASES = LabelMap(
    {
        "cup": "Кубок",
        "champions": "Кубок чемпионов",
        "free": "Тренировки и товарищеские",
        "league": "Лига",
        "offseason": "Межсезонье",
    }
)
TOURNAMENTS = LabelMap(
    {
        "league": "Лига",
        "cup": "Кубок",
        "champions": "Кубок чемпионов",
        "friendly": "Товарищеский матч",
    }
)
VENUES = LabelMap(
    {
        "home": "Дома",
        "away": "В гостях",
        "neutral": "Нейтральное поле",
        "conditional": "При выходе в следующий раунд",
        "pair": "Матч турнира",
    }
)


STAGES = LabelMap(
    {
        "round_of_16": "1/8 финала",
        "quarterfinal": "Четвертьфинал",
        "semifinal": "Полуфинал",
        "final": "Финал",
    }
)


def schedule_view(screen: ScheduleScreen) -> tuple[str, Keyboard]:
    title = tr("🗓 Ближайшие 10 дней") if screen.competition is None else tr("🏆 Сетка турнира")
    lines = [
        f"<b>{title} · {escape(latin_name(screen.club_name))}</b>",
        tr("Время событий: {zone}", zone=escape(screen.zone)),
        tr("Дни соревнований указаны по UTC."),
        "",
    ]
    for day in screen.days:
        lines.append(f"<b>{day.day:%d.%m} UTC · {PHASES[day.phase]}</b>")
        if 1 <= day.cycle <= 18:
            lines.append(tr("Игровая неделя {number}", number=day.cycle))
        if not day.entries:
            lines.append(tr("Матчей нет."))
        for entry in day.entries:
            if entry.competition == "training":
                assert entry.focus is not None and entry.intensity is not None
                lines.append(
                    tr(
                        "🏋️ Тренировка · {focus} · {intensity}",
                        focus=FOCUS_LABELS[Focus(entry.focus)],
                        intensity=INTENSITY_LABELS[Intensity(entry.intensity)],
                    )
                )
                if entry.kickoff:
                    lines.append("🕒 " + escape(local_datetime(entry.kickoff, screen.zone)))
                if entry.status == "applied":
                    lines.append(tr("✅ Тренировка проведена"))
                continue
            home = escape(latin_name(entry.home)) if entry.home else tr("Соперник определится")
            away = escape(latin_name(entry.away)) if entry.away else tr("Соперник определится")
            stage = (
                STAGES[entry.stage]
                if entry.stage is not None and entry.stage in STAGES
                else escape(entry.stage)
                if entry.stage
                else tr(
                    "Тур {number}" if entry.competition == "league" else "Раунд {number}",
                    number=entry.round_number,
                )
            )
            label = f"{TOURNAMENTS[entry.competition]} · {stage}"
            if entry.leg:
                label += " · " + (tr("Первый матч") if entry.leg == 1 else tr("Ответный матч"))
            lines.extend((label, f"{home} — {away} · {VENUES[entry.venue]}"))
            if entry.kickoff:
                lines.append("🕒 " + escape(local_datetime(entry.kickoff, screen.zone)))
            else:
                lines.append(tr("Время ещё не назначено."))
            if entry.conditional:
                lines.append(tr("Встреча состоится при выходе в следующий раунд."))
            if entry.status in ("live", "paused"):
                lines.append(
                    tr("Матч идёт") if entry.status == "live" else tr("Матч приостановлен")
                )
            elif entry.status == "finished":
                lines.append(tr("✅ Матч завершён"))
        lines.append("")
    if not screen.days:
        lines.append(tr("Пар пока нет."))
    lines.append(tr("Страница {page}/{pages}", page=screen.page, pages=screen.pages))
    key = compact_id(screen.ownership_id)
    mode = screen.competition or "list"
    nav = []
    if screen.page > 1:
        nav.append(
            Button(text=tr("← Предыдущая"), callback_data=f"ca:{mode}:{key}:{screen.page - 1}")
        )
    if screen.page < screen.pages:
        nav.append(
            Button(text=tr("Следующая →"), callback_data=f"ca:{mode}:{key}:{screen.page + 1}")
        )
    rows = [nav] if nav else []
    available = {e.competition for day in screen.days for e in day.entries}
    cups = [
        Button(text=TOURNAMENTS[c], callback_data=f"ca:{c}:{key}:1")
        for c in ("cup", "champions")
        if c in available and c != screen.competition
    ]
    if cups:
        rows.append(cups)
    if screen.competition:
        rows.append([Button(text=tr("← К расписанию"), callback_data=f"ca:list:{key}:1")])
    rows.extend(
        (
            [
                Button(text=tr("⚽ Матч"), callback_data="lv:open"),
                Button(text=tr("📋 Расстановка"), callback_data="ln:open"),
            ],
            [
                Button(text=tr("🏆 Таблица"), callback_data="nav:table"),
                Button(text=tr("🕒 Настройки времени"), callback_data="ntf:open"),
            ],
            [
                Button(text=tr("🏟 Клуб"), callback_data="vc:c"),
                Button(text=tr("🏠 Главное меню"), callback_data="vc:menu"),
            ],
        )
    )
    return "\n".join(lines), Keyboard(inline_keyboard=rows)
