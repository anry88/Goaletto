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
