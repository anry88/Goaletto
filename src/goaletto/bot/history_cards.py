"""Pure history presenters. Stored data only, no recalculation.

Scores come from immutable MatchResult receipts, names/ages/forms from frozen
kickoff snapshots, events from committed MatchTick documents, tables from
stored Standing counters ordered by rank_league. No MVP or derived ratings.
"""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.application.history import (
    ClubResultsPage,
    MatchReport,
    PlayerHistoryPage,
    SeasonArchive,
    SeasonSummary,
)
from goaletto.application.match_commentary import INSTRUCTIONS, MENTALITIES, TACTICS
from goaletto.bot.club_cards import CLUB_CALLBACK, MENU_CALLBACK, SKILL_LABELS
from goaletto.bot.history_callbacks import (
    REPORT_SECTIONS,
    ReportOrigin,
    archive_callback,
    context_report_callback,
    match_callback,
    player_history_callback,
    report_callback,
    results_callback,
    season_results_callback,
    seasons_callback,
)
from goaletto.bot.result_views import table_view
from goaletto.domain.names import latin_name
from goaletto.domain.nationalities import flag_name, legacy_nationality
from goaletto.presentation.i18n import tr

OUTCOME_MARKERS = {"win": "✅", "draw": "➖", "loss": "❌"}


