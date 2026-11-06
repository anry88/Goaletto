"""One placement prize per club and knockout edition."""

from goaletto.domain.champions_bracket import rounds_for

POLICY = "knockout-prizes-v1"
CHAMPIONS_TIER_POLICY = "knockout-prizes-v2"
CHAMPIONS_REBALANCED_POLICY = "knockout-prizes-v3"

CUP_EXIT_PRIZES = {
    "prelim": 12_000,
    "entry-16": 20_000,
    "entry-8": 30_000,
    "entry-4": 45_000,
    "round-32": 65_000,
    "round-16": 95_000,
    "quarter": 145_000,
    "semi": 220_000,
    "final": 320_000,
}
CUP_WINNER_PRIZE = 500_000
CHAMPIONS_WINNER_PRIZE = 110_000
LEGACY_CHAMPIONS_WINNER_PRIZE = 450_000


def champions_prize_multiplier(tier: int) -> int:
    if type(tier) is not int or not 1 <= tier <= 5:
        raise ValueError("Invalid champions qualifying tier")
    return 6 - tier


def champions_exit_prize(participant_count: int, round_number: int, *, legacy: bool = False) -> int:
    total_rounds = rounds_for(participant_count)
    if not 1 <= round_number <= total_rounds:
        raise ValueError("Invalid champions prize round")
    # Keep the first exit small in every bracket and scale by progress to its final.
    base, growth = (12, 268) if legacy else (3, 65)
    return 1_000 * (base + growth * (round_number - 1) ** 2 // (total_rounds - 1) ** 2)
