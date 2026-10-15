"""Lineup validation, atomic saves and editor callbacks without a database."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from goaletto.application.game_actions import Actor
from goaletto.application.lineup import (
    LineupInput,
    LineupLocked,
    LineupStale,
    load_lineup,
    save_lineup,
)
from goaletto.bot import lineup_views as views
from goaletto.bot.lineup_handlers import build_callback_response, build_open_response
from goaletto.bot.result_views import compact_id
from goaletto.domain.lineup import (
    LineupInvalid,
    RosterPlayer,
    SlotCandidate,
    StarterPick,
    reassign_slots,
    validate_lineup,
)
from goaletto.domain.match_types import FieldSlot, MatchSkills, PlayerSnapshot, TeamSnapshot
from goaletto.domain.names import latin_name
from goaletto.infrastructure.models import (
    AcceptedCommand,
    AnalyticsManager,
    Club,
    ClubLineup,
    Fixture,
    GameActionReceipt,
    LineupSlot,
    Manager,
    OwnershipPeriod,
    Person,
    Player,
)

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)

XI_POSITIONS = ("GK", "DL", "DC", "DC", "DR", "ML", "MC", "MC", "MR", "FC", "FC")
BENCH_POSITIONS = ("GK", "DL", "DC", "DR", "MC", "ML", "MR", "FC", "FC")


class Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def all(self):
        return self.rows


def _extract_in_ids(statement):
    try:
        value = statement.whereclause.right.value
    except Exception:
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        return set(value)
    return None


class Recorder:
    def __init__(self):
        self.manager = Manager(
            id=uuid4(), telegram_id=123, reminders_enabled=True, bot_available=True
        )
        self.club = Club(
            id=uuid4(), league_id=uuid4(), name="Аврора", owner_telegram_id=123, budget=50000
        )
        self.ownership = OwnershipPeriod(
            id=uuid4(),
            operation_key="ownership:test",
            club_id=self.club.id,
            manager_id=self.manager.id,
            claimed_at=NOW - timedelta(days=3),
            last_game_action_at=NOW - timedelta(days=3),
            expires_at=NOW + timedelta(days=90),
            ended_at=None,
            end_reason=None,
        )
        self.actor = Actor(self.manager.id, 123, None, uuid4(), NOW, 17)
        self.lineup = ClubLineup(
            club_id=self.club.id,
            formation="4-4-2",
            version=5,
            tactics_version=2,
            tactics={"tactic": "normal", "mentality": "balanced"},
        )
        self.persons: dict[UUID, Person] = {}
        self.club_players: list[Player] = []
        self.extra_players: list[Player] = []
        for index, position in enumerate(XI_POSITIONS + BENCH_POSITIONS):
            person_id = uuid4()
            self.persons[person_id] = Person(id=person_id, name=f"Игрок {index + 1}")
            status = "last_season" if index == 6 else "active"
            injured = timedelta(hours=2) if index == 14 else None
            self.club_players.append(
                Player(
                    person_id=person_id,
                    club_id=self.club.id,
                    position=position,
                    alternative_positions=[],
                    status=status,
                    injured_until=NOW + injured if injured else None,
                )
            )
        for person in self.persons.values():
            person.career = "player"
        self.slots = [
            LineupSlot(
                club_id=self.club.id,
                slot=index + 1,
                person_id=player.person_id,
                position=XI_POSITIONS[index] if index < 11 else player.position,
            )
            for index, player in enumerate(self.club_players)
        ]
        starters = [p.person_id for p in self.club_players[:11]]
        self.latest_command = AcceptedCommand(
            id=uuid4(),
            operation_key="lineup:seed",
            telegram_update_id=7,
            effect_number=0,
            manager_id=self.manager.id,
            ownership_id=self.ownership.id,
            club_id=self.club.id,
            match_id=None,
            kind="lineup",
            payload_version=1,
            payload={
                "formation": "4-4-2",
                "starters": [
                    {"person_id": str(pid), "position": pos}
                    for pid, pos in zip(starters, XI_POSITIONS, strict=True)
                ],
                "bench": [str(p.person_id) for p in self.club_players[11:]],
                "captain_id": str(starters[5]),
                "tactic": "normal",
                "mentality": "balanced",
            },
            status="applied",
            target_tick_number=None,
            applied_tick_number=None,
            accepted_at=NOW - timedelta(hours=1),
            applied_at=NOW - timedelta(hours=1),
        )
        self.fixture = None
        self.commands: dict[str, AcceptedCommand] = {}
        self.receipts = {}
        self.statements = []
        self.added = []
        self.events = []

    def in_transaction(self):
        return True

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            return Rows()
        if getattr(statement, "is_delete", False):
            self.slots = []
            return Rows()
        if getattr(statement, "is_insert", False) or getattr(statement, "is_update", False):
            return Rows()
        entities = [d.get("entity") for d in statement.column_descriptions]
        if entities == [Player, Person]:
            return Rows([(p, self.persons[p.person_id]) for p in self.club_players])
        if len(entities) == 1 and entities[0] is Player:
            wanted = _extract_in_ids(statement)
            pool = self.club_players + self.extra_players
            rows = [(p,) for p in pool if wanted is None or p.person_id in wanted]
            return Rows(rows)
        return Rows()

    async def scalar(self, statement, params=None):
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            return True
        entity = statement.column_descriptions[0].get("entity")
        if entity is Manager:
            return self.manager
        if entity is AnalyticsManager:
            return None
        if entity is Club:
            return self.club
        if entity is OwnershipPeriod:
            return self.ownership
        if entity is ClubLineup:
            return self.lineup
        if entity is Fixture:
            return self.fixture
        if entity is AcceptedCommand:
            compiled = statement.compile().params
            if any("operation_key" in key for key in compiled):
                key = next(v for k, v in compiled.items() if "operation_key" in k)
                return self.commands.get(key)
            return self.latest_command
        return None

    async def scalars(self, statement):
        self.statements.append(statement)
        entity = statement.column_descriptions[0].get("entity")
        if entity is LineupSlot:
            return Rows(self.slots)
        return Rows()

    async def get(self, model, key):
        if model is Manager:
            return self.manager
        if model is GameActionReceipt:
            return self.receipts.get(key)
        return None

    def add(self, value):
        self.added.append(value)
        if isinstance(value, AcceptedCommand):
            self.commands[value.operation_key] = value
            self.latest_command = value
        if isinstance(value, GameActionReceipt):
            self.receipts[(value.operation_id, value.effect_number)] = value
        if isinstance(value, LineupSlot):
            self.slots.append(value)

    def add_all(self, values):
        for value in values:
            self.add(value)

    async def flush(self):
        pass

    @asynccontextmanager
    async def begin_nested(self):
        yield


@pytest.fixture(autouse=True)
def events(monkeypatch):
    async def record(session, event):
        session.events.append(event)
        return True

    monkeypatch.setattr("goaletto.application.game_actions.record_event", record)


def roster(recorder):
    return tuple(
        RosterPlayer(p.person_id, p.position, (), p.status, p.injured_until is not None)
        for p in recorder.club_players
    )


def xi(recorder):
    return tuple(
        StarterPick(p.person_id, XI_POSITIONS[i]) for i, p in enumerate(recorder.club_players[:11])
    )


def bench(recorder):
    return tuple(p.person_id for p in recorder.club_players[11:])


def lineup_input(recorder, **overrides):
    values = {
        "starters": xi(recorder),
        "bench": bench(recorder),
        "formation": "4-4-2",
        "captain_id": recorder.club_players[5].person_id,
        "tactic": "normal",
        "mentality": "balanced",
        "expected_version": recorder.lineup.version,
    }
    values.update(overrides)
    return LineupInput(**values)  # type: ignore[arg-type]


def test_domain_rejects_goalkeeper_count_duplicates_and_bad_formation():
    recorder = Recorder()
    base = {
        "roster": roster(recorder),
        "starters": xi(recorder),
        "bench": bench(recorder),
        "formation": "4-4-2",
        "captain_id": recorder.club_players[5].person_id,
        "tactic": "normal",
        "mentality": "balanced",
    }
    with pytest.raises(LineupInvalid) as error:
        validate_lineup(**{**base, "formation": "9-9-9"})
    assert error.value.code == "formation"
    with pytest.raises(LineupInvalid) as error:
        validate_lineup(**{**base, "starters": xi(recorder)[:10]})
    assert error.value.code == "count"
    keeper_out, outfield_in = recorder.club_players[0], recorder.club_players[12]
    misassigned = (
        StarterPick(outfield_in.person_id, "GK"),
        *xi(recorder)[1:],
    )
    with pytest.raises(LineupInvalid) as error:
        validate_lineup(
            **{
                **base,
                "starters": misassigned,
                "bench": (
                    keeper_out.person_id,
                    *(p for p in bench(recorder) if p != outfield_in.person_id),
                ),
            }
        )
    assert error.value.code == "goalkeeper_assignment"
    doubled = (*xi(recorder)[:10], xi(recorder)[0])
    with pytest.raises(LineupInvalid) as error:
        validate_lineup(**{**base, "starters": doubled})
    assert error.value.code == "duplicate"


def test_domain_checks_eligibility_and_captain():
    recorder = Recorder()
    base = {
        "roster": roster(recorder),
        "formation": "4-4-2",
        "captain_id": recorder.club_players[5].person_id,
        "tactic": "normal",
        "mentality": "balanced",
    }
    foreign = StarterPick(uuid4(), "FC")
    with pytest.raises(LineupInvalid) as error:
        validate_lineup(
            **{**base, "starters": (*xi(recorder)[1:], foreign), "bench": bench(recorder)}
        )
    assert error.value.code == "foreign"
    injured_pick = StarterPick(recorder.club_players[14].person_id, "MC")
    with pytest.raises(LineupInvalid) as error:
        validate_lineup(
            **{
                **base,
                "starters": (*xi(recorder)[:7], injured_pick, *xi(recorder)[8:]),
                "bench": tuple(
                    p for p in bench(recorder) if p != recorder.club_players[14].person_id
                )
                + (recorder.club_players[7].person_id,),
            }
        )
    assert error.value.code == "injured"
    with pytest.raises(LineupInvalid) as error:
        validate_lineup(
            **{
                **base,
                "starters": xi(recorder),
                "bench": bench(recorder),
                "captain_id": recorder.club_players[15].person_id,
            }
        )
    assert error.value.code == "captain"


def test_last_season_starter_plays_with_warning():
    recorder = Recorder()
    validated = validate_lineup(
        roster=roster(recorder),
        starters=xi(recorder),
        bench=bench(recorder),
        formation="4-4-2",
        captain_id=recorder.club_players[5].person_id,
        tactic="pressing",
        mentality="attack",
    )
    assert any(w.startswith("last_season_starter:") for w in validated.warnings)
    assert any(w.startswith("injured_on_bench:") for w in validated.warnings)


def test_reassign_keeps_eleven_for_new_formation():
    recorder = Recorder()
    candidates = tuple(
        SlotCandidate(p.person_id, p.position, ()) for p in recorder.club_players[:11]
    )
    picks = reassign_slots(candidates, "4-3-3")
    assert [p.slot_position for p in picks].count("GK") == 1
    assert {p.person_id for p in picks} == {p.person_id for p in recorder.club_players[:11]}
    validated = validate_lineup(
        roster=roster(recorder),
        starters=picks,
        bench=bench(recorder),
        formation="4-3-3",
        captain_id=recorder.club_players[5].person_id,
        tactic="normal",
        mentality="balanced",
    )
    assert validated.formation == "4-3-3"


async def saved(recorder=None):
    recorder = recorder or Recorder()
    result = await save_lineup(cast(AsyncSession, recorder), recorder.actor, lineup_input(recorder))
    return recorder, result


async def test_save_persists_atomic_command_and_receipt_without_analytics():
    recorder, result = await saved()
    assert recorder.lineup.version == 6 and recorder.lineup.tactics_version == 3
    assert recorder.lineup.formation == "4-4-2"
    command = next(a for a in recorder.added if isinstance(a, AcceptedCommand))
    assert result.command_id == command.id and command.kind == "lineup"
    assert command.match_id is None and command.status == "applied"
    assert set(command.payload) == {
        "formation",
        "starters",
        "bench",
        "captain_id",
        "tactic",
        "mentality",
    }
    slots = [a for a in recorder.added if isinstance(a, LineupSlot)]
    assert len(slots) == 20 and [s.slot for s in slots] == list(range(1, 21))
    assert sum(s.position == "GK" for s in slots[:11]) == 1
    receipts = [a for a in recorder.added if isinstance(a, GameActionReceipt)]
    assert len(receipts) == 1 and receipts[0].kind == "lineup_saved"
    assert recorder.events == []
    assert any(getattr(s, "is_delete", False) for s in recorder.statements)


async def test_retry_returns_same_command_without_duplicate_writes():
    recorder, first = await saved()
    before = len(recorder.added), len(recorder.statements)
    second = await save_lineup(cast(AsyncSession, recorder), recorder.actor, lineup_input(recorder))
    assert second.command_id == first.command_id and second.warnings == ()
    assert len(recorder.added) == before[0]
    assert recorder.lineup.version == 6


async def test_stale_revision_is_rejected():
    recorder = Recorder()
    with pytest.raises(LineupStale):
        await save_lineup(
            cast(AsyncSession, recorder), recorder.actor, lineup_input(recorder, expected_version=4)
        )
    assert recorder.commands == {}


async def test_lock_denies_save_but_keeps_saved_xi():
    recorder = Recorder()
    recorder.fixture = Fixture(
        id=uuid4(),
        season_id=uuid4(),
        league_id=uuid4(),
        home_club_id=recorder.club.id,
        away_club_id=uuid4(),
        round_number=3,
        scheduled_start_at=NOW + timedelta(minutes=1),
        lineup_lock_at=NOW,
        actual_start_at=None,
        actual_finish_at=None,
        status="scheduled",
    )
    with pytest.raises(LineupLocked):
        await save_lineup(cast(AsyncSession, recorder), recorder.actor, lineup_input(recorder))
    assert recorder.lineup.version == 5 and recorder.commands == {}


async def test_expired_ownership_cannot_edit():
    recorder = Recorder()
    recorder.ownership.expires_at = NOW - timedelta(seconds=1)
    with pytest.raises(ValueError, match="ownership"):
        await save_lineup(cast(AsyncSession, recorder), recorder.actor, lineup_input(recorder))
    assert recorder.commands == {}


async def test_stale_callback_cannot_restore_retired():
    recorder = Recorder()
    stale = lineup_input(recorder)
    starter = recorder.club_players[2]
    recorder.club_players = [p for p in recorder.club_players if p.person_id != starter.person_id]
    recorder.extra_players.append(
        Player(
            person_id=starter.person_id,
            club_id=None,
            position=starter.position,
            alternative_positions=[],
            status="retired",
            injured_until=None,
        )
    )
    with pytest.raises(LineupInvalid) as error:
        await save_lineup(cast(AsyncSession, recorder), recorder.actor, stale)
    assert error.value.code == "retired"
    assert recorder.commands == {}


async def test_saved_shape_builds_runtime_snapshot_without_engine_changes():
    recorder, _ = await saved()
    players = tuple(
        PlayerSnapshot(
            p.person_id,
            recorder.persons[p.person_id].name,
            p.position,
            MatchSkills(10, 10, 10, 10, 10, 10, 10, 10),
            alternatives=tuple(p.alternative_positions),
            available=p.injured_until is None,
            last_season=p.status == "last_season",
        )
        for p in recorder.club_players
    )
    by_id = {p.id: p for p in players}
    slots = sorted((a for a in recorder.added if isinstance(a, LineupSlot)), key=lambda s: s.slot)
    snapshot = TeamSnapshot(
        recorder.club.id,
        recorder.club.name,
        players,
        tuple(FieldSlot(s.person_id, s.position) for s in slots[:11]),
        tuple(s.person_id for s in slots[11:]),
    )
    assert len(snapshot.lineup) == 11 and len(snapshot.bench) == 9
    assert all(by_id[s.player_id].available for s in snapshot.lineup)


async def test_editor_callbacks_fit_limit_and_reject_stale_format():
    recorder = Recorder()
    data = await load_lineup(cast(AsyncSession, recorder), recorder.actor)
    data = replace(data, version=2**31 - 1)
    keyboards = [
        views.main_keyboard(data),
        views.formation_keyboard(data),
        views.tactic_keyboard(data),
        views.mentality_keyboard(data),
        views.starter_keyboard(data),
        views.captain_keyboard(data),
        views.swap_keyboard(data, 3),
    ]
    seen = 0
    for keyboard in keyboards:
        for row in keyboard.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64
                if button.callback_data.startswith("ln:"):
                    views.parse_callback(button.callback_data)
                seen += 1
    assert seen > 20
    with pytest.raises(ValueError):
        views.parse_callback("ln:f:9-9-9:5")
    with pytest.raises(ValueError):
        views.parse_callback("preview:squad")
    text = views.render_main(data)
    assert "Avrora" in text and "4-4-2" in text


async def test_editor_open_change_and_retry_share_business_effect():
    recorder = Recorder()
    session = cast(AsyncSession, recorder)
    text, keyboard = await build_open_response(session, 123, "cb-open-1", NOW)
    assert "Avrora" in text and keyboard.inline_keyboard
    data = await load_lineup(session, recorder.actor)
    text, _ = await build_callback_response(
        session,
        123,
        "cb-f-1",
        f"ln:f:4-3-3:{compact_id(data.ownership_id)}:{data.version}",
        NOW,
        update_id=41,
    )
    assert "4-3-3" in text and recorder.lineup.version == 6
    commands = [a for a in recorder.added if isinstance(a, AcceptedCommand)]
    assert len(commands) == 1
    added = len(recorder.added)
    text, _ = await build_callback_response(
        session,
        123,
        "cb-f-1",
        f"ln:f:4-3-3:{compact_id(data.ownership_id)}:{data.version}",
        NOW,
        update_id=41,
    )
    assert "устарел" in text
    assert len(recorder.added) == added
    assert len([a for a in recorder.added if isinstance(a, AcceptedCommand)]) == 1


async def test_editor_swap_and_captain_callbacks_save_atomically():
    recorder = Recorder()
    session = cast(AsyncSession, recorder)
    data = await load_lineup(session, recorder.actor)
    outgoing = data.starters[5]
    assert data.captain_id == outgoing.person_id
    incoming = data.bench[1]
    text, _ = await build_callback_response(
        session,
        123,
        "cb-sw-1",
        f"ln:s:6:{compact_id(incoming.person_id)}:{compact_id(data.ownership_id)}:{data.version}",
        NOW,
        update_id=42,
    )
    assert latin_name(incoming.name) in text.split("Запас:")[0]
    current = await load_lineup(session, recorder.actor)
    assert outgoing.person_id not in {m.person_id for m in current.starters}
    fresh = await load_lineup(session, recorder.actor)
    assert fresh.captain_id == incoming.person_id
    captain = fresh.starters[3]
    text, _ = await build_callback_response(
        session,
        123,
        "cb-cap-1",
        f"ln:cap:{compact_id(captain.person_id)}:{compact_id(fresh.ownership_id)}:{fresh.version}",
        NOW,
        update_id=43,
    )
    assert "Капитан:" in text
    assert (await load_lineup(session, recorder.actor)).captain_id == captain.person_id


async def test_callback_from_previous_ownership_cannot_change_new_club():
    recorder = Recorder()
    text, _ = await build_callback_response(
        cast(AsyncSession, recorder),
        123,
        "old-ownership",
        f"ln:t:pressing:{compact_id(uuid4())}:{recorder.lineup.version}",
        NOW,
        update_id=44,
    )
    assert "прежнего владения" in text
    assert recorder.commands == {} and recorder.lineup.version == 5


async def test_deadline_is_rechecked_after_player_lock_wait():
    recorder = Recorder()
    deadline = NOW + timedelta(seconds=1)
    times = iter((NOW, deadline))
    original_scalar = recorder.scalar

    async def scalar(statement, params=None):
        entity = (
            statement.column_descriptions[0].get("entity")
            if not isinstance(statement, TextClause)
            else None
        )
        if entity is Fixture:
            at = statement.compile().params.get("lineup_lock_at_1")
            return Fixture(id=uuid4()) if at >= deadline else None
        return await original_scalar(statement, params)

    recorder.scalar = scalar
    with pytest.raises(LineupLocked, match="waiting"):
        await save_lineup(
            cast(AsyncSession, recorder),
            recorder.actor,
            lineup_input(recorder),
            clock=lambda: next(times),
        )
    assert recorder.commands == {} and recorder.lineup.version == 5


async def test_missing_real_update_id_fails_before_mutation():
    recorder = Recorder()
    with pytest.raises(ValueError, match="update_id"):
        await save_lineup(
            cast(AsyncSession, recorder),
            replace(recorder.actor, update_id=None),
            lineup_input(recorder),
        )
    assert recorder.commands == {} and recorder.statements == []


@pytest.fixture(autouse=True)
def full_energy_projection(monkeypatch):
    # These recording sessions test navigation/ownership; recovery has PG coverage.
    from unittest.mock import AsyncMock

    from goaletto.application.recovery import EnergyStatus

    monkeypatch.setattr(
        "goaletto.application.recovery.energy_status",
        AsyncMock(return_value=EnergyStatus(10000, None)),
    )