def _nav(
    buttons: list[list[InlineKeyboardButton]],
    *,
    back: str | None,
    menu: bool = True,
) -> InlineKeyboardMarkup:
    if back is not None:
        buttons.append([InlineKeyboardButton(text=tr("← Назад"), callback_data=back)])
    if menu:
        buttons.append(
            [InlineKeyboardButton(text=tr("🏠 Главное меню"), callback_data=MENU_CALLBACK)]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def results_view(
    page: ClubResultsPage, *, season_id: UUID | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        tr(
            "📜 <b>{p0}</b> · Матчи {p1}/{p2}",
            p0=escape(latin_name(page.club_name)),
            p1=page.page,
            p2=page.total_pages,
        ),
        tr("Всего: {p0}", p0=page.total),
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []

    def open_report(fixture_id: UUID) -> str:
        if season_id is not None:
            return context_report_callback(fixture_id, ReportOrigin("season", season_id, page.page))
        return match_callback(fixture_id, page.page)

    def open_page(number: int) -> str:
        return season_results_callback(season_id, number) if season_id else results_callback(number)

    for entry in page.entries:
        marker = OUTCOME_MARKERS.get(entry.outcome, "")
        date = entry.scheduled_start_at.strftime("%d.%m")
        if entry.competition in {"cup", "champions", "friendly"}:
            lines.append(
                tr("Кубок чемпионов · {date}", date=date)
                if entry.competition == "champions"
                else tr("Товарищеский · {date}", date=date)
                if entry.competition == "friendly"
                else tr("Кубок · {date}", date=date)
            )
            lines.append(
                f"{marker} <b>{escape(latin_name(entry.home_name))} "
                f"{entry.home_goals}:{entry.away_goals} "
                f"{escape(latin_name(entry.away_name))}</b>"
            )
            if entry.penalties:
                lines.append(
                    tr("Пенальти: {home}:{away}", home=entry.penalties[0], away=entry.penalties[1])
                )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=tr("📋 Отчёт о матче"),
                        callback_data=open_report(entry.fixture_id),
                    )
                ]
            )
            continue
        lines.append(
            tr(
                "{p0} Тур {p1} · {p2}\n<b>{p3} {p4}:{p5} {p6}</b>",
                p0=marker,
                p1=entry.round_number,
                p2=date,
                p3=escape(latin_name(entry.home_name)),
                p4=entry.home_goals,
                p5=entry.away_goals,
                p6=escape(latin_name(entry.away_name)),
            )
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=tr(
                        "Тур {p0}: {p1}:{p2}",
                        p0=entry.round_number,
                        p1=entry.home_goals,
                        p2=entry.away_goals,
                    )[:60],
                    callback_data=open_report(entry.fixture_id),
                )
            ]
        )
    if not page.entries:
        lines.append(tr("Сыгранных матчей пока нет."))
    nav: list[InlineKeyboardButton] = []
    if page.page > 1:
        nav.append(InlineKeyboardButton(text=tr("← Назад"), callback_data=open_page(page.page - 1)))
    if page.page < page.total_pages:
        nav.append(
            InlineKeyboardButton(text=tr("Вперёд →"), callback_data=open_page(page.page + 1))
        )
    if nav:
        buttons.append(nav)
    buttons.append(
        [InlineKeyboardButton(text=tr("🏟 Клуб"), callback_data=CLUB_CALLBACK)],
    )
    return "\n".join(lines), _nav(buttons, back=archive_callback(season_id) if season_id else None)


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
    who = f"<b>{escape(latin_name(player))}</b>" if player else "—"
    if kind == "goal":
        extra = tr(" (угловой)") if detail == "corner" else ""
        return f"⚽ {minute}′ {who} ({escape(club or '?')}){extra}"
    if kind == "substitution" and other:
        return f"🔄 {minute}′ {who} → <b>{escape(latin_name(other))}</b>"
    if kind == "automatic_substitution" and other:
        return tr(
            "🔄 {p0}′ {p1} → <b>{p2}</b> (травма, авто)",
            p0=minute,
            p1=who,
            p2=escape(latin_name(other)),
        )
    if kind in {"save", "wide", "post"}:
        outcome = {"save": tr("вратарь спасает"), "wide": tr("мимо ворот"), "post": tr("штанга")}[
            kind
        ]
        return f"🎯 {minute}′ {who} ({escape(club or '?')}): {outcome}"
    if kind == "corner":
        return tr("🚩 {p0}′ Угловой: {p1}", p0=minute, p1=escape(club or "?"))
    if kind == "second_half_started":
        return tr("⚽ {p0}′ Начался второй тайм", p0=minute)
    if kind == "yellow":
        return f"🟨 {minute}′ {who}"
    if kind == "red":
        extra = tr(" (вторая жёлтая)") if detail == "second_yellow" else ""
        return f"🟥 {minute}′ {who}{extra}"
    if kind == "injury":
        return f"🤕 {minute}′ {who}"
    if kind in ("tactic", "mentality") and detail:
        label = tr("Тактика") if kind == "tactic" else tr("Настрой")
        choices = TACTICS if kind == "tactic" else MENTALITIES
        return f"📋 {minute}′ {escape(club or '?')}: {label}: {choices.get(detail, tr('изменена'))}"
    if kind == "instruction" and detail:
        label = INSTRUCTIONS.get(detail, tr("изменена"))
        return tr("🎯 {p0}′ {p1}: Инструкция: {p2}", p0=minute, p1=who, p2=escape(label))
    if kind == "half_time":
        return tr("☕ {p0}′ Перерыв", p0=minute)
    if kind in {"extra_time_announced", "extra_time_started", "extra_second_half_started"}:
        return tr("Дополнительное время")
    if kind == "extra_half_time":
        return tr("☕ {p0}′ Перерыв", p0=minute)
    if kind == "penalties_started":
        return tr("Серия пенальти")
    if kind in {"penalty_scored", "penalty_missed"}:
        return (
            tr("Пенальти: {player} — гол", player=who)
            if kind == "penalty_scored"
            else tr("Пенальти: {player} — мимо", player=who)
        )
    if kind == "full_time":
        return tr("🏁 {p0}′ Финальный свисток", p0=minute)
    return None


