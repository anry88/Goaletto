"""Cohort persistence/aging integration with an explicit stub of readiness only.

Full completion-proof/football acceptance lives in test_cross_set_rollover.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from goaletto.application.allocation import ReadySeason
from goaletto.application.results import league_table
from goaletto.infrastructure.models import (
    ChampionsTournament,
    Club,
    League,
    Person,
    Player,
    Season,
    SeasonClub,
    SeasonRolloverReceipt,
)
from tests.integration.test_cross_set_rollover import two_aligned_sets

pytestmark = pytest.mark.integration


async def test_joint_commit_ages_once_and_rolls_back_every_set(sessions, monkeypatch):
    import goaletto.application.allocation as allocation
    import goaletto.application.season_rollover as runtime

    first, second = await two_aligned_sets(sessions)

    async def ready(session, season, at):
        leagues = list(
            (
                await session.scalars(
                    select(League)
                    .where(
                        League.league_set_id == season.league_set_id,
                    )
                    .order_by(League.tier)
                )
            ).all()
        )
        tables = {
            league.tier: await league_table(session, season.id, league.id) for league in leagues
        }
        return ReadySeason(season, leagues, tables, season.ends_at)

    monkeypatch.setattr(runtime, "load_ready_season", ready)
    monkeypatch.setattr(allocation, "load_ready_season", ready)
    async with sessions.begin() as session:
        for sid in (first, second):
            (await session.get(Season, sid)).status = "active"
        at = (await session.get(Season, first)).ends_at + timedelta(hours=1)
        ages = dict((await session.execute(select(Person.id, Person.age))).all())
    install = runtime._install_lineups
    calls = 0

    async def crash(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second set failure")
        return await install(*args, **kwargs)

    monkeypatch.setattr(runtime, "_install_lineups", crash)
    with pytest.raises(RuntimeError, match="second set failure"):
        async with sessions.begin() as session:
            await runtime.rollover_season(session, first, at)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(SeasonRolloverReceipt)) == 0
        assert dict((await session.execute(select(Person.id, Person.age))).all()) == ages
    monkeypatch.setattr(runtime, "_install_lineups", install)
    async with sessions.begin() as session:
        result = await runtime.rollover_season(session, first, at)
        assert result.created
        assert not (await runtime.rollover_season(session, second, at)).created
        assert await session.scalar(select(func.count()).select_from(SeasonRolloverReceipt)) == 2
        tournaments = (await session.scalars(select(ChampionsTournament))).all()
        assert len(tournaments) == 5 and all(t.participant_count == 16 for t in tournaments)
        next_seasons = list(
            (await session.scalars(select(Season.id).where(Season.number == 2))).all()
        )
        members = (
            await session.scalars(select(SeasonClub).where(SeasonClub.season_id.in_(next_seasons)))
        ).all()
        assert len(members) == len({m.club_id for m in members}) == 100
        assert all(
            [
                len([m for m in members if m.league_id == league]) == 10
                for league in {m.league_id for m in members}
            ]
        )
        calendars = {m.club_id: m.season_id for m in members}
        for player in (
            await session.scalars(select(Player).where(Player.club_id.is_not(None)))
        ).all():
            person = await session.get(Person, player.person_id)
            assert person.age_applied_for_season_id == calendars[player.club_id]
            if player.person_id in ages:
                assert person.age == ages[player.person_id] + 1
        assert all([(await session.get(Club, m.club_id)).league_id == m.league_id for m in members])

        # A qualifier that stays in another set must see its shared host plans.
        from goaletto.application.onboarding import begin_game, claim_club
        from goaletto.application.schedule import load_schedule
        from goaletto.infrastructure.models import FixturePlan

        host = tournaments[0].season_id
        qualified = {
            c
            for plan in (
                await session.scalars(
                    select(FixturePlan).where(
                        FixturePlan.competition == "champions",
                        FixturePlan.round_number == 1,
                    )
                )
            ).all()
            for c in (plan.home_club_id, plan.away_club_id)
        }
        guest_ids = [
            m.club_id
            for m in members
            if m.season_id != host
            and m.club_id in qualified
            and (await session.get(League, m.league_id)).tier == 5
        ]
        guest = guest_ids[0]
        entry = await begin_game(session, telegram_id=1001, update_id=8001, payload=None, at=at)
        from dataclasses import replace
        from uuid import uuid4

        actor = replace(entry.actor, operation_id=uuid4(), update_id=8002)
        await claim_club(session, actor, guest)
        from goaletto.domain.game_cycle import midnight

        own_season = await session.get(Season, calendars[guest])
        screen = await load_schedule(
            session,
            replace(
                actor, operation_id=uuid4(), update_id=8003, at=midnight(own_season.cycle_epoch)
            ),
        )
        assert any(e.competition == "champions" for day in screen.days for e in day.entries)

    from goaletto.application.history import _fixture_filters
    from goaletto.infrastructure.models import Fixture, MatchCrowd, MatchDevelopment, MatchResult
    from tests.integration.test_champions_runtime import open_season
    from tests.integration.test_friendly import _play

    for sid in next_seasons:
        await open_season(sessions, sid)
    async with sessions.begin() as session:
        fixture = await session.scalar(
            select(Fixture)
            .where(
                Fixture.competition == "champions",
                Fixture.round_number == 1,
                Fixture.home_club_id.in_([m.club_id for m in members if m.season_id != host]),
            )
            .order_by(Fixture.id)
            .limit(1)
        )
        fixture_id, guest_home = fixture.id, fixture.home_club_id
        actual_season = calendars[guest_home]
        assert fixture.season_id != actual_season
    await _play(sessions, fixture_id)
    async with sessions.begin() as session:
        result = await session.scalar(
            select(MatchResult).where(MatchResult.fixture_id == fixture_id)
        )
        assert result is not None
        assert await session.get(MatchCrowd, result.match_id) is not None
        practice = await session.get(MatchDevelopment, result.match_id)
        assert practice.eligible and practice.applied_at and practice.results
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Fixture)
                .where(*_fixture_filters(guest_home, actual_season))
            )
            == 1
        )


@pytest.mark.parametrize("count,group_sizes", [(3, [8, 16]), (4, [32])])
async def test_persisted_multi_set_grouping_and_membership(
    sessions, monkeypatch, count, group_sizes
):
    import goaletto.application.allocation as allocation
    import goaletto.application.season_rollover as runtime
    from goaletto.application.world import initialize_world
    from goaletto.domain.world import WorldOptions
    from tests.integration.test_agent_wave import NOW
    from tests.integration.test_cross_set_rollover import KICKOFF

    ids = []
    async with sessions.begin() as session:
        for number in range(count):
            result = await initialize_world(
                session,
                WorldOptions(
                    key=f"group-{number}",
                    seed=number,
                    first_kickoff=KICKOFF,
                ),
                initialized_at=NOW,
            )
            ids.append(result.season_id)
        for sid in ids:
            (await session.get(Season, sid)).status = "active"
        at = (await session.get(Season, ids[0])).ends_at + timedelta(hours=1)

    async def ready(session, season, at):
        leagues = list(
            (
                await session.scalars(
                    select(League)
                    .where(League.league_set_id == season.league_set_id)
                    .order_by(League.tier)
                )
            ).all()
        )
        tables = {
            league.tier: await league_table(session, season.id, league.id) for league in leagues
        }
        return ReadySeason(season, leagues, tables, season.ends_at)

    monkeypatch.setattr(runtime, "load_ready_season", ready)
    monkeypatch.setattr(allocation, "load_ready_season", ready)
    async with sessions.begin() as session:
        await runtime.rollover_season(session, ids[0], at)
        tournaments = (await session.scalars(select(ChampionsTournament))).all()
        for tier in range(1, 6):
            assert sorted(t.participant_count for t in tournaments if t.tier == tier) == group_sizes
        memberships = (
            await session.scalars(select(SeasonClub).join(Season).where(Season.number == 2))
        ).all()
        assert len(memberships) == len({m.club_id for m in memberships}) == count * 50
        assert sorted(
            sum(m.league_id == lid for m in memberships)
            for lid in {m.league_id for m in memberships}
        ) == [10] * (count * 5)
        assert (
            await session.scalar(select(func.count()).select_from(SeasonRolloverReceipt)) == count
        )
        assert not (await runtime.rollover_season(session, ids[-1], at)).created


async def test_cross_set_readers_use_dates_when_new_set_season_number_is_lower(
    sessions, monkeypatch
):
    """An established year-7 manager promotes into a new set's year 2."""
    from dataclasses import replace
    from uuid import uuid4

    import goaletto.application.allocation as allocation
    import goaletto.application.season_rollover as runtime
    from goaletto.application.academy import current_season
    from goaletto.application.friendly import _active_season
    from goaletto.application.market import calendar_for
    from goaletto.application.onboarding import begin_game, claim_club
    from goaletto.application.schedule import load_schedule
    from goaletto.bot.navigation import current_table
    from goaletto.bot.result_views import compact_id

    first, second = await two_aligned_sets(sessions)

    async def ready(session, season, at):
        leagues = list(
            (
                await session.scalars(
                    select(League)
                    .where(League.league_set_id == season.league_set_id)
                    .order_by(League.tier)
                )
            ).all()
        )
        tables = {
            league.tier: await league_table(session, season.id, league.id) for league in leagues
        }
        return ReadySeason(season, leagues, tables, season.ends_at)

    monkeypatch.setattr(runtime, "load_ready_season", ready)
    monkeypatch.setattr(allocation, "load_ready_season", ready)
    async with sessions.begin() as session:
        old = await session.get(Season, first)
        old.number, old.status = 7, "active"
        (await session.get(Season, second)).status = "active"
        at = old.ends_at + timedelta(hours=1)
        # This fixed cohort's tier-5 winners promote across sets. Claim is still
        # tier-5-only; the manager retains that club through the real allocation.
        club_id = (await ready(session, old, at)).tables[5][0].club_id
        entrant = await begin_game(session, telegram_id=1010, update_id=700, payload=None, at=at)
        actor = replace(entrant.actor, operation_id=uuid4(), update_id=701)
        ownership = await claim_club(session, actor, club_id)
        await runtime.rollover_season(session, first, at)
        chosen = await current_season(session, club_id)
        assert chosen.number == 2 and chosen.league_set_id != old.league_set_id
        assert (await _active_season(session, club_id)).id == chosen.id
        assert (await calendar_for(session, club_id, at))[0].id == chosen.id
        screen = await load_schedule(session, replace(actor, operation_id=uuid4()))
        assert screen.ownership_id == ownership.id
        text, keyboard = await current_table(session, replace(actor, operation_id=uuid4()))
        assert compact_id(chosen.id) in str(keyboard)
        assert ownership.club_id == club_id and text
