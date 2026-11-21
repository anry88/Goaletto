"""Pure next-tick commands. Ownership/deadlines/accepted-command storage are external."""

from collections import deque
from dataclasses import replace
from uuid import UUID

from goaletto.domain.match_types import (
    CommandKind,
    FieldSlot,
    MatchCommand,
    MatchState,
    PlayerInstruction,
    PlayerSnapshot,
    Side,
    TeamState,
)
from goaletto.domain.player_positions import ADJACENT


def replacement_candidates(team: TeamState, position: str) -> tuple[UUID, ...]:
    """Order the healthy bench by suitability, with stable bench order on ties."""
    people = {p.id: p for p in team.snapshot.players}
    available = {c.player_id for c in team.conditions if not c.injured and not c.dismissed}
    distances = {position: 0}
    unvisited = deque([position])
    while unvisited:
        current = unvisited.popleft()
        for adjacent in ADJACENT[current]:
            if adjacent not in distances:
                distances[adjacent] = distances[current] + 1
                unvisited.append(adjacent)

    def priority(player: PlayerSnapshot) -> tuple[int, int]:
        if player.position == position:
            return 0, 0
        if position == "GK":
            return 1, -player.skills.goalkeeping
        if position in player.alternatives:
            return 1, 0
        if player.position == "GK":
            return 3, 0
        return 2, min(
            distances.get(role, len(ADJACENT)) for role in (player.position, *player.alternatives)
        )

    return tuple(
        sorted((p for p in team.bench if p in available), key=lambda p: priority(people[p]))
    )


def substitute(
    team: TeamState,
    outgoing: UUID,
    incoming: UUID,
    *,
    tick: int,
    halftime: bool,
    maximum_substitutions: int,
    maximum_windows: int,
) -> tuple[TeamState, str | None]:
    slot = next((s for s in team.lineup if s.player_id == outgoing), None)
    conditions = {c.player_id: c for c in team.conditions}
    vacancy = next((s for s in team.injury_vacancies if s.player_id == outgoing), None)
    if (slot is None and vacancy is None) or incoming not in team.bench or incoming in team.removed:
        return team, "player_unavailable"
    if (
        conditions[outgoing].dismissed
        or conditions[incoming].dismissed
        or conditions[incoming].injured
    ):
        return team, "player_unavailable"
    if team.substitutions >= maximum_substitutions:
        return team, "substitution_limit"
    windows = team.substitution_windows
    if not halftime and tick not in windows:
        if len(windows) >= maximum_windows:
            return team, "window_limit"
        windows += (tick,)
    lineup = tuple(
        FieldSlot(incoming, s.position) if s.player_id == outgoing else s for s in team.lineup
    )
    if vacancy is not None:
        lineup += (FieldSlot(incoming, vacancy.position),)
    return replace(
        team,
        lineup=lineup,
        bench=tuple(p for p in team.bench if p != incoming),
        removed=team.removed + (outgoing,) if slot is not None else team.removed,
        substitutions=team.substitutions + 1,
        substitution_windows=windows,
        injury_vacancies=tuple(s for s in team.injury_vacancies if s.player_id != outgoing),
    ), None


def apply_commands(
    state: MatchState,
    commands: tuple[MatchCommand, ...],
    *,
    tick: int,
    halftime: bool,
) -> tuple[MatchState, tuple[MatchCommand, ...], tuple[tuple[UUID, str], ...]]:
    if tick > (40 if state.knockout else 30):
        raise ValueError("Command exceeds match duration")
    accepted: list[MatchCommand] = []
    rejected: list[tuple[UUID, str]] = []
    seen: set[UUID] = set()
    for command in commands:
        if command.id in seen:
            raise ValueError("Command batch repeats an ID")
        seen.add(command.id)
        if command.id in state.applied_commands:
            continue
        if command.target_tick != tick or command.halftime != halftime:
            rejected.append((command.id, "stale_command"))
            continue
        team = state.home if command.side == Side.HOME else state.away
        reason = None
        abilities = team.snapshot.coach.abilities if team.snapshot.coach else None
        if command.kind == CommandKind.SUBSTITUTION:
            assert command.outgoing_id is not None and command.incoming_id is not None
            team, reason = substitute(
                team,
                command.outgoing_id,
                command.incoming_id,
                tick=tick,
                halftime=halftime,
                maximum_substitutions=state.policy.max_substitutions,
                maximum_windows=state.policy.max_windows,
            )
        elif command.kind == CommandKind.TACTIC:
            assert command.tactic is not None
            if abilities and command.tactic not in abilities.tactics:
                reason = "tactic_unavailable"
            elif team.tactic != command.tactic:
                if team.tactic_changes >= (
                    abilities.tactic_changes if abilities else state.policy.max_tactic_changes
                ):
                    reason = "tactic_limit"
                else:
                    team = replace(
                        team, tactic=command.tactic, tactic_changes=team.tactic_changes + 1
                    )
        elif command.kind == CommandKind.INSTRUCTION:
            assert command.player_id is not None and command.instruction is not None
            slot = next((s for s in team.lineup if s.player_id == command.player_id), None)
            current = next(
                (p.instruction for p in team.instructions if p.player_id == command.player_id), None
            )
            if state.engine_version == "regulation-v1":
                reason = "unsupported_instruction"
            elif slot is None or slot.position == "GK":
                reason = "player_unavailable"
            elif current != command.instruction:
                if team.instruction_changes >= (
                    abilities.instruction_changes
                    if abilities
                    else state.policy.max_instruction_changes
                ):
                    reason = "instruction_limit"
                else:
                    team = replace(
                        team,
                        instructions=tuple(
                            p for p in team.instructions if p.player_id != command.player_id
                        )
                        + (PlayerInstruction(command.player_id, command.instruction),),
                        instruction_changes=team.instruction_changes + 1,
                    )
        else:
            assert command.mentality is not None
            if team.mentality != command.mentality:
                if abilities and team.mentality_changes >= abilities.mentality_changes:
                    reason = "mentality_limit"
                else:
                    team = replace(
                        team,
                        mentality=command.mentality,
                        mentality_changes=team.mentality_changes + (1 if abilities else 0),
                    )
        if reason is not None:
            rejected.append((command.id, reason))
        else:
            state = (
                replace(state, home=team)
                if command.side == Side.HOME
                else replace(state, away=team)
            )
            state = replace(state, applied_commands=state.applied_commands + (command.id,))
            accepted.append(command)
    return state, tuple(accepted), tuple(rejected)
