"""A bounded football timeline from committed events and frozen player names."""

from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.domain.match_types import MatchState
from goaletto.domain.names import latin_name
from goaletto.infrastructure.models import MatchStage, MatchTick
from goaletto.presentation.i18n import LabelMap, tr

IMPORTANT = frozenset(
    {
        "goal",
        "yellow",
        "red",
        "injury",
        "substitution",
        "automatic_substitution",
        "tactic",
        "mentality",
        "instruction",
        "half_time",
        "second_half_started",
        "full_time",
    }
)
TACTICS = LabelMap(
    {
        "normal": "обычная",
        "pressing": "прессинг",
        "counter": "контратаки",
        "wings": "игра по флангам",
        "defence": "оборона",
        "long_balls": "длинные мячи",
    }
)
MENTALITIES = LabelMap(
    {
        "ultra_attack": "ультраатака",
        "attack": "атака",
        "balanced": "сбалансированный",
        "defence": "защита",
        "ultra_defence": "ультразащита",
    }
)
INSTRUCTIONS = LabelMap(
    {
        "long_shots": "бить издалека",
        "move_left": "смещаться влево",
        "move_right": "смещаться вправо",
        "drop_back": "оттягиваться назад",
        "runs_behind": "рывки за спину",
    }
)


def football_line(state: MatchState, event: dict[str, Any]) -> str | None:
    kind, minute = event.get("kind"), event.get("minute")
    if not isinstance(kind, str) or type(minute) is not int or not 0 <= minute <= state.game_minute:
        return None
    team = state.home if event.get("side") == "home" else state.away
    names = {str(p.id): latin_name(p.name).replace("\n", " ")[:40] for p in team.snapshot.players}
    who = names.get(str(event.get("player_id")), tr("Игрок"))
    other = names.get(str(event.get("other_player_id")), tr("Игрок"))
    club = latin_name(team.snapshot.name).replace("\n", " ")[:40]
    detail = str(event.get("detail", ""))
    descriptions = {
        "goal": tr("⚽ Гол! {p0} ({p1})", p0=who, p1=club)
        + (tr(" — после углового") if detail == "corner" else ""),
        "save": tr("🧤 Опасный момент: удар {p0} ({p1}) — вратарь спасает", p0=who, p1=club),
        "wide": tr("🎯 Опасный момент: {p0} ({p1}) бьёт мимо", p0=who, p1=club),
        "post": tr("💥 Опасный момент: {p0} ({p1}) попадает в штангу", p0=who, p1=club),
        "corner": tr("🚩 Угловой — {p0}", p0=club),
        "yellow": tr("🟨 Жёлтая карточка: {p0} ({p1})", p0=who, p1=club),
        "red": tr("🟥 Удаление: {p0} ({p1})", p0=who, p1=club)
        + (tr(" — вторая жёлтая") if detail == "second_yellow" else ""),
        "injury": tr("🤕 Травма: {p0} ({p1})", p0=who, p1=club),
        "substitution": tr("🔄 Замена ({p0}): {p1} → {p2}", p0=club, p1=who, p2=other),
        "automatic_substitution": tr(
            "🔄 Замена из-за травмы ({p0}): {p1} → {p2}", p0=club, p1=who, p2=other
        ),
        "half_time": tr("☕ Перерыв"),
        "second_half_started": tr("⚽ Начался второй тайм"),
        "full_time": tr("🏁 Финальный свисток"),
    }
    description: str | None
    if kind == "tactic" and detail in TACTICS:
        description = tr("📋 {p0}: тактика — {p1}", p0=club, p1=TACTICS[detail])
    elif kind == "mentality" and detail in MENTALITIES:
        description = tr("💬 {p0}: настрой — {p1}", p0=club, p1=MENTALITIES[detail])
    elif kind == "instruction" and detail in INSTRUCTIONS:
        description = tr(
            "🎯 {p0} ({p1}): инструкция — {p2}", p0=who, p1=club, p2=INSTRUCTIONS[detail]
        )
    else:
        description = descriptions.get(kind)
    return f"{minute}′ {description}" if description else None


def recent_commentary(state: MatchState, events: list[dict[str, Any]]) -> tuple[str, ...]:
    """Keep eight important incidents plus four recent chances, chronologically."""
    seen: set[str] = set()
    items: list[tuple[int, int, bool, str]] = []
    for order, event in enumerate(events):
        identity = str(event.get("id"))
        line = football_line(state, event)
        if line is None or identity in seen:
            continue
        seen.add(identity)
        items.append((event["minute"], order, event.get("kind") in IMPORTANT, line))
    items.sort(key=lambda item: (item[0], item[1]))
    selected = [item for item in items if item[2]][-8:]
    selected += [item for item in items if not item[2]][-4:]
    selected.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[3] for item in selected)


async def load_commentary(session: AsyncSession, state: MatchState) -> tuple[str, ...]:
    # At most 30 regulation documents and five transition documents per match.
    ticks = (
        select(MatchTick.events)
        .where(MatchTick.match_id == state.match_id, MatchTick.tick_number <= state.tick)
        .order_by(MatchTick.tick_number)
    )
    stages = (
        select(MatchStage.events)
        .where(MatchStage.match_id == state.match_id)
        .order_by(MatchStage.occurred_at, MatchStage.stage)
    )
    events = [event for (document,) in (await session.execute(ticks)).all() for event in document]
    events += [event for (document,) in (await session.execute(stages)).all() for event in document]
    return recent_commentary(state, events)


def commentary_html(lines: tuple[str, ...] | list[str], budget: int = 1800) -> str:
    """Bound escaped HTML, preserving whole lines/entities and latest incidents."""
    selected: list[str] = []
    used = 0
    for line in reversed(lines):
        escaped = escape(line)
        if used + len(escaped) + 1 <= budget:
            selected.append(escaped)
            used += len(escaped) + 1
    return "\n".join(reversed(selected))
