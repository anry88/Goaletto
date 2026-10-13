"""Pure per-footballer season-rollover plan.

Wraps :mod:`goaletto.domain.retirement` without duplicating its probability
scale. The caller (application rollover transaction, owned by Codex) supplies
the explicit draw, persists the returned plan as its idempotency receipt, and
owns locks, market cancellation, youth replacement and outbox delivery.

No ORM, events, UUID generation, randomness, clocks or IO live here. Player
and staff careers, skill growth, morale, portraits and market rules are
explicitly out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from goaletto.domain.retirement import (
    PLAYER_RETIREMENT_POLICY,
    PROBABILITIES_BPS,
    RetirementDecision,
    player_last_season,
)

PlayerStatus = Literal["active", "last_season", "retired"]
PlayerSeasonOutcome = Literal[
    "continue_active", "continue_last_season", "retire_now", "already_retired"
]

STATUSES: tuple[str, str, str] = ("active", "last_season", "retired")
MIN_FOOTBALLER_AGE = 17
MAX_FOOTBALLER_AGE = 38
LAST_WARNING_AGE = 38
DRAW_MIN = 0
DRAW_MAX = 9999


@dataclass(frozen=True, kw_only=True)
class PlayerSeasonEntry:
    """Immutable snapshot of one footballer at a season boundary.

    ``current_age`` is the age as applied for ``completed_season_id`` (for a
    non-retired player ``current_age_applied_for_season_id`` must equal it).
    ``draw`` is the caller-owned explicit sample in 0..9999; it is consumed
    only for a continuing active player and ignored otherwise (but still
    validated so caller mistakes surface loudly).
    """

    person_id: UUID
    appearance_identity_id: str
    completed_season_id: UUID
    next_season_id: UUID
    current_age: int
    current_age_applied_for_season_id: UUID | None
    current_status: PlayerStatus
    current_last_season_id: UUID | None
    current_retired_after_season_id: UUID | None
    draw: int


@dataclass(frozen=True, kw_only=True)
class PlayerSeasonPlan:
    """Immutable transition plan; also the durable retry receipt shape.

    ``previous_*`` is the required pre-rollover image, ``next_*`` the
    post-rollover image. ``decision`` carries the persisted retirement draw
    (``None`` when no new draw was taken). ``next_age_applied_for_season_id``
    is ``None`` only for ``already_retired`` (leave the row untouched).
    Person/appearance identity is echoed unchanged, never reassigned.
    """

    person_id: UUID
    appearance_identity_id: str
    completed_season_id: UUID
    next_season_id: UUID
    outcome: PlayerSeasonOutcome
    previous_age: int
    previous_status: PlayerStatus
    previous_last_season_id: UUID | None
    previous_retired_after_season_id: UUID | None
    next_age: int
    next_age_applied_for_season_id: UUID | None
    next_status: PlayerStatus
    next_last_season_id: UUID | None
    next_retired_after_season_id: UUID | None
    decision: RetirementDecision | None


def _require_uuid(value: UUID, name: str) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be a UUID")
    return value


def _require_age(value: int, name: str) -> int:
    if type(value) is not int or not MIN_FOOTBALLER_AGE <= value <= MAX_FOOTBALLER_AGE:
        raise ValueError(
            f"{name} must be an integer {MIN_FOOTBALLER_AGE} through {MAX_FOOTBALLER_AGE}"
        )
    return value


def _require_draw(value: int) -> int:
    if type(value) is not int or not DRAW_MIN <= value <= DRAW_MAX:
        raise ValueError(f"Draw must be an integer {DRAW_MIN} through {DRAW_MAX}")
    return value


def _require_identity(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise ValueError("appearance_identity_id must be a nonempty string up to 100 chars")
    return value


def _check_status_refs(
    status: str, last_season_id: UUID | None, retired_after_season_id: UUID | None
) -> None:
    if status not in STATUSES:
        raise ValueError("status must be active, last_season or retired")
    for label, ref in (
        ("last_season_id", last_season_id),
        ("retired_after_season_id", retired_after_season_id),
    ):
        if ref is not None and not isinstance(ref, UUID):
            raise ValueError(f"{label} must be a UUID or None")
    if status == "active":
        if last_season_id is not None or retired_after_season_id is not None:
            raise ValueError("An active player must not carry season refs")
    elif status == "last_season":
        if last_season_id is None or retired_after_season_id is not None:
            raise ValueError("A last-season player needs last_season_id and no retirement ref")
    elif retired_after_season_id is None or retired_after_season_id != last_season_id:
        raise ValueError(
            "A retired player needs matching last_season_id and retired_after_season_id"
        )


def _validate_entry(entry: PlayerSeasonEntry) -> None:
    _require_uuid(entry.person_id, "person_id")
    _require_identity(entry.appearance_identity_id)
    _require_uuid(entry.completed_season_id, "completed_season_id")
    _require_uuid(entry.next_season_id, "next_season_id")
    if entry.completed_season_id == entry.next_season_id:
        raise ValueError("completed and next seasons must differ")
    _require_age(entry.current_age, "current_age")
    _require_draw(entry.draw)
    if entry.current_status == "last_season" and entry.current_age < 32:
        raise ValueError("Last season cannot be announced before age 32")
    _check_status_refs(
        entry.current_status, entry.current_last_season_id, entry.current_retired_after_season_id
    )
    applied = entry.current_age_applied_for_season_id
    if entry.current_status == "retired":
        if applied is not None and not isinstance(applied, UUID):
            raise ValueError("current_age_applied_for_season_id must be a UUID or None")
        return
    if applied != entry.completed_season_id and applied != entry.next_season_id:
        raise ValueError("A non-retired player's age must apply to the completed or next season")
    if entry.current_status == "last_season" and entry.current_last_season_id not in (
        entry.completed_season_id,
        entry.next_season_id,
    ):
        raise ValueError("last_season_id must reference the completed or next season")


def _expected_probability(age: int) -> int:
    return PROBABILITIES_BPS[age - 32] if age >= 32 else 0


def _check_decision(decision: RetirementDecision, next_age: int, *, must_warn: bool) -> None:
    if not isinstance(decision, RetirementDecision):
        raise ValueError("Invalid decision type")
    if type(decision.age) is not int or type(decision.probability_bps) is not int:
        raise ValueError("Decision age and probability must be integers")
    if type(decision.is_last_season) is not bool:
        raise ValueError("Decision outcome must be a boolean")
    if decision.age != next_age:
        raise ValueError("Decision age must equal the next season age")
    if decision.policy_version != PLAYER_RETIREMENT_POLICY:
        raise ValueError("Decision policy version mismatch")
    if decision.probability_bps != _expected_probability(next_age):
        raise ValueError("Decision probability does not match the approved scale")
    if decision.is_last_season != must_warn:
        raise ValueError("Decision outcome does not match the plan outcome")
    probability = decision.probability_bps
    if probability in (0, 10000):
        if decision.draw is not None:
            raise ValueError("Deterministic decisions must not store a draw")
    elif type(decision.draw) is not int:
        raise ValueError("A probabilistic decision must store its draw")
    elif not DRAW_MIN <= decision.draw <= DRAW_MAX:
        raise ValueError("Stored draw out of bounds")
    elif (decision.draw < probability) != decision.is_last_season:
        raise ValueError("Stored draw does not reproduce the decision outcome")


def _validate_plan(plan: PlayerSeasonPlan) -> None:
    _require_uuid(plan.person_id, "person_id")
    _require_identity(plan.appearance_identity_id)
    _require_uuid(plan.completed_season_id, "completed_season_id")
    _require_uuid(plan.next_season_id, "next_season_id")
    if plan.completed_season_id == plan.next_season_id:
        raise ValueError("completed and next seasons must differ")
    if plan.outcome not in (
        "continue_active",
        "continue_last_season",
        "retire_now",
        "already_retired",
    ):
        raise ValueError("Unknown plan outcome")
    _require_age(plan.previous_age, "previous_age")
    _require_age(plan.next_age, "next_age")
    _check_status_refs(
        plan.previous_status, plan.previous_last_season_id, plan.previous_retired_after_season_id
    )
    _check_status_refs(
        plan.next_status, plan.next_last_season_id, plan.next_retired_after_season_id
    )
    completed = plan.completed_season_id
    upcoming = plan.next_season_id
    if plan.outcome in ("continue_active", "continue_last_season"):
        must_warn = plan.outcome == "continue_last_season"
        if (
            plan.previous_status,
            plan.previous_last_season_id,
            plan.previous_retired_after_season_id,
        ) != (
            "active",
            None,
            None,
        ):
            raise ValueError("A continuation plan must start from an active player")
        if not MIN_FOOTBALLER_AGE <= plan.previous_age <= LAST_WARNING_AGE - 1:
            raise ValueError("Only ages 17 through 37 can continue into a next season")
        if plan.next_age != plan.previous_age + 1:
            raise ValueError("A continuing player ages exactly one year")
        if plan.next_age_applied_for_season_id != upcoming:
            raise ValueError("The next age must apply to the next season")
        if plan.next_retired_after_season_id is not None:
            raise ValueError("A continuing player must not carry a retirement ref")
        if must_warn:
            if plan.next_status != "last_season" or plan.next_last_season_id != upcoming:
                raise ValueError("A warned plan must flag the whole next season as last")
        elif (plan.next_status, plan.next_last_season_id) != ("active", None):
            raise ValueError("A non-warned plan must stay plain active")
        if plan.decision is None:
            raise ValueError("A continuation plan must carry its retirement decision")
        _check_decision(plan.decision, plan.next_age, must_warn=must_warn)
    elif plan.outcome == "retire_now":
        if (
            plan.previous_status,
            plan.previous_last_season_id,
            plan.previous_retired_after_season_id,
        ) != (
            "last_season",
            completed,
            None,
        ):
            raise ValueError("Retirement must follow the warned completed season")
        if (plan.next_status, plan.next_last_season_id, plan.next_retired_after_season_id) != (
            "retired",
            completed,
            completed,
        ):
            raise ValueError("Retirement must pin both refs to the completed season")
        if plan.next_age != plan.previous_age:
            raise ValueError("Retirement preserves the last played age")
        if plan.next_age_applied_for_season_id != completed:
            raise ValueError("Retirement keeps the age applied to the completed season")
        if plan.decision is not None:
            raise ValueError("Retirement takes no new draw")
    else:
        if (
            plan.previous_status != "retired"
            or (plan.previous_age, plan.previous_status, plan.previous_last_season_id)
            != (plan.next_age, plan.next_status, plan.next_last_season_id)
            or plan.previous_retired_after_season_id != plan.next_retired_after_season_id
        ):
            raise ValueError("An already-retired plan must change nothing")
        if plan.next_age_applied_for_season_id is not None:
            raise ValueError("An already-retired plan must not touch the age season")
        if plan.decision is not None:
            raise ValueError("An already-retired plan takes no new draw")


def _is_pre_image(entry: PlayerSeasonEntry, plan: PlayerSeasonPlan) -> bool:
    if (
        entry.current_age != plan.previous_age
        or entry.current_status != plan.previous_status
        or entry.current_last_season_id != plan.previous_last_season_id
        or entry.current_retired_after_season_id != plan.previous_retired_after_season_id
    ):
        return False
    if plan.previous_status == "retired":
        return True
    return entry.current_age_applied_for_season_id == plan.completed_season_id


def _is_post_image(entry: PlayerSeasonEntry, plan: PlayerSeasonPlan) -> bool:
    if (
        entry.current_age != plan.next_age
        or entry.current_status != plan.next_status
        or entry.current_last_season_id != plan.next_last_season_id
        or entry.current_retired_after_season_id != plan.next_retired_after_season_id
    ):
        return False
    expected = plan.next_age_applied_for_season_id
    return expected is None or entry.current_age_applied_for_season_id == expected


def _fresh_plan(entry: PlayerSeasonEntry) -> PlayerSeasonPlan:
    if entry.current_status == "retired":
        return PlayerSeasonPlan(
            person_id=entry.person_id,
            appearance_identity_id=entry.appearance_identity_id,
            completed_season_id=entry.completed_season_id,
            next_season_id=entry.next_season_id,
            outcome="already_retired",
            previous_age=entry.current_age,
            previous_status=entry.current_status,
            previous_last_season_id=entry.current_last_season_id,
            previous_retired_after_season_id=entry.current_retired_after_season_id,
            next_age=entry.current_age,
            next_age_applied_for_season_id=None,
            next_status="retired",
            next_last_season_id=entry.current_last_season_id,
            next_retired_after_season_id=entry.current_retired_after_season_id,
            decision=None,
        )
    if entry.current_status == "last_season":
        if entry.current_last_season_id != entry.completed_season_id:
            raise ValueError(
                "A warned flag for the next season needs its accepted receipt; pass prior"
            )
        if entry.current_age_applied_for_season_id != entry.completed_season_id:
            raise ValueError("Retirement applies only to the warned completed season")
        return PlayerSeasonPlan(
            person_id=entry.person_id,
            appearance_identity_id=entry.appearance_identity_id,
            completed_season_id=entry.completed_season_id,
            next_season_id=entry.next_season_id,
            outcome="retire_now",
            previous_age=entry.current_age,
            previous_status=entry.current_status,
            previous_last_season_id=entry.current_last_season_id,
            previous_retired_after_season_id=entry.current_retired_after_season_id,
            next_age=entry.current_age,
            next_age_applied_for_season_id=entry.completed_season_id,
            next_status="retired",
            next_last_season_id=entry.completed_season_id,
            next_retired_after_season_id=entry.completed_season_id,
            decision=None,
        )
    if entry.current_age_applied_for_season_id != entry.completed_season_id:
        raise ValueError("An already-aged player needs its accepted receipt; pass prior")
    if entry.current_age >= LAST_WARNING_AGE:
        raise ValueError(
            "An active 38-year-old is impossible; age 38 is always a warned last season"
        )
    next_age = entry.current_age + 1
    decision = player_last_season(next_age, entry.draw)
    if decision.is_last_season:
        return PlayerSeasonPlan(
            person_id=entry.person_id,
            appearance_identity_id=entry.appearance_identity_id,
            completed_season_id=entry.completed_season_id,
            next_season_id=entry.next_season_id,
            outcome="continue_last_season",
            previous_age=entry.current_age,
            previous_status=entry.current_status,
            previous_last_season_id=entry.current_last_season_id,
            previous_retired_after_season_id=entry.current_retired_after_season_id,
            next_age=next_age,
            next_age_applied_for_season_id=entry.next_season_id,
            next_status="last_season",
            next_last_season_id=entry.next_season_id,
            next_retired_after_season_id=None,
            decision=decision,
        )
    return PlayerSeasonPlan(
        person_id=entry.person_id,
        appearance_identity_id=entry.appearance_identity_id,
        completed_season_id=entry.completed_season_id,
        next_season_id=entry.next_season_id,
        outcome="continue_active",
        previous_age=entry.current_age,
        previous_status=entry.current_status,
        previous_last_season_id=entry.current_last_season_id,
        previous_retired_after_season_id=entry.current_retired_after_season_id,
        next_age=next_age,
        next_age_applied_for_season_id=entry.next_season_id,
        next_status="active",
        next_last_season_id=None,
        next_retired_after_season_id=None,
        decision=decision,
    )


def plan_player_season(
    entry: PlayerSeasonEntry, *, prior: PlayerSeasonPlan | None = None
) -> PlayerSeasonPlan:
    """Plan one footballer's season transition without any side effects.

    Fresh call (``prior=None``) requires the pre-rollover image: an active
    player aged 17..37 whose age still applies to the completed season, a
    warned player whose flag references the completed season, or an already
    retired player (returned unchanged).

    Retry call (``prior`` is the accepted plan) ignores a differing ``draw``
    and returns ``prior`` unchanged when ``entry`` matches either the stored
    pre-image or the already-applied post-image. Anything else — foreign
    person/seasons/identity or a state matching neither image — raises
    ``ValueError`` instead of masquerading as a successful retry. This pure
    function cannot guarantee transactional idempotency on its own; see
    ``docs/player-season-plan.md`` for the keys, unique constraint and locking
    the application caller must enforce.
    """
    _validate_entry(entry)
    if prior is None:
        return _fresh_plan(entry)
    _validate_plan(prior)
    if (
        prior.person_id != entry.person_id
        or prior.appearance_identity_id != entry.appearance_identity_id
        or prior.completed_season_id != entry.completed_season_id
        or prior.next_season_id != entry.next_season_id
    ):
        raise ValueError("prior receipt belongs to a different person, identity or season pair")
    if _is_pre_image(entry, prior) or _is_post_image(entry, prior):
        return prior
    raise ValueError("Entry matches neither the accepted pre-image nor the applied post-image")
