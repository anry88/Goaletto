"""Read historical extras from receipts without changing or granting development."""

from collections import defaultdict
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.domain.development import BUDGET_KEYS, GrowthBudget
from goaletto.domain.staff_effects import POLICY as LEGACY_POLICY
from goaletto.domain.training import LEGACY_SPECIALISTS, SPECIALISTS, Focus, age_multiplier
from goaletto.infrastructure.models import MatchDevelopment, Player, PlayerDevelopment, TrainingPlan


def training_experience_gain(plan: TrainingPlan, row: dict[str, Any]) -> int | None:
    if isinstance(row.get("legacy_experience_gain"), int):
        return int(row["legacy_experience_gain"])
    if isinstance(row.get("experience_gain"), int):
        return int(row["experience_gain"])
    age = row.get("development_age")
    snapshot = plan.staff_snapshot
    if not isinstance(age, int) or snapshot.get("policy") not in (
        LEGACY_POLICY,
        "staff-session-v2",
    ):
        return None
    focus = Focus(plan.focus)
    roles = LEGACY_SPECIALISTS if snapshot["policy"] == LEGACY_POLICY else SPECIALISTS
    role = "head_coach" if focus == Focus.TEAM else roles[focus]
    if role not in snapshot.get("roles", {}):
        return None
    staff = snapshot["roles"][role]
    skill = int(staff["skill"]) if staff else 0
    return (10 + skill) * age_multiplier(age) // 10000


async def people_for(
    session: AsyncSession, ids: list[UUID]
) -> dict[str, tuple[Player, PlayerDevelopment]]:
    if not ids:
        return {}
    return {
        str(player.person_id): (player, development)
        for player, development in (
            await session.execute(
                select(Player, PlayerDevelopment)
                .join(PlayerDevelopment, PlayerDevelopment.person_id == Player.person_id)
                .where(Player.person_id.in_(ids))
            )
        ).all()
    }


async def add_legacy_experience(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    at: datetime,
    people: dict[str, tuple[Player, PlayerDevelopment]],
) -> None:
    needed = {
        row["person_id"]: row
        for row in rows
        if "position_experience" not in row
        and "experience_progress" not in row
        and row.get("experience_gain") is not None
        and row["person_id"] in people
    }
    if not needed:
        return
    later: dict[str, int] = defaultdict(int)
    unknown = set()
    models: tuple[type[TrainingPlan] | type[MatchDevelopment], ...] = (
        TrainingPlan,
        MatchDevelopment,
    )
    for model in models:
        receipts = (
            await session.scalars(
                select(model).where(
                    model.applied_at > at,
                    or_(*(model.results.contains([{"person_id": pid}]) for pid in needed)),
                )
            )
        ).all()
        for receipt in cast(list[TrainingPlan | MatchDevelopment], receipts):
            for row in receipt.results:
                pid = row.get("person_id")
                if not isinstance(pid, str) or pid not in needed:
                    continue
                gain = (
                    training_experience_gain(receipt, row)
                    if isinstance(receipt, TrainingPlan)
                    else row.get("legacy_experience_gain", row.get("experience_gain"))
                )
                if not isinstance(gain, int) or gain < 0:
                    unknown.add(pid)
                else:
                    later[pid] += gain
    for pid, row in needed.items():
        player, development = people[pid]
        gain = row["experience_gain"]
        after = player.experience * 10000 + development.progress["experience"] - later[pid]
        valid = (
            pid not in unknown
            and after >= gain
            and after // 10000 == row["experience_after"]
            and (after - gain) // 10000 == row["experience_before"]
        )
        row["experience_progress"] = {
            "before": after - gain if valid else None,
            "after": after if valid else None,
            "gain": gain,
        }


def historical_fraction(
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
    fraction = (
        development.leadership_progress if skill == "leadership" else development.progress[skill]
    )
    return (fraction - later_gain) % 10000


async def match_report_rows(
    session: AsyncSession, rows: list[dict[str, Any]], at: datetime
) -> list[dict[str, Any]]:
    details = [dict(row) for row in rows]
    needed = [
        row for row in details if "skill_progress" not in row or "position_experience" not in row
    ]
    people = await people_for(session, [UUID(row["person_id"]) for row in needed])
    for row in needed:
        person = people.get(row["person_id"])
        if person is None:
            continue
        _, development = person
        if "skill_progress" not in row:
            evidence = {**row, "development_age": row["age"]}
            row["skill_progress"] = {
                skill: {
                    "before_value": row["changes"].get(skill, {}).get("before", 0),
                    "after_value": row["changes"].get(skill, {}).get("after", 0),
                    "after": historical_fraction(evidence, development, skill),
                }
                for skill, gain in row.get("granted", {}).items()
                if gain
            }
    await add_legacy_experience(session, details, at, people)
    return details
