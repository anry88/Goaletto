"""Pinned sporting audience gains and historical merchandise accrual."""

from datetime import datetime
from math import ceil
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.domain.finances import accrued_wage, merchandise_income
from goaletto.domain.game_cycle import CYCLE_SECONDS
from goaletto.domain.knockout_match import decision
from goaletto.domain.match_codec import state_hash
from goaletto.domain.match_practice import CURRENT_POLICY, PERCENT_POLICY
from goaletto.domain.match_types import MatchState
from goaletto.domain.stadium import match_stage
from goaletto.domain.supporters import POLICY, match_gain, season_gain
from goaletto.infrastructure.models import (
    Club,
    Fixture,
    MatchCrowd,
    MatchDevelopment,
    MatchResult,
    SupporterChange,
)


async def club_tier(session: AsyncSession, fixture: Fixture, club_id: UUID, at: datetime) -> int:
    from goaletto.application.champions import league_season_for
    from goaletto.application.finances import ranking_at

    season = (
        await league_season_for(session, fixture.season_id, club_id)
        if fixture.competition == "champions"
        else fixture.season_id
    )
    return (await ranking_at(session, club_id, at, season_id=season))[1]


async def frozen_terms(
    session: AsyncSession, match_id: UUID, at: datetime
) -> dict[str, Any] | None:
    practice = await session.get(MatchDevelopment, match_id)
    if (
        practice is None
        or not practice.eligible
        or practice.policy_version not in (CURRENT_POLICY, PERCENT_POLICY)
    ):
        return None
    fixture = await session.get(Fixture, UUID(practice.terms_snapshot["fixture_id"]))
    if fixture is None:
        raise ValueError("Supporter terms need a genuine fixture")
    clubs = {}
    for key in (fixture.home_club_id, fixture.away_club_id):
        club = await session.get(Club, key)
        if club is None:
            raise ValueError("Supporter club is missing")
        clubs[str(key)] = {
            "supporters": club.supporters,
            "tier": await club_tier(session, fixture, key, at),
        }
    competition = fixture.competition or "league"
    return {
        "policy": POLICY,
        "clubs": clubs,
        "competition": competition,
        "stage": match_stage(competition, fixture.stage),
    }


async def _save_change(
    session: AsyncSession,
    club: Club,
    season_id: UUID,
    match_id: UUID | None,
    gain: int,
    basis: dict[str, Any],
    at: datetime,
    recorded_at: datetime,
) -> None:
    require_utc(at)
    from goaletto.application.game_actions import allocation_lock

    await allocation_lock(session)
    kind = "match" if match_id else "season"
    key = uuid5(match_id or season_id, f"{POLICY}:{kind}:{club.id}")
    if await session.get(SupporterChange, key) is not None:
        return
    latest = await session.scalar(
        select(SupporterChange.effective_at)
        .where(SupporterChange.club_id == club.id)
        .order_by(SupporterChange.effective_at.desc())
        .limit(1)
    )
    if latest is not None and at < latest:
        raise ValueError("Supporter history cannot move backwards")
    before = club.supporters
    club.supporters += gain
    session.add(
        SupporterChange(
            id=key,
            club_id=club.id,
            season_id=season_id,
            match_id=match_id,
            kind=kind,
            policy_version=POLICY,
            before_count=before,
            after_count=club.supporters,
            effective_at=at,
            recorded_at=max(recorded_at, at),
            basis=basis,
        )
    )
    await session.flush()


