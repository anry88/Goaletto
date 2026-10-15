"""Fixed seasonal sponsorship and cumulative integer interval accrual."""

from dataclasses import dataclass

from goaletto.domain.finances import sponsor_income
from goaletto.domain.game_cycle import CYCLE_SECONDS

POLICY = "season-sponsor-v1"


@dataclass(frozen=True)
class SponsorTerms:
    tier: int
    place: int | None
    supporters: int
    cycle_rate: int


def sponsor_terms(tier: int, place: int | None, supporters: int) -> SponsorTerms:
    # A brand-new club has no previous finish: use the conservative tenth-place base.
    if type(tier) is not int or not 1 <= tier <= 5:
        raise ValueError("Invalid sponsorship tier")
    if place is not None and (type(place) is not int or not 1 <= place <= 10):
        raise ValueError("Invalid sponsorship place")
    if type(supporters) is not int or supporters < 0:
        raise ValueError("Invalid sponsorship supporters")
    return SponsorTerms(tier, place, supporters, sponsor_income(tier, place or 10, supporters))


def sponsor_accrual(rate: int, start_microseconds: int, end_microseconds: int) -> int:
    if (
        any(type(v) is not int or v < 0 for v in (rate, start_microseconds, end_microseconds))
        or end_microseconds < start_microseconds
    ):
        raise ValueError("Invalid sponsor interval")
    divisor = CYCLE_SECONDS * 1_000_000
    return rate * end_microseconds // divisor - rate * start_microseconds // divisor
