"""Champions cups end to end: qualification, brackets, worker play, gates."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, update

from goaletto.application.calendar_planning import plan_horizon
from goaletto.application.champions import initialize_season_champions
from goaletto.application.match_runtime import NotReady, activate_season, handle_calendar_job
from goaletto.application.match_simulation import simulate_match
from goaletto.application.results import finalize_result, league_table
from goaletto.application.season_rollover import rollover_season
from goaletto.domain.match_codec import restore_state, state_document
from goaletto.infrastructure.models import (
    ChampionsDraw,
    ChampionsTie,
    ChampionsTournament,
    Fixture,
    FixturePlan,
    Job,
    League,
    LedgerEntry,
    Match,
    MatchDevelopment,
    MatchDiscipline,
    MatchResult,
    Season,
    Standing,
)
from tests.integration.test_agent_wave import NOW
from tests.integration.test_candidates import funded

pytestmark = pytest.mark.integration


async def play_season(sessions, season_id, *, skip=frozenset(), only=None):
    """Real engine/finalizer in chronological order, like the rollover helper."""
    return await play_cohort(sessions, [season_id], skip=skip, only=only)


async def play_cohort(sessions, season_ids, *, skip=frozenset(), only=None):
    """Play all sets together so guest appearances never rewind recovery time."""
    for season_id in season_ids:
        await open_season(sessions, season_id)
    async with sessions.begin() as session:
        season = await session.get(Season, season_ids[0])
        epoch = season.cycle_epoch
        latest = season.ends_at
    for day in range(72):
        at = datetime.combine(epoch + timedelta(days=day), datetime.min.time(), tzinfo=UTC)
        async with sessions.begin() as session:
            for sid in season_ids:
                await plan_horizon(session, sid, at)
            fixtures = (
                await session.scalars(
                    select(Fixture)
                    .where(
                        Fixture.season_id.in_(season_ids),
                        Fixture.scheduled_start_at >= at,
                        Fixture.scheduled_start_at < at + timedelta(days=1),
                    )
                    .order_by(Fixture.scheduled_start_at, Fixture.id)
                )
            ).all()
            for f in fixtures:
                if f.status == "finished":
                    continue
                if f.competition in skip or (only is not None and f.competition not in only):
                    continue
                for kind in ("match.lock_lineup", "match.start"):
                    work = await session.scalar(
                        select(Job).where(
                            Job.kind == kind, Job.payload["fixture_id"].astext == str(f.id)
                        )
                    )
                    await handle_calendar_job(session, work, work.due_at)
                    work.status = "done"
                match = await session.scalar(select(Match).where(Match.fixture_id == f.id))
                initial = restore_state(match.state)
                final, _ = simulate_match(initial)
                assert final.phase.value == "full_time"
                penalty_minutes = (
                    len(final.knockout.shootout.kicks)
                    if final.knockout and final.knockout.shootout
                    else 0
                )
                duration = 33 if final.tick == 30 else 45 + penalty_minutes
                f.status, f.actual_finish_at = (
                    "finished",
                    f.actual_start_at + timedelta(minutes=duration),
                )
                match.state, match.phase, match.completed_tick = (
                    state_document(final),
                    "full_time",
                    final.tick,
                )
                match.home_goals, match.away_goals = final.home.stats.goals, final.away.stats.goals
                match.next_due_at = None
                await session.flush()
                assert await finalize_result(session, f.id, f.actual_finish_at)
                latest = max(latest, f.actual_finish_at + timedelta(seconds=1))
    async with sessions.begin() as session:
        await session.execute(update(Job).values(status="done"))
    return latest


async def open_season(sessions, season_id):
    async with sessions.begin() as session:
        season = await session.get(Season, season_id)
        epoch = season.cycle_epoch
        if season.status == "scheduled":
            await activate_season(
                session,
                season_id,
                datetime.combine(epoch, datetime.min.time(), tzinfo=UTC) - timedelta(days=1),
            )
            work = await session.scalar(
                select(Job).where(
                    Job.kind == "season.open", Job.payload["season_id"].astext == str(season_id)
                )
            )
            await handle_calendar_job(session, work, work.due_at)
            work.status = "done"


async def rollover_next(sessions, season_id, at):
    async with sessions.begin() as session:
        result = await rollover_season(session, season_id, at)
        assert result.created
        return result.next_season_id


async def test_first_season_has_no_champions_then_full_second_season(sessions):
    result, _, _ = await funded(sessions)
    season1 = result.season_id
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(ChampionsDraw)) == 0
        assert await session.scalar(select(func.count()).select_from(ChampionsTournament)) == 0
        assert await session.scalar(select(func.count()).select_from(ChampionsTie)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(FixturePlan)
                .where(FixturePlan.competition == "champions")
            )
            == 0
        )
    latest = await play_season(sessions, season1)
    season2 = await rollover_next(sessions, season1, latest + timedelta(hours=1))
    async with sessions.begin() as session:
        draw = await session.scalar(select(ChampionsDraw).where(ChampionsDraw.season_id == season2))
        assert draw is not None and draw.qualifying_season_id == season1
        assert draw.policy_version == "champions-groups-v1"
        tournaments = (
            await session.scalars(
                select(ChampionsTournament)
                .where(ChampionsTournament.season_id == season2)
                .order_by(ChampionsTournament.tier)
            )
        ).all()
        assert [t.tier for t in tournaments] == [1, 2, 3, 4, 5]
        assert all(t.participant_count == 8 for t in tournaments)
        assert await session.scalar(select(func.count()).select_from(ChampionsTie)) == 5 * 7
        plans = (
            await session.scalars(
                select(FixturePlan).where(
                    FixturePlan.season_id == season2, FixturePlan.competition == "champions"
                )
            )
        ).all()
        assert len(plans) == 5 * 13
        season = await session.get(Season, season2)
        assert season is not None and season.cycle_epoch is not None
        for plan in plans:
            offset = (plan.assigned_day - season.cycle_epoch).days
            assert offset % 4 == 1 and 1 <= offset // 4 + 1 <= 5
        first_round = [p for p in plans if p.round_number == 1]
        assert len(first_round) == 5 * 4
        assert all(
            p.home_club_id
            and p.away_club_id
            and {UUID(c) for c in p.possible_club_ids} == {p.home_club_id, p.away_club_id}
            for p in first_round
        )
        later = [p for p in plans if p.round_number > 1]
        assert later and all(
            p.possible_club_ids for p in later if not (p.home_club_id and p.away_club_id)
        )
        played_zero = {
            row.club_id: row.played
            for row in (
                await session.scalars(select(Standing).where(Standing.season_id == season2))
            ).all()
        }
        assert set(played_zero.values()) == {0}
        season1_row = await session.get(Season, season1)
        assert season1_row is not None
        quals = list(
            (
                await session.scalars(
                    select(League)
                    .where(League.league_set_id == season1_row.league_set_id)
                    .order_by(League.tier)
                )
            ).all()
        )
        qtables = {league.tier: await league_table(session, season1, league.id) for league in quals}
        repeat = await initialize_season_champions(session, season2, season1, qtables, quals, NOW)
        assert [t.id for t in repeat] == [t.id for t in tournaments]
        assert await session.scalar(select(func.count()).select_from(ChampionsTie)) == 35
    latest2 = await play_season(sessions, season2)
    async with sessions.begin() as session:
        results = (
            await session.scalars(
                select(MatchResult).where(
                    MatchResult.season_id == season2, MatchResult.competition == "champions"
                )
            )
        ).all()
        assert len(results) == 65 and all(r.league_id is None for r in results)
        discipline = list(
            await session.scalars(
                select(MatchDiscipline).where(
                    MatchDiscipline.match_id.in_([r.match_id for r in results])
                )
            )
        )
        assert len(discipline) == 65 and all(r.applied_at for r in discipline)
        assert {r.scope for r in discipline} == {f"champions:{t.id}" for t in tournaments}
        devs = (
            await session.scalars(
                select(MatchDevelopment).where(
                    MatchDevelopment.match_id.in_([r.match_id for r in results])
                )
            )
        ).all()
        assert len(devs) == 65 and all(d.eligible and d.applied_at for d in devs)
        assert all(d.terms_snapshot["competition"] == "champions" for d in devs)
        assert all(d.results for d in devs)
        finals = (
            await session.scalars(
                select(Fixture).where(
                    Fixture.season_id == season2,
                    Fixture.competition == "champions",
                    Fixture.neutral.is_(True),
                )
            )
        ).all()
        assert len(finals) == 5
        for final in finals:
            credits = (
                await session.scalars(
                    select(LedgerEntry).where(
                        LedgerEntry.operation_key.like(f"champions:final:tickets:{final.id}:%")
                    )
                )
            ).all()
            assert len(credits) == 2
            assert abs(credits[0].request["amount"] - credits[1].request["amount"]) <= 1
        decided = (
            await session.scalars(select(ChampionsTie).where(ChampionsTie.season_id == season2))
        ).all()
        assert len(decided) == 35 and all(t.status == "decided" for t in decided)
        assert len([t for t in decided if t.neutral]) == 5
        assert all(t.winner_club_id in (t.home_club_id, t.away_club_id) for t in decided)
        for tournament in tournaments:
            prizes = (
                await session.scalars(
                    select(LedgerEntry).where(
                        LedgerEntry.operation_key == f"champions:prize:{tournament.id}"
                    )
                )
            ).all()
            assert len({p.club_id for p in prizes}) == 8
            assert sorted(p.cash_delta for p in prizes) == [
                12_000,
                12_000,
                12_000,
                12_000,
                79_000,
                79_000,
                280_000,
                450_000,
            ]
        played = {
            row.club_id: row.played
            for row in (
                await session.scalars(select(Standing).where(Standing.season_id == season2))
            ).all()
        }
        assert all(value == 18 for value in played.values())
    season3 = await rollover_next(sessions, season2, latest2 + timedelta(hours=1))
    async with sessions.begin() as session:
        draw3 = await session.scalar(
            select(ChampionsDraw).where(ChampionsDraw.season_id == season3)
        )
        assert draw3 is not None and draw3.qualifying_season_id == season2
        assert draw3.id != draw.id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(FixturePlan)
                .where(FixturePlan.season_id == season3, FixturePlan.competition == "champions")
            )
            == 65
        )


async def test_champions_gate_blocks_rollover_until_decided(sessions):
    result, _, _ = await funded(sessions)
    season1 = result.season_id
    latest = await play_season(sessions, season1)
    season2 = await rollover_next(sessions, season1, latest + timedelta(hours=1))
    await open_season(sessions, season2)
    # Remove the independent ordinary-cup gate from this focused gate check;
    # the full chronological scenario above covers actual completion of both.
    from goaletto.application.allocation import load_ready_season

    original_get = None
    async with sessions.begin() as session:
        season = await session.get(Season, season2)
        original_get = session.get

        async def without_cup(model, key, **kwargs):
            from goaletto.infrastructure.models import CupBracket

            if model is CupBracket:
                return None
            return await original_get(model, key, **kwargs)

        session.get = without_cup
        with pytest.raises(NotReady, match="Champions"):
            await load_ready_season(session, season, season.ends_at + timedelta(hours=1))