async def apply_match(session: AsyncSession, state: MatchState, at: datetime) -> None:
    from goaletto.application.game_actions import allocation_lock

    require_utc(at)
    await allocation_lock(session)
    crowd = await session.get(MatchCrowd, state.match_id)
    terms = crowd.basis.get("supporter_terms") if crowd else None
    if terms is None:
        return  # Old and operator matches have no newly pinned audience contract.
    result = await session.get(MatchResult, state.match_id)
    if (
        terms["policy"] != POLICY
        or result is None
        or result.state_hash != state_hash(state)
        or terms["competition"] != result.competition
    ):
        raise ValueError("Supporter growth requires its frozen finalized result")
    keys = {str(state.home.snapshot.club_id), str(state.away.snapshot.club_id)}
    if set(terms["clubs"]) != keys:
        raise ValueError("Supporter participants differ from lock")
    winner = decision(state).winner if state.knockout else None
    for team, opponent in ((state.home, state.away), (state.away, state.home)):
        club_id = team.snapshot.club_id
        club = await session.get(Club, club_id, with_for_update=True)
        if club is None:
            raise ValueError("Supporter club is missing")
        previous = await session.scalar(
            select(SupporterChange).where(
                SupporterChange.club_id == club_id, SupporterChange.match_id == state.match_id
            )
        )
        if previous is not None:
            continue
        frozen = terms["clubs"][str(club_id)]
        if club.supporters != frozen["supporters"]:
            raise ValueError("Supporter base changed during a locked match")
        other = terms["clubs"][str(opponent.snapshot.club_id)]
        advanced = winner == club_id
        gain = match_gain(
            frozen["supporters"],
            frozen["tier"],
            other["tier"],
            terms["competition"],
            terms["stage"],
            team.stats.goals,
            opponent.stats.goals,
            advanced=advanced,
        )
        await _save_change(
            session,
            club,
            result.season_id,
            state.match_id,
            gain,
            dict(
                terms,
                advanced=advanced,
                goals_for=team.stats.goals,
                goals_against=opponent.stats.goals,
            ),
            at,
            result.recorded_at,
        )


async def apply_season(
    session: AsyncSession,
    club: Club,
    season_id: UUID,
    tier: int,
    place: int,
    destination_tier: int,
    at: datetime,
) -> None:
    from goaletto.application.game_actions import allocation_lock

    require_utc(at)
    await allocation_lock(session)
    if await session.scalar(
        select(SupporterChange.id).where(
            SupporterChange.club_id == club.id,
            SupporterChange.season_id == season_id,
            SupporterChange.kind == "season",
        )
    ):
        return
    gain = season_gain(club.supporters, tier, place, destination_tier)
    await _save_change(
        session,
        club,
        season_id,
        None,
        gain,
        {"tier": tier, "place": place, "destination_tier": destination_tier},
        at,
        at,
    )


async def supporters_at(session: AsyncSession, club: Club, at: datetime) -> int:
    require_utc(at)
    previous = await session.scalar(
        select(SupporterChange)
        .where(SupporterChange.club_id == club.id, SupporterChange.effective_at <= at)
        .order_by(
            SupporterChange.effective_at.desc(),
            SupporterChange.after_count.desc(),
            SupporterChange.id.desc(),
        )
        .limit(1)
    )
    if previous is not None:
        return previous.after_count
    following = await session.scalar(
        select(SupporterChange)
        .where(SupporterChange.club_id == club.id)
        .order_by(SupporterChange.effective_at, SupporterChange.after_count, SupporterChange.id)
        .limit(1)
    )
    return following.before_count if following else club.supporters


async def merchandise_for_period(
    session: AsyncSession, club: Club, start: datetime, end: datetime
) -> tuple[int, list[dict[str, Any]]]:
    require_utc(start)
    require_utc(end)
    if not 0 < (end - start).total_seconds() <= CYCLE_SECONDS:
        raise ValueError("Invalid audience merchandise period")
    fans = await supporters_at(session, club, start)
    changes = (
        await session.scalars(
            select(SupporterChange)
            .where(
                SupporterChange.club_id == club.id,
                SupporterChange.effective_at > start,
                SupporterChange.effective_at < end,
            )
            .order_by(SupporterChange.effective_at, SupporterChange.after_count, SupporterChange.id)
        )
    ).all()
    cursor, total, periods = start, 0, []
    for timestamp, new_fans in [*((r.effective_at, r.after_count) for r in changes), (end, fans)]:
        seconds = (timestamp - cursor).total_seconds()
        if seconds:
            due = accrued_wage(merchandise_income(fans), ceil(seconds), CYCLE_SECONDS)
            periods.append(
                {
                    "from": cursor.isoformat(),
                    "to": timestamp.isoformat(),
                    "supporters": fans,
                    "income": due,
                }
            )
            total += due
        cursor, fans = timestamp, new_fans
    return total, periods
