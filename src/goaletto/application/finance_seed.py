"""Financial opening rows for a new world, with explicit timestamps and IDs."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from goaletto.domain.finances import POLICY_VERSION, player_wage
from goaletto.domain.game_cycle import CYCLE_SECONDS, midnight, season_epoch
from goaletto.domain.world import WorldPlan


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
    first = midnight(season_epoch(plan.options.first_kickoff))
    duration = timedelta(seconds=CYCLE_SECONDS)
    first += ((at - first) // duration + 1) * duration
    jobs = [cycle_job(club.id, at, status="held") for club in clubs]
    for job in jobs:
        job["due_at"] = first
        job["payload"]["period_end"] = first.isoformat()
    return accounts, entries, salaries, jobs


def cycle_job(club_id: UUID, start: datetime, *, status: str = "pending") -> dict[str, Any]:
    end = start + timedelta(seconds=CYCLE_SECONDS)
    key = f"finance:cycle-v1:{club_id}:{start.isoformat()}"
    return {
        "id": uuid5(club_id, key),
        "dedupe_key": key,
        "kind": "finance.cycle",
        "due_at": end,
        "status": status,
        "attempts": 0,
        "last_error": None,
        "payload": {
            "version": 2,
            "club_id": str(club_id),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "policy": "economy-cycle-v1",
        },
    }
