"""Voluntary friendlies on the free day: explicit schedule, worker play, reduced growth."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from goaletto.application.calendar_planning import plan_horizon
from goaletto.application.friendly import (
    invite_friendly,
    load_friendly_screen,
    respond_friendly,
    schedule_friendly,
)
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.match_runtime import handle_calendar_job
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.schedule import load_schedule
from goaletto.application.training import select_training
from goaletto.domain.game_cycle import cycle_on
from goaletto.domain.training import Focus, Intensity
from goaletto.infrastructure.models import (
    Fixture,
    FixturePlan,
    FriendlyInvitation,
    Job,
    LedgerEntry,
    MatchDevelopment,
    MatchResult,
    OwnershipPeriod,
    Season,
    SeasonClub,
    Standing,
)
from tests.integration.test_agent_wave import NOW, job
from tests.integration.test_candidates import action, funded

pytestmark = pytest.mark.integration


async def _free_day_and_opponent(sessions, home_id):
    from goaletto.infrastructure.models import League

    async with sessions.begin() as session:
        season = await session.scalar(
            select(Season)
            .join(SeasonClub, SeasonClub.season_id == Season.id)
            .where(SeasonClub.club_id == home_id)
            .order_by(Season.number.desc())
            .limit(1)
        )
        assert season is not None and season.cycle_epoch is not None
        epoch = season.cycle_epoch
        season_id = season.id
        opponent = await session.scalar(
            select(SeasonClub.club_id)
            .join(League, League.id == SeasonClub.league_id)
            .where(
                SeasonClub.season_id == season_id,
                SeasonClub.club_id != home_id,
                League.tier == 5,
            )
            .order_by(SeasonClub.club_id)
            .limit(1)
        )
        assert opponent is not None
    for offset in range(1, 11):
        day = NOW.date() + timedelta(days=offset)
        cycle = cycle_on(epoch, day)
        if 0 <= cycle.index < 18 and cycle.phase.value == "free":
            return season_id, opponent, day
    raise AssertionError("No free day inside the horizon")


async def _next_job(session, fixture_id):
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
                }
            ),
        )
        .order_by(Job.due_at, Job.id)
        .limit(1)
    )


async def _play(sessions, fixture_id):
    async with sessions.begin() as session:
        while (row := await _next_job(session, fixture_id)) is not None:
            assert await handle_calendar_job(session, row, row.due_at) != "rescheduled"
            row.status = "done"


async def test_friendly_schedule_materialize_play_and_reduced_growth(sessions):
    _result, actor, home_id = await funded(sessions)
    await job(sessions, "season.open")
    season_id, opponent, day = await _free_day_and_opponent(sessions, home_id)
    async with sessions.begin() as session:
        plan = await schedule_friendly(session, action(actor), opponent, day)
        assert plan.competition == "friendly" and plan.assigned_day == day
        assert plan.league_id is None and not plan.neutral
        retry = await schedule_friendly(session, action(actor), opponent, day)
        assert retry.id == plan.id
        screen = await load_schedule(session, action(actor))
        assert any(
            e.competition == "friendly" and e.day == day for d in screen.days for e in d.entries
        )
        filtered = await load_schedule(session, action(actor), competition="friendly")
        assert any(e.day == day for d in filtered.days for e in d.entries)
        before_played = await session.scalar(select(func.sum(Standing.played))) or 0
    async with sessions.begin() as session:
        made = await plan_horizon(session, season_id, NOW)
        assert made == 0  # the explicit request already announced the fixture
        fixture = await session.get(Fixture, plan.id)
        assert fixture is not None and fixture.competition == "friendly"
        assert fixture.scheduled_start_at.date() == day
    await _play(sessions, plan.id)
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, plan.id)
        assert fixture.status == "finished"
        receipt = await session.scalar(select(MatchResult).where(MatchResult.fixture_id == plan.id))
        assert receipt is not None and receipt.competition == "friendly"
        assert receipt.league_id is None
        assert (await session.scalar(select(func.sum(Standing.played))) or 0) == before_played
        dev = await session.scalar(
            select(MatchDevelopment).where(MatchDevelopment.match_id == receipt.match_id)
        )
        assert dev is not None and dev.eligible and dev.applied_at is not None
        assert dev.terms_snapshot["competition"] == "friendly"
        assert dev.terms_snapshot["friendly_skill_factor"] == 7500
        assert dev.results, "friendly minutes must grant reduced practice, not only fatigue"
        assert all(
            r["skill_factor"] == 7500 and r["competition"] == "friendly" for r in dev.results
        )
        tickets = (
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.kind == "tickets",
                    LedgerEntry.operation_key == f"tickets:{plan.id}",
                )
            )
        ).all()
        assert len(tickets) == 1 and tickets[0].club_id == fixture.home_club_id


async def test_friendly_rejects_training_day_owned_opponent_and_league_day(sessions):
    _result, actor, home_id = await funded(sessions)
    season_id, opponent, day = await _free_day_and_opponent(sessions, home_id)
    midnight = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    async with sessions.begin() as session:
        owner = await session.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == home_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        assert owner is not None
        await select_training(
            session,
            action(actor),
            ownership_id=owner.id,
            week=midnight,
            focus=Focus.TEAM,
            intensity=Intensity.MEDIUM,
            clock=lambda: actor.at,
        )
    async with sessions.begin() as session:
        with pytest.raises(ValueError, match="Training already occupies"):
            await schedule_friendly(session, action(actor), opponent, day)
    async with sessions.begin() as session:
        league_day = await session.scalar(
            select(FixturePlan.assigned_day)
            .where(
                FixturePlan.season_id == season_id,
                FixturePlan.competition == "league",
                (FixturePlan.home_club_id == home_id) | (FixturePlan.away_club_id == home_id),
            )
            .order_by(FixturePlan.assigned_day)
            .limit(1)
        )
        assert league_day is not None
        with pytest.raises(ValueError, match="free day"):
            await schedule_friendly(session, action(actor), opponent, league_day)
    # Owned clubs play friendlies only by invitation, never by forced schedule.
    # Use another free UTC date so the training-day gate does not mask the check.
    async with sessions.begin() as session:
        season = await session.get(Season, season_id)
        assert season is not None and season.cycle_epoch is not None
        epoch = season.cycle_epoch
    second_day = None
    for offset in range(1, 11):
        candidate = NOW.date() + timedelta(days=offset)
        if candidate == day:
            continue
        cycle = cycle_on(epoch, candidate)
        if 0 <= cycle.index < 18 and cycle.phase.value == "free":
            second_day = candidate
            break
    assert second_day is not None
    async with sessions.begin() as session:
        entry = await begin_game(session, telegram_id=2002, update_id=5001, payload=None, at=NOW)
        other_actor = replace(entry.actor, operation_id=uuid4(), update_id=5002)
        other_owner = await claim_club(session, other_actor, opponent)
        assert other_owner.club_id == opponent
    async with sessions.begin() as session:
        with pytest.raises(ValueError, match="only by invitation"):
            await schedule_friendly(session, action(actor), opponent, second_day)
        from goaletto.infrastructure.models import League

        bot = await session.scalar(
            select(SeasonClub.club_id)
            .join(League, League.id == SeasonClub.league_id)
            .where(
                SeasonClub.season_id == season_id,
                SeasonClub.club_id.not_in((home_id, opponent)),
                League.tier == 5,
            )
            .order_by(SeasonClub.club_id)
            .limit(1)
        )
        assert bot is not None
        with pytest.raises(ValueError, match="need no invitation"):
            await invite_friendly(session, action(actor), bot, second_day)


async def _two_owners(sessions):
    """Two owned tier-5 clubs of one season plus a free UTC date."""
    _result, actor_a, home_a = await funded(sessions)
    await job(sessions, "season.open")
    season_id, home_b, day = await _free_day_and_opponent(sessions, home_a)
    async with sessions.begin() as session:
        entry = await begin_game(session, telegram_id=2002, update_id=5001, payload=None, at=NOW)
        actor_b = replace(entry.actor, operation_id=uuid4(), update_id=5002)
        owner_b = await claim_club(session, actor_b, home_b)
        assert owner_b.club_id == home_b
    return actor_a, home_a, actor_b, home_b, season_id, day


async def test_human_challenge_accept_and_full_match(sessions):
    actor_a, _home_a, actor_b, home_b, season_id, day = await _two_owners(sessions)
    async with sessions.begin() as session:
        invite = await invite_friendly(session, action(actor_a), home_b, day)
        assert invite.status == "pending"
        retry = await invite_friendly(session, action(actor_a), home_b, day)
        assert retry.id == invite.id
        cross = await invite_friendly(session, action(actor_b), invite.home_club_id, day)
        assert cross.id == invite.id
        screen_b = await load_friendly_screen(session, action(actor_b))
        assert [c.id for c in screen_b.incoming] == [invite.id]
        screen_a = await load_friendly_screen(session, action(actor_a))
        assert [c.id for c in screen_a.outgoing] == [invite.id]
        before = await session.scalar(select(func.sum(Standing.played))) or 0
    async with sessions.begin() as session:
        accepted = await respond_friendly(session, action(actor_b), invite.id, accept=True)
        assert accepted.status == "accepted" and accepted.plan_id is not None
        again = await respond_friendly(session, action(actor_b), invite.id, accept=True)
        assert again.status == "accepted" and again.plan_id == accepted.plan_id
        plan = await session.get(FixturePlan, accepted.plan_id)
        assert plan is not None and plan.assigned_day == day
    async with sessions.begin() as session:
        made = await plan_horizon(session, season_id, NOW)
        assert made == 0  # the explicit request already announced the fixture
        assert (await session.get(Fixture, accepted.plan_id)) is not None
    await _play(sessions, accepted.plan_id)
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, accepted.plan_id)
        assert fixture is not None and fixture.status == "finished"
        receipt = await session.scalar(
            select(MatchResult).where(MatchResult.fixture_id == accepted.plan_id)
        )
        assert receipt is not None and receipt.competition == "friendly"
        assert (await session.scalar(select(func.sum(Standing.played))) or 0) == before
        dev = await session.scalar(
            select(MatchDevelopment).where(MatchDevelopment.match_id == receipt.match_id)
        )
        assert dev is not None and dev.eligible and dev.results
        assert all(r["skill_factor"] == 7500 for r in dev.results)


async def test_human_decline_cancel_and_stranger_rejections(sessions):
    actor_a, home_a, actor_b, home_b, _season_id, day = await _two_owners(sessions)
    async with sessions.begin() as session:
        invite = await invite_friendly(session, action(actor_a), home_b, day)
        declined = await respond_friendly(session, action(actor_b), invite.id, accept=False)
        assert declined.status == "declined"
        repeat = await respond_friendly(session, action(actor_b), invite.id, accept=True)
        assert repeat.status == "declined" and repeat.plan_id is None
        fresh = await invite_friendly(session, action(actor_a), home_b, day)
        assert fresh.status == "pending" and fresh.id != invite.id
        cancelled = await respond_friendly(session, action(actor_a), fresh.id, accept=False)
        assert cancelled.status == "cancelled"
        assert (
            await respond_friendly(session, action(actor_b), fresh.id, accept=True)
        ).status == "cancelled"
        other = await invite_friendly(session, action(actor_a), home_b, day)
        entry = await begin_game(session, telegram_id=2003, update_id=6001, payload=None, at=NOW)
        stranger = replace(entry.actor, operation_id=uuid4(), update_id=6002)
        with pytest.raises(PermissionDenied):
            await respond_friendly(session, stranger, other.id, accept=True)
        with pytest.raises(PermissionDenied):
            await respond_friendly(session, stranger, other.id, accept=False)
        with pytest.raises(PermissionDenied):
            await respond_friendly(session, action(actor_a), other.id, accept=True)
        assert (await session.get(FriendlyInvitation, other.id)) is not None
        _ = home_a


async def test_human_invite_expiry_and_training_conflict(sessions):
    actor_a, _home_a, actor_b, home_b, _season_id, day = await _two_owners(sessions)
    midnight_day = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    async with sessions.begin() as session:
        invite = await invite_friendly(session, action(actor_a), home_b, day)
        late = replace(action(actor_b), at=midnight_day + timedelta(days=1))
        assert (await respond_friendly(session, late, invite.id, accept=True)).status == "expired"
        # The router commits this outcome, rather than rolling it back on an exception.
        assert (await session.get(FriendlyInvitation, invite.id)).status == "expired"
    async with sessions.begin() as session:
        retry = await invite_friendly(session, action(actor_a), home_b, day)
        assert retry.status == "pending" and retry.id != invite.id
        owner_b = await session.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == home_b, OwnershipPeriod.ended_at.is_(None)
            )
        )
        assert owner_b is not None
        await select_training(
            session,
            action(actor_b),
            ownership_id=owner_b.id,
            week=midnight_day,
            focus=Focus.TEAM,
            intensity=Intensity.MEDIUM,
            clock=lambda: actor_b.at,
        )
        with pytest.raises(ValueError, match="Training already occupies"):
            await respond_friendly(session, action(actor_b), retry.id, accept=True)
        assert (await session.get(FriendlyInvitation, retry.id)).status == "pending"


async def test_invitation_notification_expiry_commits_and_forged_admission_fails(sessions):
    from goaletto.application.friendly import authenticate_fixture
    from goaletto.application.outbox_delivery import Decision, check_recipient, valid_payload
    from goaletto.infrastructure.models import OutboxMessage

    actor_a, _home_a, actor_b, home_b, _sid, day = await _two_owners(sessions)
    async with sessions.begin() as session:
        invite = await invite_friendly(session, action(actor_a), home_b, day)
        notice = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.dedupe_key == f"friendly:invite:{invite.id}")
        )
        assert notice is not None and valid_payload(notice.payload)
        assert await check_recipient(session, notice, NOW) == Decision.DELIVER
        source_owner = await session.get(OwnershipPeriod, invite.challenger_ownership_id)
        source_owner.ended_at, source_owner.end_reason = NOW, "voluntary"
        await session.flush()
        assert (
            await check_recipient(session, notice, NOW + timedelta(minutes=2))
            == Decision.SKIP_STALE
        )
        source_owner.ended_at, source_owner.end_reason = None, None
        await session.flush()
        late = replace(action(actor_b), at=datetime.combine(day, datetime.min.time(), tzinfo=UTC))
        assert (await respond_friendly(session, late, invite.id, accept=True)).status == "expired"
    async with sessions.begin() as session:
        assert (await session.get(FriendlyInvitation, invite.id)).status == "expired"
        assert await session.get(FixturePlan, uuid4()) is None
        notice = await session.get(OutboxMessage, notice.id)
        assert await check_recipient(session, notice, late.at) == Decision.SKIP_STALE
        # A copied league fixture label is not a genuine friendly booking.
        fixture = await session.scalar(select(Fixture).limit(1))
        fixture.competition = "friendly"
        with session.no_autoflush:
            with pytest.raises(ValueError, match="genuine accepted request"):
                await authenticate_fixture(session, fixture)
        session.expunge(fixture)


async def test_previous_owner_invitation_does_not_block_new_owner(sessions):
    from goaletto.infrastructure.models import Club

    actor_a, _home_a, actor_b, home_b, _sid, day = await _two_owners(sessions)
    async with sessions.begin() as session:
        old = await invite_friendly(session, action(actor_a), home_b, day)
        owner = await session.get(OwnershipPeriod, old.challenged_ownership_id)
        owner.ended_at, owner.end_reason = NOW, "inactive"
        (await session.get(Club, home_b)).owner_telegram_id = None
        await session.flush()
        entry = await begin_game(session, telegram_id=2004, update_id=7010, payload=None, at=NOW)
        successor = replace(entry.actor, operation_id=uuid4(), update_id=7011)
        await claim_club(session, successor, home_b)
        fresh = await invite_friendly(session, action(actor_a), home_b, day)
        assert fresh.id != old.id and old.status == "expired"
        assert fresh.challenged_ownership_id != old.challenged_ownership_id
    with pytest.raises(PermissionDenied):
        async with sessions.begin() as session:
            await respond_friendly(session, action(successor), old.id, accept=True)
    with pytest.raises(PermissionDenied):
        async with sessions.begin() as session:
            await respond_friendly(session, action(actor_b), fresh.id, accept=True)
    async with sessions.begin() as session:
        assert (
            await respond_friendly(session, action(successor), fresh.id, accept=True)
        ).status == "accepted"


async def test_telegram_friendly_request_is_explicit_and_replay_safe(sessions, monkeypatch):
    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    import goaletto.bot.friendly_router as module
    from goaletto.bot.main import build_dispatcher
    from tests.integration.test_game_dispatcher import Source, Telegram

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(module, "datetime", Clock)
    _result, _actor, _club = await funded(sessions)
    source = Source(sessions)
    transport = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    sequence = 900

    async def send(data=None, *, search=None):
        nonlocal sequence
        sequence += 1
        if data is None:
            update = Update(
                update_id=sequence,
                message=Message(
                    message_id=sequence,
                    date=NOW,
                    chat=Chat(id=1001, type="private"),
                    from_user=user,
                    text=search or "/friendly",
                    reply_to_message=transport.last_message if search else None,
                ),
            )
        else:
            update = Update(
                update_id=sequence,
                callback_query=CallbackQuery(
                    id=f"friendly-ui-{sequence}",
                    from_user=user,
                    chat_instance="test",
                    message=transport.last_message,
                    data=data,
                ),
            )
        await dispatcher.feed_update(bot, update)
        return transport.last_message

    def button(message, prefix):
        return next(
            b.callback_data
            for row in message.reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith(prefix)
        )

    message = await send()
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(FriendlyInvitation)) == 0
    message = await send(button(message, "fr:c"))
    message = await send(button(message, "fr:d:"))
    # The current UI asks for a name in a ForceReply search before offering clubs.
    _, opponent, _ = await _free_day_and_opponent(sessions, _club)
    async with sessions.begin() as session:
        from goaletto.infrastructure.models import Club

        opponent_name = (await session.get(Club, opponent)).name
    message = await send(search=opponent_name)
    request = button(message, "fr:o:")
    message = await send(request)
    assert "Матч назначен" in message.text
    await send(request)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(FriendlyInvitation)) == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(Fixture).where(Fixture.competition == "friendly")
            )
            == 1
        )
    await bot.session.close()


async def test_adjacent_utc_day_overlap_rejects_request_atomically(sessions):
    from goaletto.application.kickoff_preferences import set_kickoff_preference

    result, actor, home_id = await funded(sessions)
    _sid, away_id, day = await _free_day_and_opponent(sessions, home_id)
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC) - timedelta(minutes=30)
    async with sessions.begin() as session:
        await set_kickoff_preference(session, action(actor), zone="UTC", minute=0)
        # An already scheduled operator fixture can cross into the next UTC day.
        membership = await session.scalar(
            select(SeasonClub).where(
                SeasonClub.season_id == result.season_id, SeasonClub.club_id == home_id
            )
        )
        session.add(
            Fixture(
                id=uuid4(),
                season_id=result.season_id,
                league_id=membership.league_id,
                competition="league",
                round_number=1,
                home_club_id=home_id,
                away_club_id=away_id,
                scheduled_start_at=start,
                lineup_lock_at=start - timedelta(minutes=5),
                status="scheduled",
            )
        )
    with pytest.raises(ValueError, match="fixture_overlap"):
        async with sessions.begin() as session:
            await schedule_friendly(session, action(actor), away_id, day)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(FriendlyInvitation)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(FixturePlan)
                .where(FixturePlan.competition == "friendly")
            )
            == 0
        )
