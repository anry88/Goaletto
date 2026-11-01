"""Seven fixed-point sector ratings; all inputs belong to the match snapshot."""

from dataclasses import dataclass

from goaletto.domain.match_types import (
    EnginePolicy,
    Instruction,
    Mentality,
    PlayerCondition,
    PlayerSnapshot,
    Tactic,
    TeamState,
)
from goaletto.domain.position_experience import bounded_experience_factor, experience_bonus


@dataclass(frozen=True)
class SectorRatings:
    midfield: int
    attack_center: int
    attack_left: int
    attack_right: int
    defence_center: int
    defence_left: int
    defence_right: int
    goalkeeper: int


def position_factor(player: PlayerSnapshot, position: str) -> int:
    if player.position == position:
        return 10000
    return 8500 if position in player.alternatives else 6000


def effective_skill(
    player: PlayerSnapshot,
    condition: PlayerCondition,
    position: str,
    skill: str,
    support_morale_points: int = 0,
    *,
    positional: bool = False,
    percentage: bool = False,
    bounded_experience: bool = False,
) -> int:
    value: int = getattr(player.skills, skill)
    morale_factor = 7500 + min(100, player.morale + support_morale_points) * 50
    experience = dict(player.position_experience).get(position, 0)
    bonus = experience_bonus(experience) if positional and not percentage else 0
    factor = min(1_000_000, experience) if percentage else position_factor(player, position)
    if bounded_experience:
        if not percentage:
            raise ValueError("Bounded experience requires percent units")
        factor = bounded_experience_factor(experience)
    return (
        value
        * 1000
        * player.form
        * condition.energy_bps
        * factor
        * morale_factor
        * (10000 + bonus)
        // (8 * 10000 * 10000 * 10000 * 10000 * (100 if percentage else 1))
    )


def sector_ratings(team: TeamState, policy: EnginePolicy | None = None) -> SectorRatings:
    policy = policy or EnginePolicy()
    instructions = {i.player_id: i.instruction for i in team.instructions}
    players = {p.id: p for p in team.snapshot.players}
    conditions = {p.player_id: p for p in team.conditions}
    midfield = center = left = right = dc = dl = dr = keeper = 0
    for slot in team.lineup:
        p, c = players[slot.player_id], conditions[slot.player_id]
        if c.injured or c.dismissed:
            continue

        def skill(
            name: str,
            player: PlayerSnapshot = p,
            condition: PlayerCondition = c,
            assigned_position: str = slot.position,
        ) -> int:
            return effective_skill(
                player,
                condition,
                assigned_position,
                name,
                team.support_morale_points,
                positional=policy.version
                in {"match-policy-v5", "match-policy-v6", "match-policy-v7"},
                percentage=policy.version in {"match-policy-v6", "match-policy-v7"},
                bounded_experience=policy.version == "match-policy-v7",
            )

        position = slot.position
        midfield_weights = {"DM": 7000, "MC": 10000, "ML": 6500, "MR": 6500, "AMC": 8500}
        midfield += skill("playmaking") * midfield_weights.get(position, 2000) // 10000
        if position in {"FC", "AMC"}:
            center += skill("attacking") * (10000 if position == "FC" else 7000) // 10000
        if position in {"MC", "DM"}:
            center += skill("playmaking") * 1500 // 10000
        if position in {"ML", "AML"}:
            left += (skill("winger") * 7 + skill("pace") * 3) // 10
            center += skill("winger") // 10
        if position in {"MR", "AMR"}:
            right += (skill("winger") * 7 + skill("pace") * 3) // 10
            center += skill("winger") // 10
        experience = (
            0
            if policy.version in {"match-policy-v5", "match-policy-v6", "match-policy-v7"}
            else min(p.experience, 100) * 10
        )
        if position == "DC":
            dc += skill("defending") + experience
            dl += skill("defending") // 5
            dr += skill("defending") // 5
        elif position == "DM":
            dc += skill("defending") * 3 // 10 + experience
        elif position in {"DL", "DR"}:
            dc += skill("defending") // 4
            wing_defence = (skill("defending") * 7 + skill("pace") * 3) // 10 + experience
            wing_attack = (skill("winger") + skill("pace")) // 8
            if position == "DL":
                dl += wing_defence
                left += wing_attack
            else:
                dr += wing_defence
                right += wing_attack
        elif position == "MC":
            dc += skill("defending") * 15 // 100
        if position == "GK":
            keeper = skill("goalkeeping")
        else:
            instruction = instructions.get(slot.player_id)
            shift = skill("attacking") * policy.instruction_bonus_bps // 10000
            if instruction == Instruction.MOVE_LEFT:
                center -= shift
                left += shift
            elif instruction == Instruction.MOVE_RIGHT:
                center -= shift
                right += shift
            elif instruction == Instruction.DROP_BACK:
                center -= shift
                dc += skill("defending") * policy.instruction_bonus_bps // 10000
            elif instruction == Instruction.RUNS_BEHIND:
                center += skill("pace") * policy.instruction_bonus_bps // 10000
    if team.tactic == Tactic.PRESSING:
        midfield = midfield * 11000 // 10000
        dc, dl, dr = (value * 9500 // 10000 for value in (dc, dl, dr))
    elif team.tactic == Tactic.COUNTER:
        midfield = midfield * 9000 // 10000
    elif team.tactic == Tactic.WINGS:
        center, left, right = center * 9000 // 10000, left * 12000 // 10000, right * 12000 // 10000
    elif team.tactic == Tactic.DEFENCE:
        center, left, right = (value * 8500 // 10000 for value in (center, left, right))
        dc, dl, dr = (value * 11500 // 10000 for value in (dc, dl, dr))
    attack_bonus, defence_bonus = {
        Mentality.ULTRA_ATTACK: (12500, 8000),
        Mentality.ATTACK: (11000, 9500),
        Mentality.BALANCED: (10000, 10000),
        Mentality.DEFENCE: (9500, 11000),
        Mentality.ULTRA_DEFENCE: (8000, 12000),
    }[team.mentality]
    center, left, right = (value * attack_bonus // 10000 for value in (center, left, right))
    dc, dl, dr = (value * defence_bonus // 10000 for value in (dc, dl, dr))
    return SectorRatings(
        max(1, midfield),
        max(1, center),
        max(1, left),
        max(1, right),
        max(1, dc),
        max(1, dl),
        max(1, dr),
        keeper,
    )
