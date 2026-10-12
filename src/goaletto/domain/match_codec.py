"""Closed JSON checkpoints and canonical hashes, independent of database storage."""

import hashlib
import json
from dataclasses import asdict, fields
from typing import Any
from uuid import UUID

from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_engine import validate_state
from goaletto.domain.match_types import (
    EnginePolicy,
    FieldSlot,
    HomeCrowd,
    MatchSkills,
    MatchState,
    Mentality,
    PlayerCondition,
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


def _player(value: Any) -> PlayerSnapshot:
    data = _record(value, PlayerSnapshot)
    data["id"] = _uuid(data["id"])
    data["skills"] = MatchSkills(**_record(data["skills"], MatchSkills))
    data["alternatives"] = tuple(data["alternatives"])
    data["available"] = _boolean(data["available"])
    data["last_season"] = _boolean(data["last_season"])
    return PlayerSnapshot(**data)


def _slot(value: Any) -> FieldSlot:
    data = _record(value, FieldSlot)
    return FieldSlot(_uuid(data["player_id"]), data["position"])


def _snapshot(value: Any) -> TeamSnapshot:
    data = _record(value, TeamSnapshot)
    data["club_id"] = _uuid(data["club_id"])
    data["players"] = tuple(_player(p) for p in data["players"])
    data["lineup"] = tuple(_slot(p) for p in data["lineup"])
    data["bench"] = tuple(_uuid(p) for p in data["bench"])
    data["tactic"], data["mentality"] = Tactic(data["tactic"]), Mentality(data["mentality"])
    data["automatic_injury_substitutions"] = _boolean(data["automatic_injury_substitutions"])
    return TeamSnapshot(**data)


def _team(value: Any) -> TeamState:
    data = _record(value, TeamState)
    data["snapshot"] = _snapshot(data["snapshot"])
    data["lineup"] = tuple(_slot(p) for p in data["lineup"])
    data["bench"] = tuple(_uuid(p) for p in data["bench"])
    data["removed"] = tuple(_uuid(p) for p in data["removed"])
    conditions = []
    for condition in data["conditions"]:
        record = _record(condition, PlayerCondition)
        record["player_id"] = _uuid(record["player_id"])
        record["dismissed"] = _boolean(record["dismissed"])
        record["injured"] = _boolean(record["injured"])
        conditions.append(PlayerCondition(**record))
    data["conditions"] = tuple(conditions)
    data["stats"] = TeamStats(**_record(data["stats"], TeamStats))
    data["tactic"], data["mentality"] = Tactic(data["tactic"]), Mentality(data["mentality"])
    data["substitution_windows"] = tuple(data["substitution_windows"])
    data["counter_ready"] = _boolean(data["counter_ready"])
    return TeamState(**data)


def state_document(state: MatchState) -> dict[str, Any]:
    validate_state(state)

    def encode(value: Any) -> str:
        if isinstance(value, UUID):
            return str(value)
        raise ValueError("Unsupported checkpoint value")

    result: dict[str, Any] = json.loads(json.dumps(asdict(state), default=encode))
    return result


def state_json(state: MatchState) -> str:
    return json.dumps(
        state_document(state), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def state_hash(state: MatchState) -> str:
    return hashlib.sha256(state_json(state).encode()).hexdigest()


def restore_state(value: str | dict[str, Any]) -> MatchState:
    try:
        data = _record(json.loads(value) if isinstance(value, str) else value, MatchState)
        data["match_id"] = _uuid(data["match_id"])
        data["home"], data["away"] = _team(data["home"]), _team(data["away"])
        data["policy"] = EnginePolicy(**_record(data["policy"], EnginePolicy))
        data["crowd"] = HomeCrowd(**_record(data["crowd"], HomeCrowd))
        data["phase"] = MatchPhase(data["phase"])
        data["applied_commands"] = tuple(_uuid(p) for p in data["applied_commands"])
        state = MatchState(**data)
        validate_state(state)
        return state
    except (TypeError, AttributeError, KeyError, ValueError) as error:
        raise ValueError("Invalid or unsupported match checkpoint") from error
