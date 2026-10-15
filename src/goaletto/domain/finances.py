"""Integer prototype economy. All coefficients pin to economy-v1."""

POLICY_VERSION = "economy-cycle-v1"
INITIAL_CAPITAL = 600_000
MAX_MONEY = 2**63 - 1
STAFF_WAGES = {
    "head_coach": 50_000,
    "goalkeeping_coach": 20_000,
    "defence_coach": 20_000,
    "attack_coach": 20_000,
    "fitness_coach": 20_000,
    "physiotherapist": 30_000,
    "scout": 20_000,
}
STADIUM_CAPACITIES = (5_000, 10_000, 20_000, 35_000, 50_000)
STADIUM_PRICES = (0, 300_000, 800_000, 2_000_000, 5_000_000)
PRIZES = (250_000, 180_000, 130_000, 100_000, 80_000, 60_000, 50_000, 40_000, 30_000, 20_000)


def money(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_MONEY:
        raise ValueError("Money must fit a nonnegative BIGINT")
    return value


def player_wage(skills: tuple[int, ...]) -> int:
    if len(skills) != 8 or any(type(s) is not int or not 1 <= s <= 20 for s in skills):
        raise ValueError("Invalid wage skills")
    # Fixed at contract creation. Training does not silently rewrite the contract.
    return 100 + 10 * sum(skills)


def staff_wage(role: str) -> int:
    if role not in STAFF_WAGES:
        raise ValueError("Unsupported staff wage role")
    return STAFF_WAGES[role]


def _ranking(tier: int, place: int) -> None:
    if (
        type(tier) is not int
        or type(place) is not int
        or not 1 <= tier <= 5
        or not 1 <= place <= 10
    ):
        raise ValueError("Invalid league ranking")


def sponsor_income(tier: int, place: int) -> int:
    _ranking(tier, place)
    return (6 - tier) * 70_000 + (11 - place) * 1_000


def season_prize(tier: int, place: int) -> int:
    _ranking(tier, place)
    return (6 - tier) * PRIZES[place - 1]


def ticket_income(attendance: int, capacity: int) -> int:
    money(attendance)
    money(capacity)
    if attendance > capacity:
        raise ValueError("Attendance exceeds capacity")
    return money(attendance * 5)


def merchandise_income(supporters: int) -> int:
    return money(supporters)


def stadium_upkeep(capacity: int) -> int:
    money(capacity)
    return money(2_000 + capacity // 5)


def accrued_wage(weekly: int, seconds: int, period_seconds: int = 345_600) -> int:
    money(weekly)
    if (
        period_seconds not in (86400, 345600, 604800)
        or type(seconds) is not int
        or not 0 <= seconds <= period_seconds
    ):
        raise ValueError("Invalid salary duration")
    return money((weekly * seconds + period_seconds - 1) // period_seconds)
