"""Simultaneous cross-set promotion/relegation (AND-119)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from goaletto.application.season_rollover import rollover_season
from goaletto.domain.league_allocation import LeagueSnapshot, allocate
from goaletto.infrastructure.models import (
    Club,
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


async def test_joint_rollover_moves_clubs_across_sets(sessions, monkeypatch):
    season_a, season_b = await two_aligned_sets(sessions)
    latest_a, _ = await complete_season(sessions, season_a)
    latest_b, _ = await complete_season(sessions, season_b)
    at = max(latest_a, latest_b) + timedelta(hours=1)
    import goaletto.application.season_rollover as runtime
    from goaletto.infrastructure.models import ChampionsTournament, Person, SeasonRolloverReceipt

    async with sessions.begin() as session:
        ages = dict((await session.execute(select(Person.id, Person.age))).all())
    install = runtime._install_lineups
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second cohort season")
        return await install(*args, **kwargs)

    monkeypatch.setattr(runtime, "_install_lineups", fail_second)
    with pytest.raises(RuntimeError, match="second cohort season"):
        async with sessions.begin() as session:
            await rollover_season(session, season_a, at)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(SeasonAllocationReceipt)) == 0
        assert await session.scalar(select(func.count()).select_from(SeasonRolloverReceipt)) == 0
        assert (
            dict(
                (
                    await session.execute(select(Person.id, Person.age).where(Person.id.in_(ages)))
                ).all()
            )
            == ages
        )
    monkeypatch.setattr(runtime, "_install_lineups", install)
    next_a = await rollover_next(sessions, season_a, at)
    # One transaction transitions every member; a peer job is now a pure retry.
    async with sessions.begin() as session:
        peer = await rollover_season(session, season_b, at + timedelta(minutes=1))
        assert not peer.created
        assert await session.scalar(select(func.count()).select_from(SeasonRolloverReceipt)) == 2
        groups = (await session.scalars(select(ChampionsTournament))).all()
        assert len(groups) == 5 and all(g.participant_count == 16 for g in groups)
        assert all(
            [
                (await session.get(Person, person_id)).age in (age, age + 1)
                for person_id, age in ages.items()
            ]
        )

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
        from goaletto.infrastructure.models import Player, Staff

        membership = {
            m.club_id: m.season_id
            for m in (
                await session.scalars(
                    select(SeasonClub).where(SeasonClub.season_id.in_([next_a, next_b]))
                )
            ).all()
        }
        for profile in (
            await session.scalars(select(Player).where(Player.club_id.is_not(None)))
        ).all():
            person = await session.get(Person, profile.person_id)
            assert person.age_applied_for_season_id == membership[profile.club_id]
            if profile.status == "last_season":
                assert profile.last_season_id == membership[profile.club_id]
        for profile in (
            await session.scalars(select(Staff).where(Staff.club_id.is_not(None)))
        ).all():
            assert (
                await session.get(Person, profile.person_id)
            ).age_applied_for_season_id == membership[profile.club_id]
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

    # The next full year includes shared champions fixtures with cross-set guests.
    # Complete every set in chronological order, then perform a second joint move.
    from goaletto.infrastructure.models import ChampionsTie, MatchDevelopment, MatchResult
    from tests.integration.test_champions_runtime import play_cohort

    latest_second = await play_cohort(sessions, [next_a, next_b])
    async with sessions.begin() as session:
        results = (
            await session.scalars(
                select(MatchResult).where(
                    MatchResult.season_id.in_([next_a, next_b]),
                    MatchResult.competition == "champions",
                )
            )
        ).all()
        assert len(results) == 145
        terms = (
            await session.scalars(
                select(MatchDevelopment).where(
                    MatchDevelopment.match_id.in_([r.match_id for r in results])
                )
            )
        ).all()
        assert len(terms) == 145 and all(d.eligible and d.applied_at and d.results for d in terms)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ChampionsTie)
                .where(ChampionsTie.status == "decided")
            )
            == 75
        )
        repeated = await rollover_season(session, next_a, latest_second + timedelta(hours=1))
        assert repeated.created
        assert not (
            await rollover_season(session, next_b, latest_second + timedelta(hours=1))
        ).created
        third = (
            await session.scalars(select(SeasonClub).join(Season).where(Season.number == 3))
        ).all()
        assert len(third) == len({m.club_id for m in third}) == 100
        assert (
            sorted(
                sum(m.league_id == league for m in third) for league in {m.league_id for m in third}
            )
            == [10] * 10
        )
        assert await session.scalar(select(func.count()).select_from(SeasonAllocationReceipt)) == 2


async def test_ready_season_waits_for_compatible_peer_without_partial_rollover(sessions):
    from goaletto.application.match_runtime import NotReady

    season_a, season_b = await two_aligned_sets(sessions)
    latest_a, _ = await complete_season(sessions, season_a)
    with pytest.raises(NotReady):
        async with sessions.begin() as session:
            await rollover_season(session, season_a, latest_a + timedelta(hours=1))
    async with sessions.begin() as session:
        assert (await session.get(Season, season_b)).status != "rolled_over"
        assert (await session.get(Season, season_a)).status != "rolled_over"
        assert await session.scalar(select(func.count()).select_from(SeasonAllocationReceipt)) == 0
        assert await session.scalar(select(func.count()).select_from(Season)) == 2


async def test_incompatible_seasons_never_share_allocation(sessions):
    from goaletto.application.allocation import ready_peers

    first, second = await two_aligned_sets(sessions)
    async with sessions.begin() as session:
        source = await session.get(Season, first)
        peer = await session.get(Season, second)
        peer.cycle_epoch += timedelta(days=4)
        peer.ends_at += timedelta(days=4)
        await session.flush()
        assert await ready_peers(session, source, peer.ends_at + timedelta(days=1)) == []
