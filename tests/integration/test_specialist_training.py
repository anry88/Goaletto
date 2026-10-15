"""Staff-gated sessions, persisted mentoring and shared limits on the game DB."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.candidates import load_candidates, sign_candidate
from goaletto.application.development import growth_budget
from goaletto.application.staff_changes import execute_change, quote_change
from goaletto.application.training import apply_training, load_training, select_training, week_start
from goaletto.bot.training_views import training_report, training_view
from goaletto.domain.development import BUDGET_KEYS
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.domain.training import SPECIALISTS, Focus, Intensity
from goaletto.infrastructure.models import (
    Job,
    Match,
    Person,
    Player,
    PlayerDevelopment,
    PlayerRecovery,
    Staff,
    StaffQualification,
    TrainingDecision,
    TrainingPlan,
)
from goaletto.presentation.i18n import using_locale
from tests.integration.test_agent_wave import NOW, job, start_fixture
from tests.integration.test_candidates import action, funded

pytestmark = pytest.mark.integration


async def hired(session, actor, role):
    screen = await load_candidates(session, action(actor), "staff")
    item = next(i for i in screen.items if i.offer.profile == role)
    await sign_candidate(
        session, action(actor), screen.ownership_id, item.person.id, item.offer.fee, at=actor.at
    )
    return item.person.id


async def choose(session, actor, focus, intensity=Intensity.MEDIUM, executor_id=None):
    screen = await load_training(session, action(actor), record=False)
    return await select_training(
        session,
        action(actor),
        ownership_id=screen.ownership_id,
        week=screen.week_start,
        focus=focus,
        intensity=intensity,
        executor_id=executor_id,
        clock=lambda: actor.at,
    )


async def training_job(session, plan):
    return await session.scalar(
        select(Job).where(
            Job.kind == "training.apply", Job.payload["plan_id"].astext == str(plan.id)
        )
    )


async def test_weak_coach_allows_only_general_and_forged_specialist_choice_has_no_effect(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_training(session, action(actor), record=False)
        assert screen.available == (Focus.TEAM,)
        body, keyboard = training_view(screen)
        assert "Тренер атаки" in body and "Тренер вратарей" in body
        callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        assert sum(bool(s and s.startswith("tr:f:")) for s in callbacks) == 1
        for focus in Focus:
            if focus != Focus.TEAM:
                with pytest.raises(ValueError, match="specialist"):
                    await choose(session, actor, focus)
        assert await session.scalar(select(func.count()).select_from(TrainingPlan)) == 0
        assert await session.scalar(select(func.count()).select_from(TrainingDecision)) == 0
        plan = await choose(session, actor, Focus.TEAM)
        expected = plan.staff_snapshot
        assert set(expected["roles"]) == {"head_coach", "physiotherapist"}
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.status == "applied" and len(plan.results) == 20
        assert max(row["gain_bps"] for row in plan.results) < 100
        assert all(row["leadership_gain"] == 5 for row in plan.results)
        devs = (
            await session.scalars(
                select(PlayerDevelopment)
                .join(Player, Player.person_id == PlayerDevelopment.person_id)
                .where(Player.club_id == club)
            )
        ).all()
        assert any(d.season_gains["training"] > 0 for d in devs)
        assert all(d.leadership_progress == 5 for d in devs)


@pytest.mark.parametrize(
    "focus",
    [
        Focus.GOALKEEPING,
        Focus.DEFENDING,
        Focus.PLAYMAKING,
        Focus.WINGER,
        Focus.ATTACKING,
        Focus.SET_PIECES,
        Focus.PHYSICAL,
    ],
)
async def test_every_specialist_focus_targets_the_declared_group(sessions, focus):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        person_id = await hired(session, actor, SPECIALISTS[focus])
        screen = await load_training(session, action(actor), record=False)
        assert focus in screen.available
        executor = screen.members[0].id if focus == Focus.SET_PIECES else None
        plan = await choose(session, actor, focus, executor_id=executor)
        assert plan.staff_snapshot["roles"][SPECIALISTS[focus]]["person_id"] == str(person_id)
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.status == "applied" and plan.results
        if focus == Focus.SET_PIECES:
            assert len(plan.results) == 1 and plan.results[0]["person_id"] == str(executor)
        from goaletto.domain.training import POSITIONS

        ids = [r["person_id"] for r in plan.results]
        players = (await session.scalars(select(Player).where(Player.club_id == club))).all()
        for player in players:
            trained = str(player.person_id) in ids
            assert trained == (
                focus == Focus.PHYSICAL
                or (
                    player.person_id == executor
                    if focus == Focus.SET_PIECES
                    else player.position in POSITIONS[focus]
                )
            )
            dev = await session.get(PlayerDevelopment, player.person_id)
            if trained:
                assert 0 <= dev.season_gains["training"] < 600
                if dev.season_gains["training"] == 0:
                    keys = ("stamina", "pace") if focus == Focus.PHYSICAL else (focus.value,)
                    assert all(getattr(player, key) == dev.caps[key] for key in keys)
            else:
                assert not any(dev.progress.values())


async def test_lost_specialist_cancels_without_fallback_load_or_duplicate_effect(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        specialist = await hired(session, actor, "fitness_coach")
        plan = await choose(session, actor, Focus.PHYSICAL)
        owner = (await load_training(session, action(actor), record=False)).ownership_id
        removal = await quote_change(session, action(actor), owner, outgoing_id=specialist)
        await execute_change(session, action(actor), removal.id, at=NOW)
        screen = await load_training(session, action(actor), record=False)
        assert screen.staff_changed and Focus.PHYSICAL not in screen.available
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.status == "cancelled" and plan.focus == "physical"
        assert plan.results == [{"reason": "staff_changed"}]
        assert not (await session.scalars(select(PlayerRecovery))).all()
        assert all(
            not any(d.progress.values())
            for d in (await session.scalars(select(PlayerDevelopment))).all()
        )
        await apply_training(session, await training_job(session, plan), plan.due_at)
        with using_locale("en"):
            report, _ = training_report(plan, "Club")
            assert "staff or qualifications changed" in report and "general preparation" in report


async def test_owner_can_reselect_general_after_losing_specialist_before_deadline(sessions):
    _, actor, _ = await funded(sessions)
    async with sessions.begin() as session:
        specialist = await hired(session, actor, "fitness_coach")
        request = action(actor)
        screen = await load_training(session, action(actor), record=False)
        plan = await select_training(
            session,
            request,
            ownership_id=screen.ownership_id,
            week=screen.week_start,
            focus=Focus.PHYSICAL,
            intensity=Intensity.HIGH,
            clock=lambda: NOW,
        )
        old_snapshot = dict(plan.staff_snapshot)
        removal = await quote_change(
            session, action(actor), screen.ownership_id, outgoing_id=specialist
        )
        await execute_change(session, action(actor), removal.id, at=NOW)
        updated = await choose(session, actor, Focus.TEAM)
        assert updated.id == plan.id and updated.staff_snapshot != old_snapshot
        repeated = await select_training(
            session,
            request,
            ownership_id=screen.ownership_id,
            week=screen.week_start,
            focus=Focus.PHYSICAL,
            intensity=Intensity.HIGH,
            clock=lambda: NOW,
        )
        assert repeated.focus == "team"
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "training.apply")
            )
            == 1
        )
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.status == "applied" and len(plan.results) == 20


async def test_rehire_same_person_does_not_revive_the_old_staff_snapshot(sessions):
    _, actor, _ = await funded(sessions)
    async with sessions.begin() as session:
        specialist = await hired(session, actor, "fitness_coach")
        plan = await choose(session, actor, Focus.PHYSICAL)
        owner = (await load_training(session, action(actor), record=False)).ownership_id
        removal = await quote_change(session, action(actor), owner, outgoing_id=specialist)
        await execute_change(session, action(actor), removal.id, at=NOW)
        from goaletto.infrastructure.models import CandidateOffer

        offer = await session.get(CandidateOffer, specialist)
        rehiring = await quote_change(
            session, action(actor), owner, incoming_id=specialist, quoted_fee=offer.fee
        )
        await execute_change(session, action(actor), rehiring.id, at=NOW)
        assert (await session.get(Staff, specialist)).club_id == plan.club_id
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.status == "cancelled" and plan.results[0]["reason"] == "staff_changed"


async def test_exhausted_budget_preserves_fraction_and_resets_only_for_age(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        row = await session.scalar(
            select(PlayerDevelopment)
            .join(Player, Player.person_id == PlayerDevelopment.person_id)
            .join(Person, Person.id == Player.person_id)
            .where(Player.club_id == club, Person.age < 32)
            .limit(1)
        )
        person = await session.get(Person, row.person_id)
        row.caps = dict.fromkeys(row.caps, 20)
        row.growth_age = person.age
        row.season_gains = {
            **dict.fromkeys(BUDGET_KEYS, 0),
            "training": 20000,
            "stamina": 10000,
            "pace": 10000,
            "leadership": 5000,
        }
        row.leadership_progress = 1234
        before = dict(row.progress)
        plan = await choose(session, actor, Focus.TEAM)
        await apply_training(session, await training_job(session, plan), plan.due_at)
        saved = next(r for r in plan.results if r["person_id"] == str(person.id))
        assert saved["gain_bps"] == saved["leadership_gain"] == 0
        assert row.season_gains["training"] == 20000 and row.leadership_progress == 1234
        assert all(row.progress[k] == before[k] for k in row.caps)
        assert growth_budget(row, person.age).used["training"] == 20000
        assert growth_budget(row, person.age + 1).used["training"] == 0
        assert row.leadership_progress == 1234
        previous_age = person.age
        person.age += 1
        next_actor = replace(action(actor), at=NOW + timedelta(days=1, hours=1))
        next_plan = await choose(session, next_actor, Focus.TEAM)
        await apply_training(session, await training_job(session, next_plan), next_plan.due_at)
        assert row.growth_age == previous_age + 1
        assert 0 < row.season_gains["training"] < 100
        assert row.leadership_progress == 1239


async def test_training_after_real_match_does_not_rewrite_snapshot_and_mentoring_is_persisted(
    sessions,
):
    _, actor, club = await funded(sessions)
    fixture, at = await start_fixture(sessions, club)
    for tick in range(1, 31):
        if tick == 16:
            await job(sessions, "match.second_half_start", fixture)
        await job(sessions, "match.tick", fixture, tick)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        before = (
            state_hash(restore_state(match.state)),
            state_hash(restore_state(match.starting_snapshot)),
        )
        coach = await session.scalar(
            select(Staff.person_id).where(Staff.club_id == club, Staff.role == "head_coach")
        )
        (await session.get(StaffQualification, (coach, "head_coach"))).skill = 20
        players = (await session.scalars(select(Player).where(Player.club_id == club))).all()
        for player in players:
            player.morale = 40
            (await session.get(PlayerDevelopment, player.person_id)).leadership_progress = 9990
        actor = replace(action(actor), at=week_start(at) + timedelta(days=1, hours=1))
        plan = await choose(session, actor, Focus.TEAM)
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.status == "applied"
        assert any(r["morale_after"] > r["morale_before"] for r in plan.results)
        assert all(r["morale_after"] <= 41 for r in plan.results)
        assert all(r["leadership_after"] == r["leadership_before"] + 1 for r in plan.results)
        assert before == (
            state_hash(restore_state(match.state)),
            state_hash(restore_state(match.starting_snapshot)),
        )
        report = dict(plan.results[0])
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.results[0] == report
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE training_plans SET staff_snapshot='{}'::jsonb WHERE id=:id"),
                {"id": plan.id},
            )


async def test_telegram_gates_specialists_then_saves_and_displays_actual_result(
    sessions, monkeypatch
):
    from datetime import UTC, datetime

    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from goaletto.bot import training_router
    from goaletto.bot.main import build_dispatcher
    from goaletto.bot.training_views import choice_callback
    from tests.integration.test_agent_wave import OPTIONS
    from tests.integration.test_game_dispatcher import Source, Telegram

    now = datetime.now(UTC)
    _, actor, club = await funded(
        sessions,
        options=replace(
            OPTIONS, first_kickoff=(now + timedelta(days=10)).replace(second=0, microsecond=0)
        ),
        at=now,
    )
    source = Source(sessions)
    transport = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    number = 100
    current = now

    class Clock:
        @staticmethod
        def now(zone):
            return current

    monkeypatch.setattr(training_router, "datetime", Clock)

    async def send(command=None, data=None):
        nonlocal number
        number += 1
        update = (
            Update(
                update_id=number,
                message=Message(
                    message_id=number,
                    date=current,
                    chat=Chat(id=1001, type="private"),
                    from_user=user,
                    text=command,
                ),
            )
            if command
            else Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=f"training-{number}",
                    from_user=user,
                    chat_instance="test",
                    message=transport.last_message,
                    data=data,
                ),
            )
        )
        await dispatcher.feed_update(bot, update)
        return transport.last_message

    def buttons(message, prefix):
        return [
            b.callback_data
            for row in message.reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith(prefix)
        ]

    first = await send(command="/train")
    assert len(buttons(first, "tr:f:")) == 1 and "Фитнес-тренер" in first.text
    async with sessions.begin() as session:
        screen = await load_training(session, action(actor), record=False)
    forged = choice_callback("s", screen, Focus.PHYSICAL, Intensity.MEDIUM)
    denied = await send(data=forged)
    assert "нужного специалиста" in denied.text
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(TrainingPlan)) == 0
        await hired(session, actor, "fitness_coach")
    unlocked = await send(command="/train")
    physical = next(
        s
        for s in buttons(unlocked, "tr:f:")
        if s.split(":")[2] == str(list(Focus).index(Focus.PHYSICAL))
    )
    detail = await send(data=physical)
    saved = await send(data=buttons(detail, "tr:s:")[0])
    assert "Тренировка сохранена" in saved.text
    async with sessions.begin() as session:
        plan = await session.scalar(select(TrainingPlan))
        await apply_training(session, await training_job(session, plan), plan.due_at)
        current = plan.due_at + timedelta(hours=1)
    screen = await send(command="/train")
    report = await send(data=buttons(screen, "tr:r:")[0])
    assert "Рост навыков" in report.text and "Развитие лидерства" in report.text
    assert transport.source.depth == 0
    await bot.session.close()


async def test_real_transfer_preserves_age_season_budget_and_all_fractions(sessions):
    from goaletto.application.market import place_bid
    from tests.integration.test_market import close, market_world

    people, auction, person_id = await market_world(sessions)
    async with sessions.begin() as session:
        person = await session.get(Person, person_id)
        dev = await session.get(PlayerDevelopment, person_id)
        dev.growth_age = person.age
        dev.season_gains = {
            **dict.fromkeys(BUDGET_KEYS, 0),
            "training": 15000,
            "defending": 15000,
            "leadership": 4000,
        }
        dev.progress = {**dev.progress, "playmaking": 3333}
        dev.leadership_progress = 4444
        saved = (
            dict(dev.season_gains),
            dict(dev.progress),
            dict(dev.caps),
            dev.leadership_progress,
            dev.growth_age,
        )
        buyer, club, owner = people[1]
        await place_bid(session, action(buyer), owner, auction, 100000, clock=lambda: NOW)
    await close(sessions, auction)
    async with sessions.begin() as session:
        dev = await session.get(PlayerDevelopment, person_id)
        assert (await session.get(Player, person_id)).club_id == club
        assert saved == (
            dev.season_gains,
            dev.progress,
            dev.caps,
            dev.leadership_progress,
            dev.growth_age,
        )
        assert (
            growth_budget(dev, (await session.get(Person, person_id)).age).used["training"] == 15000
        )
