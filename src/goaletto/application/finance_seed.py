"""Financial opening rows for a new world, with explicit timestamps and IDs."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from goaletto.domain.finances import POLICY_VERSION, player_wage
from goaletto.domain.world import WorldPlan


def week_start(at: datetime) -> datetime:
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise ValueError("Finance week must be UTC")
    return (at.astimezone(UTC) - timedelta(days=at.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def weekly_job(club_id: UUID, week: datetime, *, status: str = "pending") -> dict[str, Any]:
    key = f"finance:v1:{club_id}:{week.isoformat()}"
    return {
        "id": uuid5(club_id, key),
        "dedupe_key": key,
        "kind": "finance.weekly",
        "due_at": week,
        "status": status,
        "attempts": 0,
        "last_error": None,
        "payload": {
            "version": 1,
            "club_id": str(club_id),
            "week_start": week.isoformat(),
            "policy": POLICY_VERSION,
        },
    }


def opening_rows(plan: WorldPlan, at: datetime) -> tuple[list[dict[str, Any]], ...]:
    clubs = [club for league in plan.leagues for club in league.clubs]
    accounts = [{"club_id": club.id, "reserved": 0, "arrears": 0, "sequence": 1} for club in clubs]
    entries = [
        {
            "id": uuid5(club.id, "money:v1:opening"),
            "club_id": club.id,
            "operation_key": "opening",
            "sequence": 1,
            "kind": "opening",
            "cash_delta": plan.options.initial_budget,
            "reserved_delta": 0,
            "arrears_delta": 0,
            "balance_after": plan.options.initial_budget,
            "reserved_after": 0,
            "arrears_after": 0,
            "occurred_at": at,
            "request": {"kind": "opening", "amount": plan.options.initial_budget, "details": {}},
        }
        for club in clubs
    ]
    salaries = [
        {
            "id": uuid5(p.id, f"salary:v1:{p.club_id}:{at.isoformat()}"),
            "person_id": p.id,
            "club_id": p.club_id,
            "role": "player",
            "weekly_rate": player_wage(p.skills[:8]),
            "policy_version": POLICY_VERSION,
            "started_at": at,
            "ended_at": None,
        }
        for p in plan.players
    ]
    # A new world keeps its work held until the explicit launch activation.
    first = week_start(plan.options.first_kickoff) + timedelta(days=7)
    jobs = [weekly_job(club.id, first, status="held") for club in clubs]
    return accounts, entries, salaries, jobs
