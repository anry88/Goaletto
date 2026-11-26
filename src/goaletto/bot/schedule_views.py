"""Compact localized schedule and tournament rounds; UTC phases, local kickoffs."""

from html import escape

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.schedule import ScheduleEntry, ScheduleScreen
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
    }
)


STAGES = LabelMap(
    {
        "prelim": "Предварительный раунд",
        "entry-16": "Отборочный раунд 1",
        "entry-8": "Отборочный раунд 2",
        "entry-4": "Отборочный раунд 3",
        "round-32": "1/16 финала",
        "round-16": "1/8 финала",
        "round_of_16": "1/8 финала",
        "round_of_32": "1/16 финала",
        "quarter": "Четвертьфинал",
        "semi": "Полуфинал",
        "quarterfinal": "Четвертьфинал",
        "semifinal": "Полуфинал",
        "final": "Финал",
    }
)


def _match_lines(entry: ScheduleEntry, zone: str, *, bracket: bool) -> list[str]:
    stage = entry.stage
    if stage is not None and stage in STAGES:
        label = STAGES[stage]
    elif stage and stage.startswith("round_of_") and stage.removeprefix("round_of_").isdigit():
        label = tr("Раунд на {number} команд", number=int(stage.removeprefix("round_of_")))
    elif stage:
        label = escape(stage)
    else:
        label = tr(
            "Тур {number}" if entry.competition == "league" else "Раунд {number}",
            number=entry.round_number,
        )
    heading = f"{TOURNAMENTS[entry.competition]} · {label}"
    if entry.leg and entry.stage != "final":
        heading += " · " + (tr("Первый матч") if entry.leg == 1 else tr("Ответный матч"))
    home = escape(latin_name(entry.home)) if entry.home else tr("Соперник не определён")
    away = escape(latin_name(entry.away)) if entry.away else tr("Соперник не определён")
    result = entry.result
    if result:
        score_home, score_away = result.score
        teams = f"{home} <b>{score_home}:{score_away}</b> {away}"
    elif entry.home or entry.away:
        teams = f"{home} — {away}"
    else:
        teams = tr("Участники не определены")
    if entry.venue in VENUES:
        teams += f" · {VENUES[entry.venue]}"
    lines = [heading, teams]
    if entry.kickoff:
        lines.append("🕒 " + escape(local_datetime(entry.kickoff, zone)))
    elif not entry.conditional:
        lines.append(tr("Время не назначено"))
    if entry.status in ("live", "paused"):
        lines.append(tr("Матч идёт") if entry.status == "live" else tr("Матч приостановлен"))
    elif entry.status == "finished":
        lines.append(tr("✅ Матч завершён"))
    if result is None:
        return lines
    if result.first_leg and not bracket:
        first_home, first_away = result.first_leg
        lines.append(f"{tr('Первый матч')}: {away} {first_home}:{first_away} {home}")
    if result.extra_time:
        lines.append(tr("Дополнительное время"))
    if result.aggregate:
        aggregate_home, aggregate_away = result.aggregate
        lines.append(tr("Общий счёт: {home}:{away}", home=aggregate_home, away=aggregate_away))
    if result.penalties:
        penalty_home, penalty_away = result.penalties
        lines.append(tr("Пенальти: {home}:{away}", home=penalty_home, away=penalty_away))
    if result.winner:
        lines.append(
            tr(
                "Победитель: {club}" if entry.stage == "final" else "Прошёл дальше: {club}",
                club=escape(latin_name(result.winner)),
            )
        )
    return lines


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
            lines.extend(_match_lines(entry, screen.zone, bracket=screen.competition is not None))
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
    available = set(screen.tournaments) | {
        e.competition for day in screen.days for e in day.entries
    }
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