def report_view(
    report: MatchReport,
    return_page: int = 1,
    *,
    section: str = "events",
    page: int = 1,
    origin: ReportOrigin | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    if section not in REPORT_SECTIONS or type(page) is not int or page < 1:
        raise ValueError("Invalid report view")
    date = report.scheduled_start_at.strftime("%d.%m.%Y")
    lines = [
        f"⚽ <b>{escape(latin_name(report.home_name))} {report.home_goals}:{report.away_goals} "
        f"{escape(latin_name(report.away_name))}</b>",
        (
            tr("Кубок чемпионов · {date}", date=date)
            if report.competition == "champions"
            else tr("Товарищеский · {date}", date=date)
            if report.competition == "friendly"
            else tr("Кубок · {date}", date=date)
        )
        if report.competition in {"cup", "champions", "friendly"}
        else tr("Тур {p0} · {p1}", p0=report.round_number, p1=date),
        "",
        tr(
            "Владение: {p0}% / {p1}%",
            p0=report.possession_home_pct,
            p1=100 - report.possession_home_pct,
        ),
        tr("Удары: {p0} / {p1}", p0=report.home_stats.shots, p1=report.away_stats.shots),
        tr("В створ: {p0} / {p1}", p0=report.home_stats.on_target, p1=report.away_stats.on_target),
        tr("Угловые: {p0} / {p1}", p0=report.home_stats.corners, p1=report.away_stats.corners),
        "",
    ]
    entries: list[str] = []
    if report.neutral_venue is not None:
        name, capacity, crowd, payout_home, payout_away = report.neutral_venue
        lines.append(
            tr(
                "Нейтральный стадион: {name} · {crowd}/{capacity}",
                name=escape(latin_name(name)),
                crowd=crowd,
                capacity=capacity,
            )
        )
        lines.append(
            tr(
                "Билеты — {home}: {amount_home}; {away}: {amount_away}",
                home=escape(latin_name(report.home_name)),
                away=escape(latin_name(report.away_name)),
                amount_home=payout_home,
                amount_away=payout_away,
            )
        )
    if report.aggregate is not None:
        lines.append(
            tr("Общий счёт: {home}:{away}", home=report.aggregate[0], away=report.aggregate[1])
        )
    if report.penalties is not None:
        lines.append(
            tr("Пенальти: {home}:{away}", home=report.penalties[0], away=report.penalties[1])
        )
    if section == "events":
        for name, club_id, awarded, served, carried in report.discipline:
            club = report.home_name if club_id == report.home_club_id else report.away_name
            label = escape(latin_name(name)) + " (" + escape(latin_name(club)) + ")"
            if awarded:
                entries.append(
                    tr("⛔ {player}: пропуск следующего матча этого турнира.", player=label)
                )
            elif served:
                entries.append(tr("✅ {player}: дисквалификация отбыта.", player=label))
            elif carried:
                entries.append(tr("🟨 {player}: 1/2 предупреждений в турнире.", player=label))
        for event in sorted(report.events, key=lambda e: e.minute):
            line = _event_line(
                event.kind,
                event.minute,
                event.player_name,
                event.other_name,
                event.detail,
                latin_name(report.home_name),
                latin_name(report.away_name),
                event.side,
            )
            if line is not None:
                entries.append(line)
        title = tr("События")
        per_page = 10
    else:
        lineup = report.home_lineup if section == "home" else report.away_lineup
        title = tr("Игроки: ") + (
            latin_name(report.home_name) if section == "home" else latin_name(report.away_name)
        )
        per_page = 5
        for member in lineup:
            role = (
                tr("Старт")
                if member.started
                else tr("Вышел на замену")
                if member.appeared or member.minutes
                else tr("Не выходил")
            )
            extra = f" · ⚽ {member.goals}" if member.goals else ""
            extra += " · 🟨" * min(member.yellows, 2) + (" · 🟥" if member.sent_off else "")
            extra += " · 🤕" if member.injured else ""
            energy = (
                f"{member.energy_bps // 100}%"
                if member.energy_bps is not None
                else tr("нет данных")
            )
            entries.append(
                tr(
                    "<b>{p0}</b> · {p1}{p2}\n{p3} · возраст {p4} · сыграл {p5} "
                    "мин\nФорма до матча: {p6}/8 · Энергия после: {p7}",
                    p0=escape(
                        flag_name(
                            latin_name(member.name),
                            member.country_code or legacy_nationality(member.person_id),
                        )
                    ),
                    p1=escape(member.position),
                    p2=extra,
                    p3=role,
                    p4=member.age,
                    p5=member.minutes,
                    p6=member.form,
                    p7=energy,
                )
                + (
                    tr(
                        "\nРазвитие за матч: +{gain}% балла · опыт +{experience}% балла",
                        gain=f"{member.growth_bps / 100:.2f}",
                        experience=f"{(member.experience_gain or 0) / 100:.2f}",
                    )
                    if member.growth_bps is not None
                    else ""
                )
                + (
                    "\n"
                    + " · ".join(
                        tr(
                            "{skill}: +{gain}% балла",
                            skill=tr(dict(SKILL_LABELS)[key]),
                            gain=f"{gain / 100:.2f}",
                        )
                        for key, gain in member.skill_gains
                        if key in dict(SKILL_LABELS)
                    )
                    if member.skill_gains
                    else ""
                )
                + (
                    tr(
                        "\nФорма после матча: {before} → {after}/8",
                        before=member.form_before,
                        after=member.form_after,
                    )
                    if member.form_before is not None and member.form_after is not None
                    else ""
                )
            )
    # Bound whole entries by Telegram's UTF-16 text budget even for long names.
    chunks: list[list[str]] = [[]]
    used = len("\n".join(lines).encode("utf-16-le")) // 2 + len(escape(title)) + 80
    base_used = used
    for entry in entries:
        size = len(entry.encode("utf-16-le")) // 2 + 2
        if chunks[-1] and (len(chunks[-1]) >= per_page or used + size > 3900):
            chunks.append([])
            used = base_used
        chunks[-1].append(entry)
        used += size
    total_pages = len(chunks)
    if page > total_pages:
        raise ValueError("Report page unavailable")
    lines.append(f"<b>{escape(title)}</b> · {page}/{total_pages}")
    selected_entries = chunks[page - 1] or [tr("Событий пока нет.")]
    lines.append(("\n" if section == "events" else "\n\n").join(selected_entries))
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=(
                    context_report_callback(report.fixture_id, origin, tab, 1)
                    if origin
                    else report_callback(report.fixture_id, tab, 1, return_page)
                ),
            )
            for tab, label in (
                ("events", tr("⚽ События")),
                ("home", tr("🏠 Хозяева")),
                ("away", tr("✈️ Гости")),
            )
            if tab != section
        ]
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text=tr("← Предыдущая"),
                callback_data=(
                    context_report_callback(report.fixture_id, origin, section, page - 1)
                    if origin
                    else report_callback(report.fixture_id, section, page - 1, return_page)
                ),
            )
        )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                text=tr("Следующая →"),
                callback_data=(
                    context_report_callback(report.fixture_id, origin, section, page + 1)
                    if origin
                    else report_callback(report.fixture_id, section, page + 1, return_page)
                ),
            )
        )
    if nav:
        buttons.append(nav)
    return "\n".join(lines).rstrip(), _nav(
        buttons, back=origin.callback() if origin else results_callback(return_page)
    )


