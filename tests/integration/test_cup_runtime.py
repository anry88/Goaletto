"""Actual worker cup games: 97 fixtures, real ET, shootouts and pinned neutral arena."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.calendar_planning import plan_horizon
from goaletto.application.cup import initialize_season_cup
from goaletto.application.knockout_prizes import backfill_current_prizes, unpaid_current_prizes
from goaletto.application.match_runtime import NotReady, handle_calendar_job
from goaletto.application.results import finalize_result
from goaletto.application.season_rollover import rollover_season
from goaletto.domain.match_codec import restore_state
from goaletto.infrastructure.models import (
    CupBracket,
    CupTie,
    Fixture,
    FixturePlan,
    Job,
    LedgerEntry,
    Match,
    MatchDevelopment,
    MatchResult,
    MatchTick,
    NeutralFinalBooking,
    Season,
    Standing,
)
from tests.integration.test_agent_wave import NOW, job, setup_world

pytestmark = pytest.mark.integration


async def next_job(session, fixture_id):
    return await session.scalar(
        select(Job)
        .where(
            Job.payload["fixture_id"].astext == str(fixture_id),
            Job.status == "pending",
            Job.kind.in_(
                {
                    "match.remind",
                    "match.lock_lineup",
                    "match.start",
                    "match.tick",
                    "match.second_half_start",
                    "match.extra_start",
                    "match.extra_second_half_start",
                    "match.penalty",
                }
            ),
        )
        .order_by(Job.due_at, Job.id)
        .limit(1)
    )


async def play_fixture(sessions, fixture_id, *, separate=False):
    if separate:
        while True:
            async with sessions.begin() as session:
                row = await next_job(session, fixture_id)
                if row is None:
                    break
                outcome = await handle_calendar_job(session, row, row.due_at)
                assert outcome != "rescheduled"
                row.status = "done"
    else:
        async with sessions.begin() as session:
            while (row := await next_job(session, fixture_id)) is not None:
                outcome = await handle_calendar_job(session, row, row.due_at)
                assert outcome != "rescheduled"
                row.status = "done"
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, fixture_id)
        assert fixture.status == "finished"
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        assert await session.get(MatchResult, match.id)
        return match.id


async def finish_cup(sessions, season_id):
    async with sessions.begin() as session:
        season = await session.get(Season, season_id)
        epoch = season.cycle_epoch
    for cycle in range(17):
        at = datetime.combine(epoch + timedelta(days=4 * cycle), datetime.min.time(), tzinfo=UTC)
        async with sessions.begin() as session:
            await plan_horizon(session, season_id, at)
            plans = (
                await session.scalars(
                    select(FixturePlan.id)
                    .where(
                        FixturePlan.season_id == season_id,
                        FixturePlan.competition == "cup",
                        FixturePlan.assigned_day == at.date(),
                    )
                    .order_by(FixturePlan.id)
                )
            ).all()
        for fixture in plans:
            await play_fixture(sessions, fixture)


async def test_full_cup_97_real_games_neutral_final_retry_and_frozen_history(sessions, monkeypatch):
    world, _, _ = await setup_world(sessions)
    async with sessions.begin() as session:
        row = await session.get(CupBracket, world.season_id)
        assert len(row.entry_snapshot) == 5
        assert (
            await initialize_season_cup(session, world.season_id, NOW)
        ).season_id == row.season_id
        assert await session.scalar(select(func.count()).select_from(CupTie)) == 49
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        season = await session.get(Season, world.season_id)
        with pytest.raises(NotReady, match="Cup has unfinished ties"):
            await rollover_season(session, world.season_id, season.ends_at)
    import goaletto.application.cup as cup

    original_credit = cup.credit_locked
    final_credits = 0

    async def fail_second_credit(*args, **kwargs):
        nonlocal final_credits
        result = await original_credit(*args, **kwargs)
        final_credits += 1
        if final_credits == 2:
            raise RuntimeError("second finalist credit")
        return result

    monkeypatch.setattr(cup, "credit_locked", fail_second_credit)
    with pytest.raises(RuntimeError, match="second finalist credit"):
        await finish_cup(sessions, world.season_id)
    async with sessions.begin() as session:
        final_fixture = await session.scalar(select(Fixture).where(Fixture.neutral.is_(True)))
        final_id = final_fixture.id
        assert final_fixture.status != "finished"
        assert not await session.scalar(
            select(LedgerEntry.id).where(
                LedgerEntry.operation_key.like(f"cup:final:tickets:{final_id}:%")
            )
        )
        assert not await session.scalar(
            select(MatchResult.match_id).where(MatchResult.fixture_id == final_id)
        )
    monkeypatch.setattr(cup, "credit_locked", original_credit)
    await play_fixture(sessions, final_id, separate=True)
    async with sessions.begin() as session:
        ties = (await session.scalars(select(CupTie))).all()
        assert len(ties) == 49 and all(t.status == "decided" for t in ties)
        results = (await session.scalars(select(MatchResult))).all()
        assert len(results) == 97 and all(
            r.competition == "cup" and r.league_id is None for r in results
        )
        assert await session.scalar(select(func.sum(Standing.played))) == 0
        final = next(t for t in ties if t.neutral)
        fixture = await session.scalar(select(Fixture).where(Fixture.neutral.is_(True)))
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture.id))
        starting = restore_state(match.starting_snapshot)
        assert starting.home.support_morale_points == starting.away.support_morale_points == 0
        booking = await session.get(NeutralFinalBooking, fixture.id)
        assert booking.venue_club_id not in (fixture.home_club_id, fixture.away_club_id)
        credits = (
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.operation_key.like(f"cup:final:tickets:{fixture.id}:%")
                )
            )
        ).all()
        assert len(credits) == 2
        assert sum(c.request["amount"] for c in credits) == booking.basis["gross"]
        assert abs(credits[0].request["amount"] - credits[1].request["amount"]) <= 1
        all_tickets = (
            await session.scalars(select(LedgerEntry).where(LedgerEntry.kind == "tickets"))
        ).all()
        assert len(all_tickets) == 98
        import json
        from pathlib import Path

        await asyncio.to_thread(
            Path("work/cup-intake/full-cup-finances.json").write_text,
            json.dumps(
                {
                    "matches": 97,
                    "ticket_credits": len(all_tickets),
                    "total_cup_tickets": sum(c.request["amount"] for c in all_tickets),
                    "neutral_crowd": booking.basis["crowd"],
                    "neutral_capacity": booking.basis["capacity"],
                    "neutral_gross": booking.basis["gross"],
                    "final_payouts": [c.request["amount"] for c in credits],
                }
            ),
        )
        assert not await finalize_result(
            session, fixture.id, fixture.actual_finish_at + timedelta(hours=1)
        )
        assert final.winner_club_id is not None
        prizes = (
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.operation_key == f"cup:prize:{world.season_id}"
                )
            )
        ).all()
        assert len(prizes) == len({p.club_id for p in prizes}) == 50
        assert sum(p.cash_delta for p in prizes) == 4_058_000
        assert next(p.cash_delta for p in prizes if p.club_id == final.winner_club_id) == 500_000
        assert sorted(p.cash_delta for p in prizes if p.request["details"]["stage"] == "final") == [
            320_000,
            500_000,
        ]
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MatchDevelopment)
                .where(MatchDevelopment.applied_at.is_not(None), MatchDevelopment.eligible)
            )
            == 97
        )
        from goaletto.infrastructure.models import SupporterChange

        assert booking.basis["policy_version"] == "neutral-final-v2"
        assert booking.basis["demand_bps"] > 8000
        assert await session.scalar(select(func.count()).select_from(SupporterChange)) == 194
        audience = await session.scalar(
            select(SupporterChange).where(
                SupporterChange.match_id == match.id,
                SupporterChange.club_id == final.winner_club_id,
            )
        )
        assert audience.after_count > audience.before_count
        # Conditional branches have all resolved without invented entrants.
        assert not await session.scalar(
            select(FixturePlan.id)
            .where(FixturePlan.competition == "cup", FixturePlan.possible_club_ids != [])
            .limit(1)
        )
    for statement in (
        "UPDATE cup_brackets SET seed=seed+1",
        "UPDATE cup_ties SET winner_club_id=NULL WHERE status='decided'",
        "UPDATE neutral_final_bookings SET basis='{}'::jsonb",
    ):
        with pytest.raises(DBAPIError):
            async with sessions.begin() as session:
                await session.execute(text(statement))


async def test_cup_backfill_only_pays_confirmed_elimination_once(sessions, monkeypatch):
    import goaletto.application.cup as cup

    async def previous_release(session, prizes, at):
        pass

    monkeypatch.setattr(cup, "pay_prizes", previous_release)
    world, _, _ = await setup_world(sessions)
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        tie = await session.scalar(
            select(CupTie).where(CupTie.round_key == "prelim").order_by(CupTie.id).limit(1)
        )
        legs = [uuid5(world.season_id, f"cup:{tie.id}:{leg}") for leg in (1, 2)]
        season = await session.get(Season, world.season_id)
        before = season.ends_at
    await play_fixture(sessions, legs[0])
    async with sessions.begin() as session:
        assert await unpaid_current_prizes(session, before) == []
    match_id = await play_fixture(sessions, legs[1])
    async with sessions.begin() as session:
        tie = await session.get(CupTie, tie.id)
        receipt = await session.get(MatchResult, match_id)
        state = (await session.get(Match, match_id)).state
        prizes = await unpaid_current_prizes(session, before)
        assert len(prizes) == 1
        prize = prizes[0]
        assert {prize.club_id, tie.winner_club_id} == {tie.home_club_id, tie.away_club_id}
        assert prize.amount == 12_000 and prize.details["state_hash"] == receipt.state_hash
        assert await backfill_current_prizes(session, before, before) == prizes
        assert await backfill_current_prizes(session, before, before) == []
        assert (await session.get(Match, match_id)).state == state
    async with sessions.begin() as session:
        rows = (await session.scalars(select(LedgerEntry).where(LedgerEntry.kind == "prize"))).all()
        assert [(row.club_id, row.cash_delta) for row in rows] == [(prize.club_id, 12_000)]


async def test_real_et_penalties_restart_recovery_growth_and_rollback(sessions, monkeypatch):
    import goaletto.application.match_development as development
    import goaletto.application.match_runtime as runtime
    from goaletto.domain.match_types import EnginePolicy

    monkeypatch.setattr(
        runtime,
        "EnginePolicy",
        lambda **kwargs: EnginePolicy(
            **kwargs, base_chance_rate_bps=0, injury_bps=0, yellow_bps=0, direct_red_bps=0
        ),
    )
    world, actor, owned = await setup_world(sessions)
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        tie = await session.scalar(
            select(CupTie).where(CupTie.round_key == "prelim").order_by(CupTie.id).limit(1)
        )
        plans = (
            await session.scalars(
                select(FixturePlan)
                .where(
                    FixturePlan.conditional_ref.in_([f"tie:{tie.id}:leg:1", f"tie:{tie.id}:leg:2"])
                )
                .order_by(FixturePlan.assigned_day)
            )
        ).all()
        first, second = plans[0].id, plans[1].id
        if owned not in (tie.home_club_id, tie.away_club_id):
            from goaletto.application.onboarding import begin_game, claim_club

            entry = await begin_game(session, telegram_id=1002, update_id=20, payload=None, at=NOW)
            actor = replace(entry.actor, operation_id=uuid4(), update_id=21)
            owner = await claim_club(session, actor, tie.home_club_id)
            owned = owner.club_id
    await play_fixture(sessions, first, separate=True)
    await job(sessions, "match.remind", second)
    await job(sessions, "match.lock_lineup", second)
    await job(sessions, "match.start", second)
    for tick in range(1, 31):
        if tick == 16:
            await job(sessions, "match.second_half_start", second)
        await job(sessions, "match.tick", second, tick)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == second))
        assert match.phase == "extra_break"
        assert await session.get(MatchResult, match.id) is None
        assert (await session.get(MatchDevelopment, match.id)).applied_at is None
        from goaletto.application.live_commands import accept_live_command, load_live_screen

        at = match.next_due_at - timedelta(seconds=1)
        actor = replace(actor, at=at, operation_id=uuid4(), update_id=22)
        screen = await load_live_screen(
            session, actor, match_id=match.id, clock=lambda: at, record=False
        )
        team = (
            screen.state.home if owned == screen.state.home.snapshot.club_id else screen.state.away
        )
        outgoing, incoming = team.lineup[1].player_id, team.bench[0]
        assert screen.target == 31 and screen.halftime and screen.open
        accepted = await accept_live_command(
            session,
            actor,
            match_id=match.id,
            ownership_id=screen.ownership_id,
            target=31,
            halftime=True,
            kind="substitution",
            payload={"outgoing_id": str(outgoing), "incoming_id": str(incoming)},
            clock=lambda: at,
        )
        assert accepted.status == "accepted"
    await job(sessions, "match.extra_start", second)
    for tick in range(31, 41):
        if tick == 36:
            await job(sessions, "match.extra_second_half_start", second)
        await job(sessions, "match.tick", second, tick)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == second))
        assert match.phase == "penalties"
        assert (
            await session.scalar(
                select(func.count()).select_from(MatchTick).where(MatchTick.match_id == match.id)
            )
            == 40
        )
        state = restore_state(match.state)
        original_minutes = {
            c.player_id: c.minutes_played for t in (state.home, state.away) for c in t.conditions
        }
        assert max(original_minutes.values()) == 120
        assert original_minutes[outgoing] == 90 and original_minutes[incoming] == 30
        penalty_job = await next_job(session, second)
        # A late worker preserves a full next-kick window, no burst of missed kicks.
        assert (
            await handle_calendar_job(session, penalty_job, penalty_job.due_at + timedelta(hours=1))
            == "rescheduled"
        )
        await session.refresh(penalty_job)
        assert match.next_due_at == penalty_job.due_at
    original = development.apply_development

    async def fail(session, state, at):
        await original(session, state, at)
        raise RuntimeError("after practice")

    monkeypatch.setattr(development, "apply_development", fail)
    while True:
        try:
            async with sessions.begin() as session:
                row = await next_job(session, second)
                await handle_calendar_job(session, row, row.due_at)
                row.status = "done"
        except RuntimeError as exc:
            assert str(exc) == "after practice"
            break
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == second))
        assert match.phase == "penalties" and await session.get(MatchResult, match.id) is None
        assert (await session.get(MatchDevelopment, match.id)).applied_at is None
        row = await next_job(session, second)
        final_job = row.id
    monkeypatch.setattr(development, "apply_development", original)
    await play_fixture(sessions, second, separate=True)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == second))
        state = restore_state(match.state)
        assert {
            c.player_id: c.minutes_played for t in (state.home, state.away) for c in t.conditions
        } == original_minutes
        assert match.home_goals == match.away_goals == 0
        receipt = await session.get(MatchDevelopment, match.id)
        assert all(r["minutes"] == original_minutes[UUID(r["person_id"])] for r in receipt.results)
        assert max(r["minutes"] for r in receipt.results) == 120
        from goaletto.domain.training import age_multiplier

        assert receipt.policy_version == "match-practice-v5"
        for row in receipt.results:
            profiles = receipt.terms_snapshot["experience_roster"][row["person_id"]]["profiles"]
            expected = 0
            for term in row["position_terms"]:
                position = term["position"]
                nominal = 1000 * age_multiplier(row["age"]) * term["minutes"] // (10000 * 90)
                gain = nominal * 100 // (1 if position in profiles else 10)
                previous = row["position_experience"][position]["before"]
                expected += min(gain, 1000000 - previous)
            assert row["experience_gain"] == expected
        assert (await session.get(CupTie, tie.id)).shootout_result is not None
        saved = list(receipt.results)
        replay = await session.get(Job, final_job)
        assert await handle_calendar_job(session, replay, replay.due_at) is None
        assert receipt.results == saved


async def test_parallel_draw_retry_rejects_invented_final_and_wrong_league(sessions):
    import asyncio

    from goaletto.infrastructure.models import League

    world, _, _ = await setup_world(sessions)

    async def read_draw():
        async with sessions.begin() as session:
            row = await initialize_season_cup(session, world.season_id, NOW)
            return row.entry_snapshot

    draws = await asyncio.wait_for(asyncio.gather(read_draw(), read_draw()), 20)
    assert draws[0] == draws[1]
    async with sessions.begin() as session:
        final = await session.scalar(select(CupTie).where(CupTie.neutral.is_(True)))
        opening = await session.scalar(select(CupTie).where(CupTie.round_key == "prelim").limit(1))
        final_id, home, away = final.id, opening.home_club_id, opening.away_club_id
        wrong_league = await session.scalar(select(League.id).where(League.tier == 1))
        season = await session.get(Season, world.season_id)
        at = season.starts_at
        from goaletto.application.cup import context_for_fixture

        second = await session.get(Fixture, uuid5(world.season_id, f"cup:{opening.id}:2"))
        with pytest.raises(NotReady, match="first result"):
            await context_for_fixture(session, second)
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE cup_ties SET home_club_id=:home WHERE id=:id"),
                {"home": home, "id": final_id},
            )
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text(
                    "UPDATE cup_ties SET status='decided',winner_club_id=:home,"
                    "decided_at=:at WHERE id=:id"
                ),
                {"home": home, "id": final_id, "at": at},
            )
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            session.add(
                Fixture(
                    id=uuid4(),
                    season_id=world.season_id,
                    league_id=wrong_league,
                    competition="league",
                    home_club_id=home,
                    away_club_id=away,
                    round_number=1,
                    scheduled_start_at=at,
                    lineup_lock_at=at - timedelta(minutes=1),
                    status="scheduled",
                )
            )
            await session.flush()


async def test_operator_recovered_cup_plan_keeps_authenticated_tie(sessions):
    from goaletto.application.calendar_planning import recover_unannounced_plan
    from goaletto.application.cup import context_for_fixture
    from goaletto.application.match_runtime import activate_season
    from goaletto.application.world import initialize_world
    from tests.integration.test_agent_wave import OPTIONS

    async with sessions.begin() as session:
        world = await initialize_world(session, OPTIONS, initialized_at=NOW)
        plan = await session.scalar(
            select(FixturePlan)
            .where(FixturePlan.competition == "cup", FixturePlan.round_number == 1)
            .order_by(FixturePlan.id)
            .limit(1)
        )
        assert plan.announced_at is None
        moved = datetime.combine(plan.assigned_day + timedelta(days=8), datetime.min.time(), UTC)
        await recover_unannounced_plan(session, plan.id, moved, NOW)
        await activate_season(session, world.season_id, NOW + timedelta(days=1))
        fixture = await session.get(Fixture, plan.id)
        assert fixture is not None and fixture.scheduled_start_at.date() == moved.date()
        context = await context_for_fixture(session, fixture)
        assert context.segment == "leg1"
        assert context.tie_id == UUID(plan.conditional_ref.split(":")[1])
