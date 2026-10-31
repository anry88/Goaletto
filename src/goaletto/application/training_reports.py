"""Read training progress, including older receipts with cumulative growth totals."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.domain.development import BUDGET_KEYS, GrowthBudget
from goaletto.domain.training import PRIMARY_SKILLS, SKILLS, Focus
from goaletto.infrastructure.models import MatchDevelopment, Player, PlayerDevelopment, TrainingPlan


@dataclass(frozen=True)
class TrainingReport:
    id: UUID
    week_start: datetime
    focus: str
    intensity: str
    status: str
    results: list[dict[str, Any]]


async def report_details(session: AsyncSession, plan: TrainingPlan) -> TrainingReport:
    """Enrich the authorized report in memory; never modify its immutable JSON."""
    rows = [dict(row) for row in plan.results]
    legacy = [row for row in rows if "person_id" in row and "skill_progress" not in row]
    ids = [UUID(row["person_id"]) for row in legacy]
    people = (
        {
            str(player.person_id): (player, development)
            for player, development in (
                await session.execute(
                    select(Player, PlayerDevelopment)
                    .join(PlayerDevelopment, PlayerDevelopment.person_id == Player.person_id)
                    .where(Player.person_id.in_(ids))
                )
            ).all()
        }
        if ids
        else {}
    )
    previous: dict[str, tuple[datetime, int, dict[str, int]]] = {}
    if legacy and plan.focus in (Focus.TEAM, Focus.PHYSICAL) and plan.applied_at:
        # Single-skill focuses already save their exact gain. Multi-skill sessions
        # need the immediately preceding cumulative budget for each person.
        models: tuple[type[TrainingPlan] | type[MatchDevelopment], ...] = (
            TrainingPlan,
            MatchDevelopment,
        )
        for model in models:
            receipts = (
                await session.execute(
                    select(model.applied_at, model.results).where(
                        model.applied_at < plan.applied_at,
                        or_(*(model.results.contains([{"person_id": str(pid)}]) for pid in ids)),
                    )
                )
            ).all()
            for applied_at, results in receipts:
                assert applied_at is not None
                for old in results:
                    pid = old.get("person_id")
                    age = old.get("development_age", old.get("age"))
                    budget = old.get("season_gains")
                    if (
                        pid not in people
                        or not isinstance(age, int)
                        or not isinstance(budget, dict)
                    ):
                        continue
                    if pid not in previous or applied_at > previous[pid][0]:
                        previous[pid] = (applied_at, age, budget)
    for row in legacy:
        person = people.get(row["person_id"])
        if person is None:
            continue
        player, development = person
        # Historical training slots were not saved. The player's primary position
        # is a roster label; only new receipts claim the frozen training position.
        row["position"] = player.position
        gains = _session_gains(row, plan.focus, player.position, previous.get(row["person_id"]))
        if gains is None:
            continue
        progress = {}
        for skill, gain in gains.items():
            change = row["changes"].get(skill, {"before": 0, "after": 0})
            after = _historical_fraction(row, development, skill)
            progress[skill] = {
                **change,
                "progress_after": after,
                "gain_bps": gain,
            }
        row["skill_progress"] = progress
    return TrainingReport(plan.id, plan.week_start, plan.focus, plan.intensity, plan.status, rows)


def _session_gains(
    row: dict[str, Any],
    focus: str,
    position: str,
    previous: tuple[datetime, int, dict[str, int]] | None,
) -> dict[str, int] | None:
    gain = row.get("gain_bps")
    if not isinstance(gain, int) or gain < 0:
        return None
    if focus in SKILLS:
        return {focus: gain}
    budget = row.get("season_gains", {})
    age = row.get("development_age")
    if not isinstance(age, int):
        return None
    before = previous[2] if previous and previous[1] == age else dict.fromkeys(BUDGET_KEYS, 0)
    try:
        GrowthBudget(age, budget)
        GrowthBudget(age, before)
    except ValueError:
        return None
    deltas = {skill: budget[skill] - before[skill] for skill in SKILLS}
    if (
        any(value < 0 for value in deltas.values())
        or sum(deltas.values()) != gain
        or budget["training"] - before["training"] != gain
        or budget["match"] != before["match"]
    ):
        return None
    if focus == Focus.PHYSICAL:
        if any(value for skill, value in deltas.items() if skill not in ("stamina", "pace")):
            return None
        return {skill: deltas[skill] for skill in ("stamina", "pace")}
    gained = {skill: value for skill, value in deltas.items() if value}
    return gained or {PRIMARY_SKILLS[position]: 0}


def _historical_fraction(
    row: dict[str, Any], development: PlayerDevelopment, skill: str
) -> int | None:
    age = row.get("development_age")
    if age != development.growth_age:
        return None
    recorded = row.get("season_gains", {})
    current = development.season_gains
    try:
        GrowthBudget(age, recorded)
        GrowthBudget(age, current)
    except ValueError:
        return None
    if any(current[key] < recorded[key] for key in BUDGET_KEYS):
        return None
    # Skills carry fractions through age changes/transfers. Within this age,
    # cumulative budget differences remove all later match AND training grants.
    recorded_gain = recorded.get(skill)
    if not isinstance(recorded_gain, int):
        return None
    later_gain = current[skill] - recorded_gain
    return (development.progress[skill] - later_gain) % 10000