def player_history_view(page: PlayerHistoryPage) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        tr(
            "📜 <b>{p0}</b> · Матчи {p1}/{p2}",
            p0=escape(
                flag_name(
                    latin_name(page.person_name),
                    page.country_code or legacy_nationality(page.person_id),
                )
            ),
            p1=page.page,
            p2=page.total_pages,
        ),
        tr("Всего: {p0}", p0=page.total),
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
        if entry.competition == "league":
            heading = tr("Тур {p0} · {p1}", p0=entry.round_number, p1=date)
        elif entry.competition == "cup":
            heading = tr("Кубок · {date}", date=date)
        elif entry.competition == "champions":
            heading = tr("Кубок чемпионов · {date}", date=date)
        else:
            heading = tr("Товарищеский · {date}", date=date)
        lines.append(
            f"{heading}\n<b>{escape(latin_name(entry.home_name))} "
            f"{entry.home_goals}:{entry.away_goals} "
            f"{escape(latin_name(entry.away_name))}</b>{extra}"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=tr("Тур {p0}: отчёт", p0=entry.round_number)[:60]
                    if entry.competition == "league"
                    else tr("📋 Отчёт о матче"),
                    callback_data=context_report_callback(
                        entry.fixture_id, ReportOrigin("player", page.person_id, page.page)
                    ),
                )
            ]
        )
    if not page.entries:
        lines.append(tr("Матчей в истории пока нет."))
    nav: list[InlineKeyboardButton] = []
    if page.page > 1:
        nav.append(
            InlineKeyboardButton(
                text=tr("← Назад"),
                callback_data=player_history_callback(page.person_id, page.page - 1),
            )
        )
    if page.page < page.total_pages:
        nav.append(
            InlineKeyboardButton(
                text=tr("Вперёд →"),
                callback_data=player_history_callback(page.person_id, page.page + 1),
            )
        )
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text=tr("🏟 Клуб"), callback_data=CLUB_CALLBACK)])
    return "\n".join(lines), _nav(buttons, back=None)


