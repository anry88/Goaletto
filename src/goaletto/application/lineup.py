"""Pre-match lineup use cases. Caller owns one PostgreSQL transaction."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.domain.lineup import (
    LineupInvalid,
    RosterPlayer,
    StarterPick,
    ValidatedLineup,
    validate_lineup,
)
from goaletto.infrastructure.models import (
    AcceptedCommand,
    Club,
    ClubLineup,
    Fixture,
    LineupSlot,
    OwnershipPeriod,
    Person,
    Player,
)

LINEUP_PAYLOAD_VERSION = 1


class LineupLocked(ValueError):
    """The imminent fixture passed its lineup lock; the saved XI stays in force."""


class LineupStale(ValueError):
    """The editor revision is outdated; re-render instead of applying."""


@dataclass(frozen=True)
class LineupInput:
    starters: tuple[StarterPick, ...]
    bench: tuple[UUID, ...]
    formation: str
    captain_id: UUID
    tactic: str
    mentality: str
    expected_version: int


@dataclass(frozen=True)
class SquadMember:
    person_id: UUID
    name: str
    position: str
    alternatives: tuple[str, ...]
    slot_position: str
    last_season: bool
    injured: bool


@dataclass(frozen=True)
class LineupData:
    club_id: UUID
    club_name: str
    formation: str
    starters: tuple[SquadMember, ...]
    bench: tuple[SquadMember, ...]
    captain_id: UUID | None
    tactic: str
    mentality: str
    version: int
    locked: bool
    lock_at: datetime | None
    ownership_id: UUID
    reserves: tuple[SquadMember, ...] = ()


@dataclass(frozen=True)
class SaveResult:
    command_id: UUID
    version: int
    warnings: tuple[str, ...]


def lineup_operation_key(actor: Actor) -> str:
    return f"lineup:v1:{actor.operation_id}"


def command_payload(validated: ValidatedLineup) -> dict[str, object]:
    return {
        "formation": validated.formation,
        "starters": [
            {"person_id": str(p.person_id), "position": p.slot_position} for p in validated.starters
        ],
        "bench": [str(p) for p in validated.bench],
        "captain_id": str(validated.captain_id),
        "tactic": validated.tactic.value,
        "mentality": validated.mentality.value,
    }


def _raw_payload(lineup_input: LineupInput) -> dict[str, object]:
    """Canonical pre-validation payload for retry comparison."""
    return {
        "formation": lineup_input.formation,
        "starters": [
            {"person_id": str(p.person_id), "position": p.slot_position}
            for p in lineup_input.starters
        ],
        "bench": [str(p) for p in lineup_input.bench],
        "captain_id": str(lineup_input.captain_id),
        "tactic": lineup_input.tactic,
        "mentality": lineup_input.mentality,
    }


def _update_number(actor: Actor) -> int:
    if actor.update_id is None:
        raise ValueError("A real Telegram update_id is required to save a lineup")
    return actor.update_id


async def _locked_fixture(session: AsyncSession, club_id: UUID, at: datetime) -> Fixture | None:
    result = await session.scalar(
        select(Fixture)
        .where(
            ((Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id)),
            Fixture.status.in_(("scheduled", "live", "paused")),
            Fixture.lineup_lock_at <= at,
            Fixture.actual_finish_at.is_(None),
        )
        .order_by(Fixture.lineup_lock_at)
        .execution_options(populate_existing=True)
    )
    return result


async def _next_fixture(session: AsyncSession, club_id: UUID) -> Fixture | None:
    result = await session.scalar(
        select(Fixture)
        .where(
            ((Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id)),
            Fixture.status == "scheduled",
            Fixture.actual_finish_at.is_(None),
        )
        .order_by(Fixture.scheduled_start_at)
    )
    return result


async def _club_squad(
    session: AsyncSession, club_id: UUID, at: datetime, *, lock: bool = False
) -> tuple[list[Player], dict[UUID, Person]]:
    statement = (
        select(Player, Person)
        .join(Person, Person.id == Player.person_id)
        .where(Player.club_id == club_id)
        .order_by(Player.person_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update(of=(Player, Person))
    rows = (await session.execute(statement)).all()
    return [p for p, _ in rows], {person.id: person for _, person in rows}


def _roster(
    players: list[Player], persons: dict[UUID, Person], at: datetime
) -> tuple[RosterPlayer, ...]:
    roster: list[RosterPlayer] = []
    for player in players:
        person = persons.get(player.person_id)
        if person is None or person.career != "player" or player.status == "retired":
            continue
        roster.append(
            RosterPlayer(
                player.person_id,
                player.position,
                tuple(player.alternative_positions),
                player.status,
                player.injured_until is not None and player.injured_until > at,
            )
        )
    return tuple(roster)


async def _latest_captain(session: AsyncSession, club_id: UUID) -> UUID | None:
    row = await session.scalar(
        select(AcceptedCommand)
        .where(AcceptedCommand.club_id == club_id, AcceptedCommand.kind == "lineup")
        .where(AcceptedCommand.status == "applied")
        .order_by(
            AcceptedCommand.accepted_at.desc(),
            AcceptedCommand.telegram_update_id.desc(),
            AcceptedCommand.id,
        )
        .limit(1)
    )
    if row is None:
        return None
    payload = row.payload
    if row.payload_version != LINEUP_PAYLOAD_VERSION or not isinstance(payload, dict):
        return None
    try:
        return UUID(str(payload.get("captain_id")))
    except (ValueError, TypeError, AttributeError):
        return None


async def load_lineup(session: AsyncSession, actor: Actor) -> LineupData:
    """Read the current editor state. No analytics side effects; open_lineup extends."""
    await allocation_lock(session)
    ownership, club = await owned_club(session, actor)
    return await _assemble(session, actor, ownership, club)


async def open_lineup(session: AsyncSession, actor: Actor) -> LineupData:
    await allocation_lock(session)
    ownership, club = await owned_club(session, actor)
    data = await _assemble(session, actor, ownership, club)
    await user_effect(
        session,
        actor,
        "lineup_opened",
        Surface.SQUAD,
        ownership=ownership,
        club_id=club.id,
    )
    return data


async def _assemble(
    session: AsyncSession, actor: Actor, ownership: OwnershipPeriod, club: Club
) -> LineupData:
    lineup = await session.scalar(
        select(ClubLineup).where(ClubLineup.club_id == club.id).with_for_update()
    )
    if lineup is None:
        raise LineupInvalid("coverage", "Club has no saved lineup")
    slots = (
        await session.scalars(
            select(LineupSlot).where(LineupSlot.club_id == club.id).order_by(LineupSlot.slot)
        )
    ).all()
    players, persons = await _club_squad(session, club.id, actor.at)
    by_player = {p.person_id: p for p in players}
    starters: list[SquadMember] = []
    bench: list[SquadMember] = []
    for slot in slots:
        player = by_player.get(slot.person_id)
        person = persons.get(slot.person_id)
        if player is None or person is None:
            continue
        member = SquadMember(
            slot.person_id,
            person.name,
            player.position,
            tuple(player.alternative_positions),
            slot.position,
            player.status == "last_season",
            player.injured_until is not None and player.injured_until > actor.at,
        )
        if slot.slot <= 11:
            starters.append(member)
        else:
            bench.append(member)
    selected = {m.person_id for m in (*starters, *bench)}
    reserves = tuple(
        SquadMember(
            p.person_id,
            persons[p.person_id].name,
            p.position,
            tuple(p.alternative_positions),
            p.position,
            p.status == "last_season",
            p.injured_until is not None and p.injured_until > actor.at,
        )
        for p in players
        if p.person_id not in selected
    )
    tactics = lineup.tactics if isinstance(lineup.tactics, dict) else {}
    captain = await _latest_captain(session, club.id)
    if captain is not None and captain not in {m.person_id for m in starters}:
        captain = None
    upcoming = await _locked_fixture(session, club.id, actor.at) or await _next_fixture(
        session, club.id
    )
    lock_at = upcoming.lineup_lock_at if upcoming is not None else None
    locked = lock_at is not None and lock_at <= actor.at
    return LineupData(
        club_id=club.id,
        club_name=club.name,
        formation=lineup.formation,
        starters=tuple(starters),
        bench=tuple(bench),
        captain_id=captain,
        tactic=str(tactics.get("tactic", "normal")),
        mentality=str(tactics.get("mentality", "balanced")),
        version=lineup.version,
        locked=locked,
        lock_at=lock_at,
        ownership_id=ownership.id,
        reserves=reserves,
    )


async def save_lineup(
    session: AsyncSession,
    actor: Actor,
    lineup_input: LineupInput,
    *,
    clock: Callable[[], datetime] | None = None,
) -> SaveResult:
    """Validate and atomically persist XI/bench/formation/captain/tactics.

    Only the current owner edits. The imminent-fixture deadline and fresh player
    statuses are rechecked under row locks. Retries with the same operation key
    return the original command without duplicate writes.
    """
    if not session.in_transaction():
        raise ValueError("Lineup save needs a caller-owned transaction")
    if type(lineup_input.expected_version) is not int or lineup_input.expected_version < 1:
        raise LineupInvalid("stale", "Editor revision is required")
    update_id = _update_number(actor)
    await allocation_lock(session)
    ownership, club = await owned_club(session, actor)
    lineup = await session.scalar(
        select(ClubLineup).where(ClubLineup.club_id == club.id).with_for_update()
    )
    if lineup is None:
        raise LineupInvalid("coverage", "Club has no saved lineup")
    key = lineup_operation_key(actor)
    previous = await session.scalar(
        select(AcceptedCommand).where(AcceptedCommand.operation_key == key)
    )
    if previous is not None:
        if (
            previous.manager_id != actor.manager_id
            or previous.kind != "lineup"
            or previous.ownership_id != ownership.id
            or previous.club_id != club.id
        ):
            raise PermissionDenied("Stale or conflicting lineup identity")
        if previous.payload != _raw_payload(lineup_input):
            raise PermissionDenied("Stale or conflicting lineup identity")
        return SaveResult(previous.id, lineup.version, ())
    if lineup.version != lineup_input.expected_version:
        raise LineupStale("Lineup changed; reload the editor")
    checked_at = max(actor.at, clock() if clock is not None else datetime.now(UTC))
    locked = await _locked_fixture(session, club.id, checked_at)
    if locked is not None:
        raise LineupLocked("Lineup lock passed; the saved XI stays in force")

    players, persons = await _club_squad(session, club.id, checked_at, lock=True)
    selected = [p.person_id for p in lineup_input.starters] + list(lineup_input.bench)
    selected_rows = (
        await session.execute(
            select(Player)
            .where(Player.person_id.in_(selected))
            .order_by(Player.person_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    for (player,) in selected_rows:
        if player.status == "retired":
            raise LineupInvalid("retired", "A retired player cannot be selected")
        if player.club_id != club.id:
            raise LineupInvalid("foreign", "XI and bench must be current club players")
    checked_at = max(checked_at, clock() if clock is not None else datetime.now(UTC))
    if await _locked_fixture(session, club.id, checked_at) is not None:
        raise LineupLocked("Lineup lock passed while waiting for player locks")
    if ownership.expires_at <= checked_at:
        raise PermissionDenied("Ownership expired while saving lineup")
    roster = _roster(players, persons, checked_at)
    validated = validate_lineup(
        roster=roster,
        starters=lineup_input.starters,
        bench=lineup_input.bench,
        formation=lineup_input.formation,
        captain_id=lineup_input.captain_id,
        tactic=lineup_input.tactic,
        mentality=lineup_input.mentality,
    )
    payload = command_payload(validated)

    lineup.formation = validated.formation
    lineup.tactics = {"tactic": validated.tactic.value, "mentality": validated.mentality.value}
    lineup.version += 1
    lineup.tactics_version += 1
    await session.execute(delete(LineupSlot).where(LineupSlot.club_id == club.id))
    by_id = {p.person_id: p for p in players}
    session.add_all(
        [
            LineupSlot(
                club_id=club.id,
                slot=index,
                person_id=pick.person_id,
                position=pick.slot_position,
            )
            for index, pick in enumerate(validated.starters, start=1)
        ]
        + [
            LineupSlot(
                club_id=club.id,
                slot=index,
                person_id=person_id,
                position=by_id[person_id].position,
            )
            for index, person_id in enumerate(validated.bench, start=12)
        ]
    )
    command = AcceptedCommand(
        id=uuid5(actor.operation_id, "lineup:v1"),
        operation_key=key,
        telegram_update_id=update_id,
        effect_number=0,
        manager_id=actor.manager_id,
        ownership_id=ownership.id,
        club_id=club.id,
        match_id=None,
        kind="lineup",
        payload_version=LINEUP_PAYLOAD_VERSION,
        payload=dict(payload),
        status="applied",
        target_tick_number=None,
        applied_tick_number=None,
        accepted_at=actor.at,
        applied_at=actor.at,
    )
    session.add(command)
    await session.flush()
    await user_effect(
        session,
        actor,
        "lineup_saved",
        Surface.SQUAD,
        ownership=ownership,
        club_id=club.id,
        command_id=command.id,
    )
    return SaveResult(command.id, lineup.version, validated.warnings)


async def swap_and_save(
    session: AsyncSession,
    actor: Actor,
    *,
    outgoing_id: UUID,
    incoming_id: UUID,
    slot_position: str,
    expected_version: int,
) -> SaveResult:
    """One-tap editor swap: exchange a starter with a bench player atomically."""
    data = await load_lineup(session, actor)
    if data.locked:
        raise LineupLocked("Lineup lock passed; the saved XI stays in force")
    starters = tuple(
        StarterPick(
            incoming_id if m.person_id == outgoing_id else m.person_id,
            slot_position if m.person_id == outgoing_id else m.slot_position,
        )
        for m in data.starters
    )
    bench_ids = tuple(m.person_id for m in data.bench)
    if outgoing_id not in {m.person_id for m in data.starters} or incoming_id not in set(bench_ids):
        raise LineupInvalid("foreign", "Swap needs a starter and a bench player")
    bench = tuple(outgoing_id if p == incoming_id else p for p in bench_ids)
    captain = data.captain_id
    if captain is None or captain == outgoing_id:
        captain = incoming_id
    return await save_lineup(
        session,
        actor,
        LineupInput(
            starters,
            bench,
            data.formation,
            captain,
            data.tactic,
            data.mentality,
            expected_version,
        ),
    )
