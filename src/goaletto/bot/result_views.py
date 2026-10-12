"""Compact Telegram result/table presenters, independent of delivery and the database."""

import base64
from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_types import MatchState
from goaletto.domain.standings import LeagueStanding, rank_league


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
) -> tuple[str, InlineKeyboardMarkup]:
    if type(tier) is not int or not 1 <= tier <= 5:
        raise ValueError("Invalid tier")
    if type(page) is not int or page not in {1, 2}:
        raise ValueError("Invalid table page")
    callback = table_callback(season_id, league_id, 3 - page)
    ranked = rank_league(rows)
    lines = [f"🏆 <b>Лига {tier}</b> · {page}/2", "И — матчи · О — очки · М — мячи", ""]
    for index, row in enumerate(ranked[(page - 1) * 5 : page * 5], start=(page - 1) * 5 + 1):
        marker = " ↑" if index <= 2 and tier > 1 else " ↓" if index >= 9 and tier < 5 else ""
        lines.append(
            f"<b>{index}. {escape(row.name)}{marker}</b>\n"
            f"И {row.played} · О {row.points} · М {row.goals_for}:{row.goals_against}"
        )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← 1–5" if page == 2 else "6–10 →", callback_data=callback)],
            [InlineKeyboardButton(text="Мой клуб", callback_data="game:club")],
        ]
    )
    return "\n".join(lines), keyboard


def result_view(state: MatchState) -> str:
    if state.phase not in {MatchPhase.FULL_TIME, MatchPhase.ABANDONED}:
        raise ValueError("A result view needs a terminal state")
    h, a = state.home, state.away
    heading = "Матч остановлен" if state.phase == MatchPhase.ABANDONED else "Финальный свисток"
    total = h.stats.possession_units + a.stats.possession_units
    share = round(h.stats.possession_units * 100 / total) if total else 50
    return (
        f"⚽ <b>{heading} · {state.game_minute}′</b>\n"
        f"<b>{escape(h.snapshot.name)} {h.stats.goals}:{a.stats.goals} "
        f"{escape(a.snapshot.name)}</b>\n\n"
        f"Владение: {share}% / {100 - share}%\n"
        f"Удары: {h.stats.shots} / {a.stats.shots}\n"
        f"В створ: {h.stats.on_target} / {a.stats.on_target}\n"
        f"Угловые: {h.stats.corners} / {a.stats.corners}\n"
        f"Трибуны: {state.crowd.home_fans:,} / {state.crowd.stadium_capacity:,}"
        + (
            "\nОфициальный результат ещё не назначен."
            if state.phase == MatchPhase.ABANDONED
            else ""
        )
    )


def result_keyboard(fixture_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Отчёт о матче", callback_data=f"report:{compact_id(fixture_id)}"
                )
            ],
            [InlineKeyboardButton(text="Мой клуб", callback_data="game:club")],
        ]
    )
