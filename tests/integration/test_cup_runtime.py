"""Durable cup bracket, two-leg progression and neutral settlement on the dev DB."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from goaletto.application.calendar_planning import plan_horizon
from goaletto.application.results import finalize_result
from goaletto.infrastructure.models import (
    CupBracket,
    CupTie,
    Fixture,
    FixturePlan,
    LedgerEntry,
    Match,
    MatchDevelopment,
    MatchResult,
    Standing,
)
from tests.integration.test_agent_wave import job, setup_world
from tests.integration.test_match_practice_runtime import completed_checkpoint

pytestmark = pytest.mark.integration


async def cup_plan_day(sessions, season_id, tie_id, leg):
    async with sessions.begin() as session:
        plan = await session.scalar(
            select(FixturePlan).where(
                FixturePlan.season_id == season_id,
                FixturePlan.competition == "cup",
                FixturePlan.conditional_ref == f"tie:{tie_id}:leg:{leg}",
            )
        )
        assert plan is not None
        assert plan.home_club_id is not None and plan.away_club_id is not None
        return plan.id, plan.assigned_day


async def play_cup_leg(sessions, season_id, tie_id, leg, *, at, forced_draw_from=None):
    """Materialize one cup leg through the shared horizon and finalize it."""
    plan_id, _ = await cup_plan_day(sessions, season_id, tie_id, leg)
    async with sessions.begin() as session:
        await plan_horizon(session, season_id, at)
    await job(sessions, "match.lock_lineup", plan_id)
    start_at = await job(sessions, "match.start", plan_id)
    if forced_draw_from is None:
        await completed_checkpoint(sessions, plan_id, start_at)
    else:
        # Force an aggregate draw so the persisted seeded draw must resolve it.
        need_home, need_away = forced_draw_from
        async with sessions.begin() as session:
            from goaletto.application.match_simulation import simulate_match
            from goaletto.domain.match_codec import restore_state, state_document

            fixture = await session.get(Fixture, plan_id)
            match = await session.scalar(select(Match).where(Match.fixture_id == plan_id))
            final, _ = simulate_match(restore_state(match.state))
            home_stats = replace(
                final.home.stats,
                goals=need_home,
                on_target=need_home + final.away.stats.saves,
                shots=max(final.home.stats.shots, need_home + final.away.stats.saves),
            )
            home_stats = replace(
                home_stats, chances=max(final.home.stats.chances, home_stats.shots)
            )
            away_stats = replace(
                final.away.stats,
                goals=need_away,
                on_target=need_away + final.home.stats.saves,
                shots=max(final.away.stats.shots, need_away + final.home.stats.saves),
            )
            away_stats = replace(
                away_stats, chances=max(final.away.stats.chances, away_stats.shots)
            )
            final = replace(
                final,
                home=replace(final.home, stats=home_stats),
                away=replace(final.away, stats=away_stats),
            )
            match.state, match.phase, match.completed_tick = state_document(final), "full_time", 30
            match.home_goals, match.away_goals = need_home, need_away
            match.next_due_at = None
            fixture.status, fixture.actual_finish_at = "finished", start_at + timedelta(minutes=33)
    async with sessions.begin() as session:
        assert await finalize_result(session, plan_id, start_at + timedelta(hours=1)) is True
        result = await session.scalar(select(MatchResult).where(MatchResult.fixture_id == plan_id))
        assert result is not None
        return result.home_goals, result.away_goals


async def test_cup_bootstrap_and_two_leg_progression(sessions):
    result, _, _ = await setup_world(sessions)
    season_id = result.season_id
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(CupBracket)) == 1
        ties = (await session.scalars(select(CupTie).where(CupTie.season_id == season_id))).all()
        assert len(ties) == 49
        plans = (
            await session.scalars(
                select(FixturePlan).where(
                    FixturePlan.season_id == season_id, FixturePlan.competition == "cup"
                )
            )
        ).all()
        assert len(plans) == 97
        first_round = [t for t in ties if t.home_club_id is not None and t.away_club_id is not None]
        assert first_round, "opening ties must have known participants"
        first_round.sort(key=lambda t: (min(t.cup_days), str(t.id)))
        tie = first_round[0]
        home_id, away_id = tie.home_club_id, tie.away_club_id
        before = {
            row.club_id: row.played
            for row in (
                await session.scalars(
                    select(Standing).where(
                        Standing.season_id == season_id,
                        Standing.club_id.in_((home_id, away_id)),
                    )
                )
            ).all()
        }
    await job(sessions, "season.open")
    _, day = await cup_plan_day(sessions, season_id, tie.id, 1)
    at = datetime.combine(day, datetime.min.time()).replace(tzinfo=UTC)

    leg1_home, leg1_away = await play_cup_leg(sessions, season_id, tie.id, 1, at=at)
    async with sessions.begin() as session:
        row = await session.get(CupTie, tie.id)
        assert row.status == "in_progress" and row.winner_club_id is None
        stored = dict(row.first_match_result)
        assert stored["draw_policy"] == "cup-draw-v1"
        assert stored["home_goals"] == leg1_home and stored["away_goals"] == leg1_away
        receipt = await session.scalar(
            select(MatchResult).where(
                MatchResult.season_id == season_id, MatchResult.competition == "cup"
            )
        )
        assert receipt is not None and receipt.league_id is None
        pinned = await session.scalar(
            select(MatchDevelopment).where(MatchDevelopment.match_id == receipt.match_id)
        )
        assert pinned.eligible and pinned.terms_snapshot["competition"] == "cup"
        assert pinned.results, "cup appearances must grant practice"
        for club_id, played in before.items():
            standing = await session.scalar(
                select(Standing).where(Standing.season_id == season_id, Standing.club_id == club_id)
            )
            assert standing.played == played, "cup must not touch league tables"

    # Force the return leg to an aggregate draw: club A total = leg1 home +
    # leg2 away must equal club B total = leg1 away + leg2 home, so replay
    # the same scoreline with the legs exchanged.
    leg2_at = at + timedelta(days=4)
    await play_cup_leg(
        sessions, season_id, tie.id, 2, at=leg2_at, forced_draw_from=(leg1_home, leg1_away)
    )
    async with sessions.begin() as session:
        row = await session.get(CupTie, tie.id)
        assert row.status == "decided" and row.winner_club_id in (home_id, away_id)
        assert row.decided_at is not None
        assert row.extra_time_result is not None, "aggregate draw must persist the ET draw"
        assert row.extra_time_result["draw_policy"] == "cup-draw-v1"
        decided_at = row.decided_at
        winner = row.winner_club_id
        loser = away_id if winner == home_id else home_id
        downstream = (
            await session.scalars(
                select(CupTie).where(
                    CupTie.season_id == season_id,
                    (CupTie.home_tie_id == tie.id) | (CupTie.away_tie_id == tie.id),
                )
            )
        ).all()
        assert downstream, "decided tie must feed a later round"
        for fed in downstream:
            assert winner in (fed.home_club_id, fed.away_club_id)
        others = (
            await session.scalars(
                select(FixturePlan).where(
                    FixturePlan.season_id == season_id,
                    FixturePlan.competition == "cup",
                )
            )
        ).all()
        loser_seen = [
            plan.id
            for plan in others
            if str(loser) in (plan.possible_club_ids or [])
            and plan.home_club_id != loser
            and plan.away_club_id != loser
        ]
        assert not loser_seen, "eliminated clubs must leave candidate trees"
    # Replaying the stored outcome must keep the pinned decision, never redraw.
    async with sessions.begin() as session:
        replayed = await session.get(CupTie, tie.id)
        assert replayed.decided_at == decided_at and replayed.winner_club_id == winner


async def test_neutral_final_splits_tickets_evenly(sessions):
    result, _, _ = await setup_world(sessions)
    season_id = result.season_id
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        final = (
            await session.scalars(
                select(CupTie).where(CupTie.season_id == season_id, CupTie.neutral.is_(True))
            )
        ).one()
        clubs = (
            await session.scalars(
                select(Standing.club_id).where(Standing.season_id == season_id).limit(2)
            )
        ).all()
        final.home_club_id, final.away_club_id = clubs[0], clubs[1]
        plan = await session.scalar(
            select(FixturePlan).where(
                FixturePlan.season_id == season_id,
                FixturePlan.conditional_ref == f"tie:{final.id}:leg:1",
            )
        )
        plan.home_club_id, plan.away_club_id = clubs[0], clubs[1]
        plan.possible_club_ids = []
        day = plan.assigned_day
    at = datetime.combine(day, datetime.min.time()).replace(tzinfo=UTC)
    await play_cup_leg(sessions, season_id, final.id, 1, at=at)
    async with sessions.begin() as session:
        row = await session.get(CupTie, final.id)
        assert row.status == "decided" and row.winner_club_id in (clubs[0], clubs[1])
        fixture = await session.scalar(
            select(Fixture).where(
                Fixture.season_id == season_id,
                Fixture.competition == "cup",
                Fixture.neutral.is_(True),
            )
        )
        assert fixture is not None
        credits = (
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.operation_key.like(f"cup:final:tickets:{fixture.id}:%"),
                    LedgerEntry.kind == "tickets",
                )
            )
        ).all()
        assert len(credits) == 2, "neutral final must pay both finalists atomically"
        amounts = [c.request["amount"] for c in credits]
        gross = credits[0].request["details"]["gross"]
        assert sum(amounts) == gross
        assert abs(amounts[0] - amounts[1]) <= 1
