"""Authorization and receipt tests with a recording session. No database."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from goaletto.application.club_view import (
    actor_for_callback,
    get_owned_club,
    get_player_card,
    get_squad_page,
)
from goaletto.application.game_actions import Actor, PermissionDenied
from goaletto.domain.ownership import ownership_deadline
from goaletto.infrastructure.models import (
    AnalyticsManager,
    AppearanceIdentity,
    Club,
    GameActionReceipt,
    League,
    LineupSlot,
    Manager,
    OwnershipPeriod,
    Person,
    Player,
)

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
FOREIGN_CLUB = uuid4()


def skills(**over: int) -> dict[str, int]:
    base = {
        "goalkeeping": 1,
        "defending": 9,
        "playmaking": 12,
        "winger": 12,
        "attacking": 9,
        "set_pieces": 10,
        "stamina": 11,
        "pace": 10,
        "leadership": 10,
        "loyalty": 10,
        "form": 6,
        "experience": 0,
        "morale": 50,
    }
    base.update(over)
    return base


class Rows:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class Recorder:
    def __init__(self) -> None:
        self.manager = Manager(
            id=uuid4(), telegram_id=123, reminders_enabled=True, bot_available=True
        )
        self.league_id = uuid4()
        self.club = Club(
            id=uuid4(),
            league_id=self.league_id,
            name="<Аврора>",
            owner_telegram_id=123,
            budget=500000,
            kit_primary="#743D42",
            kit_secondary="#F2E9D8",
            kit_version=1,
            stadium_capacity=5000,
            supporters=2500,
        )
        self.league = League(id=self.league_id, league_set_id=uuid4(), tier=5)
        claimed = NOW - timedelta(days=10)
        last = NOW - timedelta(days=1)
        self.ownership = OwnershipPeriod(
            id=uuid4(),
            operation_key="op",
            manager_id=self.manager.id,
            club_id=self.club.id,
            claimed_at=claimed,
            last_game_action_at=last,
            expires_at=ownership_deadline(last),
            ended_at=None,
            end_reason=None,
        )
        self.actor = Actor(self.manager.id, 123, None, uuid4(), NOW)
        self.persons: dict[UUID, Person] = {}
        self.players: dict[UUID, Player] = {}
        self.slots: list[LineupSlot] = []
        self.identities: dict[str, AppearanceIdentity] = {}
        self.receipts: dict[tuple[UUID, int], GameActionReceipt] = {}
        self.statements: list[object] = []
        self.added: list[object] = []
        self.events: list[object] = []
        self._seed_roster()

    def _person(
        self,
        name: str,
        age: int,
        status: str,
        club_id: UUID | None,
        slot: int | None,
        position: str = "MC",
    ) -> UUID:
        pid = uuid4()
        identity_id = f"face-{len(self.persons):04d}"
        self.identities[identity_id] = AppearanceIdentity(
            id=identity_id, origin_bank="players", catalog_version=2
        )
        self.persons[pid] = Person(
            id=pid,
            creation_key=f"key-{pid}",
            name=name,
            age=age,
            career="player",
            appearance_identity_id=identity_id,
            appearance_variant_id=f"{identity_id}-v",
            home_league_set_id=None,
            age_applied_for_season_id=None,
        )
        self.players[pid] = Player(
            person_id=pid,
            club_id=club_id,
            position=position,
            alternative_positions=[],
            **skills(),
            status=status,
            last_season_id=uuid4() if status != "active" else None,
            retired_after_season_id=None,
            injured_until=None,
        )
        if slot is not None and club_id is not None:
            self.slots.append(
                LineupSlot(club_id=club_id, slot=slot, person_id=pid, position=position)
            )
        return pid

    def _seed_roster(self) -> None:
        for i in range(1, 6):
            self._person(f"Игрок {i}", 20 + i, "active", self.club.id, i)
        self.veteran = self._person("Ветеран", 34, "last_season", self.club.id, 6)
        self.retired = self._person("Пенсионер", 38, "retired", None, None, "FC")
        self.foreign = self._person("Чужой", 25, "active", FOREIGN_CLUB, None, "FC")

    def in_transaction(self) -> bool:
        return True

    async def execute(self, statement: object, params: object = None) -> None:
        self.statements.append(statement)

    async def scalar(self, statement: object, params: object = None) -> object:
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            return True
        entity = statement.column_descriptions[0].get("entity")  # type: ignore[union-attr]
        if entity is Manager:
            return self.manager
        if entity is AnalyticsManager:
            return None
        if entity is OwnershipPeriod:
            return self.ownership
        if entity is Club:
            return self.club
        if entity is League:
            return self.league
        if entity is Player:
            for player in self.players.values():
                params_map = statement.compile().params  # type: ignore[union-attr]
                wanted = params_map.get("person_id_1")
                if wanted is not None and str(player.person_id) == str(wanted):
                    return player
            return None
        if entity is AppearanceIdentity:
            params_map = statement.compile().params  # type: ignore[union-attr]
            for key, value in params_map.items():
                if "appearance" in key or key.endswith("_1"):
                    hit = self.identities.get(str(value))
                    if hit is not None:
                        return hit
            return next(iter(self.identities.values()))
        return None

    async def scalars(self, statement: object) -> Rows:
        self.statements.append(statement)
        entity = statement.column_descriptions[0].get("entity")  # type: ignore[union-attr]
        if entity is LineupSlot:
            return Rows(list(self.slots))
        if entity is Player:
            return Rows([p for p in self.players.values() if p.club_id == self.club.id])
        if entity is Person:
            return Rows(list(self.persons.values()))
        return Rows([])

    async def get(self, model: object, key: object) -> object:
        if model is Manager:
            return self.manager
        if model is Person:
            return self.persons.get(cast(UUID, key))
        if model is GameActionReceipt:
            return self.receipts.get(cast(tuple[UUID, int], key))
        return None

    def add(self, value: object) -> None:
        self.added.append(value)
        if isinstance(value, GameActionReceipt):
            self.receipts[(value.operation_id, value.effect_number)] = value

    async def flush(self) -> None:
        pass

    @asynccontextmanager
    async def begin_nested(self) -> object:
        yield


@pytest.fixture(autouse=True)
def events(monkeypatch: pytest.MonkeyPatch) -> None:
    async def record(session: Recorder, event: object) -> bool:
        session.events.append(event)
        return True

    monkeypatch.setattr("goaletto.application.game_actions.record_event", record)


def session(recorder: Recorder) -> AsyncSession:
    return cast(AsyncSession, recorder)


async def test_club_view_records_receipt_without_analytics() -> None:
    recorder = Recorder()
    before = recorder.ownership.expires_at
    card = await get_owned_club(session(recorder), recorder.actor)
    assert card.name == "<Аврора>" and card.tier == 5 and card.budget == 500000
    assert len(recorder.receipts) == 1  # business dedupe works with analytics opt-out
    assert recorder.events == []
    assert recorder.ownership.expires_at >= before
    # Retry of the same operation never duplicates the receipt or lease bump.
    again = await get_owned_club(session(recorder), recorder.actor)
    assert again == card and len(recorder.receipts) == 1


async def test_former_owner_is_rechecked_on_every_view() -> None:
    recorder = Recorder()
    recorder.ownership = None  # type: ignore[assignment]
    with pytest.raises(PermissionDenied):
        await get_owned_club(session(recorder), recorder.actor)
    with pytest.raises(PermissionDenied):
        await get_squad_page(session(recorder), recorder.actor, 1)
    recorder.ownership = OwnershipPeriod(
        id=uuid4(),
        operation_key="op",
        manager_id=recorder.manager.id,
        club_id=recorder.club.id,
        claimed_at=NOW - timedelta(days=10),
        last_game_action_at=NOW - timedelta(days=1),
        expires_at=ownership_deadline(NOW - timedelta(days=1)),
        ended_at=None,
        end_reason=None,
    )
    recorder.club.owner_telegram_id = 999  # projection no longer matches the actor
    with pytest.raises(PermissionDenied):
        await get_owned_club(session(recorder), recorder.actor)


async def test_squad_pagination_is_stable_and_hides_retired() -> None:
    recorder = Recorder()
    page1 = await get_squad_page(
        session(recorder), Actor(recorder.manager.id, 123, None, uuid4(), NOW), 1
    )
    assert page1.total_members == 6 and page1.total_pages == 2
    assert [m.slot for m in page1.members] == [1, 2, 3, 4, 5]
    page2 = await get_squad_page(
        session(recorder), Actor(recorder.manager.id, 123, None, uuid4(), NOW), 2
    )
    assert len(page2.members) == 1 and page2.members[0].status == "last_season"
    assert all(m.name != "Пенсионер" for m in (*page1.members, *page2.members))
    with pytest.raises(ValueError):
        await get_squad_page(
            session(recorder), Actor(recorder.manager.id, 123, None, uuid4(), NOW), 3
        )


async def test_player_cards_last_season_retired_and_foreign() -> None:
    recorder = Recorder()

    def actor() -> Actor:
        return Actor(recorder.manager.id, 123, None, uuid4(), NOW)

    veteran = await get_player_card(session(recorder), actor(), recorder.veteran)
    assert veteran.status == "last_season" and veteran.age == 34
    assert veteran.appearance_identity_id.startswith("face-")
    assert veteran.kit_primary == "#743D42"  # active kits follow the current club

    retired = await get_player_card(session(recorder), actor(), recorder.retired)
    assert retired.status == "retired" and retired.club_id is None
    assert retired.club_name is None  # history view, no game actions attached

    with pytest.raises(PermissionDenied):
        await get_player_card(session(recorder), actor(), recorder.foreign)
    with pytest.raises(PermissionDenied):
        await get_player_card(session(recorder), actor(), uuid4())


async def test_callback_actor_needs_start_and_valid_identity() -> None:
    recorder = Recorder()
    actor = await actor_for_callback(session(recorder), telegram_id=123, callback_id="cb-1", at=NOW)
    assert actor.telegram_id == 123 and actor.manager_id == recorder.manager.id
    with pytest.raises(PermissionDenied):
        await actor_for_callback(session(recorder), telegram_id=0, callback_id="cb-1", at=NOW)
    with pytest.raises(PermissionDenied):
        await actor_for_callback(session(recorder), telegram_id=123, callback_id="", at=NOW)
