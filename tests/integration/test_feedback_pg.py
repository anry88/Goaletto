"""PostgreSQL voluntary-feedback flow for Codex handoff. Resets the dev Goaletto DB.

Run only on the agreed player-free Goaletto database after saving the world::

    GOALETTO_ALLOW_DEVELOPMENT_RESET=1 python -m pytest -q -m integration \\
        tests/integration/test_feedback_pg.py

Covers one prompt per manager/campaign, duplicate callbacks, stale/foreign
scope, skip/opt-out terminal, 7-day cooldown, exact 30-minute/500-char
boundaries, restart idempotency, comment cleanup, ownership preservation, and
restricted-text separation. No real accounts, polling, or соседние services.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from goaletto.application.feedback import (
    CooldownActive,
    FeedbackSettings,
    answer_with_reason,
    cleanup_comments,
    ensure_campaign,
    find_open_comment,
    open_prompt,
    opt_out,
    prompt_outbox_id,
    record_prompt_delivered,
    save_comment,
    skip_prompt,
)
from goaletto.application.game_actions import Actor, PermissionDenied
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.world import initialize_world
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.models import (
    Club,
    FeedbackComment,
    FeedbackResponse,
    JourneyEventRow,
    Manager,
    OwnershipPeriod,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 17, 18, tzinfo=UTC)
SETTINGS = FeedbackSettings(enabled=True, comment_retention_days=30, cooldown_days=7)


async def _internal_owner(sessions, telegram_id: int = 9001):  # type: ignore[no-untyped-def]
    async with sessions.begin() as session:
        await initialize_world(
            session,
            WorldOptions(key="feedback", seed=0, first_kickoff=KICKOFF, initial_budget=50000),
            initialized_at=NOW,
        )
        entry = await begin_game(
            session, telegram_id=telegram_id, update_id=1, payload=None, at=NOW, internal=True
        )
        assert entry.actor.analytics_id is None
        manager_id = entry.actor.manager_id
        actor = replace(entry.actor, operation_id=uuid4(), update_id=4)
        ownership = await claim_club(session, actor, entry.offers[0].id)
        ownership_id, club_id = ownership.id, ownership.club_id
        deadline = (ownership.last_game_action_at, ownership.expires_at)
    return manager_id, ownership_id, club_id, deadline


async def _actor(sessions, manager_id, telegram_id: int, operation=None, at=NOW):  # type: ignore[no-untyped-def]
    async with sessions.begin() as session:
        manager = await session.get(Manager, manager_id)
        assert manager is not None
    return Actor(manager_id, telegram_id, None, operation or uuid4(), at)


async def test_manual_prompt_answer_comment_idempotency_and_delivery(sessions):  # type: ignore[no-untyped-def]
    manager_id, _, _, _ = await _internal_owner(sessions)
    actor = await _actor(sessions, manager_id, 9001)
    async with sessions.begin() as session:
        first = await open_prompt(session, actor, slug="manual-v1", settings=SETTINGS, at=NOW)
        assert first.created and first.status == "prompted"
    # Retry with a different operation stays on the same durable prompt.
    actor2 = await _actor(sessions, manager_id, 9001, at=NOW + timedelta(seconds=5))
    async with sessions.begin() as session:
        second = await open_prompt(
            session, actor2, slug="manual-v1", settings=SETTINGS, at=NOW + timedelta(seconds=5)
        )
        assert second.response_id == first.response_id and not second.created
    # Delivery recording is a separate idempotent step after transport confirmation.
    async with sessions.begin() as session:
        assert await record_prompt_delivered(
            session,
            response_id=first.response_id,
            outbox_id=prompt_outbox_id(first.response_id),
            at=NOW + timedelta(seconds=6),
        )
    async with sessions.begin() as session:
        assert not await record_prompt_delivered(
            session,
            response_id=first.response_id,
            outbox_id=prompt_outbox_id(first.response_id),
            at=NOW + timedelta(seconds=7),
        )
    # Answer once; a repeated reason click creates no second response/event.
    async with sessions.begin() as session:
        answered = await answer_with_reason(
            session, actor, response_id=first.response_id, reason_code="long_wait", at=NOW
        )
        assert answered.applied and answered.reason == "long_wait"
    async with sessions.begin() as session:
        repeat = await answer_with_reason(
            session, actor2, response_id=first.response_id, reason_code="other", at=NOW
        )
        assert not repeat.applied and repeat.duplicate and repeat.reason == "long_wait"
        stored = await session.get(FeedbackResponse, first.response_id)
        assert stored is not None and stored.reason == "long_wait"
    # Comment inside the exact 30-minute edge; repeat delivery is safe.
    secret = "отзыв-" + "ы" * 10
    async with sessions.begin() as session:
        saved = await save_comment(
            session,
            actor,
            response_id=first.response_id,
            text=secret,
            at=NOW + timedelta(minutes=30),
        )
        assert saved.applied
    async with sessions.begin() as session:
        again = await save_comment(
            session,
            actor2,
            response_id=first.response_id,
            text=secret,
            at=NOW + timedelta(minutes=30),
        )
        assert not again.applied and again.duplicate
        row = await session.get(FeedbackComment, first.response_id)
        assert row is not None and row.text == secret.strip()
    # Free text never lands in journey envelopes.
    async with sessions.begin() as session:
        rows = (await session.scalars(select(JourneyEventRow))).all()
        blob = "|".join(str(r.envelope) for r in rows)
        assert secret not in blob and "отзыв-" not in blob


async def test_boundaries_skip_optout_cooldown_and_cleanup(sessions):  # type: ignore[no-untyped-def]
    manager_id, ownership_id, club_id, (last_action, expires) = await _internal_owner(
        sessions, telegram_id=9002
    )
    actor = await _actor(sessions, manager_id, 9002)
    async with sessions.begin() as session:
        prompt = await open_prompt(session, actor, slug="manual-v1", settings=SETTINGS, at=NOW)
    # Stale/foreign scope is rejected without mutation.
    foreign = await _actor(sessions, manager_id, 9002)
    async with sessions.begin() as session:
        with pytest.raises(PermissionDenied):
            await answer_with_reason(
                session, foreign, response_id=uuid4(), reason_code="other", at=NOW
            )
    # 501 chars rejected; exactly 500 accepted after a fresh answer window.
    async with sessions.begin() as session:
        await answer_with_reason(
            session, actor, response_id=prompt.response_id, reason_code="other", at=NOW
        )
        with pytest.raises(ValueError, match="1..500"):
            await save_comment(
                session, actor, response_id=prompt.response_id, text="z" * 501, at=NOW
            )
        ok = await save_comment(
            session, actor, response_id=prompt.response_id, text="z" * 500, at=NOW
        )
        assert ok.applied
    # Window closes strictly after 30 minutes.
    async with sessions.begin() as session:
        stored = await session.get(FeedbackResponse, prompt.response_id)
        assert stored is not None and stored.comment_due_at == NOW + timedelta(minutes=30)
    # Global cooldown blocks a second campaign inside 7 days.
    async with sessions.begin() as session:
        await ensure_campaign(session, "extra-v1", NOW)
        with pytest.raises(CooldownActive):
            await open_prompt(
                session, actor, slug="extra-v1", settings=SETTINGS, at=NOW + timedelta(days=6)
            )
    async with sessions.begin() as session:
        later = await open_prompt(
            session,
            replace(actor, operation_id=uuid4()),
            slug="extra-v1",
            settings=SETTINGS,
            at=NOW + timedelta(days=8),
        )
        assert later.created
        skipped = await skip_prompt(
            session, actor, response_id=later.response_id, at=NOW + timedelta(days=8)
        )
        assert skipped.applied and skipped.status == "skipped"
        repeat_skip = await skip_prompt(
            session, actor, response_id=later.response_id, at=NOW + timedelta(days=8)
        )
        assert repeat_skip.duplicate
    # Feedback never moves the six-month ownership deadline.
    async with sessions.begin() as session:
        ownership = await session.get(OwnershipPeriod, ownership_id)
        club = await session.get(Club, club_id)
        assert ownership is not None and club is not None
        assert (ownership.last_game_action_at, ownership.expires_at) == (last_action, expires)
        assert club.owner_telegram_id == 9002
    # Cleanup is idempotent and keeps game rows.
    async with sessions.begin() as session:
        assert await cleanup_comments(session, at=NOW + timedelta(days=60), retention_days=30) == 1
        assert await cleanup_comments(session, at=NOW + timedelta(days=60), retention_days=30) == 0
        assert await session.get(FeedbackResponse, prompt.response_id) is not None
        assert await session.get(Club, club_id) is not None
    # Opt-out erases text and blocks new prompts; status stays terminal.
    async with sessions.begin() as session:
        outcome = await opt_out(
            session, actor, response_id=prompt.response_id, at=NOW + timedelta(days=61)
        )
        assert outcome.status == "opted_out"
        assert await session.get(FeedbackComment, prompt.response_id) is None
    async with sessions.begin() as session:
        with pytest.raises(PermissionDenied):
            await open_prompt(
                session, actor, slug="manual-v1", settings=SETTINGS, at=NOW + timedelta(days=62)
            )


async def test_external_managers_stay_excluded(sessions):  # type: ignore[no-untyped-def]
    await _internal_owner(sessions, telegram_id=9001)
    async with sessions.begin() as session:
        entry = await begin_game(
            session, telegram_id=9003, update_id=90, payload=None, at=NOW, internal=False
        )
        external_actor = replace(entry.actor, operation_id=uuid4())
    async with sessions.begin() as session:
        with pytest.raises(PermissionDenied):
            await open_prompt(session, external_actor, slug="manual-v1", settings=SETTINGS, at=NOW)


async def test_comment_window_lookup_for_router(sessions):  # type: ignore[no-untyped-def]
    manager_id, _, _, _ = await _internal_owner(sessions, telegram_id=9004)
    actor = await _actor(sessions, manager_id, 9004)
    async with sessions.begin() as session:
        prompt = await open_prompt(session, actor, slug="manual-v1", settings=SETTINGS, at=NOW)
        assert await find_open_comment(session, manager_id, NOW) is None
        await answer_with_reason(
            session, actor, response_id=prompt.response_id, reason_code="other", at=NOW
        )
        found = await find_open_comment(session, manager_id, NOW + timedelta(minutes=29))
        assert found is not None and found.id == prompt.response_id
        assert await find_open_comment(session, manager_id, NOW + timedelta(minutes=31)) is None
    async with sessions.begin() as session:
        await save_comment(
            session, actor, response_id=prompt.response_id, text="ok", at=NOW + timedelta(minutes=5)
        )
        assert await find_open_comment(session, manager_id, NOW + timedelta(minutes=6)) is None


async def test_campaign_creation_is_safe_across_managers(sessions):
    import asyncio

    from goaletto.infrastructure.models import FeedbackCampaign

    one_id, _, _, _ = await _internal_owner(sessions)
    async with sessions.begin() as session:
        two = await begin_game(
            session, telegram_id=9005, update_id=500, payload=None, at=NOW, internal=True
        )
    one = await _actor(sessions, one_id, 9001)
    two_actor = two.actor

    async def create(actor):
        async with sessions.begin() as session:
            return await open_prompt(session, actor, slug="race-v1", settings=SETTINGS, at=NOW)

    prompts = await asyncio.wait_for(asyncio.gather(create(one), create(two_actor)), 10)
    assert prompts[0].campaign_id == prompts[1].campaign_id
    async with sessions.begin() as session:
        campaigns = (await session.scalars(select(FeedbackCampaign))).all()
        assert len(campaigns) == 1


async def test_comment_skip_closes_durable_window_and_cleanup_runs_as_job(sessions):
    from goaletto.application.feedback import schedule_comment_cleanup, skip_comment
    from goaletto.infrastructure.jobs import process_one_job
    from goaletto.infrastructure.models import Job

    manager_id, _, _, _ = await _internal_owner(sessions)
    actor = await _actor(sessions, manager_id, 9001)
    async with sessions.begin() as session:
        prompt = await open_prompt(session, actor, slug="manual-v1", settings=SETTINGS, at=NOW)
        await answer_with_reason(
            session, actor, response_id=prompt.response_id, reason_code="other", at=NOW
        )
        assert await skip_comment(session, actor, response_id=prompt.response_id, at=NOW)
        assert not await skip_comment(session, actor, response_id=prompt.response_id, at=NOW)
        assert await find_open_comment(session, manager_id, NOW) is None
        with pytest.raises(PermissionDenied):
            await save_comment(session, actor, response_id=prompt.response_id, text="late", at=NOW)
        # Insert only fixture text, never private user content.
        session.add(FeedbackComment(response_id=prompt.response_id, created_at=NOW, text="fixture"))
        # Hold unrelated reminder jobs so this test proves the cleanup handler.
        from sqlalchemy import update

        await session.execute(
            update(Job).where(Job.kind != "feedback.cleanup").values(status="held")
        )
        await schedule_comment_cleanup(session, at=NOW + timedelta(days=30), retention_days=30)
    async with sessions.begin() as session:
        assert await process_one_job(session, at=NOW + timedelta(days=30))
        assert await session.get(FeedbackComment, prompt.response_id) is not None
        cleanup = await session.scalar(select(Job).where(Job.kind == "feedback.cleanup"))
        assert cleanup.status == "pending" and cleanup.due_at == NOW + timedelta(days=31)
    async with sessions.begin() as session:
        assert await process_one_job(session, at=NOW + timedelta(days=31))
        assert await session.get(FeedbackComment, prompt.response_id) is None
        assert await session.get(FeedbackResponse, prompt.response_id) is not None
