"""Pure regulation transitions. One explicit tick = three simulated game minutes."""

import hashlib
import math
from dataclasses import replace
from random import Random
from uuid import UUID, uuid5

from goaletto.domain.clock import MatchPhase
from goaletto.domain.knockout_match import KnockoutContext
from goaletto.domain.match_commands import apply_commands, substitute
from goaletto.domain.match_ratings import SectorRatings, effective_skill, sector_ratings
from goaletto.domain.match_types import (
    BOUNDED_EXPERIENCE_ENGINE_VERSION,
    COACH_ENGINE_VERSION,
    ENGINE_VERSION,
    EXPERIENCE_ENGINE_VERSION,
    KNOCKOUT_ENGINE_VERSION,
    LIVE_ENGINE_VERSION,
    PERCENT_EXPERIENCE_ENGINES,
    POSITION_ENGINE_VERSION,
    POSITIONAL_ENGINES,
    POSITIONS,
    EnginePolicy,
    FieldSlot,
    HomeCrowd,
    Instruction,
    MatchCommand,
    MatchEvent,
    MatchState,
    Mentality,
    PlayerCondition,
    Side,
    Tactic,
    TeamSnapshot,
    TeamState,
    TeamStats,
    TickResult,
)


def validate_state(state: MatchState) -> None:
    if state.engine_version not in {
        ENGINE_VERSION,
        LIVE_ENGINE_VERSION,
        COACH_ENGINE_VERSION,
        KNOCKOUT_ENGINE_VERSION,
        POSITION_ENGINE_VERSION,
        EXPERIENCE_ENGINE_VERSION,
        BOUNDED_EXPERIENCE_ENGINE_VERSION,
    }:
        raise ValueError("Unsupported engine version; never replay with another implementation")
    expected_policy = "match-policy-" + state.engine_version.removeprefix("regulation-")
    if state.policy.version != expected_policy:
        raise ValueError("Engine and policy versions disagree")
    if (
        not isinstance(state.match_id, UUID)
        or type(state.seed) is not int
        or not 0 <= state.seed < 2**63
    ):
        raise ValueError("Match requires a UUID and explicit nonnegative BIGINT seed")
    knockout = state.knockout is not None
    maximum = 40 if knockout else 30
    if (
        state.engine_version == KNOCKOUT_ENGINE_VERSION
        and not knockout
        or knockout
        and state.engine_version not in {KNOCKOUT_ENGINE_VERSION, *POSITIONAL_ENGINES}
    ):
        raise ValueError("Knockout context and engine disagree")
    if type(state.tick) is not int or not 0 <= state.tick <= maximum:
        raise ValueError("Invalid regulation tick")
    if type(state.game_minute) is not int or not 0 <= state.game_minute <= maximum * 3:
        raise ValueError("Invalid game minute")
    if state.home.snapshot.club_id == state.away.snapshot.club_id:
        raise ValueError("A club cannot play itself")
    home_ids = {p.id for p in state.home.snapshot.players}
    if home_ids.intersection(p.id for p in state.away.snapshot.players):
        raise ValueError("One person cannot play for both sides")
    if not isinstance(state.phase, MatchPhase):
        raise ValueError("Invalid match phase")
    if state.phase != MatchPhase.ABANDONED and state.game_minute != state.tick * 3:
        raise ValueError("Game minute does not match regulation tick")
    if state.phase == MatchPhase.FIRST_HALF and state.tick >= 15:
        raise ValueError("First half cannot continue past tick 15")
    if state.phase == MatchPhase.HALF_TIME and state.tick != 15:
        raise ValueError("Half time must follow tick 15")
    if state.phase == MatchPhase.SECOND_HALF and not 15 <= state.tick < 30:
        raise ValueError("Invalid second-half tick")
    if state.phase == MatchPhase.FULL_TIME and state.tick not in ((30, 40) if knockout else (30,)):
        raise ValueError("Full time requires tick 30")
    from goaletto.domain.knockout_match import BREAK_PHASES, PLAY_PHASES, decision

    if state.phase in BREAK_PHASES | PLAY_PHASES | {MatchPhase.PENALTIES} and not knockout:
        raise ValueError("Extra time requires knockout engine")
    if knockout:
        context = state.knockout
        assert context is not None
        a, b = context.tie.club_a_id, context.tie.club_b_id
        expected = (b, a) if context.segment == "leg2" else (a, b)
        if (state.home.snapshot.club_id, state.away.snapshot.club_id) != expected:
            raise ValueError("Knockout orientation changed")
        if state.tick < 30 and (context.regulation is not None or context.shootout is not None):
            raise ValueError("Premature knockout result")
        if context.regulation and (
            context.regulation.home_goals > state.home.stats.goals
            or context.regulation.away_goals > state.away.stats.goals
            or state.tick == 30
            and (context.regulation.home_goals, context.regulation.away_goals)
            != (state.home.stats.goals, state.away.stats.goals)
        ):
            raise ValueError("Regulation score differs from the live checkpoint")
        if state.phase in BREAK_PHASES | PLAY_PHASES | {MatchPhase.PENALTIES}:
            if context.regulation is None or context.segment == "leg1":
                raise ValueError("Extra time has no drawn regulation")
            if state.phase == MatchPhase.EXTRA_BREAK and state.tick != 30:
                raise ValueError("Extra time starts after tick 30")
            if state.phase == MatchPhase.EXTRA_FIRST_HALF and not 30 <= state.tick < 35:
                raise ValueError("Invalid extra first half")
            if state.phase == MatchPhase.EXTRA_HALF_TIME and state.tick != 35:
                raise ValueError("Extra halftime follows tick 35")
            if state.phase == MatchPhase.EXTRA_SECOND_HALF and not 35 <= state.tick < 40:
                raise ValueError("Invalid extra second half")
            if state.phase == MatchPhase.PENALTIES and (
                state.tick != 40 or context.shootout is None
            ):
                raise ValueError("Penalties follow completed extra time")
            outcome = decision(state)
            if outcome.status != (
                "await_penalties" if state.phase == MatchPhase.PENALTIES else "await_extra_time"
            ):
                raise ValueError("Knockout phase has no drawn basis")
        if state.phase == MatchPhase.FULL_TIME and (
            context.regulation is None
            or (context.segment != "leg1" and decision(state).status != "decided")
        ):
            raise ValueError("Unresolved knockout cannot finish")
    if (state.phase == MatchPhase.ABANDONED) != (state.termination_reason is not None):
        raise ValueError("Abandonment must have an explicit reason")
    if len(set(state.applied_commands)) != len(state.applied_commands):
        raise ValueError("Applied commands repeat an ID")
    if (
        type(state.home.support_morale_points) is not int
        or type(state.away.support_morale_points) is not int
        or state.home.support_morale_points
        != (
            0
            if state.knockout and state.knockout.tie.tie_format == "single"
            else crowd_morale(state.crowd, state.policy)
        )
        or state.away.support_morale_points != 0
    ):
        raise ValueError("Support morale must match the pinned crowd and policy")
    for team in (state.home, state.away):
        if state.engine_version in PERCENT_EXPERIENCE_ENGINES:
            from goaletto.domain.position_experience import validate_percent_experience

            for player in team.snapshot.players:
                validate_percent_experience(dict(player.position_experience))
        coach = team.snapshot.coach
        modern = state.engine_version in {
            COACH_ENGINE_VERSION,
            KNOCKOUT_ENGINE_VERSION,
            POSITION_ENGINE_VERSION,
            EXPERIENCE_ENGINE_VERSION,
            BOUNDED_EXPERIENCE_ENGINE_VERSION,
        }
        if modern != (coach is not None):
            raise ValueError("Coaching snapshot and engine disagree")
        abilities = coach.abilities if coach else None
        if abilities and (
            team.tactic not in abilities.tactics or team.snapshot.tactic not in abilities.tactics
        ):
            raise ValueError("Tactic outside the pinned repertoire")
        if type(team.mentality_changes) is not int or not 0 <= team.mentality_changes <= (
            abilities.mentality_changes if abilities else 0
        ):
            raise ValueError("Invalid mentality count")
        snapshot_ids = {p.id for p in team.snapshot.players}
        instruction_ids = [i.player_id for i in team.instructions]
        if (
            len(set(instruction_ids)) != len(instruction_ids)
            or not set(instruction_ids) <= snapshot_ids
        ):
            raise ValueError("Instruction references must be unique snapshot players")
        if type(team.instruction_changes) is not int or not 0 <= team.instruction_changes <= (
            abilities.instruction_changes if abilities else 3
        ):
            raise ValueError("Invalid instruction count")
        if len(team.instructions) > team.instruction_changes:
            raise ValueError("Instruction assignments exceed the change counter")
        if state.engine_version == ENGINE_VERSION and (
            team.instructions or team.instruction_changes
        ):
            raise ValueError("Legacy engine has no individual instructions")
        conditions = {p.player_id: p for p in team.conditions}
        if state.engine_version in POSITIONAL_ENGINES and (
            sum(c.goals for c in team.conditions) != team.stats.goals
            or sum(c.saves for c in team.conditions) != team.stats.saves
            or sum(c.assists for c in team.conditions) > team.stats.goals
        ):
            raise ValueError("Player performance must reconcile with team statistics")
        membership = [s.player_id for s in team.lineup] + list(team.bench) + list(team.removed)
        if len(conditions) != len(team.conditions) or set(conditions) != snapshot_ids:
            raise ValueError("Player conditions do not match the snapshot")
        if len(set(membership)) != len(membership) or set(membership) != snapshot_ids:
            raise ValueError("Field, unused bench and removed people must partition the roster")
        if len(team.lineup) > 11 or (
            len(team.lineup) < state.policy.minimum_players and state.phase != MatchPhase.ABANDONED
        ):
            raise ValueError("Invalid number of available players")
        if sum(s.position == "GK" for s in team.lineup) != 1 and team.lineup:
            raise ValueError("Available team must have one goalkeeper")
        if any(
            conditions[s.player_id].injured or conditions[s.player_id].dismissed
            for s in team.lineup
        ):
            raise ValueError("Unavailable player remains on the field")
        for condition in team.conditions:
            if type(condition.energy_bps) is not int or not 0 <= condition.energy_bps <= 10000:
                raise ValueError("Invalid energy")
            if (
                type(condition.minutes_played) is not int
                or not 0 <= condition.minutes_played <= state.game_minute
            ):
                raise ValueError("Invalid player exposure")
            if type(condition.yellows) is not int or not 0 <= condition.yellows <= 2:
                raise ValueError("Invalid card count")
            positions = dict(condition.position_minutes)
            if any(
                type(value) is not int or value < 0
                for value in (condition.goals, condition.assists, condition.saves)
            ):
                raise ValueError("Invalid player performance")
            if state.engine_version in POSITIONAL_ENGINES:
                if (
                    len(positions) != len(condition.position_minutes)
                    or any(
                        position not in POSITIONS or type(minutes) is not int or minutes <= 0
                        for position, minutes in positions.items()
                    )
                    or sum(positions.values()) != condition.minutes_played
                ):
                    raise ValueError("Positional minutes must reconcile with actual exposure")
            elif positions:
                raise ValueError("Historic engines have no positional exposure")
            elif condition.goals or condition.assists or condition.saves:
                raise ValueError("Historic engines have no performance counters")
            if condition.yellows == 2 and not condition.dismissed:
                raise ValueError("Second yellow requires dismissal")
        if (
            type(team.substitutions) is not int
            or not 0 <= team.substitutions <= state.policy.max_substitutions
        ):
            raise ValueError("Invalid substitution count")
        if (
            len(set(team.substitution_windows)) != len(team.substitution_windows)
            or len(team.substitution_windows) > 3
        ):
            raise ValueError("Invalid substitution windows")
        if any(type(t) is not int or not 1 <= t <= maximum for t in team.substitution_windows):
            raise ValueError("Invalid substitution window tick")
        if type(team.tactic_changes) is not int or not 0 <= team.tactic_changes <= (
            abilities.tactic_changes if abilities else 3
        ):
            raise ValueError("Invalid tactic count")
        if not isinstance(team.tactic, Tactic) or not isinstance(team.mentality, Mentality):
            raise ValueError("Invalid team tactics")
        stats = team.stats
        for value in (
            stats.goals,
            stats.chances,
            stats.shots,
            stats.on_target,
            stats.saves,
            stats.interceptions,
            stats.corners,
            stats.fouls,
            stats.possession_units,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("Invalid match statistic")
        if not stats.goals <= stats.on_target <= stats.shots <= stats.chances:
            raise ValueError("Shot and goal statistics are inconsistent")
    if state.home.stats.on_target != state.home.stats.goals + state.away.stats.saves:
        raise ValueError("Home shots on target do not reconcile")
    if state.away.stats.on_target != state.away.stats.goals + state.home.stats.saves:
        raise ValueError("Away shots on target do not reconcile")
    if (
        state.home.stats.possession_units + state.away.stats.possession_units
        != state.game_minute * 10000
    ):
        raise ValueError("Possession does not reconcile with played minutes")


def crowd_morale(crowd: HomeCrowd, policy: EnginePolicy) -> int:
    return (
        policy.max_support_morale
        * min(crowd.home_fans, policy.full_support_fans)
        // policy.full_support_fans
    )


def start_match(
    match_id: UUID,
    seed: int,
    home: TeamSnapshot,
    away: TeamSnapshot,
    *,
    policy: EnginePolicy | None = None,
    crowd: HomeCrowd | None = None,
    engine_version: str = ENGINE_VERSION,
    initial_energy: dict[UUID, int] | None = None,
    knockout: "KnockoutContext | None" = None,
) -> MatchState:
    def team(snapshot: TeamSnapshot) -> TeamState:
        return TeamState(
            snapshot,
            snapshot.lineup,
            snapshot.bench,
            (),
            tuple(
                PlayerCondition(
                    p.id,
                    energy_bps=(initial_energy or {}).get(p.id, 10000),
                    injured=not p.available,
                )
                for p in snapshot.players
            ),
            TeamStats(),
            snapshot.tactic,
            snapshot.mentality,
        )

    policy = policy or EnginePolicy(
        version="match-policy-" + engine_version.removeprefix("regulation-")
    )
    crowd = crowd or HomeCrowd()
    home_team = replace(
        team(home),
        support_morale_points=0
        if knockout and knockout.tie.tie_format == "single"
        else crowd_morale(crowd, policy),
    )
    state = MatchState(
        match_id,
        seed,
        home_team,
        team(away),
        policy,
        crowd,
        engine_version=engine_version,
        knockout=knockout,
    )
    validate_state(state)
    return state


def _random(state: MatchState, tick: int, label: str) -> Random:
    digest = hashlib.sha256(
        f"{state.engine_version}:{state.match_id}:{state.seed}:{tick}:{label}".encode()
    ).digest()
    return Random(int.from_bytes(digest, "big"))


def _poisson(rate_bps: int, rng: Random) -> int:
    rate = rate_bps / 10000
    probability = math.exp(-rate)
    cumulative = probability
    draw = rng.randrange(1_000_000)
    for count in range(3):
        if draw < round(cumulative * 1_000_000):
            return count
        probability *= rate / (count + 1)
        cumulative += probability
    return 3


def _team(state: MatchState, side: Side) -> TeamState:
    return state.home if side == Side.HOME else state.away


def _with_team(state: MatchState, side: Side, team: TeamState) -> MatchState:
    return replace(state, home=team) if side == Side.HOME else replace(state, away=team)


def _stat(team: TeamState, **increments: int) -> TeamState:
    return replace(
        team,
        stats=replace(
            team.stats,
            **{name: getattr(team.stats, name) + value for name, value in increments.items()},
        ),
    )


def _possession(home: SectorRatings, away: SectorRatings) -> int:
    return home.midfield * 10000 // (home.midfield + away.midfield)


def _chance_weights(state: MatchState, possession: int) -> tuple[int, int]:
    return (
        5000 if state.home.tactic == Tactic.LONG_BALLS else possession,
        5000 if state.away.tactic == Tactic.LONG_BALLS else 10000 - possession,
    )


def _condition(
    team: TeamState,
    player_id: UUID,
    *,
    yellows: int | None = None,
    injured: bool | None = None,
    dismissed: bool | None = None,
) -> TeamState:
    return replace(
        team,
        conditions=tuple(
            replace(
                c,
                yellows=c.yellows if yellows is None else yellows,
                injured=c.injured if injured is None else injured,
                dismissed=c.dismissed if dismissed is None else dismissed,
            )
            if c.player_id == player_id
            else c
            for c in team.conditions
        ),
    )


def _command_events(
    state: MatchState,
    accepted: tuple[MatchCommand, ...],
    tick: int,
    minute: int,
    stage: str,
) -> list[MatchEvent]:
    return [
        MatchEvent(
            uuid5(state.match_id, f"{stage}:command:{command.id}"),
            command.kind.value,
            tick,
            minute,
            command.side,
            command.player_id or command.outgoing_id,
            command.incoming_id,
            command.tactic.value
            if command.tactic
            else command.mentality.value
            if command.mentality
            else command.instruction.value
            if command.instruction
            else None,
        )
        for command in accepted
    ]


def start_second_half(state: MatchState, commands: tuple[MatchCommand, ...] = ()) -> TickResult:
    validate_state(state)
    if state.phase == MatchPhase.SECOND_HALF and state.tick == 15:
        if commands:
            raise ValueError("Second half already started; new commands belong to the next tick")
        return TickResult(state, ())
    if state.phase != MatchPhase.HALF_TIME:
        raise ValueError("Second half must start from half time")
    state, accepted, rejected = apply_commands(state, commands, tick=16, halftime=True)
    events = _command_events(state, accepted, 15, 45, "half-time")
    for side in Side:
        team = _team(state, side)
        active = {s.player_id for s in team.lineup}
        team = replace(
            team,
            conditions=tuple(
                replace(c, energy_bps=min(10000, c.energy_bps + 200))
                if c.player_id in active
                else c
                for c in team.conditions
            ),
        )
        state = _with_team(state, side, team)
    state = replace(state, phase=MatchPhase.SECOND_HALF)
    events.append(
        MatchEvent(uuid5(state.match_id, "second-half:start"), "second_half_started", 15, 45)
    )
    validate_state(state)
    return TickResult(state, tuple(events), tuple(c.id for c in accepted), rejected)


def advance_tick(
    state: MatchState,
    tick_number: int,
    commands: tuple[MatchCommand, ...] = (),
) -> TickResult:
    validate_state(state)
    if (
        type(tick_number) is not int
        or tick_number != state.tick + 1
        or tick_number > (40 if state.knockout else 30)
    ):
        raise ValueError("Only the next unprocessed regulation tick may execute")
    if state.phase not in {
        MatchPhase.FIRST_HALF,
        MatchPhase.SECOND_HALF,
        MatchPhase.EXTRA_FIRST_HALF,
        MatchPhase.EXTRA_SECOND_HALF,
    }:
        raise ValueError("No simulation during half time or after completion")
    state, accepted, rejected = apply_commands(state, commands, tick=tick_number, halftime=False)
    events = _command_events(state, accepted, tick_number, state.game_minute, f"tick:{tick_number}")
    minute = state.game_minute

    def emit(
        kind: str,
        side: Side | None = None,
        player: UUID | None = None,
        other: UUID | None = None,
        detail: str | None = None,
        assist: UUID | None = None,
    ) -> None:
        events.append(
            MatchEvent(
                uuid5(state.match_id, f"tick:{tick_number}:event:{len(events)}"),
                kind,
                tick_number,
                minute,
                side,
                player,
                other,
                detail,
                assist,
            )
        )

    def ensure_goalkeeper(team: TeamState, side: Side) -> TeamState:
        if team.lineup and not any(s.position == "GK" for s in team.lineup):
            slot = team.lineup[0]  # Fixed slot order, never rating-based optimisation.
            team = replace(team, lineup=(FieldSlot(slot.player_id, "GK"), *team.lineup[1:]))
            emit("emergency_goalkeeper", side, slot.player_id)
        return team

    def performance(team: TeamState, player_id: UUID, key: str) -> TeamState:
        if state.engine_version not in POSITIONAL_ENGINES:
            return team
        return replace(
            team,
            conditions=tuple(
                replace(condition, **{key: getattr(condition, key) + 1})
                if condition.player_id == player_id
                else condition
                for condition in team.conditions
            ),
        )

    def remove(team: TeamState, player_id: UUID, side: Side) -> TeamState:
        team = replace(
            team,
            lineup=tuple(s for s in team.lineup if s.player_id != player_id),
            removed=team.removed + (player_id,),
        )
        return ensure_goalkeeper(team, side)

    def chance(side: Side, rng: Random, *, set_piece: bool = False) -> None:
        nonlocal state
        attacking, defending = _team(state, side), _team(state, side.opponent)
        attack_rates, defence_rates = (
            sector_ratings(attacking, state.policy),
            sector_ratings(defending, state.policy),
        )
        channel = rng.randrange(100)
        if attacking.tactic == Tactic.WINGS:
            channel = rng.randrange(40) if rng.randrange(100) < 70 else rng.randrange(40, 100)
        sector = "left" if channel < 20 else "right" if channel < 40 else "center"
        attack = getattr(attack_rates, f"attack_{sector}")
        defence = getattr(
            defence_rates,
            f"defence_{'right' if sector == 'left' else 'left' if sector == 'right' else 'center'}",
        )
        players = {p.id: p for p in attacking.snapshot.players}
        conditions = {c.player_id: c for c in attacking.conditions}
        candidates = [slot for slot in attacking.lineup if slot.position != "GK"] or list(
            attacking.lineup
        )
        weights = [
            max(
                1,
                effective_skill(
                    players[s.player_id],
                    conditions[s.player_id],
                    s.position,
                    "set_pieces" if set_piece else "attacking",
                    attacking.support_morale_points,
                    positional=state.engine_version in POSITIONAL_ENGINES,
                    percentage=state.engine_version in PERCENT_EXPERIENCE_ENGINES,
                    bounded_experience=state.engine_version == BOUNDED_EXPERIENCE_ENGINE_VERSION,
                ),
            )
            for s in candidates
        ]
        instructions = {i.player_id: i.instruction for i in attacking.instructions}
        for index, slot in enumerate(candidates):
            if instructions.get(slot.player_id) == Instruction.LONG_SHOTS and not set_piece:
                weights[index] = (
                    weights[index] * (10000 + state.policy.instruction_bonus_bps) // 10000
                )
        selected = rng.randrange(sum(weights))
        shooter = candidates[-1]
        for candidate, weight in zip(candidates, weights, strict=True):
            selected -= weight
            if selected < 0:
                shooter = candidate
                break
        if set_piece:
            taker_rating = effective_skill(
                players[shooter.player_id],
                conditions[shooter.player_id],
                shooter.position,
                "set_pieces",
                attacking.support_morale_points,
                positional=state.engine_version in POSITIONAL_ENGINES,
                percentage=state.engine_version in PERCENT_EXPERIENCE_ENGINES,
                bounded_experience=state.engine_version == BOUNDED_EXPERIENCE_ENGINE_VERSION,
            )
            attack = (attack + taker_rating * 2) // 2
        assister = None
        if state.engine_version in POSITIONAL_ENGINES and rng.randrange(10000) < 7500:
            creators = [slot for slot in candidates if slot.player_id != shooter.player_id]
            weights = [
                max(
                    1,
                    effective_skill(
                        players[slot.player_id],
                        conditions[slot.player_id],
                        slot.position,
                        "set_pieces" if set_piece else "playmaking",
                        attacking.support_morale_points,
                        positional=True,
                        percentage=state.engine_version in PERCENT_EXPERIENCE_ENGINES,
                        bounded_experience=state.engine_version
                        == BOUNDED_EXPERIENCE_ENGINE_VERSION,
                    ),
                )
                for slot in creators
            ]
            if weights:
                pick = rng.randrange(sum(weights))
                for creator, weight in zip(creators, weights, strict=True):
                    pick -= weight
                    if pick < 0:
                        assister = creator.player_id
                        break
        if attacking.tactic == Tactic.COUNTER and attacking.counter_ready:
            attack = attack * 11500 // 10000
        attacking = replace(_stat(attacking, chances=1), counter_ready=False)
        attack_power = attack * rng.randint(8000, 12000)
        defence_power = defence * rng.randint(8000, 12000)
        outcome: str
        defender_id = next((s.player_id for s in defending.lineup if s.position == "GK"), None)
        if attack_power <= defence_power:
            defending = replace(_stat(defending, interceptions=1), counter_ready=True)
            outcome = "interception"
        else:
            attacking = _stat(attacking, shots=1)
            accuracy = rng.randrange(10000)
            miss_threshold = 1300
            if instructions.get(shooter.player_id) == Instruction.LONG_SHOTS and not set_piece:
                miss_threshold += state.policy.long_shot_extra_miss_bps
            if accuracy < miss_threshold:
                outcome = "wide"
            elif accuracy < 1600:
                outcome = "post"
            else:
                attacking = _stat(attacking, on_target=1)
                quality_bps = (attack_power - defence_power) * 10000 // attack_power
                save_bps = max(
                    2000, min(9700, 4500 + defence_rates.goalkeeper // 4 - quality_bps // 8)
                )
                if rng.randrange(10000) < save_bps:
                    defending = _stat(defending, saves=1)
                    if defender_id is not None:
                        defending = performance(defending, defender_id, "saves")
                    outcome = "save"
                else:
                    attacking = _stat(attacking, goals=1)
                    attacking = performance(attacking, shooter.player_id, "goals")
                    if assister is not None:
                        attacking = performance(attacking, assister, "assists")
                    outcome = "goal"
        state = _with_team(_with_team(state, side, attacking), side.opponent, defending)
        emit(
            outcome,
            side,
            shooter.player_id,
            defender_id,
            "corner" if set_piece else sector,
            assist=assister if outcome == "goal" else None,
        )
        if (
            not set_piece
            and outcome in {"interception", "save", "post"}
            and rng.randrange(10000) < state.policy.corner_bps
        ):
            state = _with_team(state, side, _stat(_team(state, side), corners=1))
            emit("corner", side)
            chance(side, rng, set_piece=True)  # At most one extra chance; no recursive corners.

    initial_possession = _possession(
        sector_ratings(state.home, state.policy), sector_ratings(state.away, state.policy)
    )
    weights_home, weights_away = _chance_weights(state, initial_possession)
    rate = state.policy.base_chance_rate_bps * (weights_home + weights_away) // 10000
    count = _poisson(rate, _random(state, tick_number, "chance-count"))
    chance_minutes = [
        _random(state, tick_number, f"chance-minute:{i}").randint(1, 3) for i in range(count)
    ]
    incidents: dict[Side, list[tuple[int, str]]] = {side: [] for side in Side}
    for side in Side:
        team = _team(state, side)
        intensity = 15000 if team.tactic == Tactic.PRESSING else 10000
        rng = _random(state, tick_number, f"incidents:{side}")
        if rng.randrange(10000) < min(10000, state.policy.direct_red_bps * intensity // 10000):
            incidents[side].append((rng.randint(1, 3), "red"))
        elif rng.randrange(10000) < min(10000, state.policy.yellow_bps * intensity // 10000):
            incidents[side].append((rng.randint(1, 3), "yellow"))
        elif rng.randrange(10000) < min(10000, state.policy.foul_bps * intensity // 10000):
            incidents[side].append((rng.randint(1, 3), "foul"))
        average_energy = sum(
            c.energy_bps
            for c in team.conditions
            if c.player_id in {s.player_id for s in team.lineup}
        ) // len(team.lineup)
        fatigue = 10000 + (10000 - average_energy) // 2
        injury_rate = min(10000, state.policy.injury_bps * intensity * fatigue // 10000**2)
        if rng.randrange(10000) < injury_rate:
            incidents[side].append((rng.randint(1, 3), "injury"))
    for step in range(1, 4):
        minute = state.tick * 3 + step
        possession = _possession(
            sector_ratings(state.home, state.policy), sector_ratings(state.away, state.policy)
        )
        state = _with_team(state, Side.HOME, _stat(state.home, possession_units=possession))
        state = _with_team(state, Side.AWAY, _stat(state.away, possession_units=10000 - possession))
        for side in Side:
            team = _team(state, side)
            players = {p.id: p for p in team.snapshot.players}
            active = {s.player_id: s.position for s in team.lineup}
            conditions = []
            for c in team.conditions:
                if c.player_id in active:
                    positional_minutes = dict(c.position_minutes)
                    if state.engine_version in POSITIONAL_ENGINES:
                        position = active[c.player_id]
                        positional_minutes[position] = positional_minutes.get(position, 0) + 1
                    loss = max(10, 50 - 2 * players[c.player_id].skills.stamina)
                    if team.tactic == Tactic.PRESSING:
                        loss = loss * 13500 // 10000
                    if team.mentality == Mentality.ULTRA_ATTACK:
                        loss = loss * 11500 // 10000
                    c = replace(
                        c,
                        energy_bps=max(1000, c.energy_bps - loss),
                        minutes_played=c.minutes_played + 1,
                        position_minutes=tuple(positional_minutes.items()),
                    )
                conditions.append(c)
            state = _with_team(state, side, replace(team, conditions=tuple(conditions)))
        for index, chance_minute in enumerate(chance_minutes):
            if chance_minute == step:
                home_weight, away_weight = _chance_weights(state, possession)
                rng = _random(state, tick_number, f"chance:{index}")
                side = (
                    Side.HOME
                    if rng.randrange(home_weight + away_weight) < home_weight
                    else Side.AWAY
                )
                chance(side, rng)
        for side in Side:
            for incident_index, (incident_minute, kind) in enumerate(incidents[side]):
                if incident_minute != step:
                    continue
                team = _team(state, side)
                rng = _random(state, tick_number, f"incident-player:{side}:{incident_index}")
                slot = team.lineup[rng.randrange(len(team.lineup))]
                player_id = slot.player_id
                condition = next(c for c in team.conditions if c.player_id == player_id)
                if kind == "injury":
                    team = _condition(team, player_id, injured=True)
                    emit("injury", side, player_id)
                    replacement = None
                    if team.snapshot.automatic_injury_substitutions:
                        people = {p.id: p for p in team.snapshot.players}
                        available = {
                            c.player_id
                            for c in team.conditions
                            if not c.injured and not c.dismissed
                        }
                        for alternative in (False, True):
                            replacement = next(
                                (
                                    p
                                    for p in team.bench
                                    if p in available
                                    and (
                                        slot.position in people[p].alternatives
                                        if alternative
                                        else people[p].position == slot.position
                                    )
                                ),
                                None,
                            )
                            if replacement is not None:
                                break
                    updated, reason = (
                        substitute(
                            team,
                            player_id,
                            replacement,
                            tick=tick_number,
                            halftime=False,
                            maximum_substitutions=5,
                            maximum_windows=3,
                        )
                        if replacement
                        else (team, "no_healthy_reserve")
                    )
                    if reason is None:
                        team = updated
                        emit("automatic_substitution", side, player_id, replacement, "injury")
                    else:
                        team = remove(team, player_id, side)
                        emit("replacement_unavailable", side, player_id, detail=reason)
                else:
                    team = _stat(team, fouls=1)
                    emit("foul", side, player_id)
                    if kind == "yellow":
                        team = _condition(team, player_id, yellows=condition.yellows + 1)
                        emit("yellow", side, player_id)
                        if condition.yellows == 1:
                            team = _condition(team, player_id, dismissed=True)
                            team = remove(team, player_id, side)
                            emit("red", side, player_id, detail="second_yellow")
                    elif kind == "red":
                        team = _condition(team, player_id, dismissed=True)
                        team = remove(team, player_id, side)
                        emit("red", side, player_id, detail="direct_red")
                state = _with_team(state, side, team)
        if min(len(state.home.lineup), len(state.away.lineup)) < state.policy.minimum_players:
            state = replace(
                state, phase=MatchPhase.ABANDONED, termination_reason="insufficient_players"
            )
            emit("match_abandoned", detail="insufficient_players")
            break
    phase = state.phase
    if phase != MatchPhase.ABANDONED:
        phase = (
            MatchPhase.HALF_TIME
            if tick_number == 15
            else MatchPhase.EXTRA_HALF_TIME
            if tick_number == 35
            else MatchPhase.FULL_TIME
            if tick_number in (30, 40)
            else phase
        )
    state = replace(state, tick=tick_number, game_minute=minute, phase=phase)
    if state.knockout and tick_number in (30, 40) and phase != MatchPhase.ABANDONED:
        from goaletto.domain.knockout_match import complete_segment

        state = complete_segment(state)
        phase = state.phase
    if phase == MatchPhase.HALF_TIME:
        emit("half_time")
    elif phase == MatchPhase.FULL_TIME:
        emit("full_time")
    elif phase == MatchPhase.EXTRA_BREAK:
        emit("extra_time_announced")
    elif phase == MatchPhase.EXTRA_HALF_TIME:
        emit("extra_half_time")
    elif phase == MatchPhase.PENALTIES:
        emit("penalties_started")
    validate_state(state)
    return TickResult(state, tuple(events), tuple(c.id for c in accepted), rejected)
