"""Audience growth from official sporting success; explicit frozen inputs only."""

from goaletto.domain.stadium import STAGE_FACTORS, match_stage

POLICY = "supporters-v1"
TIER_SCALE = {1: 120000, 2: 75000, 3: 45000, 4: 25000, 5: 15000}


def _validate(supporters: int, tier: int) -> None:
    if (
        type(supporters) is not int
        or not 0 <= supporters <= 2_000_000_000
        or type(tier) is not int
        or tier not in TIER_SCALE
    ):
        raise ValueError("Invalid supporter base or tier")


def _gain(supporters: int, tier: int, raw: int) -> int:
    # Diminishing growth, without deleting loyal fans after an ordinary defeat.
    return (
        min(
            2_000_000_000 - supporters,
            max(1, raw * TIER_SCALE[tier] // (TIER_SCALE[tier] + supporters)),
        )
        if raw
        else 0
    )


def match_gain(
    supporters: int,
    tier: int,
    opponent_tier: int,
    competition: str,
    stage: str,
    goals_for: int,
    goals_against: int,
    *,
    advanced: bool = False,
) -> int:
    _validate(supporters, tier)
    _validate(0, opponent_tier)
    stage = match_stage(competition, stage)
    if (
        any(type(g) is not int or g < 0 for g in (goals_for, goals_against))
        or type(advanced) is not bool
    ):
        raise ValueError("Invalid supporter match result")
    if advanced and competition not in {"cup", "champions"}:
        raise ValueError("Only knockout games advance")
    if competition == "friendly":
        return 0
    surprise = max(0, tier - opponent_tier)
    raw = 0
    if goals_for > goals_against:
        raw = (
            80 + supporters * 120 // 10000 + min(3, goals_for - goals_against) * 15 + surprise * 30
        )
    elif goals_for == goals_against and surprise:
        raw = 20 + supporters * 25 // 10000 + surprise * 15
    if competition in {"cup", "champions"}:
        factor = STAGE_FACTORS[stage] + (2000 if competition == "champions" else 0)
        raw = raw * factor // 10000
        if advanced:
            raw += (100 + supporters * (300 if stage == "final" else 80) // 10000) * factor // 10000
    return _gain(supporters, tier, raw)


def season_gain(supporters: int, tier: int, place: int, destination_tier: int) -> int:
    _validate(supporters, tier)
    _validate(0, destination_tier)
    if type(place) is not int or not 1 <= place <= 10 or abs(destination_tier - tier) > 1:
        raise ValueError("Invalid supporter season result")
    raw = (
        600 + supporters * 600 // 10000
        if place == 1
        else 200 + supporters * 200 // 10000
        if place == 2
        else 100 + supporters * 100 // 10000
        if place == 3
        else 0
    )
    if destination_tier < tier:
        raw += 400 + supporters * 500 // 10000
    return _gain(supporters, min(tier, destination_tier), raw)
