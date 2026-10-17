"""Durable paid academy intakes on the single development DB with real ledger rows."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.academy import load_academy, sign_candidate, upgrade_academy
from goaletto.application.finances import cycle_finances
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.money import InsufficientFunds, debit_locked, locked_accounts
from goaletto.domain.academy import candidate_count, sign_limit, skill_band
from goaletto.infrastructure.models import (
    AcademyCandidate,
    Job,
    LedgerEntry,
    Person,
    Player,
    PlayerDevelopment,
    SalaryContract,
)
from tests.integration.test_agent_wave import NOW
from tests.integration.test_candidates import action, funded

pytestmark = pytest.mark.integration


async def test_intake_is_durable_single_and_signing_persists_person(sessions):
    _, actor, club_id = await funded(sessions)
    request = action(actor)
    async with sessions.begin() as session:
        screen = await load_academy(session, action(actor))
        assert screen.level == 1 and screen.sign_limit == sign_limit(1) == 1
        assert len(screen.candidates) == candidate_count(1) == 3
        low, high = skill_band(1)
        for view in screen.candidates:
            assert view.age == 17
            assert len(view.alternatives) <= 2 and view.position not in view.alternatives
            for name, value in view.skills.items():
                if name in ("leadership", "loyalty"):
                    assert low <= value <= high + 5
                elif name == "goalkeeping" and view.position != "GK":
                    assert 1 <= value <= 3
                else:
                    assert low <= value <= high
        slots = [view.slot for view in screen.candidates]
        names = [view.name for view in screen.candidates]
    async with sessions.begin() as session:
        again = await load_academy(session, action(actor))
        assert [view.slot for view in again.candidates] == slots
        assert [view.name for view in again.candidates] == names
        assert await session.scalar(select(func.count()).select_from(AcademyCandidate)) == 3

    slot = slots[0]
    async with sessions.begin() as session:
        person_id = await sign_candidate(session, request, screen.ownership_id, slot)
        row = await session.get(AcademyCandidate, person_id)
        assert row is not None and row.status == "signed" and row.signed_at == NOW
        player = await session.get(Player, person_id)
        assert player is not None
        assert player.club_id == club_id and player.position == row.position
        assert player.alternative_positions == list(row.alternative_positions)
        assert player.form == 6 and player.morale == 50 and player.experience == 0
        dev = await session.get(PlayerDevelopment, person_id)
        assert dev is not None and dict(dev.caps) == dict(row.caps)
        contract = await session.scalar(
            select(SalaryContract).where(SalaryContract.person_id == person_id)
        )
        assert contract is not None and contract.club_id == club_id and contract.role == "player"
        assert await session.get(Person, person_id) is not None
    async with sessions.begin() as session:
        assert await sign_candidate(session, request, screen.ownership_id, slot) == person_id
        assert (
            await session.scalar(
                select(func.count()).select_from(Player).where(Player.club_id == club_id)
            )
            == 21
        )
    other = next(item for item in slots if item != slot)
    with pytest.raises(ValueError, match="limit"):
        async with sessions.begin() as session:
            await sign_candidate(session, action(actor), screen.ownership_id, other)
    with pytest.raises(PermissionDenied):
        async with sessions.begin() as session:
            await sign_candidate(session, action(actor), uuid4(), other)


async def test_upgrade_charges_once_and_cycle_raises_upkeep(sessions):
    _, actor, club_id = await funded(sessions)
    request = action(actor)
    async with sessions.begin() as session:
        screen = await load_academy(session, action(actor))
    async with sessions.begin() as session:
        assert await upgrade_academy(session, request, screen.ownership_id) == 2
        assert await upgrade_academy(session, request, screen.ownership_id) == 2
        entry = await session.scalar(
            select(LedgerEntry).where(LedgerEntry.kind == "academy_upgrade")
        )
        assert entry is not None and entry.cash_delta == -200_000
        assert entry.request["amount"] == 200_000
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "academy_upgrade")
            )
            == 1
        )
        assert (await locked_accounts(session, club_id))[
            club_id
        ].available == screen.available - 200_000
    async with sessions.begin() as session:
        job = await session.scalar(
            select(Job).where(
                Job.kind == "finance.cycle", Job.payload["club_id"].astext == str(club_id)
            )
        )
        await cycle_finances(session, job, job.due_at)
        upkeep = await session.scalar(
            select(LedgerEntry).where(LedgerEntry.kind == "academy_upkeep")
        )
        assert (
            upkeep is not None and upkeep.request["amount"] == 4_500
        )  # first partial cycle: 36 of 96 hours
    with pytest.raises(PermissionDenied):
        async with sessions.begin() as session:
            await upgrade_academy(session, action(actor), uuid4())


async def test_squad_limit_and_wallet_block_signing(sessions, monkeypatch):
    from goaletto.application import academy as module

    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_academy(session, action(actor))
        slot = screen.candidates[0].slot
    monkeypatch.setattr(module, "ROSTER_LIMIT", 20)
    with pytest.raises(ValueError, match="Squad limit"):
        async with sessions.begin() as session:
            await sign_candidate(session, action(actor), screen.ownership_id, slot)
    monkeypatch.undo()
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        await debit_locked(session, account, "drain", "stadium_upgrade", account.available, NOW)
    with pytest.raises(InsufficientFunds):
        async with sessions.begin() as session:
            await upgrade_academy(session, action(actor), screen.ownership_id)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(AcademyCandidate)) == 3
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "academy_upgrade")
            )
            == 0
        )


async def test_candidate_rows_and_club_level_are_constrained(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_academy(session, action(actor))
        person_id = await sign_candidate(session, action(actor), screen.ownership_id, 1)
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE academy_candidates SET status='expired' WHERE id=:id"),
                {"id": person_id},
            )
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE academy_candidates SET age=99 WHERE id=:id"), {"id": person_id}
            )
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE clubs SET academy_level=9 WHERE id=:id"), {"id": club_id}
            )


async def test_telegram_router_opens_signs_and_upgrades(sessions, monkeypatch):
    from datetime import datetime as real_datetime

    import goaletto.bot.academy_router as module

    class Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(module, "datetime", Clock)
    from datetime import UTC, datetime

    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from goaletto.bot.main import build_dispatcher
    from tests.integration.test_game_dispatcher import Source, Telegram

    _, _, club_id = await funded(sessions)
    source = Source(sessions)
    transport = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    number = 500

    async def send(text=None, data=None):
        nonlocal number
        number += 1
        if text:
            update = Update(
                update_id=number,
                message=Message(
                    message_id=number,
                    date=datetime.now(UTC),
                    chat=Chat(id=1001, type="private"),
                    from_user=user,
                    text=text,
                ),
            )
        else:
            update = Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=f"academy-ui-{number}",
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

    message = await send(text="/academy")
    assert "Академия" in message.text and "Уровень: 1/5" in message.text
    sign = button(message, "ac:g:")
    message = await send(data=sign)
    assert "Юноша подписан" in message.text
    async with sessions.begin() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Player).where(Player.club_id == club_id)
            )
            == 21
        )
    upgrade = button(message, "ac:u:")
    message = await send(data=upgrade)
    assert "Академия улучшена" in message.text and "Уровень: 2/5" in message.text
    message = await send(data=button(message, "ac:o:"))
    assert "Уровень: 2/5" in message.text
    await bot.session.close()


async def test_frozen_intake_free_signing_and_stale_upgrade_quotes(sessions):
    from goaletto.infrastructure.models import AcademyIntake

    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_academy(session, action(actor))
        before = (await locked_accounts(session, club_id))[club_id].available
        person_id = await sign_candidate(
            session, action(actor), screen.ownership_id, 1, season_id=screen.season_id
        )
        assert (await locked_accounts(session, club_id))[club_id].available == before
        person = await session.get(Person, person_id)
        assert person.age == 17 and person.age_applied_for_season_id == screen.season_id
        assert (
            await upgrade_academy(session, action(actor), screen.ownership_id, expected_level=1)
            == 2
        )
        frozen = await session.get(AcademyIntake, (club_id, screen.season_id))
        assert frozen.level == 1
        refreshed = await load_academy(session, action(actor))
        assert refreshed.level == 2 and refreshed.sign_limit == 1
        assert len(refreshed.candidates) == 3
    with pytest.raises(ValueError, match="price changed"):
        async with sessions.begin() as session:
            await upgrade_academy(session, action(actor), screen.ownership_id, expected_level=1)
    with pytest.raises(ValueError, match="another season"):
        async with sessions.begin() as session:
            await sign_candidate(session, action(actor), screen.ownership_id, 2, season_id=uuid4())
    with pytest.raises(ValueError, match="limit"):
        async with sessions.begin() as session:
            await sign_candidate(session, action(actor), screen.ownership_id, 2)


async def test_season_open_releases_unsigned_graduates_once(sessions):
    from dataclasses import replace

    from goaletto.infrastructure.models import AcademyIntake, Season
    from tests.integration.test_agent_wave import job

    result, actor, _club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_academy(session, action(actor))
        rows = (await session.scalars(select(AcademyCandidate))).all()
        ids = {r.id for r in rows}
        signed = await sign_candidate(session, action(actor), screen.ownership_id, rows[0].slot)
        season = await session.get(Season, result.season_id)
        late = replace(action(actor), at=season.starts_at)
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        screen = await load_academy(session, late)
        assert not screen.intake_open
        assert await session.scalar(select(func.count()).select_from(AcademyIntake)) == 1
        rows = (await session.scalars(select(AcademyCandidate))).all()
        assert {r.id for r in rows} == ids
        assert sorted(r.status for r in rows) == ["expired", "expired", "signed"]
        released = (
            await session.scalars(select(Player).where(Player.person_id.in_(ids - {signed})))
        ).all()
        assert len(released) == 2 and all(p.club_id is None for p in released)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SalaryContract)
                .where(SalaryContract.person_id.in_(ids - {signed}))
            )
            == 0
        )
        from goaletto.application.academy import release_intakes

        await release_intakes(session, result.season_id, late.at)
        assert (
            await session.scalar(select(func.count()).select_from(Person).where(Person.id.in_(ids)))
            == 3
        )
    with pytest.raises(ValueError, match="offseason"):
        async with sessions.begin() as session:
            await sign_candidate(session, late, screen.ownership_id, rows[1].slot)


async def test_delayed_upkeep_uses_historical_levels(sessions):
    from dataclasses import replace
    from datetime import timedelta

    from goaletto.application.academy import upkeep_for_period

    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_academy(session, action(actor))
        await upgrade_academy(
            session, replace(action(actor), at=NOW + timedelta(days=2)), screen.ownership_id
        )
        await upgrade_academy(
            session, replace(action(actor), at=NOW + timedelta(days=6)), screen.ownership_id
        )
        # Reading the first bill after the second upgrade must not apply level 3 retroactively.
        assert await upkeep_for_period(session, club_id, NOW, NOW + timedelta(days=4)) == 9_000
        assert (
            await upkeep_for_period(
                session, club_id, NOW + timedelta(days=4), NOW + timedelta(days=8)
            )
            == 16_000
        )
