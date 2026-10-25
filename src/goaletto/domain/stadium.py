"""Versioned deterministic attendance demand and integer facility costs."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from goaletto.domain.finances import STADIUM_CAPACITIES, accrued_wage, money, stadium_upkeep
from goaletto.domain.game_cycle import CYCLE_SECONDS

LEGACY_DEMAND_POLICY = "stadium-demand-v1"
DEMAND_POLICY = "stadium-demand-v2"
EXPANSION_DAYS = 7


@dataclass(frozen=True)
class Attendance:
    supporters: int
    capacity: int
    tier: int
    place: int
    recent_points: tuple[int, ...]
    demand_bps: int
    fans: int
    competition: str = "league"
    stage: str = "league"


STAGE_FACTORS = {
    "qualification": 9000,
    "round_of_64": 10000,
    "round_of_32": 11000,
    "round_of_16": 12000,
    "quarterfinal": 13500,
    "semifinal": 15000,
    "final": 18000,
}


def match_stage(competition: str, stage: str | None) -> str:
    if competition in {"league", "friendly"}:
        return competition
    if competition not in {"cup", "champions"}:
        raise ValueError("Unknown attendance competition")
    if stage in {"prelim", "entry-16", "entry-8", "entry-4"}:
        return "qualification"
    mapped = {
        "round-32": "round_of_32",
        "round-16": "round_of_16",
        "quarter": "quarterfinal",
        "semi": "semifinal",
    }.get(stage or "", stage)
    if mapped not in STAGE_FACTORS:
        raise ValueError("Unknown attendance tournament stage")
    return mapped


def match_attendance(
    supporters: int,
    capacity: int,
    tier: int,
    place: int,
    recent_points: tuple[int, ...],
    *,
    competition: str,
    stage: str | None = None,
) -> Attendance:
    base = attendance(supporters, capacity, tier, place, recent_points)
    normalized = match_stage(competition, stage)
    factor = (
        10000
        if competition == "league"
        else 5500
        if competition == "friendly"
        else STAGE_FACTORS[normalized] + (2000 if competition == "champions" else 0)
    )
    bps = min(30000, base.demand_bps * factor // 10000)
    return Attendance(
        supporters,
        capacity,
        tier,
        place,
        recent_points,
        bps,
        min(capacity, supporters * bps // 10000),
        competition,
        normalized,
    )


def attendance(
    supporters: int, capacity: int, tier: int, place: int, recent_points: tuple[int, ...]
) -> Attendance:
    """Frozen V1 formula; new gameplay matches use match_attendance."""
    money(supporters)
    money(capacity)
    if (
        type(tier) is not int
        or not 1 <= tier <= 5
        or type(place) is not int
        or not 1 <= place <= 10
    ):
        raise ValueError("Invalid attendance league position")
    if len(recent_points) > 5 or any(
        type(p) is not int or p not in (0, 1, 3) for p in recent_points
    ):
        raise ValueError("Attendance uses up to five win/draw/loss results")
    # Base demand 80%; place contributes 4..40%, tier 0..40%, recent form ±20%.
    bps = min(
        20000,
        max(
            5000,
            8000
            + (11 - place) * 400
            + (5 - tier) * 1000
            + sum(400 if p == 3 else -400 if p == 0 else 0 for p in recent_points),
        ),
    )
    return Attendance(
        supporters,
        capacity,
        tier,
        place,
        recent_points,
        bps,
        min(capacity, supporters * bps // 10000),
    )


def stadium_level(capacity: int) -> int:
    if type(capacity) is not int or capacity not in STADIUM_CAPACITIES:
        raise ValueError("Unsupported stadium capacity")
    return STADIUM_CAPACITIES.index(capacity) + 1


def period_upkeep(
    capacity: int,
    start: datetime,
    end: datetime,
    changes: tuple[tuple[datetime, int], ...],
) -> tuple[int, list[dict[str, str | int]]]:
    """Prorate effective capacity intervals. Explicit dates; no clock reads."""
    if any(
        v.tzinfo is None or v.utcoffset() != timedelta(0) for v in (start, end)
    ) or not timedelta(0) < end - start <= timedelta(seconds=CYCLE_SECONDS):
        raise ValueError("Invalid stadium billing period")
    cursor, total = start, 0
    rows: list[dict[str, str | int]] = []
    for changed_at, new_capacity in (*changes, (end, capacity)):
        if (
            changed_at.tzinfo is None
            or changed_at.utcoffset() != timedelta(0)
            or not cursor <= changed_at <= end
        ):
            raise ValueError("Capacity changes must be ordered within the period")
        seconds = math.ceil((changed_at - cursor).total_seconds())
        charge = accrued_wage(stadium_upkeep(capacity), seconds, CYCLE_SECONDS)
        if seconds:
            rows.append(
                {
                    "from": cursor.isoformat(),
                    "to": changed_at.isoformat(),
                    "capacity": capacity,
                    "charge": charge,
                }
            )
            total += charge
        cursor, capacity = changed_at, new_capacity
    return total, rows
