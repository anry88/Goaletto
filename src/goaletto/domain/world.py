"""Deterministic 5 x 10 x 20 starting world. No art or persistence dependencies."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import product
from random import Random
from uuid import UUID, uuid5

from goaletto.domain.calendar import validate_kickoff
from goaletto.domain.club_name_catalog import CLUB_NAMING_POLICY, allocate_names
from goaletto.domain.names import INTERNATIONAL_CLUBS, NAMING_POLICY, international_catalog
from goaletto.domain.person_names import PERSON_NAMING_POLICY, generated_name
from goaletto.domain.retirement import RetirementDecision, player_last_season
from goaletto.domain.starter_squads import LEGACY_STARTER_POLICY, STARTER_POLICY, starter_skills

WORLD_POLICY = "world-seed-v1"
WORLD_NAMESPACE = UUID("4c5e18f0-c8ad-5bfd-a852-a2f93342c42e")
ROSTER = (
    ("GK", 25),
    ("DL", 24),
    ("DC", 29),
    ("DC", 31),
    ("DR", 26),
    ("ML", 23),
    ("MC", 27),
    ("MC", 28),
    ("MR", 22),
    ("FC", 30),
    ("FC", 24),
    ("GK", 19),
    ("DL", 21),
    ("DC", 33),
    ("DR", 35),
    ("MC", 20),
    ("ML", 32),
    ("MR", 36),
    ("FC", 18),
    ("FC", 38),
)
SKILL_NAMES = (
    "goalkeeping",
    "defending",
    "playmaking",
    "winger",
    "attacking",
    "set_pieces",
    "stamina",
    "pace",
    "leadership",
    "loyalty",
)
PRIMARY_SKILL = {
    "GK": "goalkeeping",
    "DL": "defending",
    "DC": "defending",
    "DR": "defending",
    "ML": "winger",
    "MC": "playmaking",
    "MR": "winger",
    "FC": "attacking",
}
FIRST_NAMES = (
    "Александр",
    "Алексей",
    "Андрей",
    "Артём",
    "Борис",
    "Вадим",
    "Валерий",
    "Василий",
    "Виктор",
    "Виталий",
    "Владимир",
    "Владислав",
    "Георгий",
    "Глеб",
    "Даниил",
    "Денис",
    "Дмитрий",
    "Евгений",
    "Егор",
    "Иван",
    "Игорь",
    "Илья",
    "Кирилл",
    "Константин",
    "Лев",
    "Леонид",
    "Максим",
    "Марк",
    "Матвей",
    "Михаил",
    "Никита",
    "Николай",
    "Олег",
    "Павел",
    "Пётр",
    "Роман",
    "Руслан",
    "Семён",
    "Сергей",
    "Станислав",
    "Степан",
    "Тимофей",
    "Фёдор",
    "Филипп",
    "Юрий",
    "Ярослав",
)
LAST_NAMES = (
    "Белов",
    "Быков",
    "Власов",
    "Волков",
    "Воробьёв",
    "Горин",
    "Громов",
    "Данилов",
    "Демин",
    "Дорохов",
    "Егоров",
    "Ершов",
    "Жаров",
    "Зайцев",
    "Зорин",
    "Иванов",
    "Ильин",
    "Казаков",
    "Калинин",
    "Климов",
    "Козлов",
    "Котов",
    "Крылов",
    "Лапин",
    "Лебедев",
    "Логинов",
    "Макаров",
    "Миронов",
    "Морозов",
    "Назаров",
    "Никитин",
    "Новиков",
    "Орлов",
    "Панов",
    "Петров",
    "Поляков",
    "Родин",
    "Романов",
    "Рыбаков",
    "Савин",
    "Серов",
    "Смирнов",
    "Соколов",
    "Соловьёв",
    "Титов",
    "Федотов",
    "Фомин",
    "Чернов",
    "Шаров",
    "Щукин",
    "Юдин",
    "Яковлев",
)
CLUB_NAMES = (
    "Аврора",
    "Атлас",
    "Берег",
    "Бор",
    "Бриз",
    "Буревестник",
    "Вектор",
    "Вершина",
    "Волна",
    "Восход",
    "Горизонт",
    "Гранит",
    "Дозор",
    "Дубрава",
    "Заря",
    "Зенитный",
    "Искра",
    "Каскад",
    "Кедр",
    "Клевер",
    "Комета",
    "Крепость",
    "Кристалл",
    "Ладога",
    "Локомотивный",
    "Луч",
    "Маяк",
    "Меридиан",
    "Мост",
    "Олимпийский",
    "Орбита",
    "Парус",
    "Платан",
    "Полюс",
    "Прибой",
    "Рассвет",
    "Речной",
    "Родник",
    "Рубеж",
    "Север",
    "Сокол",
    "Спутник",
    "Сталь",
    "Стрела",
    "Тайга",
    "Темп",
    "Тополь",
    "Факельный",
    "Шторм",
    "Янтарь",
)
KIT_PAIRS = (
    ("#743D42", "#F2E9D8"),
    ("#315B49", "#E2BF57"),
    ("#304B62", "#E8DECB"),
    ("#B06A36", "#2A2824"),
    ("#675478", "#E8DECB"),
)


@dataclass(frozen=True, kw_only=True)
class WorldOptions:
    key: str
    seed: int
    first_kickoff: datetime
    initial_budget: int = 0
    naming_policy: str = PERSON_NAMING_POLICY
    starter_policy: str = STARTER_POLICY
    club_naming_policy: str | None = CLUB_NAMING_POLICY

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", self.key):
            raise ValueError("World key must be a short lowercase slug")
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError("Seed must fit a nonnegative BIGINT")
        if type(self.initial_budget) is not int or not 0 <= self.initial_budget < 2**63:
            raise ValueError("Budget must be nonnegative integer units")
        if self.naming_policy not in {NAMING_POLICY, PERSON_NAMING_POLICY, "legacy-v1"}:
            raise ValueError("Invalid naming policy")
        if self.club_naming_policy not in (None, CLUB_NAMING_POLICY):
            raise ValueError("Invalid club naming policy")
        validate_kickoff(self.first_kickoff)
        if self.starter_policy not in (LEGACY_STARTER_POLICY, STARTER_POLICY):
            raise ValueError("Invalid starter skill policy")

    @property
    def fingerprint(self) -> str:
        data = asdict(self)
        data["first_kickoff"] = self.first_kickoff.isoformat()
        if self.naming_policy == "legacy-v1":
            data.pop("naming_policy")
        if self.starter_policy == LEGACY_STARTER_POLICY:
            data.pop("starter_policy")
        if self.club_naming_policy is None:
            data.pop("club_naming_policy")
        data["policy"] = WORLD_POLICY
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class StartingPlayer:
    id: UUID
    club_id: UUID
    slot: int
    name: str
    age: int
    position: str
    skills: tuple[int, ...]
    retirement: RetirementDecision


@dataclass(frozen=True)
class StartingClub:
    id: UUID
    league_id: UUID
    tier: int
    name: str
    tie_break_order: int
    kit_primary: str
    kit_secondary: str
    players: tuple[StartingPlayer, ...]


@dataclass(frozen=True)
class StartingLeague:
    id: UUID
    tier: int
    clubs: tuple[StartingClub, ...]


@dataclass(frozen=True)
class WorldPlan:
    options: WorldOptions
    league_set_id: UUID
    season_id: UUID
    leagues: tuple[StartingLeague, ...]

    @property
    def players(self) -> tuple[StartingPlayer, ...]:
        return tuple(p for league in self.leagues for club in league.clubs for p in club.players)


def stable_seed(seed: int, key: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}:{key}".encode()).digest()[:8], "big") % 2**63


def _skills(position: str, rng: Random, policy: str = STARTER_POLICY) -> tuple[int, ...]:
    return starter_skills(position, rng, policy)


def build_world(options: WorldOptions, *, existing_club_names: tuple[str, ...] = ()) -> WorldPlan:
    set_id = uuid5(WORLD_NAMESPACE, options.key)
    season_id = uuid5(set_id, "season:1")
    rng = Random(options.seed)
    names = rng.sample(list(product(FIRST_NAMES, LAST_NAMES)), 1000)
    international_names = international_catalog(options.seed)
    club_names = list(CLUB_NAMES if options.naming_policy == "legacy-v1" else INTERNATIONAL_CLUBS)
    rng.shuffle(club_names)
    # Consume the frozen 50-name shuffle above to preserve all gameplay draws.
    if options.club_naming_policy == CLUB_NAMING_POLICY:
        club_names = list(allocate_names(50, seed=options.key, existing=existing_club_names))
    used_names: set[str] = set()
    leagues: list[StartingLeague] = []
    for tier in range(1, 6):
        league_id = uuid5(set_id, f"league:{tier}")
        draw = list(range(1, 11))
        rng.shuffle(draw)
        clubs: list[StartingClub] = []
        for index in range(10):
            club_number = (tier - 1) * 10 + index
            club_id = uuid5(set_id, f"club:{club_number + 1}")
            players: list[StartingPlayer] = []
            for slot, (position, age) in enumerate(ROSTER, start=1):
                person_id = uuid5(club_id, f"person:{slot}")
                player_rng = Random(stable_seed(options.seed, str(person_id)))
                first, last = names[club_number * 20 + slot - 1]
                display_name = (
                    f"{first} {last}"
                    if options.naming_policy == "legacy-v1"
                    else international_names[club_number * 20 + slot - 1][1]
                )
                if options.naming_policy == PERSON_NAMING_POLICY:
                    for attempt in range(10000):
                        display_name = generated_name(person_id, attempt=attempt).name
                        if display_name not in used_names:
                            break
                    else:
                        raise ValueError("Starting world name pool exhausted")
                used_names.add(display_name)
                players.append(
                    StartingPlayer(
                        person_id,
                        club_id,
                        slot,
                        display_name,
                        age,
                        position,
                        _skills(position, player_rng, options.starter_policy),
                        player_last_season(age, player_rng.randrange(10000)),
                    )
                )
            primary, secondary = KIT_PAIRS[club_number % len(KIT_PAIRS)]
            clubs.append(
                StartingClub(
                    club_id,
                    league_id,
                    tier,
                    club_names[club_number],
                    draw[index],
                    primary,
                    secondary,
                    tuple(players),
                )
            )
        leagues.append(StartingLeague(league_id, tier, tuple(clubs)))
    return WorldPlan(options, set_id, season_id, tuple(leagues))
