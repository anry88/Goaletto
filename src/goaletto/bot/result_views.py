"""Telegram results and complete ten-club tables, independent of delivery/DB."""

import base64
from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_types import MatchState
from goaletto.domain.names import latin_name
from goaletto.domain.standings import LeagueStanding, rank_league
from goaletto.presentation.club_status import club_label
from goaletto.presentation.i18n import tr


def compact_id(value: UUID) -> str:
    return base64.urlsafe_b64encode(value.bytes).decode().rstrip("=")


def expand_id(value: str) -> UUID:
    if len(value) != 22 or any(
        c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in value
    ):
        raise ValueError("Invalid callback identity")
    result = UUID(bytes=base64.urlsafe_b64decode(value + "=="))
    if compact_id(result) != value:
        raise ValueError("Noncanonical callback identity")
    return result


def table_callback(season_id: UUID, league_id: UUID, page: int) -> str:
    if type(page) is not int or page not in {1, 2}:
        raise ValueError("Table page must be 1 or 2")
    return f"table:{compact_id(season_id)}:{compact_id(league_id)}:{page}"


def parse_table_callback(value: str) -> tuple[UUID, UUID, int]:
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "table" or parts[3] not in {"1", "2"}:
        raise ValueError("Invalid table callback")
    return expand_id(parts[1]), expand_id(parts[2]), int(parts[3])


def table_view(
    rows: tuple[LeagueStanding, ...],
    season_id: UUID,
    league_id: UUID,
    tier: int,
    page: int = 1,
    vip_clubs: frozenset[UUID] = frozenset(),
) -> tuple[str, InlineKeyboardMarkup]:
    if type(tier) is not int or not 1 <= tier <= 5:
        raise ValueError("Invalid tier")
    if type(page) is not int or page not in {1, 2}:
        raise ValueError("Invalid table page")
    # Existing page-2 buttons still open this same full table.
    callback = table_callback(season_id, league_id, 1)
    ranked = rank_league(rows)
    lines = [
        tr("🏆 <b>Лига {p0}</b>", p0=tier),
        tr("Голы — забитые:пропущенные."),
        "",
    ]
    for index, row in enumerate(ranked, start=1):
        marker = " ↑" if index <= 2 and tier > 1 else " ↓" if index >= 9 and tier < 5 else ""
        lines.append(
            tr(
                "<b>{p0}. {p1}{p2}</b>\nОчки: {p4} · Матчи: {p3} · Голы: {p5}:{p6}",
                p0=index,
                p1=escape(club_label(latin_name(row.name), row.club_id in vip_clubs, table=True)),
                p2=marker,
                p3=row.played,
                p4=row.points,
                p5=row.goals_for,
                p6=row.goals_against,
            )
        )
        lines.append("")
    if tier > 1:
        lines.append(tr("↑ Повышение: места 1–2."))
    if tier < 5:
        lines.append(tr("↓ Вылет: места 9–10."))
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr("🔄 Обновить"), callback_data=callback)],
            [InlineKeyboardButton(text=tr("Мой клуб"), callback_data="vc:c")],
        ]
    )
    return "\n".join(lines), keyboard


def result_view(state: MatchState) -> str:
    if state.phase not in {MatchPhase.FULL_TIME, MatchPhase.ABANDONED}:
        raise ValueError("A result view needs a terminal state")
    h, a = state.home, state.away
    heading = (
        tr("Матч остановлен") if state.phase == MatchPhase.ABANDONED else tr("Финальный свисток")
    )
    total = h.stats.possession_units + a.stats.possession_units
    share = round(h.stats.possession_units * 100 / total) if total else 50
    from goaletto.domain.match_performance import player_of_match

    best = player_of_match(state)
    name = next(
        (
            p.name
            for team in (state.home, state.away)
            for p in team.snapshot.players
            if p.id == best
        ),
        None,
    )
    return (
        tr(
            "⚽ <b>{p0} · {p1}′</b>\n<b>{p2} {p3}:{p4} {p5}</b>\n\nВладение: "
            "{p6}% / {p7}%\nУдары: {p8} / {p9}\nВ створ: {p10} / "
            "{p11}\nУгловые: {p12} / {p13}\nТрибуны: {p14:,} / {p15:,}",
            p0=heading,
            p1=state.game_minute,
            p2=escape(latin_name(h.snapshot.name)),
            p3=h.stats.goals,
            p4=a.stats.goals,
            p5=escape(latin_name(a.snapshot.name)),
            p6=share,
            p7=100 - share,
            p8=h.stats.shots,
            p9=a.stats.shots,
            p10=h.stats.on_target,
            p11=a.stats.on_target,
            p12=h.stats.corners,
            p13=a.stats.corners,
            p14=state.crowd.home_fans,
            p15=state.crowd.stadium_capacity,
        )
        + (tr("\n⭐ Игрок матча: {player}", player=escape(latin_name(name))) if name else "")
        + (
            tr("\nОфициальный результат ещё не назначен.")
            if state.phase == MatchPhase.ABANDONED
            else ""
        )
    )


def result_keyboard(fixture_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr("📋 Отчёт о матче"), callback_data=f"report:{compact_id(fixture_id)}"
                )
            ],
            [InlineKeyboardButton(text=tr("Мой клуб"), callback_data="vc:c")],
        ]
    )
