"""One attended session: bounded head-coach morale and gradual leadership."""

import hashlib
from dataclasses import dataclass
from uuid import UUID

from goaletto.domain.development import LEADERSHIP_LIMIT, GrowthBudget

POLICY = "staff-session-v1"


def seeded_draw(seed: str, person_id: UUID, kind: str, *, policy: str = POLICY) -> int:
    return (
        int.from_bytes(
            hashlib.sha256(f"{policy}:{seed}:{person_id}:{kind}".encode()).digest()[:8], "big"
        )
        % 10000
    )


@dataclass(frozen=True)
class StaffEffect:
    morale: int
    leadership: int
    leadership_progress: int
    budget: GrowthBudget
    leadership_gain: int


def mentor_session(
    person_id: UUID,
    morale: int,
    leadership: int,
    fraction: int,
    budget: GrowthBudget,
    *,
    head_skill: int,
    seed: str,
    personal_morale: bool = True,
) -> StaffEffect:
    if any(type(v) is not int for v in (morale, leadership, fraction, head_skill)) or not (
        0 <= morale <= 100
        and 1 <= leadership <= 20
        and 0 <= fraction < 10000
        and 0 <= head_skill <= 20
    ):
        raise ValueError("Invalid mentoring input")
    target = 50 + head_skill
    if type(personal_morale) is not bool:
        raise ValueError("Personal morale switch must be a boolean")
    if (
        personal_morale
        and morale < target
        and seeded_draw(seed, person_id, "morale") < (500 + head_skill * 150)
    ):
        morale += 1
    gain = max(
        0,
        min(
            head_skill * 5,
            LEADERSHIP_LIMIT - budget.used["leadership"],
            (20 - leadership) * 10000 - fraction,
        ),
    )
    used = {**budget.used, "leadership": budget.used["leadership"] + gain}
    whole, fraction = divmod(fraction + gain, 10000)
    leadership += whole
    return StaffEffect(
        morale, leadership, fraction if leadership < 20 else 0, GrowthBudget(budget.age, used), gain
    )
