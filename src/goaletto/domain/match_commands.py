"""Pure next-tick commands. Ownership/deadlines/accepted-command storage are external."""

from dataclasses import replace
from uuid import UUID

from goaletto.domain.match_types import (
    CommandKind,
    FieldSlot,
    MatchCommand,
    MatchState,
    Side,
    TeamState,
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
    if slot is None or incoming not in team.bench or incoming in team.removed:
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
    return replace(
        team,
        lineup=tuple(
            FieldSlot(incoming, s.position) if s.player_id == outgoing else s for s in team.lineup
        ),
        bench=tuple(p for p in team.bench if p != incoming),
        removed=team.removed + (outgoing,),
        substitutions=team.substitutions + 1,
        substitution_windows=windows,
    ), None


def apply_commands(
    state: MatchState,
    commands: tuple[MatchCommand, ...],
    *,
    tick: int,
    halftime: bool,
) -> tuple[MatchState, tuple[MatchCommand, ...], tuple[tuple[UUID, str], ...]]:
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
            if team.tactic != command.tactic:
                if team.tactic_changes >= state.policy.max_tactic_changes:
                    reason = "tactic_limit"
                else:
                    team = replace(
                        team, tactic=command.tactic, tactic_changes=team.tactic_changes + 1
                    )
        else:
            assert command.mentality is not None
            team = replace(team, mentality=command.mentality)
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
