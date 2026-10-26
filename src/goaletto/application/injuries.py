"""Durable match injuries and a bounded repair of a proven missing projection."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.domain.clock import MatchPhase
from goaletto.domain.injuries import DAYS, POLICY, unavailable_until
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.domain.match_types import MatchState
from goaletto.infrastructure.models import (
    Fixture,
    Match,
    MatchDevelopment,
    MatchInjury,
    MatchResult,
    Player,
    PlayerRecovery,
)


def injured_players(state: MatchState) -> dict[UUID, tuple[UUID, int]]:
    return {
        c.player_id: (team.snapshot.club_id, c.minutes_played)
        for team in (state.home, state.away)
        for c in team.conditions
        if c.injured
        and c.minutes_played
        and next(p for p in team.snapshot.players if p.id == c.player_id).available
    }


async def _terminal(session: AsyncSession, state: MatchState, at: datetime) -> Fixture:
    require_utc(at)
    match = await session.get(Match, state.match_id)
    fixture = await session.get(Fixture, match.fixture_id) if match else None
    if (
        match is None
        or fixture is None
        or fixture.actual_finish_at is None
        or at < fixture.actual_finish_at
        or match.seed != state.seed
        or match.phase != state.phase.value
        or state.phase not in {MatchPhase.FULL_TIME, MatchPhase.ABANDONED}
        or fixture.status != ("finished" if state.phase == MatchPhase.FULL_TIME else "abandoned")
        or state_hash(restore_state(match.state)) != state_hash(state)
    ):
        raise ValueError("Injuries require the actual terminal checkpoint")
    if state.phase == MatchPhase.FULL_TIME:
        result = await session.get(MatchResult, state.match_id)
        if (
            result is None
            or result.fixture_id != fixture.id
            or result.home_club_id != state.home.snapshot.club_id
            or result.away_club_id != state.away.snapshot.club_id
            or result.state_hash != state_hash(state)
            or result.actual_finish_at != fixture.actual_finish_at
        ):
            raise ValueError("Injuries require the finalized result")
    return fixture


async def _apply(
    session: AsyncSession,
    state: MatchState,
    fixture: Fixture,
    ids: set[UUID],
    at: datetime,
    source: str,
) -> list[MatchInjury]:
    assert fixture.actual_finish_at is not None
    until = unavailable_until(fixture.actual_finish_at)
    injured = injured_players(state)
    rows = []
    for person_id in sorted(ids):
        prior = await session.get(MatchInjury, (state.match_id, person_id))
        if prior is not None:
            if prior.state_hash != state_hash(state):
                raise ValueError("Injury source changed")
            rows.append(prior)
            continue
        club_id, minutes = injured[person_id]
        player = await session.get(Player, person_id, with_for_update=True)
        if (
            player is None
            or player.club_id != club_id
            or player.status not in {"active", "last_season"}
        ):
            raise ValueError("Injured player changed club or career")
        previous = player.injured_until
        player.injured_until = max(previous, until) if previous else until
        row = MatchInjury(
            match_id=state.match_id,
            person_id=person_id,
            club_id=club_id,
            policy_version=POLICY,
            days=DAYS,
            minutes_played=minutes,
            started_at=fixture.actual_finish_at,
            unavailable_until=until,
            applied_at=at,
            previous_until=previous,
            source=source,
            state_hash=state_hash(state),
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def apply_match_injuries(session: AsyncSession, state: MatchState, at: datetime) -> None:
    await allocation_lock(session)
    terms = await session.get(MatchDevelopment, state.match_id)
    if terms is None or not terms.eligible or terms.terms_snapshot.get("injury_policy") is None:
        return
    if (
        terms.terms_snapshot["injury_policy"] != POLICY
        or terms.terms_snapshot.get("injury_days") != DAYS
    ):
        raise ValueError("Unknown frozen injury terms")
    fixture = await _terminal(session, state, at)
    await _apply(session, state, fixture, set(injured_players(state)), at, "runtime")


async def repair_match_injuries(
    session: AsyncSession,
    match_id: UUID,
    club_id: UUID,
    person_ids: set[UUID],
    expected_hash: str,
    at: datetime,
) -> list[MatchInjury]:
    """Explicit evidence-bound operator repair; no bulk/history rewriting."""
    require_utc(at)
    await allocation_lock(session)
    match = await session.get(Match, match_id, with_for_update=True)
    if match is None:
        raise ValueError("Unknown injury match")
    state = restore_state(match.state)
    if state_hash(state) != expected_hash or state.phase != MatchPhase.FULL_TIME or not person_ids:
        raise ValueError("Repair checkpoint differs from reviewed evidence")
    fixture = await _terminal(session, state, at)
    expected = {pid for pid, (club, _) in injured_players(state).items() if club == club_id}
    if person_ids != expected:
        raise ValueError("Repair must name exactly the proven club injuries")
    pending = {pid for pid in person_ids if await session.get(MatchInjury, (match_id, pid)) is None}
    if pending:
        assert fixture.actual_finish_at is not None
        if unavailable_until(fixture.actual_finish_at) <= at:
            raise ValueError("Reported injury already expired")
        for pid in pending:
            recovery = await session.get(PlayerRecovery, pid)
            if recovery and recovery.active_match_id:
                raise ValueError("Repair player is locked in a match")
        later = (
            await session.scalars(
                select(Match)
                .join(Fixture, Fixture.id == Match.fixture_id)
                .where(
                    Fixture.actual_finish_at > fixture.actual_finish_at,
                    Fixture.status.in_(("finished", "abandoned")),
                    (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
                )
            )
        ).all()
        if any(
            c.player_id in pending and c.minutes_played
            for m in later
            for t in (restore_state(m.state).home, restore_state(m.state).away)
            for c in t.conditions
        ):
            raise ValueError("Repair player has a later appearance")
    return await _apply(session, state, fixture, person_ids, at, "repair")
