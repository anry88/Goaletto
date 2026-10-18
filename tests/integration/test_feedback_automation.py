"""Internal-only automatic feedback, real PostgreSQL, no Telegram network calls."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.analytics import Surface
from goaletto.application.feedback import (
    answer_with_reason,
    opt_out,
    resume_automatic_prompt,
    save_comment,
    skip_prompt,
)
from goaletto.application.feedback_automation import SCAN_ID, candidate, configure, scan
from goaletto.application.game_actions import user_effect
from goaletto.application.match_runtime import activate_season
from goaletto.application.onboarding import begin_game
from goaletto.application.outbox_delivery import (
    Decision,
    Outcome,
    check_recipient,
    claim_outbox,
    finish_delivery,
    preflight_delivery,
    render_text,
)
from goaletto.application.results import finalize_result
from goaletto.bot import feedback_router
from goaletto.bot.main import build_dispatcher
from goaletto.config import Settings
from goaletto.infrastructure.analytics import exclude_manager
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.feedback_queries import feedback_report
from goaletto.infrastructure.models import (
    AnalyticsManager,
    FeedbackComment,
    FeedbackResponse,
    GameActionReceipt,
    Job,
    Manager,
    OutboxDeliveryAttempt,
    OutboxMessage,
    OwnershipPeriod,
    Season,
)
from goaletto.infrastructure.onboarding_queries import launch_report
from tests.integration.test_agent_wave import start_fixture
from tests.integration.test_feedback_pg import NOW, SETTINGS, _actor, _internal_owner
from tests.integration.test_game_dispatcher import Source, Telegram
from tests.integration.test_match_practice_runtime import completed_checkpoint

pytestmark = pytest.mark.integration
DAY = NOW + timedelta(days=1)


async def initialize(sessions, *, owned=True):
    manager_id, owner_id, club_id, _ = await _internal_owner(sessions)
    if not owned:
        async with sessions.begin() as session:
            entry = await begin_game(
                session, telegram_id=9002, update_id=2, payload=None, at=NOW, internal=True
            )
            manager_id, owner_id = entry.actor.manager_id, None
    async with sessions.begin() as session:
        # Do not make the other helper account a second candidate.
        if not owned:
            other = await session.scalar(select(Manager).where(Manager.telegram_id == 9001))
            other.bot_available = False
        await configure(session, NOW, SETTINGS, enabled=True)
    return manager_id, owner_id, club_id


async def run_scan(sessions, at):
    async with sessions.begin() as session:
        root = await session.get(Job, SCAN_ID, with_for_update=True)
        await scan(session, root, at)


async def prompt_rows(sessions):
    async with sessions.begin() as session:
        return (await session.scalars(select(FeedbackResponse))).all()


@pytest.mark.parametrize(
    "reason", ["blocked", "optout", "prepared", "precoverage", "historic_unknown"]
)
async def test_suppression_and_explicit_disable(sessions, reason):
    manager_id, owner_id, club = await initialize(sessions)
    actor = await _actor(sessions, manager_id, 9001, at=NOW)
    async with sessions.begin() as session:
        manager = await session.get(Manager, manager_id)
        if reason == "blocked":
            manager.bot_available = False
        elif reason == "optout":
            await exclude_manager(session, manager_id, "opt_out", NOW)
        elif reason == "prepared":
            owner = await session.get(OwnershipPeriod, owner_id)
            await user_effect(
                session,
                actor,
                "lineup_saved",
                Surface.SQUAD,
                ownership=owner,
                club_id=club,
                command_id=uuid4(),
            )
        elif reason == "historic_unknown":
            manager.first_game_start_at = None
            await session.flush()
            await begin_game(
                session,
                telegram_id=9001,
                update_id=999,
                payload=None,
                at=NOW + timedelta(hours=1),
                internal=True,
            )
            assert manager.first_game_start_at is None
        else:
            manager.first_game_start_at = NOW - timedelta(seconds=1)
    await run_scan(sessions, DAY)
    assert await prompt_rows(sessions) == []


async def test_disable_preserves_coverage_and_cancels_an_unsent_offer(sessions):
    await initialize(sessions)
    await run_scan(sessions, DAY)
    async with sessions.begin() as session:
        await configure(session, DAY, SETTINGS, enabled=False)
        root = await session.get(Job, SCAN_ID)
        assert root.status == "held" and root.payload["coverage_at"] == NOW.isoformat()
        message = await session.scalar(select(OutboxMessage))
        assert await check_recipient(session, message, DAY) == Decision.SKIP_STALE
        await configure(session, DAY, SETTINGS, enabled=True)
        assert root.payload["coverage_at"] == NOW.isoformat()
    await run_scan(sessions, DAY)
    assert len(await prompt_rows(sessions)) == 1


@pytest.mark.parametrize("owned", [False, True])
async def test_24h_boundary_rollback_restart_and_no_external_pollution(sessions, owned):
    manager_id, owner_id, _ = await initialize(sessions, owned=owned)
    async with sessions.begin() as session:
        await begin_game(session, telegram_id=9100, update_id=100, payload=None, at=NOW)
    await run_scan(sessions, DAY - timedelta(microseconds=1))
    assert await prompt_rows(sessions) == []
    with pytest.raises(RuntimeError, match="rollback"):
        async with sessions.begin() as session:
            root = await session.get(Job, SCAN_ID, with_for_update=True)
            await scan(session, root, DAY)
            raise RuntimeError("rollback")
    assert await prompt_rows(sessions) == []
    await asyncio.gather(run_scan(sessions, DAY), run_scan(sessions, DAY))
    rows = await prompt_rows(sessions)
    assert len(rows) == 1 and rows[0].manager_id == manager_id
    assert rows[0].trigger_kind == "entry_help" and rows[0].ownership_id == owner_id
    await run_scan(sessions, DAY + timedelta(hours=1))
    assert len(await prompt_rows(sessions)) == 1
    async with sessions.begin() as session:
        message = await session.scalar(select(OutboxMessage))
        assert await check_recipient(session, message, DAY) == Decision.DELIVER
        rendered = render_text(message.payload)
        assert rendered.startswith("/lineup" if owned else "/start")
        assert rendered.index("/feedback") > 0 and message.priority == 0
        # Only the external synthetic manager has an analytics identity.
        assert await session.scalar(select(func.count()).select_from(AnalyticsManager)) == 1
        report = await launch_report(session, NOW, DAY, report="match-opportunities")
        assert report["summary"]["total_managers"] == 1
        assert report["summary"]["first"]["eligible_N"] == 0


async def test_return_and_disable_cancel_queued_prompt_without_reopening_campaign(sessions):
    manager_id, owner_id, club = await initialize(sessions)
    await run_scan(sessions, DAY)
    actor = await _actor(sessions, manager_id, 9001, at=DAY + timedelta(seconds=1))
    async with sessions.begin() as session:
        owner = await session.get(OwnershipPeriod, owner_id)
        before = (owner.last_game_action_at, owner.expires_at)
        await user_effect(
            session, actor, "club_opened", Surface.CLUB, ownership=owner, club_id=club
        )
        message = await session.scalar(select(OutboxMessage))
        assert await check_recipient(session, message, actor.at) == Decision.SKIP_STALE
    await run_scan(sessions, DAY + timedelta(hours=1))
    assert len(await prompt_rows(sessions)) == 1
    async with sessions.begin() as session:
        await configure(session, actor.at, SETTINGS, enabled=False)
        assert (await session.get(Job, SCAN_ID)).status == "held"
        owner = await session.get(OwnershipPeriod, owner_id)
        # Only the explicit game action, never the prompt, renews ownership.
        assert owner.last_game_action_at > before[0]


@pytest.mark.parametrize("outcome", ["sent", "unknown", "crash"])
async def test_transport_confirmation_unknown_and_crash_do_not_resend(sessions, outcome):
    manager_id, _, _ = await initialize(sessions)
    await run_scan(sessions, DAY)
    async with sessions.begin() as session:
        claimed = await claim_outbox(session, DAY, lease_owner="fake-sender")
        assert len(claimed) == 1
        message = claimed[0]
        assert await preflight_delivery(
            session, message.id, DAY, lease_owner="fake-sender", attempt=1
        )
    if outcome != "crash":
        async with sessions.begin() as session:
            await finish_delivery(
                session,
                message.id,
                DAY,
                Outcome(outcome),
                lease_owner="fake-sender",
                attempt=1,
                telegram_message_id=123 if outcome == "sent" else None,
            )
    async with sessions.begin() as session:
        assert (
            await claim_outbox(session, DAY + timedelta(minutes=6), lease_owner="restarted") == []
        )
        stored = await session.get(OutboxMessage, message.id)
        response = await session.scalar(select(FeedbackResponse))
        assert stored.status == ("sent" if outcome == "sent" else "failed")
        assert (response.delivered_at is not None) == (outcome == "sent")
        assert await session.scalar(select(func.count()).select_from(OutboxDeliveryAttempt)) == 1
        actor = await _actor(sessions, manager_id, 9001, at=DAY)
        resumed = await resume_automatic_prompt(session, actor, DAY, settings=SETTINGS)
        assert resumed.response_id == response.id


@pytest.mark.parametrize("participation", ["absent", "scoped", "legacy", "lost_owner"])
async def test_only_actual_mature_missed_opportunity_qualifies(sessions, participation):
    manager_id, owner_id, club = await initialize(sessions)
    async with sessions.begin() as session:
        season = await session.scalar(select(Season))
        await activate_season(session, season.id, NOW)
        manager = await session.get(Manager, manager_id)
        # Day 7 is calendar waiting, not a missed opportunity.
        assert await candidate(session, manager, NOW + timedelta(days=3), NOW) is None
    fixture, start = await start_fixture(sessions, club)
    await completed_checkpoint(sessions, fixture, start)
    async with sessions.begin() as session:
        await finalize_result(session, fixture, start + timedelta(minutes=33))
    finish = start + timedelta(minutes=33)
    if participation in {"scoped", "legacy"}:
        actor = await _actor(sessions, manager_id, 9001, at=start)
        async with sessions.begin() as session:
            owner = await session.get(OwnershipPeriod, owner_id)
            if participation == "legacy":
                session.add(
                    GameActionReceipt(
                        operation_id=uuid4(),
                        effect_number=0,
                        manager_id=manager_id,
                        ownership_id=owner_id,
                        club_id=club,
                        kind="match_opened",
                        occurred_at=start,
                        fixture_id=None,  # Historical scope was never recorded.
                    )
                )
            else:
                await user_effect(
                    session,
                    actor,
                    "match_opened",
                    Surface.MATCH,
                    ownership=owner,
                    club_id=club,
                    fixture_id=fixture,
                )
                receipt = await session.get(GameActionReceipt, (actor.operation_id, 0))
                assert receipt.fixture_id == fixture
    if participation == "lost_owner":
        async with sessions.begin() as session:
            owner = await session.get(OwnershipPeriod, owner_id)
            owner.ended_at = finish + timedelta(hours=1)
            owner.end_reason = "voluntary"
    await run_scan(sessions, finish + timedelta(hours=24) - timedelta(microseconds=1))
    assert await prompt_rows(sessions) == []
    await run_scan(sessions, finish + timedelta(hours=24))
    rows = await prompt_rows(sessions)
    if participation == "absent":
        assert len(rows) == 1 and rows[0].trigger_kind == "missed_match"
        assert (rows[0].ownership_id, rows[0].fixture_id) == (owner_id, fixture)
    else:
        assert rows == []


async def test_handcount_feedback_privacy_cleanup_and_readonly_export(sessions):
    first, _, _ = await initialize(sessions)
    async with sessions.begin() as session:
        for uid in (9002, 9003, 9004):
            await begin_game(
                session, telegram_id=uid, update_id=uid, payload=None, at=NOW, internal=True
            )
    await run_scan(sessions, DAY)
    rows = await prompt_rows(sessions)
    assert len(rows) == 4
    by_manager = {row.manager_id: row for row in rows}
    actor = await _actor(sessions, first, 9001, at=DAY)
    secret = "private-feedback-never-export-this"
    async with sessions.begin() as session:
        await answer_with_reason(
            session, actor, response_id=by_manager[first].id, reason_code="other", at=DAY
        )
        await save_comment(session, actor, response_id=by_manager[first].id, text=secret, at=DAY)
        others = (
            await session.scalars(
                select(Manager).where(Manager.id != first).order_by(Manager.telegram_id)
            )
        ).all()
        skipped = replace(actor, manager_id=others[0].id, telegram_id=9002)
        await skip_prompt(session, skipped, response_id=by_manager[skipped.manager_id].id, at=DAY)
        opted = replace(actor, manager_id=others[1].id, telegram_id=9003)
        await opt_out(session, opted, response_id=by_manager[opted.manager_id].id, at=DAY)
        report = await feedback_report(session, NOW, DAY)
        row = report["rows"][0]
        assert (
            row["offered_N"],
            row["answered_n"],
            row["skipped_n"],
            row["opted_out_n"],
            row["waiting"],
        ) == (4, 1, 1, 1, 1)
        assert row["delivery_confirmed"] == 0 and row["delivery_unconfirmed"] == 4
        assert row["answer_rate"] == row["skip_rate"] == 0.25
        assert report["reasons"] == [
            {"campaign": "entry-help-v1", "internal": True, "reason": "other", "answers": 1}
        ]
        assert secret not in json.dumps(report) and str(first) not in json.dumps(report)
        assert report["reason_for_silent_managers"] is None
    # Cleanup/opt-out must not reconstruct a previous erased answer as inactivity.
    async with sessions.begin() as session:
        await opt_out(session, actor, response_id=by_manager[first].id, at=DAY + timedelta(hours=1))
        historical = await feedback_report(session, NOW, DAY)
        assert historical["rows"][0]["redacted_unknown"] == 1
        assert historical["rows"][0]["waiting"] == 1
        expired = await feedback_report(session, NOW, DAY + timedelta(days=2))
        assert expired["rows"][0]["expired_unanswered"] == 1
        assert expired["reasons"] == []
    database = Database(Settings())
    try:
        async with database.read_transaction() as session:
            assert await feedback_report(session, NOW, DAY) == historical
            assert await session.scalar(text("SHOW transaction_read_only")) == "on"
            with pytest.raises(DBAPIError):
                await session.execute(text("DELETE FROM feedback_responses"))
    finally:
        await database.close()


async def test_automatic_offer_resumes_through_real_dispatcher_and_commands_work(
    sessions, monkeypatch
):
    manager_id, _, _ = await initialize(sessions)
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        manager = await session.get(Manager, manager_id)
        manager.first_game_start_at = now - timedelta(hours=25)
    await run_scan(sessions, now)
    response = (await prompt_rows(sessions))[0]
    config = Settings(_env_file=None, feedback_enabled=True, feedback_comment_retention_days=30)
    monkeypatch.setattr(feedback_router, "Settings", lambda: config)
    source = Source(sessions)
    telegram = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=telegram)
    dispatcher = build_dispatcher(
        source, game_enabled=True, internal_telegram_ids=frozenset({9001})
    )
    user = User(id=9001, is_bot=False, first_name="Synthetic")

    async def command(serial, value):
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1000 + serial,
                message=Message(
                    message_id=serial,
                    date=now,
                    chat=Chat(id=9001, type="private"),
                    from_user=user,
                    text=value,
                ),
            ),
        )

    try:
        await command(1, "/feedback")
        message = telegram.last_message
        async with sessions.begin() as session:
            queued = await session.scalar(select(OutboxMessage))
            # Explicitly opening the question first cancels its obsolete notification.
            assert await check_recipient(session, queued, datetime.now(UTC)) == Decision.SKIP_STALE
        data = message.reply_markup.inline_keyboard[0][0].callback_data
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=1002,
                callback_query=CallbackQuery(
                    id="synthetic-feedback-reason",
                    from_user=user,
                    chat_instance="fake",
                    message=message.model_copy(update={"chat": Chat(id=9001, type="private")}),
                    data=data,
                ),
            ),
        )
        await command(3, "/settings")
        async with sessions.begin() as session:
            stored = await session.get(FeedbackResponse, response.id)
            assert stored.status == "answered" and stored.delivered_at is not None
            assert await session.get(FeedbackComment, response.id) is None
            assert await session.scalar(select(func.count()).select_from(FeedbackResponse)) == 1
        await command(4, "explicit synthetic comment")
        async with sessions.begin() as session:
            comment = await session.get(FeedbackComment, response.id)
            assert comment.text == "explicit synthetic comment"
    finally:
        await bot.session.close()
