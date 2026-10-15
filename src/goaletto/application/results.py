"""Immutable result receipt and standings updates in a single caller-owned transaction."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.domain.standings import LeagueStanding, add_result, rank_league
from goaletto.infrastructure.models import Club, Fixture, Match, MatchResult, SeasonClub, Standing


def standing_snapshot(row: Standing, membership: SeasonClub, name: str) -> LeagueStanding:
    snapshot = LeagueStanding(
        row.club_id,
        name,
        membership.tie_break_order,
        row.played,
        row.won,
        row.drawn,
        row.lost,
        row.goals_for,
        row.goals_against,
    )
    if row.points != snapshot.points:
        raise ValueError("Stored points do not reconcile")
    return snapshot


async def finalize_result(session: AsyncSession, fixture_id: UUID, at: datetime) -> bool:
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Result needs the caller's transaction")
    from goaletto.application.game_actions import allocation_lock

    await allocation_lock(session)
    fixture = await session.scalar(
        select(Fixture).where(Fixture.id == fixture_id).with_for_update(key_share=True)
    )
    if fixture is None or fixture.status != "finished" or fixture.actual_finish_at is None:
        raise ValueError("Only completed regulation fixtures produce standings")
    match = await session.scalar(
        select(Match).where(Match.fixture_id == fixture_id).with_for_update()
    )
    if match is None or match.phase != "full_time" or match.completed_tick != 30:
        raise ValueError("Fixture has no completed regulation checkpoint")
    state = restore_state(match.state)
    if (
        state.phase != MatchPhase.FULL_TIME
        or state.match_id != match.id
        or state.seed != match.seed
        or state.engine_version != match.engine_version
        or match.state_version != int(state.engine_version.removeprefix("regulation-v"))
        or state.home.snapshot.club_id != fixture.home_club_id
        or state.away.snapshot.club_id != fixture.away_club_id
        or state.home.stats.goals != match.home_goals
        or state.away.stats.goals != match.away_goals
    ):
        raise ValueError("Final checkpoint projections do not reconcile")
    hash_value = state_hash(state)
    receipt = await session.get(MatchResult, match.id)
    if receipt is not None:
        if (
            receipt.state_hash != hash_value
            or receipt.fixture_id != fixture.id
            or receipt.season_id != fixture.season_id
            or receipt.league_id != fixture.league_id
            or receipt.home_club_id != fixture.home_club_id
            or receipt.away_club_id != fixture.away_club_id
            or receipt.actual_start_at != fixture.actual_start_at
            or receipt.actual_finish_at != fixture.actual_finish_at
        ):
            raise ValueError("Previously finalized result has changed")
        return False
    if fixture.actual_start_at is None or at < fixture.actual_finish_at:
        raise ValueError("Invalid result dates")
    memberships = (
        await session.scalars(
            select(SeasonClub).where(
                SeasonClub.season_id == fixture.season_id,
                SeasonClub.club_id.in_((fixture.home_club_id, fixture.away_club_id)),
                SeasonClub.league_id == fixture.league_id,
            )
        )
    ).all()
    rows = (
        await session.scalars(
            select(Standing)
            .where(
                Standing.season_id == fixture.season_id,
                Standing.club_id.in_((fixture.home_club_id, fixture.away_club_id)),
            )
            .order_by(Standing.club_id)
            .with_for_update()
        )
    ).all()
    if len(rows) != 2 or len(memberships) != 2:
        raise ValueError("Result is missing historical standings membership")
    member_by_id = {m.club_id: m for m in memberships}
    for row in rows:
        home = row.club_id == fixture.home_club_id
        if row.club_id not in {fixture.home_club_id, fixture.away_club_id}:
            raise ValueError("Wrong result club")
        snapshot = standing_snapshot(row, member_by_id[row.club_id], "Club")
        new = add_result(
            snapshot,
            match.home_goals if home else match.away_goals,
            match.away_goals if home else match.home_goals,
        )
        for field in ("played", "won", "drawn", "lost", "goals_for", "goals_against", "points"):
            setattr(row, field, getattr(new, field))
    session.add(
        MatchResult(
            match_id=match.id,
            fixture_id=fixture.id,
            season_id=fixture.season_id,
            league_id=fixture.league_id,
            home_club_id=fixture.home_club_id,
            away_club_id=fixture.away_club_id,
            version=1,
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            actual_start_at=fixture.actual_start_at,
            actual_finish_at=fixture.actual_finish_at,
            recorded_at=at,
            state_hash=hash_value,
        )
    )
    await session.flush()
    from goaletto.application.finances import match_income
    from goaletto.application.recovery import finish_energy

    await finish_energy(session, state, fixture.actual_finish_at)
    await match_income(session, fixture, state, at)
    from goaletto.application.match_development import apply_development

    await apply_development(session, state, at)
    return True


async def league_table(
    session: AsyncSession, season_id: UUID, league_id: UUID
) -> tuple[LeagueStanding, ...]:
    rows = (
        await session.execute(
            select(Standing, SeasonClub, Club)
            .join(
                SeasonClub,
                (SeasonClub.season_id == Standing.season_id)
                & (SeasonClub.club_id == Standing.club_id),
            )
            .join(Club, Club.id == Standing.club_id)
            .where(Standing.season_id == season_id, SeasonClub.league_id == league_id)
        )
    ).all()
    return rank_league(
        tuple(standing_snapshot(row, member, club.name) for row, member, club in rows)
    )
