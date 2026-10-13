"""Durable live admission. Fixture -> Match -> Ownership -> Club locks, no network."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.application.match_commentary import load_commentary
from goaletto.application.match_runtime import domain_command, due_for
from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_codec import restore_state
from goaletto.domain.match_commands import apply_commands
from goaletto.domain.match_types import Instruction, MatchState, Mentality, Side, Tactic
from goaletto.infrastructure.models import (
    AcceptedCommand,
    Club,
    Fixture,
    Match,
    OwnershipPeriod,
    Person,
    Player,
)


class CommandUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class CommandReceipt:
    id: UUID
    kind: str
    status: str
    tick: int


@dataclass(frozen=True)
class LiveScreen:
    ownership_id: UUID
    club_id: UUID
    club_name: str
    fixture_id: UUID
    match_id: UUID | None
    state: MatchState | None
    projected: MatchState | None
    side: Side
    target: int | None
    halftime: bool
    deadline: datetime | None
    open: bool
    recent: tuple[CommandReceipt, ...]
    commentary: tuple[str, ...] = ()


async def _context(
    session: AsyncSession, actor: Actor, match_id: UUID | None
) -> tuple[OwnershipPeriod, Club, Fixture, Match | None]:
    if not session.in_transaction():
        raise ValueError("Live controls require caller-owned transaction")
    await allocation_lock(session)
    peek = await session.scalar(
        select(OwnershipPeriod).where(
            OwnershipPeriod.manager_id == actor.manager_id, OwnershipPeriod.ended_at.is_(None)
        )
    )
    if peek is None or peek.expires_at <= actor.at:
        raise PermissionDenied("No current ownership")
    query = select(Fixture).where(
        or_(Fixture.home_club_id == peek.club_id, Fixture.away_club_id == peek.club_id)
    )
    if match_id is not None:
        query = query.join(Match, Match.fixture_id == Fixture.id).where(Match.id == match_id)
    else:
        query = (
            query.where(Fixture.status.in_(("live", "paused", "scheduled")))
            .order_by(Fixture.status == "scheduled", Fixture.scheduled_start_at, Fixture.id)
            .limit(1)
        )
    fixture = await session.scalar(
        query.with_for_update(of=Fixture).execution_options(populate_existing=True)
    )
    if fixture is None:
        raise CommandUnavailable("No available fixture for this club")
    match = await session.scalar(
        select(Match)
        .where(Match.fixture_id == fixture.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    ownership, club = await owned_club(session, actor)
    if club.id != peek.club_id:
        raise PermissionDenied("Ownership changed while waiting")
    return ownership, club, fixture, match


def _window(
    match: Match, fixture: Fixture, state: MatchState, at: datetime
) -> tuple[int | None, bool, datetime | None, bool]:
    if fixture.status != "live" or state.phase not in {
        MatchPhase.FIRST_HALF,
        MatchPhase.HALF_TIME,
        MatchPhase.SECOND_HALF,
    }:
        return None, False, None, False
    halftime = state.phase == MatchPhase.HALF_TIME
    target = 16 if halftime else state.tick + 1
    deadline = due_for(match, fixture, None if halftime else target)
    return target, halftime, deadline, at < deadline


async def _pending(
    session: AsyncSession, match: Match, ownership: OwnershipPeriod
) -> list[AcceptedCommand]:
    return list(
        (
            await session.scalars(
                select(AcceptedCommand)
                .where(
                    AcceptedCommand.match_id == match.id,
                    AcceptedCommand.ownership_id == ownership.id,
                    AcceptedCommand.status == "accepted",
                )
                .order_by(
                    AcceptedCommand.accepted_at,
                    AcceptedCommand.telegram_update_id,
                    AcceptedCommand.effect_number,
                    AcceptedCommand.id,
                )
            )
        ).all()
    )


def _project(
    state: MatchState, rows: list[AcceptedCommand], target: int, halftime: bool, deadline: datetime
) -> MatchState:
    commands = tuple(
        domain_command(row, state, halftime=halftime)
        for row in rows
        if row.target_tick_number == target and row.accepted_at < deadline
    )
    projected, _, _ = apply_commands(state, commands, tick=target, halftime=halftime)
    return projected


async def load_live_screen(
    session: AsyncSession,
    actor: Actor,
    *,
    match_id: UUID | None = None,
    clock: Callable[[], datetime] | None = None,
    record: bool = True,
) -> LiveScreen:
    ownership, club, fixture, match = await _context(session, actor, match_id)
    at = max(actor.at, clock() if clock else datetime.now(UTC))
    if ownership.expires_at <= at:
        raise PermissionDenied("Ownership expired while waiting")
    state = restore_state(match.state) if match else None
    side = Side.HOME if fixture.home_club_id == club.id else Side.AWAY
    target, halftime, deadline, opened = (
        _window(match, fixture, state, at) if match and state else (None, False, None, False)
    )
    projected = state
    if match and state and target is not None and deadline is not None:
        projected = _project(
            state, await _pending(session, match, ownership), target, halftime, deadline
        )
    recent: tuple[CommandReceipt, ...] = ()
    if match:
        rows = (
            await session.scalars(
                select(AcceptedCommand)
                .where(
                    AcceptedCommand.match_id == match.id,
                    AcceptedCommand.ownership_id == ownership.id,
                )
                .order_by(AcceptedCommand.accepted_at.desc(), AcceptedCommand.id)
                .limit(5)
            )
        ).all()
        recent = tuple(
            CommandReceipt(r.id, r.kind, r.status, r.target_tick_number or 0) for r in rows
        )
    if record:
        await user_effect(
            session,
            replace(actor, at=at),
            "match_opened",
            Surface.MATCH,
            ownership=ownership,
            club_id=club.id,
            fixture_id=fixture.id,
        )
    return LiveScreen(
        ownership.id,
        club.id,
        club.name,
        fixture.id,
        match.id if match else None,
        state,
        projected,
        side,
        target,
        halftime,
        deadline,
        opened,
        recent,
        await load_commentary(session, state) if state else (),
    )


async def accept_live_command(
    session: AsyncSession,
    actor: Actor,
    *,
    match_id: UUID,
    ownership_id: UUID,
    target: int,
    halftime: bool,
    kind: str,
    payload: dict[str, str],
    clock: Callable[[], datetime] | None = None,
) -> CommandReceipt:
    if actor.update_id is None:
        raise ValueError("A real Telegram update_id is required")
    if type(target) is not int or not 1 <= target <= 30 or type(halftime) is not bool:
        raise ValueError("Invalid command window")
    fields = {
        "substitution": {"outgoing_id", "incoming_id"},
        "tactics": {"tactic"},
        "mentality": {"mentality"},
        "instruction": {"player_id", "instruction"},
    }
    if (
        kind not in fields
        or set(payload) != fields[kind]
        or any(type(v) is not str for v in payload.values())
    ):
        raise ValueError("Invalid closed command payload")
    if not isinstance(match_id, UUID) or not isinstance(ownership_id, UUID):
        raise ValueError("Command needs UUID context")
    payload = {
        name: str(UUID(value)) if name.endswith("_id") else value for name, value in payload.items()
    }
    ownership, club, fixture, match = await _context(session, actor, match_id)
    if ownership.id != ownership_id or match is None:
        raise PermissionDenied("Stale ownership or match")
    checked_at = max(actor.at, clock() if clock else datetime.now(UTC))
    if ownership.expires_at <= checked_at:
        raise PermissionDenied("Ownership expired")
    key = f"live:v1:{actor.operation_id}"
    previous = await session.scalar(
        select(AcceptedCommand).where(
            or_(
                AcceptedCommand.operation_key == key,
                AcceptedCommand.telegram_update_id == actor.update_id,
            )
        )
    )
    if previous is not None:
        if (
            previous.manager_id != actor.manager_id
            or previous.ownership_id != ownership.id
            or previous.match_id != match.id
            or previous.kind != kind
            or previous.payload != payload
            or previous.target_tick_number != target
        ):
            raise PermissionDenied("Command identity reused with different content")
        return CommandReceipt(previous.id, previous.kind, previous.status, target)
    state = restore_state(match.state)
    actual_target, actual_half, deadline, opened = _window(match, fixture, state, checked_at)
    if not opened or (actual_target, actual_half) != (target, halftime) or deadline is None:
        raise CommandUnavailable("Decision window is closed or stale")
    ids = [UUID(value) for name, value in payload.items() if name.endswith("_id")]
    if ids:
        rows = (
            await session.execute(
                select(Player, Person)
                .join(Person, Person.id == Player.person_id)
                .where(Player.person_id.in_(ids), Player.club_id == club.id)
                .order_by(Player.person_id)
                .with_for_update(of=(Player, Person))
                .execution_options(populate_existing=True)
            )
        ).all()
        if len(rows) != len(set(ids)):
            raise CommandUnavailable("Unknown player")
        for player, person in rows:
            if (
                player.club_id != club.id
                or player.status not in {"active", "last_season"}
                or person.career != "player"
            ):
                raise CommandUnavailable("Player left the active club squad")
            if (
                str(player.person_id) == payload.get("incoming_id")
                and player.injured_until
                and player.injured_until > checked_at
            ):
                raise CommandUnavailable("Incoming player is injured")
    checked_at = max(checked_at, clock() if clock else datetime.now(UTC))
    if checked_at >= deadline or ownership.expires_at <= checked_at:
        raise CommandUnavailable("Deadline passed while waiting for locks")
    command = AcceptedCommand(
        id=uuid5(actor.operation_id, "live:v1"),
        operation_key=key,
        telegram_update_id=actor.update_id,
        effect_number=0,
        manager_id=actor.manager_id,
        ownership_id=ownership.id,
        club_id=club.id,
        match_id=match.id,
        kind=kind,
        payload_version=1,
        payload=dict(payload),
        status="accepted",
        target_tick_number=target,
        applied_tick_number=None,
        accepted_at=checked_at,
        applied_at=None,
    )
    candidate = domain_command(command, state, halftime=halftime)
    projected = _project(
        state, await _pending(session, match, ownership), target, halftime, deadline
    )
    _, accepted, rejected = apply_commands(projected, (candidate,), tick=target, halftime=halftime)
    if rejected or not accepted:
        raise CommandUnavailable(rejected[0][1] if rejected else "Duplicate command")
    session.add(command)
    await session.flush()
    await user_effect(
        session,
        replace(actor, at=checked_at),
        "match_command_accepted",
        Surface.MATCH,
        ownership=ownership,
        club_id=club.id,
        fixture_id=fixture.id,
        command_id=command.id,
    )
    return CommandReceipt(command.id, kind, "accepted", target)


def button_payload(
    screen: LiveScreen, action: str, args: tuple[int, ...]
) -> tuple[str, dict[str, str]]:
    """Indexes refer to the immutable roster of this pinned match, never a live list."""
    if screen.state is None:
        raise CommandUnavailable("Match has not started")
    team = screen.state.home if screen.side == Side.HOME else screen.state.away

    def player(index: int) -> str:
        if not 1 <= index <= len(team.snapshot.players):
            raise ValueError("Unknown snapshot roster index")
        return str(team.snapshot.players[index - 1].id)

    if action == "s" and len(args) == 2:
        return "substitution", {"outgoing_id": player(args[0]), "incoming_id": player(args[1])}
    if action == "t" and len(args) == 1 and 0 <= args[0] < len(Tactic):
        return "tactics", {"tactic": list(Tactic)[args[0]].value}
    if action == "m" and len(args) == 1 and 0 <= args[0] < len(Mentality):
        return "mentality", {"mentality": list(Mentality)[args[0]].value}
    if action == "i" and len(args) == 2 and 0 <= args[1] < len(Instruction):
        return "instruction", {
            "player_id": player(args[0]),
            "instruction": list(Instruction)[args[1]].value,
        }
    raise ValueError("Unknown live action")
