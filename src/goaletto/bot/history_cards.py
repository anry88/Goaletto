"""Pure history presenters. Stored data only, no recalculation.

Scores come from immutable MatchResult receipts, names/ages/forms from frozen
kickoff snapshots, events from committed MatchTick documents, tables from
stored Standing counters ordered by rank_league. No MVP or derived ratings.
"""

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.application.history import (
    ClubResultsPage,
    MatchReport,
    PlayerHistoryPage,
    SeasonArchive,
    SeasonSummary,
)
from goaletto.bot.club_cards import CLUB_CALLBACK, MENU_CALLBACK
from goaletto.bot.history_callbacks import (
    archive_callback,
    match_callback,
    player_history_callback,
    results_callback,
    seasons_callback,
)
from goaletto.bot.result_views import table_view

OUTCOME_MARKERS = {"win": "✅", "draw": "➖", "loss": "❌"}


def _nav(
    buttons: list[list[InlineKeyboardButton]],
    *,
    back: str | None,
    menu: bool = True,
) -> InlineKeyboardMarkup:
    if back is not None:
        buttons.append([InlineKeyboardButton(text="← Назад", callback_data=back)])
    if menu:
        buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data=MENU_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def results_view(page: ClubResultsPage) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"📜 <b>{escape(page.club_name)}</b> · Матчи {page.page}/{page.total_pages}",
        f"Всего: {page.total}",
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for entry in page.entries:
        marker = OUTCOME_MARKERS.get(entry.outcome, "")
        date = entry.scheduled_start_at.strftime("%d.%m")
        lines.append(
            f"{marker} Тур {entry.round_number} · {date}\n"
            f"<b>{escape(entry.home_name)} {entry.home_goals}:{entry.away_goals} "
            f"{escape(entry.away_name)}</b>"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"Тур {entry.round_number}: {entry.home_goals}:{entry.away_goals}"[:60],
                    callback_data=match_callback(entry.fixture_id, page.page),
                )
            ]
        )
    if not page.entries:
        lines.append("Сыгранных матчей пока нет.")
    nav: list[InlineKeyboardButton] = []
    if page.page > 1:
        nav.append(
            InlineKeyboardButton(text="← Назад", callback_data=results_callback(page.page - 1))
        )
    if page.page < page.total_pages:
        nav.append(
            InlineKeyboardButton(text="Вперёд →", callback_data=results_callback(page.page + 1))
        )
    if nav:
        buttons.append(nav)
    buttons.append(
        [InlineKeyboardButton(text="🏟 Клуб", callback_data=CLUB_CALLBACK)],
    )
    return "\n".join(lines), _nav(buttons, back=None)


def _event_line(
    kind: str,
    minute: int,
    player: str | None,
    other: str | None,
    detail: str | None,
    home_name: str,
    away_name: str,
    side: str | None,
) -> str | None:
    club = home_name if side == "home" else away_name if side == "away" else None
    who = f"<b>{escape(player)}</b>" if player else "—"
    if kind == "goal":
        extra = " (угловой)" if detail == "corner" else ""
        return f"⚽ {minute}′ {who} ({escape(club or '?')}){extra}"
    if kind == "substitution" and other:
        return f"🔄 {minute}′ {who} → <b>{escape(other)}</b>"
    if kind == "automatic_substitution" and other:
        return f"🔄 {minute}′ {who} → <b>{escape(other)}</b> (травма, авто)"
    if kind == "yellow":
        return f"🟨 {minute}′ {who}"
    if kind == "red":
        extra = " (вторая жёлтая)" if detail == "second_yellow" else ""
        return f"🟥 {minute}′ {who}{extra}"
    if kind == "injury":
        return f"🤕 {minute}′ {who}"
    if kind in ("tactic", "mentality") and detail:
        return f"📋 {minute}′ {escape(club or '?')}: {escape(detail)}"
    if kind == "instruction" and detail:
        labels = {
            "long_shots": "Бить издалека",
            "move_left": "Смещаться влево",
            "move_right": "Смещаться вправо",
            "drop_back": "Оттягиваться назад",
            "runs_behind": "Рывки за спину",
        }
        return f"🎯 {minute}′ {who}: {escape(labels.get(detail, detail))}"
    if kind == "half_time":
        return "— ☕ Перерыв —"
    if kind == "full_time":
        return "— ⏱ Финальный свисток —"
    return None


