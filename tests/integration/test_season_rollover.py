"""Full finalized seasons, atomic rollover and history on the single Goaletto DB."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from goaletto.application.candidates import load_candidates, sign_candidate
from goaletto.application.market import load_market, place_bid
from goaletto.application.match_runtime import (
    activate_season,
    handle_calendar_job,
)
from goaletto.application.match_simulation import simulate_match
from goaletto.application.outbox_delivery import Decision, check_recipient
from goaletto.application.results import finalize_result, league_table
from goaletto.application.season_rollover import rollover_season
from goaletto.domain.match_codec import state_document
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.models import (
    Auction,
    Club,
    Fixture,
    FixturePlan,
    Job,
    League,
    LineupSlot,
    Match,
    MoneyReservation,
    OutboxMessage,
    OwnershipPeriod,
    Person,
    PersonSeasonEvent,
    Player,
    Season,
    SeasonClub,
    SeasonRolloverReceipt,
    Staff,
    StaffCareer,
    StaffQualification,
    Standing,
)
from tests.integration.test_agent_wave import NOW
from tests.integration.test_candidates import action, funded
from tests.integration.test_market import market_world

pytestmark = pytest.mark.integration


async def complete_season(sessions, season_id):
    """Real engine/finalizer for league and cup in chronological UTC order."""
    from datetime import UTC, datetime

    from goaletto.application.calendar_planning import plan_horizon
    from goaletto.domain.match_codec import restore_state

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
    historical = {}
    latest = season.ends_at
    for day in range(72):
        at = datetime.combine(epoch + timedelta(days=day), datetime.min.time(), tzinfo=UTC)
        async with sessions.begin() as session:
            await plan_horizon(session, season_id, at)
            fixtures = (
                await session.scalars(
                    select(Fixture)
                    .where(
                        Fixture.season_id == season_id,
                        Fixture.scheduled_start_at >= at,
                        Fixture.scheduled_start_at < at + timedelta(days=1),
                    )
                    .order_by(Fixture.scheduled_start_at, Fixture.id)
                )
            ).all()
            for f in fixtures:
                if f.status == "finished":
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
                historical[f.id] = match.starting_snapshot
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
        rollover_job = await session.scalar(
            select(Job).where(
                Job.kind == "season.rollover", Job.payload["season_id"].astext == str(season_id)
            )
        )
        rollover_job.status, rollover_job.due_at = "pending", latest
    return latest, historical


async def test_full_rollover_concurrent_worker_refunds_aging_calendar_history(sessions):
    from goaletto.application.academy import ensure_prospects
    from goaletto.infrastructure.models import AcademyCandidate, AcademyProspect

    people, auction_id, person_id = await market_world(sessions)
    seller, seller_club, _ = people[0]
    async with sessions.begin() as session:
        auction = await session.get(Auction, auction_id)
        season_id = auction.season_id
        prospects = await ensure_prospects(
            session, await session.get(Club, seller_club), await session.get(Season, season_id)
        )
        academy_profiles = {p.id: (p.name, p.skills.copy(), p.caps.copy()) for p in prospects}
        person = await session.get(Person, person_id)
        face = person.appearance_identity_id
        await place_bid(
            session, action(people[1][0]), people[1][2], auction_id, 100000, clock=lambda: NOW
        )
        person.age = 37  # Rollover will announce the guaranteed last season at 38.
        memberships = dict(
            (
                await session.execute(
                    select(SeasonClub.club_id, SeasonClub.league_id).where(
                        SeasonClub.season_id == season_id
                    )
                )
            ).all()
        )
        ages = dict((await session.execute(select(Person.id, Person.age))).all())
        archived_decisions = await session.scalar(
            select(func.count()).select_from(PersonSeasonEvent)
        )
    at, historical = await complete_season(sessions, season_id)

    async def run():
        async with sessions.begin() as session:
            return await process_one_job(session, at=at)

    assert sum(await asyncio.wait_for(asyncio.gather(run(), run()), 60)) == 1
    async with sessions.begin() as session:
        receipt = await session.get(SeasonRolloverReceipt, season_id)
        assert receipt is not None
        assert (await session.get(Season, season_id)).status == "rolled_over"
        next_season = await session.get(Season, receipt.next_season_id)
        assert next_season.number == 2 and next_season.status == "scheduled"
        assert next_season.starts_at >= at + timedelta(days=8)
        graduates = (await session.scalars(select(AcademyCandidate))).all()
        assert {p.id: (p.name, p.skills, p.caps) for p in graduates} == academy_profiles
        assert all(p.season_id == next_season.id for p in graduates)
        assert all(
            p.graduation_season_id == next_season.id
            for p in (await session.scalars(select(AcademyProspect))).all()
        )
        replay = await rollover_season(session, season_id, at + timedelta(hours=1))
        assert not replay.created and replay.next_season_id == next_season.id
        assert replay.aged + replay.retired == 1000
        assert replay.retired > 0 and replay.replacements > 0 and replay.warned > 0
        careers = (await session.scalars(select(StaffCareer))).all()
        assert len(careers) == replay.retired
        assert any(c.role == "physiotherapist" for c in careers)
        for career in careers:
            person = await session.get(Person, career.person_id)
            assert (
                person.career == "staff"
                and (await session.get(Player, person.id)).status == "retired"
            )
            assert (
                await session.get(StaffQualification, (person.id, career.role))
            ).skill == career.skill
            assert person.age == ages[person.id]  # No extra year in the career change.
            assert career.origin_club_id in memberships
        members = (
            await session.scalars(select(SeasonClub).where(SeasonClub.season_id == next_season.id))
        ).all()
        assert len(members) == len({m.club_id for m in members}) == 50
        tiers = dict((await session.execute(select(League.id, League.tier))).all())
        for league_id in tiers:
            assert sum(m.league_id == league_id for m in members) == 10
        for tier in range(1, 6):
            league_id = next(key for key, value in tiers.items() if value == tier)
            table = await league_table(session, season_id, league_id)
            new_leagues = {m.club_id: m.league_id for m in members}
            assert tiers[new_leagues[table[0].club_id]] == max(1, tier - 1)
            assert tiers[new_leagues[table[1].club_id]] == max(1, tier - 1)
            assert tiers[new_leagues[table[-1].club_id]] == min(5, tier + 1)
            assert tiers[new_leagues[table[-2].club_id]] == min(5, tier + 1)
            assert all(tiers[new_leagues[r.club_id]] == tier for r in table[2:8])
            from goaletto.domain.sponsorship import sponsor_terms
            from goaletto.infrastructure.models import SponsorContract

            for place, row in enumerate(table, 1):
                sponsor = await session.scalar(
                    select(SponsorContract).where(
                        SponsorContract.club_id == row.club_id,
                        SponsorContract.season_id == next_season.id,
                    )
                )
                club = await session.get(Club, row.club_id)
                destination = tiers[new_leagues[row.club_id]]
                assert sponsor.source_season_id == season_id
                assert sponsor.basis == "season_result" and sponsor.place == place
                assert sponsor.tier == destination and sponsor.supporters == club.supporters
                assert (
                    sponsor.cycle_rate
                    == sponsor_terms(destination, place, club.supporters).cycle_rate
                )
                assert sponsor.starts_at == at and sponsor.ends_at is None
                old = await session.scalar(
                    select(SponsorContract).where(
                        SponsorContract.club_id == club.id,
                        SponsorContract.season_id == season_id,
                    )
                )
                assert old.ends_at == at
        assert (
            dict(
                (
                    await session.execute(
                        select(SeasonClub.club_id, SeasonClub.league_id).where(
                            SeasonClub.season_id == season_id
                        )
                    )
                ).all()
            )
            == memberships
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(FixturePlan)
                .where(FixturePlan.season_id == next_season.id, FixturePlan.competition == "league")
            )
            == 450
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.payload["season_id"].astext == str(next_season.id),
                    Job.status == "pending",
                    ~Job.kind.like("match.%"),
                )
            )
            == 3
        )
        assert all(
            s.played == s.points == 0
            for s in (
                await session.scalars(select(Standing).where(Standing.season_id == next_season.id))
            ).all()
        )
        assert (await session.get(Auction, auction_id)).close_reason == "last_season"
        assert not (
            await session.scalars(
                select(MoneyReservation).where(MoneyReservation.status == "active")
            )
        ).all()
        player = await session.get(Player, person_id)
        person = await session.get(Person, person_id)
        assert person.age == 38 and person.appearance_identity_id == face
        assert player.status == "last_season" and player.last_season_id == next_season.id
        for person_id_, age in ages.items():
            old_player = await session.get(Player, person_id_)
            current = await session.get(Person, person_id_)
            profile = old_player if old_player is not None else await session.get(Staff, person_id_)
            assert current.age == age + (profile.status != "retired")
        notices = (
            await session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.payload["kind"].astext == "player_lifecycle"
                )
            )
        ).all()
        assert notices and all(
            [await check_recipient(session, n, at) == Decision.DELIVER for n in notices]
        )
        # Retry does not redraw, recreate youth, re-refund or rewrite old snapshots.
        assert (
            await session.scalar(select(func.count()).select_from(PersonSeasonEvent))
            > archived_decisions
        )
        for f_id, doc in historical.items():
            match = await session.scalar(select(Match).where(Match.fixture_id == f_id))
            assert match.starting_snapshot == doc
        original_owner = await session.get(OwnershipPeriod, people[0][2])
        assert original_owner.last_game_action_at == NOW


async def test_unfinished_season_defers_without_mutations(sessions):
    result, _, _ = await funded(sessions)
    async with sessions.begin() as session:
        work = await session.scalar(select(Job).where(Job.kind == "season.rollover"))
        await session.execute(update(Job).where(Job.id != work.id).values(status="done"))
        work.status = "pending"
        await session.flush()
        count = await session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "pending", Job.due_at <= work.due_at)
        )
        assert count == 1, (work.status, work.due_at, count)
        due = work.due_at
        assert not await process_one_job(session, at=due)
        assert work.due_at == due + timedelta(seconds=60)
        assert work.status == "pending" and work.last_error == "not_ready"
        assert (await session.get(Season, result.season_id)).status == "scheduled"
        assert not (await session.scalars(select(SeasonRolloverReceipt))).all()
        assert await session.scalar(select(func.count()).select_from(Person)) == 1050


async def test_rollover_fault_rolls_everything_back_and_retry_succeeds(sessions, monkeypatch):
    result, _, _ = await funded(sessions)
    at, _ = await complete_season(sessions, result.season_id)
    import goaletto.application.season_rollover as module

    original = module._install_lineups

    async def fail(*args, **kwargs):
        raise RuntimeError("injected crash after aging and refunds")

    monkeypatch.setattr(module, "_install_lineups", fail)
    with pytest.raises(RuntimeError, match="injected crash"):
        async with sessions.begin() as session:
            await rollover_season(session, result.season_id, at)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(Season)) == 1
        assert await session.scalar(select(func.count()).select_from(Person)) == 1050
        assert await session.scalar(select(func.count()).select_from(LineupSlot)) == 1000
        assert (await session.get(Season, result.season_id)).status == "active"
    monkeypatch.setattr(module, "_install_lineups", original)
    async with sessions.begin() as session:
        assert (await rollover_season(session, result.season_id, at)).created


async def test_global_candidates_and_staff_warn_full_season_then_retire(sessions):
    result, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        players = await load_candidates(session, action(actor), "player")
        staff = await load_candidates(session, action(actor), "staff")
        item = staff.items[0]
        await sign_candidate(
            session, action(actor), staff.ownership_id, item.person.id, item.offer.fee, at=NOW
        )
        person = await session.get(Person, item.person.id)
        person.age = 69
        face = person.appearance_identity_id
        free_ages = {i.person.id: i.person.age for i in players.items}
    at, _ = await complete_season(sessions, result.season_id)
    async with sessions.begin() as session:
        first = await rollover_season(session, result.season_id, at)
        assert first.staff_aged == 65 and first.staff_warned == 1 and first.staff_retired == 0
        worker = await session.get(Staff, item.person.id)
        assert worker.status == "last_season" and worker.last_season_id == first.next_season_id
        assert (await session.get(Person, worker.person_id)).age == 70
        for person_id, initial_age in free_ages.items():
            assert (await session.get(Person, person_id)).age == initial_age + 1
        next_season = await session.get(Season, first.next_season_id)
        active_actor = replace(action(actor), at=next_season.starts_at - timedelta(days=1))
        # An explicit owner action, rather than rollover, renews the ownership lease.
        await load_market(session, active_actor)
        first_next_id = first.next_season_id
    at2, _ = await complete_season(sessions, first_next_id)
    async with sessions.begin() as session:
        second = await rollover_season(session, first_next_id, at2)
        assert second.staff_retired == 1
        worker = await session.get(Staff, item.person.id)
        person = await session.get(Person, item.person.id)
        assert worker.status == "retired" and worker.club_id is None and worker.role is None
        assert person.age == 70 and person.appearance_identity_id == face
        assert not (await rollover_season(session, first_next_id, at2)).created
