"""Simultaneous cross-set promotion/relegation (AND-119)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from goaletto.application.season_rollover import rollover_season
from goaletto.domain.league_allocation import LeagueSnapshot, allocate
from goaletto.infrastructure.models import (
    Club,
    FixturePlan,
    League,
    Season,
    SeasonAllocationReceipt,
    SeasonClub,
)
from tests.integration.test_agent_wave import NOW
from tests.integration.test_season_rollover import complete_season

pytestmark = pytest.mark.integration

KICKOFF = datetime(2026, 10, 17, 18, tzinfo=UTC)


async def two_aligned_sets(sessions):
    from goaletto.application.world import initialize_world
    from goaletto.domain.world import WorldOptions

    async with sessions.begin() as session:
        first = await initialize_world(
            session,
            WorldOptions(key="cohort-a", seed=0, first_kickoff=KICKOFF, initial_budget=50000),
            initialized_at=NOW,
        )
        second = await initialize_world(
            session,
            WorldOptions(key="cohort-b", seed=1, first_kickoff=KICKOFF, initial_budget=50000),
            initialized_at=NOW,
        )
    return first.season_id, second.season_id


async def rollover_next(sessions, season_id, at):
    async with sessions.begin() as session:
        result = await rollover_season(session, season_id, at)
        assert result.created
        return result.next_season_id


async def test_joint_rollover_moves_clubs_across_sets(sessions):
    season_a, season_b = await two_aligned_sets(sessions)
    latest_a, _ = await complete_season(sessions, season_a)
    latest_b, _ = await complete_season(sessions, season_b)
    at = max(latest_a, latest_b) + timedelta(hours=1)
    next_a = await rollover_next(sessions, season_a, at)
    # Peers transition through their own jobs reusing the joint receipt.
    await rollover_next(sessions, season_b, at + timedelta(minutes=1))
    async with sessions.begin() as session:
        next_b_row = await session.get(Season, season_b)
        assert next_b_row is not None and next_b_row.status == "rolled_over"
        receipts = (await session.scalars(select(SeasonAllocationReceipt))).all()
        assert len(receipts) == 1
        receipt = receipts[0]
        assert sorted(receipt.season_ids) == sorted([str(season_a), str(season_b)])
        assert len(receipt.assignments) == 100
        assert len(receipt.draws) == 5
        leagues = {row.id: row for row in (await session.scalars(select(League))).all()}
        next_b = await session.scalar(
            select(Season.id).where(
                Season.league_set_id == next_b_row.league_set_id, Season.number == 2
            )
        )
        assert next_b is not None
        counts: dict[UUID, int] = {}
        for row in (
            await session.scalars(
                select(SeasonClub).where(SeasonClub.season_id.in_([next_a, next_b]))
            )
        ).all():
            counts[row.league_id] = counts.get(row.league_id, 0) + 1
        assert sorted(counts.values()) == [10] * 10
        # Cross-set moves really happened.
        crossed = sum(
            1
            for a in receipt.assignments
            if leagues[UUID(a["source_league_id"])].league_set_id
            != leagues[UUID(a["destination_league_id"])].league_set_id
        )
        assert crossed > 0
        # Durable membership replays the persisted draw exactly.
        for a in receipt.assignments:
            club = await session.get(Club, UUID(a["club_id"]))
            assert club is not None and str(club.league_id) == a["destination_league_id"]
        # A later job for the peer season finds finished work, never redraws.
        replay = await rollover_season(session, season_b, at + timedelta(hours=1))
        assert not replay.created
        assert await session.scalar(select(func.count()).select_from(SeasonAllocationReceipt)) == 1
        # Pure replay from the frozen final tables reproduces the receipt.
        from goaletto.application.results import league_table

        snapshots = []
        by_set_season = {
            row.league_set_id: row.id
            for row in (
                await session.scalars(select(Season).where(Season.id.in_([season_a, season_b])))
            ).all()
        }
        set_of_league = {league.id: league.league_set_id for league in leagues.values()}
        for league in leagues.values():
            qualifying = by_set_season[set_of_league[league.id]]
            table = await league_table(session, qualifying, league.id)
            snapshots.append(
                LeagueSnapshot(
                    cohort_key=receipt.cohort_key,
                    season_id=qualifying,
                    league_id=league.id,
                    league_set_id=league.league_set_id,
                    tier=league.tier,
                    order=tuple(row.club_id for row in table),
                )
            )
        expected = allocate(snapshots, seed=receipt.seed)
        assert [str(a.destination_league_id) for a in expected.assignments] == [
            a["destination_league_id"] for a in receipt.assignments
        ]


async def test_lone_ready_season_rolls_within_its_own_set(sessions):
    season_a, season_b = await two_aligned_sets(sessions)
    latest_a, _ = await complete_season(sessions, season_a)
    next_a = await rollover_next(sessions, season_a, latest_a + timedelta(hours=1))
    async with sessions.begin() as session:
        assert (await session.get(Season, season_b)).status != "rolled_over"
        receipts = (await session.scalars(select(SeasonAllocationReceipt))).all()
        assert len(receipts) == 1 and receipts[0].season_ids == [str(season_a)]
        leagues = {row.id: row for row in (await session.scalars(select(League))).all()}
        for a in receipts[0].assignments:
            assert (
                leagues[UUID(a["source_league_id"])].league_set_id
                == leagues[UUID(a["destination_league_id"])].league_set_id
            )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(FixturePlan)
                .where(FixturePlan.season_id == next_a, FixturePlan.competition == "league")
            )
            == 450
        )