def report_view(report: MatchReport, return_page: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    date = report.scheduled_start_at.strftime("%d.%m.%Y")
    lines = [
        f"⚽ <b>{escape(report.home_name)} {report.home_goals}:{report.away_goals} "
        f"{escape(report.away_name)}</b>",
        f"Тур {report.round_number} · {date}",
        "",
        f"Владение: {report.possession_home_pct}% / {100 - report.possession_home_pct}%",
        f"Удары: {report.home_stats.shots} / {report.away_stats.shots}",
        f"В створ: {report.home_stats.on_target} / {report.away_stats.on_target}",
        f"Угловые: {report.home_stats.corners} / {report.away_stats.corners}",
        "",
    ]
    for event in report.events:
        line = _event_line(
            event.kind,
            event.minute,
            event.player_name,
            event.other_name,
            event.detail,
            report.home_name,
            report.away_name,
            event.side,
        )
        if line is not None:
            lines.append(line)
    lines.append("")
    for side_name, lineup in (("Хозяева", report.home_lineup), ("Гости", report.away_lineup)):
        lines.append(f"<b>{side_name}</b> (старт; возраст и форма — на момент матча):")
        for member in lineup:
            if not member.started:
                continue
            extra = f" ⚽{member.goals}" if member.goals else ""
            extra += " 🟨" * min(member.yellows, 2) + (" 🟥" if member.sent_off else "")
            lines.append(
                f"• <b>{escape(member.name)}</b> {escape(member.position)} · "
                f"{member.age} · {member.minutes}′{extra}"
            )
        bench = ", ".join(escape(m.name) for m in lineup if not m.started)
        if bench:
            lines.append(f"Запас: {bench}")
        lines.append("")
    keyboard = _nav([], back=results_callback(return_page))
    return "\n".join(lines).rstrip(), keyboard


def player_history_view(page: PlayerHistoryPage) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"📜 <b>{escape(page.person_name)}</b> · Матчи {page.page}/{page.total_pages}",
        f"Всего: {page.total}",
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for entry in page.entries:
        date = entry.scheduled_start_at.strftime("%d.%m")
        extra = f" · {entry.minutes}′"
        if entry.goals:
            extra += f" · ⚽{entry.goals}"
        if entry.sent_off:
            extra += " · 🟥"
        elif entry.yellows:
            extra += " · 🟨"
        lines.append(
            f"Тур {entry.round_number} · {date}\n"
            f"<b>{escape(entry.home_name)} {entry.home_goals}:{entry.away_goals} "
            f"{escape(entry.away_name)}</b>{extra}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"Тур {entry.round_number}: отчёт"[:60],
                    callback_data=match_callback(entry.fixture_id, 1),
                )
            ]
        )
    if not page.entries:
        lines.append("Матчей в истории пока нет.")
    nav: list[InlineKeyboardButton] = []
    if page.page > 1:
        nav.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=player_history_callback(page.person_id, page.page - 1),
            )
        )
    if page.page < page.total_pages:
        nav.append(
            InlineKeyboardButton(
                text="Вперёд →",
                callback_data=player_history_callback(page.person_id, page.page + 1),
            )
        )
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🏟 Клуб", callback_data=CLUB_CALLBACK)])
    return "\n".join(lines), _nav(buttons, back=None)


def seasons_view(
    summaries: tuple[SeasonSummary, ...], *, page: int = 1, total_pages: int = 1
) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🗂 <b>Сезонный архив</b>", ""]
    buttons: list[list[InlineKeyboardButton]] = []
    for summary in summaries:
        lines.append(f"Сезон {summary.number} · Лига {summary.tier}")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"Сезон {summary.number} · Лига {summary.tier}",
                    callback_data=archive_callback(summary.season_id),
                )
            ]
        )
    if not summaries:
        lines.append("Архивных сезонов пока нет.")
    if total_pages > 1:
        lines.append(f"Страница {page}/{total_pages}")
        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(text="← Назад", callback_data=seasons_callback(page - 1))
            )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(text="Далее →", callback_data=seasons_callback(page + 1))
            )
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🏟 Клуб", callback_data=CLUB_CALLBACK)])
    return "\n".join(lines), _nav(buttons, back=None)


def archive_view(archive: SeasonArchive) -> tuple[str, InlineKeyboardMarkup]:
    # Table text (with promotion/relegation markers) reused from result_views;
    # keyboard rebuilt for archive navigation. Counters are stored, never re-awarded.
    text, _ = table_view(archive.table, archive.season_id, archive.league_id, archive.tier, 1)
    text2, _ = table_view(archive.table, archive.season_id, archive.league_id, archive.tier, 2)
    position_line = (
        f"\n🏟 Ваш клуб — <b>{archive.club_position}-е место</b>: "
        f"{archive.played} матчей · {archive.won}В {archive.drawn}Н {archive.lost}П · "
        f"мячи {archive.goals_for}:{archive.goals_against} · {archive.points} очков."
    )
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🏟 Клуб", callback_data=CLUB_CALLBACK)],
    ]
    keyboard = _nav(buttons, back=seasons_callback(1))
    return (
        f"🗂 <b>Сезон {archive.number}</b> · Лига {archive.tier}\n\n"
        + text
        + "\n\n"
        + text2
        + position_line,
        keyboard,
    )
