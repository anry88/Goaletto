"""One placement prize per club and knockout edition."""

from goaletto.domain.champions_bracket import rounds_for

POLICY = "knockout-prizes-v1"

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
CHAMPIONS_WINNER_PRIZE = 450_000


def champions_exit_prize(participant_count: int, round_number: int) -> int:
    total_rounds = rounds_for(participant_count)
    if not 1 <= round_number <= total_rounds:
        raise ValueError("Invalid champions prize round")
    # Keep the first exit small in every bracket and scale by progress to its final.
    return 12_000 + 1_000 * (268 * (round_number - 1) ** 2 // (total_rounds - 1) ** 2)
