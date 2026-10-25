"""Closed JSON checkpoints and canonical hashes, independent of database storage."""

import hashlib
import json
from dataclasses import asdict, fields
from typing import Any
from uuid import UUID

from goaletto.domain.clock import MatchPhase
from goaletto.domain.knockout import KnockoutTie, MatchResult, PenaltyKick, PenaltyShootout
from goaletto.domain.knockout_match import KnockoutContext
from goaletto.domain.match_engine import validate_state
from goaletto.domain.match_types import (
    CoachSnapshot,
    EnginePolicy,
    FieldSlot,
    HomeCrowd,
    Instruction,
    MatchSkills,
    MatchState,
    Mentality,
    PlayerCondition,
    PlayerInstruction,
    PlayerSnapshot,
    Tactic,
    TeamSnapshot,
    TeamState,
    TeamStats,
)


def _uuid(value: Any) -> UUID:
    if not isinstance(value, str):
        raise ValueError("Checkpoint ID must be a UUID string")
    return UUID(value)


def _record(value: Any, cls: type) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {f.name for f in fields(cls)}:
        raise ValueError("Unknown or missing checkpoint fields")
    return dict(value)


def _boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("Checkpoint flag must be a boolean")
    return value


def _player(value: Any, *, positional: bool) -> PlayerSnapshot:
    if not positional and isinstance(value, dict):
        if "position_experience" in value:
            raise ValueError("Historic player has positional experience fields")
        value = dict(value, position_experience=[])
    data = _record(value, PlayerSnapshot)
    data["id"] = _uuid(data["id"])
    data["skills"] = MatchSkills(**_record(data["skills"], MatchSkills))
    data["alternatives"] = tuple(data["alternatives"])
    data["available"] = _boolean(data["available"])
    data["last_season"] = _boolean(data["last_season"])
    data["position_experience"] = tuple(tuple(row) for row in data["position_experience"])
    return PlayerSnapshot(**data)


def _slot(value: Any) -> FieldSlot:
    data = _record(value, FieldSlot)
    return FieldSlot(_uuid(data["player_id"]), data["position"])


def _snapshot(value: Any, *, coaching: bool, positional: bool) -> TeamSnapshot:
    if not coaching and isinstance(value, dict):
        if "coach" in value:
            raise ValueError("Historic snapshot contains coaching fields")
        value = dict(value, coach=None)
    data = _record(value, TeamSnapshot)
    if coaching:
        coach = _record(data["coach"], CoachSnapshot)
        coach["person_id"] = _uuid(coach["person_id"]) if coach["person_id"] is not None else None
        data["coach"] = CoachSnapshot(**coach)
    data["club_id"] = _uuid(data["club_id"])
    data["players"] = tuple(_player(p, positional=positional) for p in data["players"])
    data["lineup"] = tuple(_slot(p) for p in data["lineup"])
    data["bench"] = tuple(_uuid(p) for p in data["bench"])
    data["tactic"], data["mentality"] = Tactic(data["tactic"]), Mentality(data["mentality"])
    data["automatic_injury_substitutions"] = _boolean(data["automatic_injury_substitutions"])
    return TeamSnapshot(**data)


def _team(value: Any, *, legacy: bool, coaching: bool, positional: bool) -> TeamState:
    if not coaching and isinstance(value, dict):
        if "mentality_changes" in value:
            raise ValueError("Historic checkpoint contains mentality counter")
        value = dict(value, mentality_changes=0)
    if legacy and isinstance(value, dict):
        if set(value) & {"instructions", "instruction_changes"}:
            raise ValueError("Legacy checkpoint has new fields")
        value = dict(value, instructions=[], instruction_changes=0)
    data = _record(value, TeamState)
    data["snapshot"] = _snapshot(data["snapshot"], coaching=coaching, positional=positional)
    data["lineup"] = tuple(_slot(p) for p in data["lineup"])
    data["bench"] = tuple(_uuid(p) for p in data["bench"])
    data["removed"] = tuple(_uuid(p) for p in data["removed"])
    conditions = []
    for condition in data["conditions"]:
        if not positional and isinstance(condition, dict):
            if "position_minutes" in condition:
                raise ValueError("Historic condition has positional minutes")
            condition = dict(condition, position_minutes=[])
            if set(condition) & {"goals", "assists", "saves"}:
                raise ValueError("Historic condition has performance counters")
            condition = dict(condition, goals=0, assists=0, saves=0)
        record = _record(condition, PlayerCondition)
        record["player_id"] = _uuid(record["player_id"])
        record["dismissed"] = _boolean(record["dismissed"])
        record["injured"] = _boolean(record["injured"])
        record["position_minutes"] = tuple(tuple(row) for row in record["position_minutes"])
        conditions.append(PlayerCondition(**record))
    data["conditions"] = tuple(conditions)
    data["stats"] = TeamStats(**_record(data["stats"], TeamStats))
    data["tactic"], data["mentality"] = Tactic(data["tactic"]), Mentality(data["mentality"])
    data["substitution_windows"] = tuple(data["substitution_windows"])
    data["counter_ready"] = _boolean(data["counter_ready"])
    data["instructions"] = tuple(
        PlayerInstruction(_uuid(p["player_id"]), Instruction(p["instruction"]))
        for p in (_record(raw, PlayerInstruction) for raw in data["instructions"])
    )
    return TeamState(**data)


