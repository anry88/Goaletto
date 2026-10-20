"""Real Dispatcher/SQL launch acceptance, fake Telegram, single development DB."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select

from goaletto.application.analytics import Reason, Surface
from goaletto.application.lineup import LineupInput, load_lineup, save_lineup
from goaletto.application.onboarding_measurement import rejection, response_accepted
from goaletto.application.world import initialize_world
from goaletto.bot.main import build_dispatcher
from goaletto.domain.lineup import StarterPick
from goaletto.infrastructure.acquisition_queries import source_funnel
from goaletto.infrastructure.models import (
    AcquisitionFirstTouch,
    AcquisitionTouch,
    JourneyEventRow,
    JourneySession,
    OwnershipPeriod,
)
from goaletto.infrastructure.onboarding_queries import launch_report
from tests.integration.test_agent_wave import NOW, OPTIONS, setup_world
from tests.integration.test_game_dispatcher import Source, Telegram

pytestmark = pytest.mark.integration


async def test_campaign_entry_errors_help_and_manual_ready_through_dispatcher(sessions):
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        await initialize_world(
            session,
            replace(
                OPTIONS,
                first_kickoff=(now + timedelta(days=4)).replace(
                    hour=18, minute=0, second=0, microsecond=0
                ),
            ),
            initialized_at=now,
        )
    campaign_code = "ai_match_prediction"  # The URL needs no operator registration.
    source = Source(sessions)
    telegram = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=telegram)
    dispatcher = build_dispatcher(
        source, game_enabled=True, internal_telegram_ids=frozenset({1004})
    )
    serial = 0

    async def send(uid, text=None, data=None, repeat=None):
        nonlocal serial
        serial += 1
        user = User(id=uid, is_bot=False, first_name="Synthetic")
        if data is None:
            update = Update(
                update_id=serial,
                message=Message(
                    message_id=serial,
                    date=now,
                    chat=Chat(id=uid, type="private"),
                    from_user=user,
                    text=text,
                ),
            )
        else:
            message = telegram.last_message.model_copy(
                update={"chat": Chat(id=uid, type="private")}
            )
            update = Update(
                update_id=serial,
                callback_query=CallbackQuery(
                    id=f"launch-{serial}",
                    from_user=user,
                    chat_instance="fake",
                    message=message,
                    data=data,
                ),
            )
        await dispatcher.feed_update(bot, repeat or update)
        return telegram.last_message, update

    try:
        first, update = await send(1001, f"/start {campaign_code}")
        await send(1001, repeat=update)
        chosen = first.reply_markup.inline_keyboard[0][0].callback_data
        club_code = chosen.split(":")[2]
        await send(1001, data=chosen)
        await send(1001, data=f"en:c:{club_code}")
        await send(1002, "/start private/unknown_payload")
        await send(1002, data=f"en:c:{club_code}")  # Lost claim race.
        await send(1002, data="en:o:1")
        # The claim shortcut is valid; a preview is optional.
        direct_code = telegram.last_message.reply_markup.inline_keyboard[0][0].callback_data.split(
            ":"
        )[2]
        await send(1002, data=f"en:c:{direct_code}")
        await send(1003, "/start")
        await send(1004, f"/start {campaign_code}")
        await send(1001, data="ln:malformed")
        current, _ = await send(1001, "/lineup")
        panel = next(
            b.callback_data
            for row in current.reply_markup.inline_keyboard
            for b in row
            if b.text == "Капитан"
        )
        current, _ = await send(1001, data=panel)
        captain = current.reply_markup.inline_keyboard[0][0].callback_data
        _, saved_update = await send(1001, data=captain)
        await send(1001, repeat=saved_update)  # Transport retry is not a stale user action.
        current, _ = await send(1001, data=captain)  # Old revision, explicit fresh help.
        assert "устарел" in current.text
        panel = next(
            b.callback_data
            for row in current.reply_markup.inline_keyboard
            for b in row
            if b.text == "Капитан"
        )
        current, _ = await send(1001, data=panel)
        # Pick a different captain so this is a real second manual save.
        await send(1001, data=current.reply_markup.inline_keyboard[1][0].callback_data)
        async with sessions.begin() as session:
            events = (await session.scalars(select(JourneyEventRow))).all()
            assert sum(e.name == "manager_started" for e in events) == 3
            assert sum(e.name == "club_preview_opened" for e in events) == 1
            assert sum(e.name == "club_claimed" for e in events) == 2
            assert sum(e.name == "lineup_saved" for e in events) == 2
            errors = [e for e in events if e.name == "action_rejected"]
            assert {e.envelope["reason"] for e in errors} == {
                "club_unavailable",
                "invalid_input",
                "stale_action",
            }
            assert all(not e.user_activity for e in errors)
            assert sum(e.name == "help_delivered" for e in events) == 3
            assert not any("private/unknown_payload" in str(e.envelope) for e in events)
            assert (
                await session.scalar(select(func.count()).select_from(AcquisitionFirstTouch)) == 3
            )
            assert await session.scalar(select(func.count()).select_from(AcquisitionTouch)) == 3
            source_report = await source_funnel(session, now, now + timedelta(days=1, minutes=5))
            rows = {r["kind"]: r for r in source_report.rows}
            assert rows["known"]["placement"] == campaign_code
            assert {k: (v["starts"], v["claimed"], v["ready"]) for k, v in rows.items()} == {
                "known": (1, 1, 1),
                "unknown": (1, 1, 0),
                "direct": (1, 0, 0),
            }
            recovery = await launch_report(
                session, now, now + timedelta(hours=1), report="recovery"
            )
            assert sum(r["eligible_N"] for r in recovery["rows"]) == 3
            assert sum(r["recovered_n"] for r in recovery["rows"]) == 3
            steps = await launch_report(session, now, now + timedelta(hours=1), report="last-steps")
            assert sum(r["managers"] for r in steps["rows"]) == 3
            assert {r["entry_path"] for r in steps["rows"]} == {
                "preview_claim",
                "direct_claim",
                "unclaimed",
            }
            assert all(r["offers_accepted"] == r["managers"] for r in steps["rows"])
            assert {r["last_step"] for r in steps["rows"]} == {
                "lineup_saved",
                "club_claimed",
                "start_accepted",
            }
    finally:
        await bot.session.close()


async def test_failure_rollback_retry_and_background_do_not_renew_or_recover(sessions):
    _, actor, club = await setup_world(sessions)
    rejected = replace(actor, operation_id=uuid4(), update_id=100, at=NOW + timedelta(hours=1))
    async with sessions.begin() as session:
        before = await session.get(
            OwnershipPeriod, (await session.scalar(select(OwnershipPeriod.id)))
        )
        lease = before.last_game_action_at, before.expires_at
        previous_session = await session.scalar(select(func.max(JourneySession.last_action_at)))
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            await rejection(session, rejected, Surface.SQUAD, Reason.LINEUP_INVALID)
            raise RuntimeError("rollback")

    async def fail_once():
        async with sessions.begin() as session:
            return await rejection(session, rejected, Surface.SQUAD, Reason.LINEUP_INVALID)

    assert sorted(await asyncio.gather(fail_once(), fail_once())) == [False, True]
    async with sessions.begin() as session:
        current = await session.get(OwnershipPeriod, before.id)
        assert (current.last_game_action_at, current.expires_at) == lease
        assert (
            await session.scalar(select(func.max(JourneySession.last_action_at)))
            == previous_session
        )
        # No confirmation before successful network => unknown, not delivered.
        waiting = await launch_report(
            session, NOW, rejected.at + timedelta(minutes=29), report="recovery"
        )
        assert waiting["rows"][0]["waiting"] == waiting["rows"][0]["help_unconfirmed"] == 1
        await response_accepted(session, rejected, rejected.at + timedelta(seconds=1))
        await response_accepted(session, rejected, rejected.at + timedelta(seconds=2))
        assert (
            await session.scalar(
                select(func.count())
                .select_from(JourneyEventRow)
                .where(JourneyEventRow.name == "help_delivered")
            )
            == 1
        )
        unchanged = await session.get(OwnershipPeriod, before.id)
        assert (unchanged.last_game_action_at, unchanged.expires_at) == lease
        later = replace(
            actor, operation_id=uuid4(), update_id=101, at=rejected.at + timedelta(minutes=30)
        )
        data = await load_lineup(session, later)
        draft = LineupInput(
            tuple(StarterPick(p.person_id, p.slot_position) for p in data.starters),
            tuple(p.person_id for p in data.bench),
            data.formation,
            data.starters[0].person_id,
            data.tactic,
            data.mentality,
            data.version,
        )
        await save_lineup(session, later, draft, clock=lambda: later.at)
        result = await launch_report(
            session, NOW, later.at + timedelta(seconds=1), report="recovery"
        )
        assert result["rows"][0]["eligible_N"] == 1
        assert result["rows"][0]["recovered_n"] == 0  # Exactly +30min is outside.


async def test_real_match_opportunities_late_action_future_plans_and_ownership(sessions):
    from goaletto.application.analytics import JourneyEvent, Origin
    from goaletto.application.game_actions import user_effect
    from goaletto.application.results import finalize_result
    from goaletto.infrastructure.analytics import record_event
    from goaletto.infrastructure.models import Fixture, FixturePlan
    from tests.integration.test_agent_wave import job, start_fixture
    from tests.integration.test_match_practice_runtime import completed_checkpoint

    _, actor, club = await setup_world(sessions)
    first_id, first_at = await start_fixture(sessions, club)
    await completed_checkpoint(sessions, first_id, first_at)
    async with sessions.begin() as session:
        await finalize_result(session, first_id, first_at + timedelta(minutes=33))
        ownership = await session.scalar(select(OwnershipPeriod))
        active = replace(actor, at=first_at, operation_id=uuid4(), update_id=100)
        await user_effect(
            session,
            active,
            "match_opened",
            Surface.MATCH,
            ownership=ownership,
            club_id=club,
            fixture_id=first_id,
        )
        first_report_at = first_at + timedelta(days=1, minutes=33)
        report = await launch_report(session, NOW, first_report_at, report="match-opportunities")
        rows = {(r["stage"], r["state"]): r for r in report["rows"]}
        assert rows[("first", "mature")]["participated_n"] == 1
        assert rows[("second", "waiting")]["managers"] == 1
        second = await session.scalar(
            select(Fixture)
            .where(
                Fixture.competition == "league",
                Fixture.id != first_id,
                (Fixture.home_club_id == club) | (Fixture.away_club_id == club),
            )
            .order_by(Fixture.scheduled_start_at)
            .limit(1)
        )
        second_id = second.id
        # Unannounced plans (no invented kickoff) still prove a future opportunity.
        # Only inspect; the real second fixture is needed for the next acceptance.
        count = await session.scalar(
            select(func.count())
            .select_from(FixturePlan)
            .where(FixturePlan.competition == "league", FixturePlan.announced_at.is_(None))
        )
        assert count > 0
    await job(sessions, "match.lock_lineup", second_id)
    second_at = await job(sessions, "match.start", second_id)
    await completed_checkpoint(sessions, second_id, second_at)
    at = second_at + timedelta(days=1, minutes=33)
    async with sessions.begin() as session:
        await finalize_result(session, second_id, second_at + timedelta(minutes=33))
        # Delivery is system activity and cannot prove manager participation.
        await response_accepted(session, actor, second_at, offer=True)
        event = JourneyEvent(
            event_id=uuid4(),
            name="match_report_opened",
            origin=Origin.USER,
            operation_id=uuid4(),
            effect_number=0,
            surface=Surface.REPORT,
            occurred_at=second_at + timedelta(minutes=35),
            recorded_at=at + timedelta(hours=1),
            analytics_manager_id=actor.analytics_id,
            session_id=uuid4(),
            club_id=club,
            ownership_id=ownership.id,
            fixture_id=second_id,
        )
        await record_event(session, event)
        old = await launch_report(session, NOW, at, report="match-opportunities")
        rows = {(r["stage"], r["state"]): r for r in old["rows"]}
        assert rows[("first", "mature")]["participated_n"] == 1
        assert rows[("second", "mature")]["participated_n"] == 0
        assert rows[("second", "mature")]["unknown_late"] == 1
        # Compare the SQL projection with the previously accepted pure policy,
        # as well as the literal 1/1 first, 0/1 second + one late observation.
        from goaletto.application.match_opportunities import (
            CoverageState,
            FixtureProjection,
            ManagerProjection,
            OwnershipProjection,
            UserEffectProjection,
            evaluate_match_opportunities,
        )

        fixtures = (
            await session.scalars(select(Fixture).where(Fixture.id.in_((first_id, second_id))))
        ).all()
        effects = (
            await session.scalars(
                select(JourneyEventRow).where(
                    JourneyEventRow.name.in_(("match_opened", "match_report_opened"))
                )
            )
        ).all()
        pure = evaluate_match_opportunities(
            managers=(ManagerProjection(actor.manager_id, False, True),),
            ownerships=(
                OwnershipProjection(
                    ownership.id, actor.manager_id, club, ownership.claimed_at, None
                ),
            ),
            fixtures=tuple(
                FixtureProjection(
                    f.id,
                    club,
                    "league",
                    f.status,
                    f.scheduled_start_at,
                    f.lineup_lock_at,
                    f.actual_start_at,
                    f.actual_finish_at,
                    True,
                )
                for f in fixtures
            ),
            effects=tuple(
                UserEffectProjection(
                    str(e.id),
                    actor.manager_id,
                    UUID(e.envelope["fixture_id"]),
                    UUID(e.envelope["ownership_id"]),
                    e.name,
                    e.origin,
                    e.occurred_at,
                    e.recorded_at,
                )
                for e in effects
            ),
            coverage=CoverageState(True, True, True),
            report_at=at,
        )
        assert (pure.first.eligible_N, pure.first.participated_n) == (1, 1)
        assert (pure.second.eligible_N, pure.second.participated_n, pure.second.unknown_late) == (
            1,
            0,
            1,
        )
        assert old["summary"]["first"]["eligible_N"] == pure.first.eligible_N
        assert old["summary"]["second"]["participated_n"] == pure.second.participated_n
        fresh = await launch_report(
            session, NOW, at + timedelta(hours=1), report="match-opportunities"
        )
        rows = {(r["stage"], r["state"]): r for r in fresh["rows"]}
        assert rows[("second", "mature")]["participated_n"] == 1
        assert rows[("second", "mature")]["unknown_late"] == 0
        # Historical ownership loss is a separate group, never a silent non-participant.
        period = await session.get(OwnershipPeriod, ownership.id)
        period.ended_at = at - timedelta(minutes=1)
        period.end_reason = "inactive"
        await session.flush()
        lost = await launch_report(session, NOW, at, report="match-opportunities")
        rows = {(r["stage"], r["state"]): r for r in lost["rows"]}
        assert rows[("second", "interrupted")]["managers"] == 1


async def test_launch_reports_read_only_and_empty_calendar(sessions):
    from sqlalchemy import text

    from goaletto.application.onboarding import begin_game

    _, actor, club = await setup_world(sessions)
    failure = replace(actor, operation_id=uuid4(), at=NOW + timedelta(hours=1))
    async with sessions.begin() as session:
        await rejection(session, failure, Surface.SQUAD, Reason.STALE_ACTION)
        period = await session.scalar(select(OwnershipPeriod))
        # An expired period is no longer eligible even if the expiry worker has
        # not projected ended_at yet. Use a future true expiry, never shorten it.
        at = period.expires_at + timedelta(seconds=1)
        other = await begin_game(session, telegram_id=1002, update_id=500, payload=None, at=at)
        # A new manager with no club is no_calendar, not a failed opportunity.
        report = await launch_report(session, at, at, report="match-opportunities")
        assert report["summary"]["first"] == {"eligible_N": 0, "participated_n": 0, "rate": None}
        assert report["rows"][0]["state"] == "no_calendar"
        assert other.actor.manager_id != actor.manager_id
    async with sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        for name in ("last-steps", "recovery", "match-opportunities"):
            report = await launch_report(session, NOW, NOW + timedelta(days=1), report=name)
            assert "telegram_id" not in str(report)
            assert "ownership_id" not in str(report)


async def test_recovery_never_crosses_ownership_even_for_same_manager_and_club(sessions):
    from goaletto.application.onboarding import begin_game, claim_club
    from goaletto.infrastructure.models import Club

    _, actor, club = await setup_world(sessions)
    failed = replace(actor, operation_id=uuid4(), at=NOW + timedelta(minutes=1))
    async with sessions.begin() as session:
        await rejection(session, failed, Surface.SQUAD, Reason.STALE_ACTION)
        period = await session.scalar(select(OwnershipPeriod))
        period.ended_at = failed.at + timedelta(seconds=1)
        period.end_reason = "inactive"
        (await session.get(Club, club)).owner_telegram_id = None
        await session.flush()
        returned = await begin_game(
            session,
            telegram_id=actor.telegram_id,
            update_id=800,
            payload=None,
            at=failed.at + timedelta(minutes=2),
        )
        claimant = replace(returned.actor, operation_id=uuid4(), update_id=801)
        new = await claim_club(session, claimant, club)
        assert new.id != period.id
        saving = replace(
            claimant, operation_id=uuid4(), update_id=802, at=failed.at + timedelta(minutes=3)
        )
        data = await load_lineup(session, saving)
        draft = LineupInput(
            tuple(StarterPick(p.person_id, p.slot_position) for p in data.starters),
            tuple(p.person_id for p in data.bench),
            data.formation,
            data.starters[0].person_id,
            data.tactic,
            data.mentality,
            data.version,
        )
        await save_lineup(session, saving, draft, clock=lambda: saving.at)
        report = await launch_report(session, NOW, NOW + timedelta(hours=1), report="recovery")
        assert report["rows"][0]["eligible_N"] == 1
        assert report["rows"][0]["recovered_n"] == 0


async def test_failed_telegram_help_remains_unconfirmed_and_retry_is_one_error(sessions):
    from aiogram.methods import EditMessageText

    await setup_world(sessions)
    source = Source(sessions)

    class TimeoutTelegram(Telegram):
        failing = True

        async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
            if self.failing and isinstance(method, EditMessageText):
                assert self.source.depth == 0
                raise TimeoutError("Synthetic uncertain Telegram response")
            return await super().make_request(bot, method, timeout)

    telegram = TimeoutTelegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=telegram)
    dispatcher = build_dispatcher(source, game_enabled=True)
    message = Message(message_id=77, date=NOW, chat=Chat(id=1001, type="private"), text="Squad")
    update = Update(
        update_id=900,
        callback_query=CallbackQuery(
            id="failed-response",
            from_user=User(id=1001, is_bot=False, first_name="Synthetic"),
            chat_instance="fake",
            message=message,
            data="ln:malformed",
        ),
    )
    try:
        with pytest.raises(TimeoutError):
            await dispatcher.feed_update(bot, update)
        async with sessions.begin() as session:
            errors = await session.scalar(
                select(func.count())
                .select_from(JourneyEventRow)
                .where(JourneyEventRow.name == "action_rejected")
            )
            help_count = await session.scalar(
                select(func.count())
                .select_from(JourneyEventRow)
                .where(JourneyEventRow.name == "help_delivered")
            )
            assert (errors, help_count) == (1, 0)
        telegram.failing = False
        await dispatcher.feed_update(bot, update)
        async with sessions.begin() as session:
            errors = await session.scalar(
                select(func.count())
                .select_from(JourneyEventRow)
                .where(JourneyEventRow.name == "action_rejected")
            )
            help_count = await session.scalar(
                select(func.count())
                .select_from(JourneyEventRow)
                .where(JourneyEventRow.name == "help_delivered")
            )
            assert (errors, help_count) == (1, 1)
    finally:
        await bot.session.close()
