"""Integer economy-cycle-v2. Rates are per four-day cycle, fixed on signing."""

POLICY_VERSION = "economy-cycle-v2"
SALARY_POLICIES = ("economy-cycle-v1", POLICY_VERSION)
INITIAL_CAPITAL = 600_000
MAX_MONEY = 2**63 - 1
SCOUT_SEARCH_DAYS = (4, 8, 12, 16)
# Tier 1..5; age changes the new quote, never an existing contract.
STAFF_RATES = {
    "head_coach": (8_000, 14_000, 26_000, 48_000, 80_000),
    "goalkeeping_coach": (4_000, 8_000, 14_000, 24_000, 40_000),
    "defence_coach": (4_000, 8_000, 14_000, 24_000, 40_000),
    "attack_coach": (4_000, 8_000, 14_000, 24_000, 40_000),
    "fitness_coach": (4_000, 8_000, 14_000, 24_000, 40_000),
    "physiotherapist": (6_000, 10_000, 18_000, 30_000, 50_000),
    "scout": (800, 1_200, 1_800, 2_600, 4_000),
    "midfield_coach": (4_000, 8_000, 14_000, 24_000, 40_000),
    "wing_coach": (4_000, 8_000, 14_000, 24_000, 40_000),
}
STAFF_WAGES = {role: rates[0] for role, rates in STAFF_RATES.items()}
PRIMARY = {
    "GK": 0,
    "DL": 1,
    "DC": 1,
    "DR": 1,
    "DM": 1,
    "ML": 3,
    "MC": 2,
    "MR": 3,
    "AMC": 4,
    "AML": 3,
    "AMR": 3,
    "FC": 4,
}
STADIUM_CAPACITIES = (5_000, 10_000, 20_000, 35_000, 50_000)
STADIUM_PRICES = (0, 300_000, 800_000, 2_000_000, 5_000_000)
PRIZES = (100_000, 80_000, 65_000, 55_000, 45_000, 38_000, 32_000, 27_000, 23_000, 20_000)


def money(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_MONEY:
        raise ValueError("Money must fit a nonnegative BIGINT")
    return value


def _quality_units(skills: tuple[int, ...], position: str) -> int:
    if len(skills) != 8 or any(type(s) is not int or not 1 <= s <= 20 for s in skills):
        raise ValueError("Invalid wage skills")
    if position not in PRIMARY:
        raise ValueError("Invalid valuation position")
    return 3 * skills[PRIMARY[position]] + skills[6] + skills[7]


def player_quality(skills: tuple[int, ...], position: str = "MC") -> int:
    return _quality_units(skills, position) // 5


def _player_inputs(age: int, positions: int) -> None:
    if type(age) is not int or not 17 <= age <= 38:
        raise ValueError("Invalid player valuation age")
    if type(positions) is not int or not 1 <= positions <= 3:
        raise ValueError("Invalid position count")


def player_wage(skills: tuple[int, ...], age: int, position: str, positions: int = 1) -> int:
    _player_inputs(age, positions)
    q = player_quality(skills, position)
    return money(
        (100 + 15 * q * q) * (10_000 + (28 - age) * 200) * (100 + 8 * (positions - 1)) // 1_000_000
    )


def player_fee(skills: tuple[int, ...], age: int, position: str, positions: int = 1) -> int:
    _player_inputs(age, positions)
    q = player_quality(skills, position)
    base = max(100_000, 2_500 * q * q * (10_000 + (28 - age) * 1_000) // 10_000)
    return money(base * (100 + 15 * (positions - 1)) // 100)


def staff_wage(role: str, skill: int = 1, age: int = 50, *, loyal: bool = False) -> int:
    if role not in STAFF_RATES or type(skill) is not int or not 1 <= skill <= 20:
        raise ValueError("Unsupported staff wage role or skill")
    if type(age) is not int or not 17 <= age <= 150 or type(loyal) is not bool:
        raise ValueError("Invalid staff valuation age or loyalty")
    age_factor = 10_000 - (min(70, max(50, age)) - 50) * 100
    return money(
        STAFF_RATES[role][(skill - 1) // 4] * age_factor * (80 if loyal else 100) // 1_000_000
    )


def staff_fee(role: str, skill: int, age: int) -> int:
    rate = staff_wage(role, skill, age)
    factor = 11_000 - (min(70, max(50, age)) - 50) * 500
    return money(4 * rate * max(3_000, factor) // 10_000)


def scout_mission_fee(skill: int, days: int) -> int:
    if type(skill) is not int or not 1 <= skill <= 20:
        raise ValueError("Invalid scout skill")
    if type(days) is not int or days not in SCOUT_SEARCH_DAYS:
        raise ValueError("Scout search must last 4, 8, 12 or 16 days")
    return (100_000 + 5_000 * skill) * (days // 4)


def scout_signing_fee(skills: tuple[int, ...], age: int, position: str, positions: int = 1) -> int:
    if type(age) is not int or not 17 <= age <= 21:
        raise ValueError("Scout discoveries must be 17..21")
    _player_inputs(age, positions)
    # Preserve fifth-point resolution; examples q6/q10/q18 are not price bands.
    q = _quality_units(skills, position)
    base = max(100_000, 2_500 * q * q * (100 + (21 - age) * 5) // (25 * 100))
    return money(base * (100 + 15 * (positions - 1)) // 100)


def _ranking(tier: int, place: int) -> None:
    if (
        type(tier) is not int
        or type(place) is not int
        or not 1 <= tier <= 5
        or not 1 <= place <= 10
    ):
        raise ValueError("Invalid league ranking")


def sponsor_income(tier: int, place: int, supporters: int = 2500) -> int:
    """Cycle quote; AND-117 will freeze its inputs for the whole season."""
    _ranking(tier, place)
    money(supporters)
    return 48_000 + (5 - tier) * 4_000 + (11 - place) * 500 + min(supporters, 10_000)


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