def state_document(state: MatchState) -> dict[str, Any]:
    validate_state(state)

    def encode(value: Any) -> str:
        if isinstance(value, UUID):
            return str(value)
        raise ValueError("Unsupported checkpoint value")

    result: dict[str, Any] = json.loads(json.dumps(asdict(state), default=encode))
    if state.engine_version != "regulation-v5":
        for side in ("home", "away"):
            for player in result[side]["snapshot"]["players"]:
                del player["position_experience"]
            for condition in result[side]["conditions"]:
                del condition["position_minutes"]
                for key in ("goals", "assists", "saves"):
                    del condition[key]
    if state.engine_version not in {"regulation-v4", "regulation-v5"}:
        del result["knockout"]
    if state.engine_version not in {"regulation-v3", "regulation-v4", "regulation-v5"}:
        for side in ("home", "away"):
            del result[side]["snapshot"]["coach"]
            del result[side]["mentality_changes"]
    if state.engine_version == "regulation-v1":
        for side in ("home", "away"):
            del result[side]["instructions"]
            del result[side]["instruction_changes"]
        for key in ("max_instruction_changes", "instruction_bonus_bps", "long_shot_extra_miss_bps"):
            del result["policy"][key]
    return result


def state_json(state: MatchState) -> str:
    return json.dumps(
        state_document(state), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def state_hash(state: MatchState) -> str:
    return hashlib.sha256(state_json(state).encode()).hexdigest()


def restore_state(value: str | dict[str, Any]) -> MatchState:
    try:
        raw = json.loads(value) if isinstance(value, str) else value
        if isinstance(raw, dict) and raw.get("engine_version") not in {
            "regulation-v4",
            "regulation-v5",
        }:
            if "knockout" in raw:
                raise ValueError("Historic checkpoint contains knockout fields")
            raw = dict(raw, knockout=None)
        data = _record(raw, MatchState)
        if (
            data["engine_version"] in {"regulation-v4", "regulation-v5"}
            and data["knockout"] is not None
        ):
            k = _record(data["knockout"], KnockoutContext)
            k["tie_id"] = _uuid(k["tie_id"])
            tie = _record(k["tie"], KnockoutTie)
            for field in ("club_a_id", "club_b_id"):
                tie[field] = _uuid(tie[field])
            k["tie"] = KnockoutTie(**tie)
            for field in ("first_match", "regulation"):
                if k[field] is not None:
                    segment = _record(k[field], MatchResult)
                    for club in ("home_id", "away_id"):
                        segment[club] = _uuid(segment[club])
                    k[field] = MatchResult(**segment)
            if k["shootout"] is not None:
                shootout = _record(k["shootout"], PenaltyShootout)
                for club in ("club_a_id", "club_b_id", "first_kicker_id"):
                    shootout[club] = _uuid(shootout[club])
                kicks = []
                for row in shootout["kicks"]:
                    kick = _record(row, PenaltyKick)
                    kick["club_id"] = _uuid(kick["club_id"])
                    kicks.append(PenaltyKick(**kick))
                shootout["kicks"] = tuple(kicks)
                k["shootout"] = PenaltyShootout(**shootout)
            data["knockout"] = KnockoutContext(**k)
        data["match_id"] = _uuid(data["match_id"])
        legacy = data["engine_version"] == "regulation-v1"
        coaching = data["engine_version"] in {"regulation-v3", "regulation-v4", "regulation-v5"}
        positional = data["engine_version"] == "regulation-v5"
        data["home"], data["away"] = (
            _team(data["home"], legacy=legacy, coaching=coaching, positional=positional),
            _team(data["away"], legacy=legacy, coaching=coaching, positional=positional),
        )
        if legacy:
            raw_policy = data["policy"]
            extra = {"max_instruction_changes", "instruction_bonus_bps", "long_shot_extra_miss_bps"}
            if not isinstance(raw_policy, dict) or set(raw_policy) & extra:
                raise ValueError("Invalid legacy policy fields")
            data["policy"] = dict(
                raw_policy,
                max_instruction_changes=3,
                instruction_bonus_bps=1500,
                long_shot_extra_miss_bps=1000,
            )
        data["policy"] = EnginePolicy(**_record(data["policy"], EnginePolicy))
        data["crowd"] = HomeCrowd(**_record(data["crowd"], HomeCrowd))
        data["phase"] = MatchPhase(data["phase"])
        data["applied_commands"] = tuple(_uuid(p) for p in data["applied_commands"])
        state = MatchState(**data)
        validate_state(state)
        return state
    except (TypeError, AttributeError, KeyError, ValueError) as error:
        raise ValueError("Invalid or unsupported match checkpoint") from error
