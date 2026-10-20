"""Bounded supply, atomic signing and retired identities on the one development DB."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from goaletto.application.candidates import (
    load_candidates,
    replenish_pool,
    sign_candidate,
    supply_count,
)
from goaletto.application.finances import close_salary
from goaletto.application.market import list_player
from goaletto.application.money import credit_locked, locked_accounts
from goaletto.infrastructure.models import (
    CandidateOffer,
    CandidatePool,
    CandidateSigning,
    LedgerEntry,
    LineupSlot,
    Person,
    Player,
    Staff,
)
from tests.integration.test_agent_wave import NOW, setup_world

pytestmark = pytest.mark.integration


def action(actor):
    return replace(actor, operation_id=uuid4(), update_id=uuid4().int % 1000000000)


async def funded(sessions, **kwargs):
    result, actor, club_id = await setup_world(sessions, **kwargs)
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        await credit_locked(session, account, "candidate-test-grant", "grant", 2000000, NOW)
    return result, actor, club_id


async def test_parallel_open_and_purchase_refill_once_preserve_faces(sessions):
    _, actor, club_id = await funded(sessions)

    async def load():
        async with sessions.begin() as session:
            return await load_candidates(session, action(actor), "player")

    screens = await asyncio.wait_for(asyncio.gather(load(), load()), 20)
    assert len(screens[0].items) == len(screens[1].items) == 12
    assert {i.person.id for i in screens[0].items} == {i.person.id for i in screens[1].items}
    item = screens[0].items[0]
    request = action(actor)

    async def buy(actor):
        try:
            async with sessions.begin() as session:
                return await sign_candidate(
                    session, actor, screens[0].ownership_id, item.person.id, item.offer.fee, at=NOW
                )
        except ValueError:
            return None

    results = await asyncio.wait_for(asyncio.gather(buy(request), buy(action(actor))), 20)
    assert sum(r is not None for r in results) == 1
    winner = next(r for r in results if r is not None)
    # The two operations race; retry the actual accepted envelope.
    retry_actor = replace(request, operation_id=winner.operation_id)
    assert (await buy(retry_actor)).operation_id == winner.operation_id
    async with sessions.begin() as session:
        assert await supply_count(session, "player", "player", NOW) == 12
        assert (await session.get(CandidatePool, ("player", "player"))).generation == 13
        assert await session.scalar(select(func.count()).select_from(CandidateSigning)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "signing_fee")
            )
            == 1
        )
        person = await session.get(Person, item.person.id)
        assert person.appearance_identity_id == item.person.appearance_identity_id
        assert (await session.get(Player, person.id)).club_id == club_id
        # The starting set already consumes all 1000 catalog identities. Reuse works
        # with different person IDs, while the original 1000 people remain distinct.
        assert await session.scalar(select(func.count()).select_from(Person)) == 1063
        assert (
            await session.scalar(select(func.count(func.distinct(Person.appearance_identity_id))))
            == 1050
        )


async def test_staff_role_hire_refills_only_one_and_does_not_duplicate_role(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "staff")
        assert len(screen.items) == 14
        first = screen.items[0]
        await sign_candidate(
            session, action(actor), screen.ownership_id, first.person.id, first.offer.fee, at=NOW
        )
        pool = await session.get(CandidatePool, ("staff", first.offer.pool_profile))
        assert pool.generation == 3
        assert await supply_count(session, "staff", first.offer.profile, NOW) == 2
        second = await session.scalar(
            select(CandidateOffer).where(
                CandidateOffer.profile == first.offer.profile, CandidateOffer.status == "available"
            )
        )
        second_id, fee = second.person_id, second.fee
    with pytest.raises(ValueError, match="employment changed"):
        async with sessions.begin() as session:
            await sign_candidate(
                session, action(actor), screen.ownership_id, second_id, fee, at=NOW
            )
    async with sessions.begin() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Staff).where(Staff.club_id == club_id)
            )
            == 2
        )
        assert (await session.get(CandidatePool, ("staff", first.offer.profile))).generation == 3


async def test_supply_counts_auctions_and_never_generates_above_twelve(sessions):
    from tests.integration.test_market import market_world

    people, _, _ = await market_world(sessions)
    async with sessions.begin() as session:
        for actor, club_id, owner_id in people:
            players = (
                await session.scalars(
                    select(Player)
                    .where(
                        Player.club_id == club_id,
                        Player.status == "active",
                        Player.position != "GK",
                    )
                    .order_by(Player.person_id)
                )
            ).all()
            # The helper already lists one seller player; fill each club's four slots.
            from goaletto.infrastructure.models import Auction

            listed = (
                await session.scalars(
                    select(Auction.person_id).where(Auction.seller_club_id == club_id)
                )
            ).all()
            for player in [p for p in players if p.person_id not in listed][: 4 - len(listed)]:
                await list_player(
                    session, action(actor), owner_id, player.person_id, 100000, clock=lambda: NOW
                )
        assert await supply_count(session, "player", "player", NOW) == 12
        assert await replenish_pool(session, "player", "player", NOW) == 0
        assert (await session.get(CandidatePool, ("player", "player"))).generation == 0
        assert await session.scalar(select(func.count()).select_from(Person)) == 1050


async def test_staff_uses_retired_player_without_new_person_or_face(sessions):
    from goaletto.application.staff_career import assign_career

    result, _, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        player = await session.scalar(
            select(Player).where(Player.club_id == club_id, Player.position == "DC").limit(1)
        )
        person = await session.get(Person, player.person_id)
        original = (person.id, person.appearance_identity_id, person.appearance_variant_id)
        await session.execute(delete(LineupSlot).where(LineupSlot.person_id == person.id))
        await close_salary(session, person.id, NOW)
        player.status, player.last_season_id, player.retired_after_season_id = (
            "retired",
            result.season_id,
            result.season_id,
        )
        player.club_id, person.career, person.age = None, "retired", 36
        await session.flush()
        career = await assign_career(session, person, result.season_id, club_id, NOW)
        assert await replenish_pool(session, "staff", "defence_coach", NOW) == 2
        assert career.person_id == person.id
        assert (person.id, person.appearance_identity_id, person.appearance_variant_id) == original
        assert person.career == "staff"
        assert (await session.get(Staff, person.id)).club_id is None
        assert (await session.get(Player, person.id)).status == "retired"
        assert await session.scalar(select(func.count()).select_from(Person)) == 1052
        assert await replenish_pool(session, "staff", "defence_coach", NOW) == 0


async def test_one_global_pool_visible_in_another_set_without_more_people(sessions):
    from datetime import timedelta

    from goaletto.application.onboarding import begin_game, claim_club
    from goaletto.application.world import initialize_world
    from goaletto.domain.world import WorldOptions
    from goaletto.infrastructure.models import League
    from tests.integration.test_agent_wave import KICKOFF

    result, actor, _ = await funded(sessions)
    async with sessions.begin() as session:
        first = await load_candidates(session, action(actor), "player")
        staff = await load_candidates(session, action(actor), "staff")
        second = await initialize_world(
            session,
            WorldOptions(
                key="second-global-pool",
                seed=17,
                first_kickoff=KICKOFF,
            ),
            initialized_at=NOW + timedelta(minutes=1),
        )
        entry = await begin_game(
            session, telegram_id=1002, update_id=20, payload=None, at=NOW + timedelta(minutes=2)
        )
        # Offers are normal tier-5 clubs from both sets. Select the second set explicitly.
        from goaletto.infrastructure.models import Club

        second_club = await session.scalar(
            select(Club.id)
            .join(League)
            .where(League.league_set_id == second.league_set_id, League.tier == 5)
            .order_by(Club.id)
            .limit(1)
        )
        second_actor = action(entry.actor)
        await claim_club(session, second_actor, second_club)
        second_players = await load_candidates(session, action(second_actor), "player")
        second_staff = await load_candidates(session, action(second_actor), "staff")
        assert {i.person.id for i in second_players.items} == {i.person.id for i in first.items}
        assert {i.person.id for i in second_staff.items} == {i.person.id for i in staff.items}
        assert await session.scalar(select(func.count()).select_from(Person)) == 2126
        assert (
            await session.get(CandidatePool, ("player", "player"))
        ).home_league_set_id == result.league_set_id
        assert await supply_count(session, "player", "player", NOW) == 12


async def test_failed_replenishment_rolls_back_signing_and_money(sessions, monkeypatch):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "player")
        item = screen.items[0]
        account = (await locked_accounts(session, club_id))[club_id]
        cash = account.club.budget
    import goaletto.application.candidates as module

    async def fail(*args, **kwargs):
        raise RuntimeError("injected replenishment failure")

    monkeypatch.setattr(module, "replenish_pool", fail)
    with pytest.raises(RuntimeError, match="injected replenishment"):
        async with sessions.begin() as session:
            await sign_candidate(
                session, action(actor), screen.ownership_id, item.person.id, item.offer.fee, at=NOW
            )
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        assert account.club.budget == cash
        assert (await session.get(Player, item.person.id)).club_id is None
        assert (await session.get(CandidateOffer, item.person.id)).status == "available"
        assert (await session.get(CandidatePool, ("player", "player"))).generation == 12
        assert await session.scalar(select(func.count()).select_from(CandidateSigning)) == 0


async def test_actual_telegram_router_signs_player_and_staff_after_commit(sessions):
    from datetime import UTC, datetime

    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from goaletto.application.onboarding import next_initial_kickoff
    from goaletto.bot.main import build_dispatcher
    from tests.integration.test_agent_wave import OPTIONS
    from tests.integration.test_game_dispatcher import Source, Telegram

    now = datetime.now(UTC)
    _, _, club_id = await funded(
        sessions, options=replace(OPTIONS, first_kickoff=next_initial_kickoff(now)), at=now
    )
    source = Source(sessions)
    transport = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    number = 100

    async def send(text=None, data=None):
        nonlocal number
        number += 1
        now = datetime.now(UTC)
        update = (
            Update(
                update_id=number,
                message=Message(
                    message_id=number,
                    date=now,
                    chat=Chat(id=1001, type="private"),
                    from_user=user,
                    text=text,
                ),
            )
            if text
            else Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=f"candidate-{number}",
                    from_user=user,
                    chat_instance="test",
                    message=transport.last_message,
                    data=data,
                ),
            )
        )
        await dispatcher.feed_update(bot, update)
        return transport.last_message

    for command in ("/freeagents", "/staff"):
        screen = await send(text=command)
        if command == "/staff":
            screen = await send(data="cg:h:2")  # Head coaches on the separate staff market.
        choices = [
            b.callback_data
            for row in screen.reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith("cg:q:" if command == "/freeagents" else "cg:v:")
        ]
        button = choices[1 if command == "/staff" else 0]
        if command == "/staff":
            card = await send(data=button)
            assert card.photo and "Зарплата:" in card.caption and "Главный тренер" in card.caption
            button = next(
                b.callback_data
                for row in card.reply_markup.inline_keyboard
                for b in row
                if (b.callback_data or "").startswith("cg:q:")
            )
        quote = await send(data=button)
        assert "Разово:" in quote.text and (
            "Зарплата:" in quote.text if command == "/freeagents" else "Приходит:" in quote.text
        )
        confirm = quote.reply_markup.inline_keyboard[0][0].callback_data
        assert (
            confirm.startswith("cg:b:" if command == "/freeagents" else "cg:x:")
            and len(confirm.encode()) <= 64
        )
        signed = await send(data=confirm)
        assert ("Контракт подписан" if command == "/freeagents" else "Штаб обновлён") in signed.text
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(CandidateSigning)) == 2
        assert (
            await session.scalar(
                select(func.count()).select_from(Player).where(Player.club_id == club_id)
            )
            == 21
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Staff).where(Staff.club_id == club_id)
            )
            == 1
        )
        assert (await session.get(CandidatePool, ("player", "player"))).generation == 13
    screen = await send(text="/staff")
    employee = next(
        b.callback_data
        for row in screen.reply_markup.inline_keyboard
        for b in row
        if (b.callback_data or "").startswith("cg:v:")
    )
    card = await send(data=employee)
    assert not any(
        (b.callback_data or "").startswith("cg:f:")
        for row in card.reply_markup.inline_keyboard
        for b in row
    )
    assert "Главного тренера можно только заменить" in card.caption
    # An old/forged dismissal button is rejected by the application too.
    rejected = await send(data=employee.replace("cg:v:", "cg:f:"))
    assert "Главного тренера можно только заменить" in rejected.text
    async with sessions.begin() as session:
        assert await session.scalar(select(Staff.person_id).where(Staff.club_id == club_id))
    await bot.session.close()


async def test_free_player_can_enter_staff_pool_with_separate_career_receipts(sessions):
    from datetime import timedelta

    _, actor, club_id = await funded(sessions)
    player_action = action(actor)
    at = NOW + timedelta(hours=1)
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "player")
        item = next(i for i in screen.items if i.offer.profile == "DC")
        first = await sign_candidate(
            session, player_action, screen.ownership_id, item.person.id, item.offer.fee, at=NOW
        )
        person = await session.get(Person, item.person.id)
        identity = (person.id, person.appearance_identity_id, person.name)
        player = await session.get(Player, person.id)
        season_id = person.age_applied_for_season_id
        await close_salary(session, person.id, at)
        player.club_id = None
        player.status, player.last_season_id, player.retired_after_season_id = (
            "retired",
            season_id,
            season_id,
        )
        person.career, person.age = "retired", 36
        await session.flush()
        from goaletto.application.staff_career import assign_career
        from goaletto.application.staff_changes import execute_change, quote_change

        career = await assign_career(session, person, season_id, club_id, at)
        offer = await session.get(CandidateOffer, person.id)
        assert offer.kind == "staff" and offer.profile == career.role
        second_action = replace(action(actor), at=at)
        quote = await quote_change(
            session,
            action(second_action),
            screen.ownership_id,
            incoming_id=person.id,
            quoted_fee=offer.fee,
        )
        await execute_change(session, second_action, quote.id, at=at)
        second = await session.get(CandidateSigning, second_action.operation_id)
        assert second.kind == "staff" and second.profile == career.role
        assert (person.id, person.appearance_identity_id, person.name) == identity
        assert (await session.get(Player, person.id)).status == "retired"
        assert (await session.get(Staff, person.id)).club_id == club_id
        history = (
            await session.scalars(
                select(CandidateSigning).where(CandidateSigning.person_id == person.id)
            )
        ).all()
        assert {r.kind: (r.profile, r.fee) for r in history} == {
            "player": ("DC", first.fee),
            "staff": (career.role, second.fee),
        }
        assert (
            await sign_candidate(
                session, player_action, screen.ownership_id, person.id, first.fee, at=at
            )
        ).kind == "player"
        assert (
            len(
                (
                    await session.scalars(
                        select(CandidateSigning).where(CandidateSigning.person_id == person.id)
                    )
                ).all()
            )
            == 2
        )


async def test_new_staff_quality_is_persisted_and_people_are_not_regenerated(sessions):
    from goaletto.domain.staff_quality import quality_tier
    from goaletto.infrastructure.models import StaffQualification

    _, actor, _ = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "staff")
        identities = {
            i.person.id: (
                i.person.age,
                i.person.appearance_identity_id,
                i.person.appearance_variant_id,
            )
            for i in screen.items
        }
        assert len(identities) == 14
        assert all(50 <= i.person.age <= 56 for i in screen.items)
        for item in screen.items:
            qualification = await session.get(
                StaffQualification, (item.person.id, item.offer.profile)
            )
            assert qualification.quality_tier == quality_tier(item.skills[0])
            assert qualification.policy_version == "staff-quality-v1"
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "staff")
        assert identities == {
            i.person.id: (
                i.person.age,
                i.person.appearance_identity_id,
                i.person.appearance_variant_id,
            )
            for i in screen.items
        }
