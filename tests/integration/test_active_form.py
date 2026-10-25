"""Inactivity clocks, frozen live participants, assists and awards on PostgreSQL."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from goaletto.application.club_view import get_player_card
from goaletto.application.history import get_match_report
from goaletto.application.recovery import recover_player
from goaletto.application.results import finalize_result
from goaletto.bot.history_cards import report_view
from goaletto.infrastructure.models import Match, MatchDevelopment, MatchTick, Player
from tests.integration.test_agent_wave import NOW, start_fixture
from tests.integration.test_candidates import action, funded
from tests.integration.test_friendly import _play
from tests.integration.test_match_practice_runtime import completed_checkpoint

pytestmark = pytest.mark.integration


async def test_idle_form_settles_once_by_elapsed_time_on_player_card(sessions):
    from dataclasses import replace

    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        player = await session.scalar(select(Player).where(Player.club_id == club))
        player.form, player.form_activity_at, player.form_idle_steps = 8, NOW, 0
        person_id = player.person_id
    late = replace(action(actor), at=NOW + timedelta(days=7))
    async with sessions.begin() as session:
        assert (await get_player_card(session, late, person_id)).form == 6
    async with sessions.begin() as session:
        assert (await get_player_card(session, action(late), person_id)).form == 6
        player = await session.get(Player, person_id)
        assert player.form_idle_steps == 2 and player.form_activity_at == NOW


async def test_live_match_freezes_form_and_finalization_uses_actual_finish_once(sessions):
    _, _, club = await funded(sessions)
    fixture, at = await start_fixture(sessions, club)
    async with sessions.begin() as session:
        from goaletto.domain.match_codec import restore_state

        match = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        state = restore_state(match.state)
        team = state.home if state.home.snapshot.club_id == club else state.away
        person_id = team.lineup[0].player_id
        before = (await session.get(Player, person_id)).form
        await recover_player(session, person_id, at + timedelta(days=20))
        assert (await session.get(Player, person_id)).form == before
    final = await completed_checkpoint(sessions, fixture, at)
    async with sessions.begin() as session:
        await finalize_result(session, fixture, at + timedelta(hours=2))
        player = await session.get(Player, person_id)
        assert player.form_activity_at == at + timedelta(minutes=33)
        assert player.form_idle_steps == 0
        receipt = await session.get(MatchDevelopment, final.match_id)
        assert sum(bool(row["player_of_match"]) for row in receipt.results) == 1
        saved = player.form
        await finalize_result(session, fixture, at + timedelta(hours=3))
        assert player.form == saved


async def test_real_ticks_persist_assists_and_compact_report_award(sessions):
    _, actor, club = await funded(sessions)
    fixture, _ = await start_fixture(sessions, club)
    await _play(sessions, fixture)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        ticks = (
            await session.scalars(select(MatchTick).where(MatchTick.match_id == match.id))
        ).all()
        assisted = [
            e
            for tick in ticks
            for e in tick.events
            if e["kind"] == "goal" and e.get("assist_player_id")
        ]
        assert assisted
        receipt = await session.get(MatchDevelopment, match.id)
        assert sum(r["assists"] for r in receipt.results) == len(assisted)
        report = await get_match_report(session, action(actor), fixture)
        pages = []
        for page in range(1, 100):
            body, keyboard = report_view(report, page=page)
            pages.append(body)
            if not any(
                button.text == "Следующая →" for row in keyboard.inline_keyboard for button in row
            ):
                break
        body = "\n".join(pages)
        assert " · пас " in body and "⭐ Игрок матча:" in body
        own = "home" if report.home_club_id == club else "away"
        text, _ = report_view(report, section=own)
        assert "Форма до матча" not in text and "Форма после матча" not in text
        assert "множитель" not in text and "потенциал" not in text
