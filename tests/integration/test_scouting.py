"""Private paid missions on the single development DB, with real ledger/jobs."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.candidates import (
    load_candidates,
    replenish_pool,
    sign_candidate,
    supply_count,
)
from goaletto.application.finances import close_salary
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.outbox_delivery import Decision, check_recipient, valid_payload
from goaletto.application.scouting import (
    cancel_search,
    complete_search,
    decline_finding,
    finding_for,
    load_scout,
    sign_finding,
    start_search,
)
from goaletto.domain.finances import scout_mission_fee
from goaletto.infrastructure.models import (
    CandidateOffer,
    Job,
    LedgerEntry,
    OutboxMessage,
    Person,
    Player,
    PlayerDevelopment,
    SalaryContract,
    ScoutDiscovery,
    ScoutMission,
    ScoutSigning,
    Staff,
)
from tests.integration.test_agent_wave import NOW, OPTIONS
from tests.integration.test_candidates import action, funded

pytestmark = pytest.mark.integration


async def ready(sessions):
    _, actor, club = await funded(
        sessions, options=replace(OPTIONS, first_kickoff=NOW + timedelta(days=24, hours=6))
    )
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "staff")
        item = next(i for i in screen.items if i.offer.profile == "scout")
        await sign_candidate(
            session, action(actor), screen.ownership_id, item.person.id, item.offer.fee, at=NOW
        )
        skill = item.skills[0]
        assert await session.scalar(select(func.count()).select_from(ScoutMission)) == 0
    return actor, club, screen.ownership_id, item.person.id, skill


@pytest.mark.parametrize("days", [4, 8, 12, 16])
async def test_only_one_paid_final_batch_no_public_leak_or_reroll(sessions, days):
    actor, club, owner, scout, skill = await ready(sessions)
    request = action(actor)
    fee = scout_mission_fee(skill, days)
    async with sessions.begin() as session:
        mission = await start_search(session, request, owner, scout, "FC", days, fee)
        assert (
            await start_search(session, request, owner, scout, "FC", days, fee)
        ).id == mission.id
        job = await session.scalar(select(Job).where(Job.kind == "scout.complete"))
        assert mission.due_at == NOW + timedelta(days=days)
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == 0
    for elapsed in (1, days - 1):
        with pytest.raises(ValueError, match="Search has not finished"):
            async with sessions.begin() as session:
                await complete_search(
                    session, await session.get(Job, job.id), NOW + timedelta(days=elapsed)
                )
    at = NOW + timedelta(days=days)
    async with sessions.begin() as session:
        await complete_search(session, await session.get(Job, job.id), at)
        rows = (await session.scalars(select(ScoutDiscovery).order_by(ScoutDiscovery.rank))).all()
        assert len(rows) == days // 4
        original = []
        for offer in rows:
            person = await session.get(Person, offer.person_id)
            player = await session.get(Player, person.id)
            dev = await session.get(PlayerDevelopment, person.id)
            assert 17 <= person.age <= 21 and player.position == "FC" and player.club_id is None
            original.append(
                (
                    person.id,
                    person.appearance_identity_id,
                    person.age,
                    player.attacking,
                    offer.fee,
                    dict(dev.caps),
                )
            )
        assert await supply_count(session, "player", "player", at) == 0
        assert await replenish_pool(session, "player", "player", at) == 12
        public = await load_candidates(session, replace(action(actor), at=at), "player")
        assert len(public.items) == 12
        assert all(i.person.id not in {r.person_id for r in rows} for i in public.items)
        assert all(26 <= i.person.age <= 30 for i in public.items)
        notice = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.payload["kind"].astext == "scout_report")
        )
        assert valid_payload(notice.payload) and notice.payload["count"] == days // 4
        assert await check_recipient(session, notice, at) == Decision.DELIVER
        await complete_search(session, await session.get(Job, job.id), at + timedelta(hours=1))
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == days // 4
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "scout_search")
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.payload["kind"].astext == "scout_report")
            )
            == 1
        )
        for pid, face, age, attack, price, caps in original:
            assert (await session.get(Person, pid)).age == age
            assert (await session.get(Person, pid)).appearance_identity_id == face
            assert (await session.get(ScoutDiscovery, pid)).fee == price
            assert (await session.get(PlayerDevelopment, pid)).caps == caps
            assert (await session.get(Player, pid)).attacking == attack
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE scout_missions SET days=4 WHERE id=:id"), {"id": mission.id}
            ) if days != 4 else await session.execute(
                text("UPDATE scout_missions SET fee=1 WHERE id=:id"), {"id": mission.id}
            )


async def test_concurrent_start_sign_retry_and_pinned_salary_without_refill(sessions):
    actor, club, owner, scout, skill = await ready(sessions)

    async def start(request):
        try:
            async with sessions.begin() as session:
                return await start_search(
                    session, request, owner, scout, "DC", 4, scout_mission_fee(skill, 4)
                )
        except ValueError:
            return None

    results = await asyncio.gather(start(action(actor)), start(action(actor)))
    assert sum(m is not None for m in results) == 1
    mission = next(m for m in results if m)
    async with sessions.begin() as session:
        job = await session.scalar(select(Job).where(Job.kind == "scout.complete"))
        await complete_search(session, job, mission.due_at)
        offer = await session.scalar(select(ScoutDiscovery))
        fee, rate, pid = offer.fee, offer.cycle_rate, offer.person_id
        scout_identity = (await session.get(Person, pid)).appearance_identity_id
    # The report remains available through the next open window (day 13..21).
    at = NOW + timedelta(days=14)
    request = replace(action(actor), at=at)

    async def buy(request):
        try:
            async with sessions.begin() as session:
                return await sign_finding(session, request, owner, pid, fee)
        except PermissionDenied:
            return None

    bought = await asyncio.gather(buy(request), buy(replace(action(actor), at=at)))
    assert sum(r is not None for r in bought) == 1
    winner = next(r for r in bought if r)
    assert (
        await buy(replace(request, operation_id=winner.operation_id))
    ).operation_id == winner.operation_id
    async with sessions.begin() as session:
        contract = await session.scalar(
            select(SalaryContract).where(SalaryContract.person_id == pid)
        )
        assert contract.weekly_rate == rate and contract.policy_version == winner.economy_policy
        assert (await session.get(Player, pid)).club_id == club
        assert (await session.get(Person, pid)).appearance_identity_id == scout_identity
        assert await session.scalar(select(func.count()).select_from(ScoutSigning)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CandidateOffer)
                .where(CandidateOffer.kind == "player")
            )
            == 0
        )
        assert await supply_count(session, "player", "player", at) == 0
        assert await session.scalar(select(func.count()).select_from(ScoutMission)) == 1


async def test_decline_expiry_owner_scout_change_cancel_without_free_players(sessions):
    actor, club, owner, scout, skill = await ready(sessions)
    fee = scout_mission_fee(skill, 4)
    async with sessions.begin() as session:
        m = await start_search(session, action(actor), owner, scout, "MC", 4, fee)
        job = await session.scalar(select(Job).where(Job.kind == "scout.complete"))
        await complete_search(session, job, m.due_at)
        offer = await session.scalar(select(ScoutDiscovery))
        at = m.due_at + timedelta(hours=1)
        await decline_finding(session, replace(action(actor), at=at), owner, offer.person_id)
        assert await supply_count(session, "player", "player", at) == 0
        with pytest.raises(PermissionDenied):
            await finding_for(session, replace(action(actor), at=at), uuid4(), offer.person_id)
        expiry = await session.scalar(select(Job).where(Job.kind == "scout.expire"))
        await complete_search(session, expiry, expiry.due_at)
        notice = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.payload["kind"].astext == "scout_report")
        )
        assert await check_recipient(session, notice, expiry.due_at) == Decision.SKIP_STALE
        assert await supply_count(session, "player", "player", expiry.due_at) == 0
        later = replace(action(actor), at=expiry.due_at + timedelta(hours=1))
        m2 = await start_search(session, later, owner, scout, "GK", 4, fee)
        await cancel_search(session, replace(action(actor), at=later.at), owner, m2.id)
        await cancel_search(session, replace(action(actor), at=later.at), owner, m2.id)
        await complete_search(
            session,
            await session.scalar(
                select(Job).where(
                    Job.kind == "scout.complete", Job.payload["mission_id"].astext == str(m2.id)
                )
            ),
            m2.due_at,
        )
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == 1
        m3 = await start_search(
            session, replace(action(actor), at=later.at), owner, scout, "DL", 4, fee
        )
        worker = await session.get(Staff, scout)
        await close_salary(session, scout, later.at)
        worker.club_id, worker.role = None, None
        await session.flush()
        await complete_search(
            session,
            await session.scalar(select(Job).where(Job.payload["mission_id"].astext == str(m3.id))),
            m3.due_at,
        )
        assert m3.status == "cancelled"
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == 1
        assert not (await load_scout(session, replace(action(actor), at=m3.due_at))).scout


async def test_failure_rolls_back_entire_batch_and_money(sessions, monkeypatch):
    from goaletto.application import scouting

    actor, club, owner, scout, skill = await ready(sessions)
    async with sessions.begin() as session:
        m = await start_search(
            session, action(actor), owner, scout, "AML", 8, scout_mission_fee(skill, 8)
        )
        job_id = await session.scalar(select(Job.id).where(Job.kind == "scout.complete"))
    original = scouting.create_person
    count = 0

    async def fail_second(session, request):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("Portrait failure")
        return await original(session, request)

    monkeypatch.setattr(scouting, "create_person", fail_second)
    with pytest.raises(ValueError, match="Portrait failure"):
        async with sessions.begin() as session:
            await complete_search(session, await session.get(Job, job_id), m.due_at)
    async with sessions.begin() as session:
        assert (await session.get(ScoutMission, m.id)).status == "active"
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Person)
                .where(Person.creation_key.like("scouting-v1:%"))
            )
            == 0
        )
    monkeypatch.setattr(scouting, "create_person", original)
    async with sessions.begin() as session:
        await complete_search(session, await session.get(Job, job_id), m.due_at)
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == 2


async def test_real_dispatcher_paid_search_final_report_and_sign(sessions, monkeypatch):
    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from goaletto.bot import scouting_router
    from goaletto.bot.main import build_dispatcher
    from goaletto.infrastructure.jobs import process_one_job
    from tests.integration.test_game_dispatcher import Source, Telegram

    actor, club, owner, scout, skill = await ready(sessions)
    current = NOW + timedelta(days=1)

    class Clock:
        @staticmethod
        def now(tz):
            return current

    monkeypatch.setattr(scouting_router, "datetime", Clock)
    source = Source(sessions)
    transport = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    number = 400

    async def send(text=None, data=None):
        nonlocal number
        number += 1
        if text:
            u = Update(
                update_id=number,
                message=Message(
                    message_id=number,
                    date=current,
                    chat=Chat(id=1001, type="private"),
                    from_user=user,
                    text=text,
                ),
            )
        else:
            u = Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=f"scout-ui-{number}",
                    from_user=user,
                    chat_instance="test",
                    message=transport.last_message,
                    data=data,
                ),
            )
        await dispatcher.feed_update(bot, u)
        return transport.last_message

    def button(message, prefix):
        return next(
            b.callback_data
            for row in message.reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith(prefix)
        )

    message = await send(text="/scout")
    message = await send(data=button(message, "ss:n:"))
    message = await send(
        data=next(
            b.callback_data
            for row in message.reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").endswith(":AMC")
        )
    )
    callback = next(
        b.callback_data
        for row in message.reply_markup.inline_keyboard
        for b in row
        if ":AMC:8:" in (b.callback_data or "")
    )
    message = await send(data=callback)
    assert "Подписание игроков оплачивается отдельно" in message.text
    message = await send(data=button(message, "ss:b:"))
    assert "Итоговый отчёт:" in message.text
    async with sessions.begin() as session:
        mission = await session.scalar(select(ScoutMission))
        assert mission.days == 8 and mission.position == "AMC"
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == 0
    current = mission.due_at
    async with sessions.begin() as session:
        jobs = (await session.scalars(select(Job))).all()
        for j in jobs:
            if j.kind != "scout.complete":
                j.status = "done"
        await session.flush()
        assert await process_one_job(session, at=current)
        assert (
            await session.scalar(select(Job).where(Job.kind == "scout.complete"))
        ).status == "done"
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == 2
    message = await send(text="/scout")
    message = await send(data=button(message, "ss:r:"))
    message = await send(data=button(message, "ss:f:"))
    assert "Оценка потенциала позиции:" in message.text and "/4 дня" in message.text
    current = NOW + timedelta(days=14)
    confirm = button(message, "ss:s:")
    message = await send(data=confirm)
    assert "Контракт подписан" in message.text
    await send(data=confirm)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(ScoutSigning)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "scout_search")
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Player).where(Player.club_id == club)
            )
            == 21
        )
    await bot.session.close()


async def test_insufficient_budget_creates_no_paid_search_or_job(sessions):
    from goaletto.application.money import InsufficientFunds, debit_locked, locked_accounts

    actor, club, owner, scout, skill = await ready(sessions)
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club))[club]
        await debit_locked(
            session, account, "test-spend", "stadium_upgrade", account.available - 100, NOW
        )
    with pytest.raises(InsufficientFunds):
        async with sessions.begin() as session:
            await start_search(
                session, action(actor), owner, scout, "GK", 16, scout_mission_fee(skill, 16)
            )
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(ScoutMission)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "scout.complete")
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "scout_search")
            )
            == 0
        )


async def test_ownership_change_cancels_running_search_and_hides_old_report(sessions):
    from goaletto.infrastructure.models import Club, OwnershipPeriod

    actor, club, owner, scout, skill = await ready(sessions)
    async with sessions.begin() as session:
        mission = await start_search(
            session, action(actor), owner, scout, "DR", 4, scout_mission_fee(skill, 4)
        )
        period = await session.get(OwnershipPeriod, owner)
        period.ended_at = NOW + timedelta(hours=1)
        period.end_reason = "operator"
        (await session.get(Club, club)).owner_telegram_id = None
        await session.flush()
        job = await session.scalar(select(Job).where(Job.kind == "scout.complete"))
        await complete_search(session, job, mission.due_at)
        assert mission.status == "cancelled"
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.payload["kind"].astext == "scout_report")
            )
            == 0
        )
    with pytest.raises(PermissionDenied):
        async with sessions.begin() as session:
            await load_scout(session, replace(action(actor), at=mission.due_at))