def seasons_view(
    summaries: tuple[SeasonSummary, ...], *, page: int = 1, total_pages: int = 1
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [tr("🗂 <b>Сезонный архив</b>"), ""]
    buttons: list[list[InlineKeyboardButton]] = []
    for summary in summaries:
        lines.append(tr("Сезон {p0} · Лига {p1}", p0=summary.number, p1=summary.tier))
        buttons.append(
            [
                InlineKeyboardButton(
                    text=tr("Сезон {p0} · Лига {p1}", p0=summary.number, p1=summary.tier),
                    callback_data=archive_callback(summary.season_id, page),
                )
            ]
        )
    if not summaries:
        lines.append(tr("Архивных сезонов пока нет."))
    if total_pages > 1:
        lines.append(tr("Страница {p0}/{p1}", p0=page, p1=total_pages))
        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(text=tr("← Назад"), callback_data=seasons_callback(page - 1))
            )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(text=tr("Далее →"), callback_data=seasons_callback(page + 1))
            )
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text=tr("🏟 Клуб"), callback_data=CLUB_CALLBACK)])
    return "\n".join(lines), _nav(buttons, back=None)


def archive_view(archive: SeasonArchive, return_page: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    # Table text (with promotion/relegation markers) reused from result_views;
    # keyboard rebuilt for archive navigation. Counters are stored, never re-awarded.
    text, _ = table_view(
        archive.table,
        archive.season_id,
        archive.league_id,
        archive.tier,
        vip_clubs=archive.vip_clubs,
    )
    position_line = tr(
        "\n🏟 Ваш клуб — <b>{p0}-е место</b>: {p1} матчей · {p2}В "
        "{p3}Н {p4}П · мячи {p5}:{p6} · {p7} очков.",
        p0=archive.club_position,
        p1=archive.played,
        p2=archive.won,
        p3=archive.drawn,
        p4=archive.lost,
        p5=archive.goals_for,
        p6=archive.goals_against,
        p7=archive.points,
    )
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=tr("📜 Матчи сезона"), callback_data=season_results_callback(archive.season_id)
            )
        ],
        [InlineKeyboardButton(text=tr("🏟 Клуб"), callback_data=CLUB_CALLBACK)],
    ]
    keyboard = _nav(buttons, back=seasons_callback(return_page))
    return (
        tr("🗂 <b>Сезон {p0}</b> · Лига {p1}\n\n", p0=archive.number, p1=archive.tier)
        + text
        + position_line,
        keyboard,
    )
