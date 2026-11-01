"""Lifetime positional experience in fractions of a point; no I/O or randomness."""

from goaletto.domain.match_types import POSITIONS


def validate_experience(values: dict[str, int]) -> dict[str, int]:
    if type(values) is not dict or any(
        position not in POSITIONS or type(value) is not int or value < 0
        for position, value in values.items()
    ):
        raise ValueError("Invalid positional experience")
    return dict(values)


def credit_experience(values: dict[str, int], gains: dict[str, int]) -> dict[str, int]:
    result = validate_experience(values)
    for position, gain in validate_experience(gains).items():
        result[position] = result.get(position, 0) + gain
    return result


def experience_bonus(experience_bps: int) -> int:
    """One experience point adds 0.1%, bounded at +10% for 100 points."""
    if type(experience_bps) is not int or experience_bps < 0:
        raise ValueError("Invalid positional experience")
    return min(experience_bps, 1_000_000) // 1000


PERCENT_POLICY = "position-experience-v2"
PERCENT_MAX = 1_000_000
LEGACY_TO_PERCENT = 100


def validate_percent_experience(values: dict[str, int]) -> dict[str, int]:
    result = validate_experience(values)
    if any(value > PERCENT_MAX for value in result.values()):
        raise ValueError("Positional experience must be 0..100%")
    return result


def percent_practice(
    values: dict[str, int],
    nominal: dict[str, int],
    profiles: tuple[str, ...],
    *,
    age: int,
    decay: bool = False,
    experience_factor: int = 10000,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    from goaletto.domain.training import age_multiplier

    result = validate_percent_experience(values)
    age_coefficient = age_multiplier(age)
    validate_experience(nominal)
    if not profiles or any(position not in POSITIONS for position in profiles):
        raise ValueError("Invalid profile positions")
    if type(experience_factor) is not int or not 1 <= experience_factor <= 10000:
        raise ValueError("Invalid experience factor")
    grants, losses = {}, {}
    for position, requested in nominal.items():
        requested *= LEGACY_TO_PERCENT
        requested = requested if position in profiles else requested // 10
        grant = min(requested, PERCENT_MAX - result.get(position, 0))
        result[position] = result.get(position, 0) + grant
        grants[position] = grant
    if decay:
        rate = (
            (1000 * age_coefficient // 10000) * experience_factor // 10000 * LEGACY_TO_PERCENT // 10
        )
        for position in values.keys() - nominal.keys():
            loss = min(result[position], rate)
            if loss:
                result[position] -= loss
                losses[position] = loss
    return result, grants, losses


def bounded_experience_factor(experience: int) -> int:
    """V7: 0..100% earned experience retains 80..100% of the relevant skill."""
    if type(experience) is not int or not 0 <= experience <= PERCENT_MAX:
        raise ValueError("Positional experience must be 0..100%")
    return 800000 + experience // 5
