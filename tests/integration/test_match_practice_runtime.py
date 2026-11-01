"""Persisted practice, legacy continuation and false competition admission guards."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.history import get_match_report
from goaletto.application.match_development import apply_development
from goaletto.application.match_simulation import simulate_match
from goaletto.application.results import finalize_result
from goaletto.bot.history_cards import report_view
from goaletto.domain.match_codec import restore_state, state_document
from goaletto.domain.match_development import POLICY as LEGACY_POLICY
from goaletto.domain.match_practice import PERCENT_POLICY as CURRENT_POLICY
from goaletto.infrastructure.models import (
    Fixture,
    FixturePlan,
    Match,
    MatchDevelopment,
    Player,
    PlayerDevelopment,
)
from tests.integration.test_agent_wave import KICKOFF, start_fixture
from tests.integration.test_candidates import action, funded

pytestmark = pytest.mark.integration


async def completed_checkpoint(sessions, fixture_id, at):
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, fixture_id)
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        final, _ = simulate_match(restore_state(match.state))
        match.state, match.phase, match.completed_tick = state_document(final), "full_time", 30
        match.home_goals, match.away_goals = final.home.stats.goals, final.away.stats.goals
        match.next_due_at = None
        fixture.status, fixture.actual_finish_at = "finished", at + timedelta(minutes=33)
    return final


async def test_concurrent_finalize_persists_practice_once_and_history_is_frozen(sessions):
    _, actor, club = await funded(sessions)
    # Give form 10 before the snapshot: V3 must permit a fall from the maximum.
    async with sessions.begin() as session:
        players = (await session.scalars(select(Player))).all()
        for player in players:
            player.form = 10
            player.form_activity_at = KICKOFF
    fixture, at = await start_fixture(sessions, club)
    final = await completed_checkpoint(sessions, fixture, at)
    async with sessions.begin() as session:
        pinned = await session.get(MatchDevelopment, final.match_id)
        assert pinned.policy_version == CURRENT_POLICY and pinned.eligible
        assert pinned.terms_snapshot["competition"] == "league"
        terms = dict(pinned.terms_snapshot)
        before = {p.person_id: p.form for p in (await session.scalars(select(Player))).all()}

    async def finish():
        async with sessions.begin() as session:
            return await finalize_result(session, fixture, at + timedelta(hours=1))

    async with sessions.begin() as stale:
        cached = await stale.get(MatchDevelopment, final.match_id)
        assert cached.applied_at is None
        outcomes = await asyncio.wait_for(asyncio.gather(finish(), finish()), 20)
        assert sorted(outcomes) == [False, True]
        # A long-lived session must refresh its cached pending receipt under lock.
        assert cached.applied_at is None
        await apply_development(stale, final, at + timedelta(hours=2))
        assert cached.applied_at is not None
    async with sessions.begin() as session:
        pinned = await session.get(MatchDevelopment, final.match_id)
        assert pinned.terms_snapshot == terms
        rows = {r["person_id"]: r for r in pinned.results}
        for team in (final.home, final.away):
            for condition in team.conditions:
                player = await session.get(Player, condition.player_id)
                row = rows.get(str(condition.player_id))
                if not condition.minutes_played:
                    assert row is not None and row["experience_gain"] == row["gain_bps"] == 0
                    assert player.form == before[player.person_id]
                    continue
                assert row["minutes"] == condition.minutes_played
                assert row["form_before"] == 10 and row["form_after"] == player.form
                assert row["competition"] == "league" and row["skill_factor"] == 10000
                assert 0 <= row["form_draw"] < 10000
                expected = 9 if row["form_draw"] < row["form_down_chance"] else 10
                assert player.form == expected
                dev = await session.get(PlayerDevelopment, player.person_id)
                assert dev.season_gains["match"] == row["gain_bps"]
        report = await get_match_report(session, action(actor), fixture)
        own = "home" if report.home_club_id == club else "away"
        for member in (*report.home_lineup, *report.away_lineup):
            row = rows.get(str(member.person_id))
            assert member.form_after == (
                row["form_after"] if row and row["club_id"] == str(club) else None
            )
        frozen = report_view(report, section=own)[0]
        members = report.home_lineup if own == "home" else report.away_lineup
        changed = await session.get(Player, members[0].person_id)
        changed.form = 2
        await session.flush()
        reread = await get_match_report(session, action(actor), fixture)
        assert report_view(reread, section=own)[0] == frozen
        await apply_development(session, final, at + timedelta(hours=2))
        assert changed.form == 2  # Direct retry never applies or rerolls again.
    for assignment in ("terms_snapshot='{}'::jsonb", "results='[]'::jsonb"):
        with pytest.raises(DBAPIError):
            async with sessions.begin() as session:
                await session.execute(
                    text(f"UPDATE match_development SET {assignment} WHERE match_id=:id"),
                    {"id": final.match_id},
                )


async def test_unfinished_game_cannot_apply_practice_and_pin_is_immutable(sessions):
    _, _, club = await funded(sessions)
    fixture, at = await start_fixture(sessions, club)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        state = restore_state(match.state)
        with pytest.raises(ValueError, match="finalized gameplay result"):
            await apply_development(session, state, at)
        assert (await session.get(MatchDevelopment, match.id)).applied_at is None
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text(
                    "UPDATE match_development SET terms_snapshot="
                    "jsonb_set(terms_snapshot,'{friendly_skill_factor}','2500') WHERE match_id=:id"
                ),
                {"id": state.match_id},
            )


@pytest.mark.parametrize("competition", ["cup", "champions", "friendly"])
async def test_renaming_a_league_plan_does_not_create_another_competition_runtime(
    sessions, monkeypatch, competition
):
    import goaletto.application.match_development as module

    _, _, club = await funded(sessions)
    original = module.pin_development

    async def renamed(session, fixture, state, at):
        plan = await session.get(FixturePlan, fixture.id)
        plan.competition = competition
        await original(session, fixture, state, at)

    monkeypatch.setattr(module, "pin_development", renamed)
    fixture, at = await start_fixture(sessions, club)
    final = await completed_checkpoint(sessions, fixture, at)
    async with sessions.begin() as session:
        await finalize_result(session, fixture, at + timedelta(hours=1))
        receipt = await session.get(MatchDevelopment, final.match_id)
        assert not receipt.eligible and receipt.applied_at and receipt.results == []
        assert receipt.terms_snapshot["competition"] == competition


@pytest.mark.parametrize("legacy", [True, False])
async def test_legacy_pin_and_missing_receipts_never_gain_new_form(sessions, monkeypatch, legacy):
    import goaletto.application.match_development as module

    _, actor, club = await funded(sessions)

    async def legacy_pin(session, fixture, state, at):
        if legacy:
            staff = {
                str(t.snapshot.club_id): await module.staff_roster(session, t.snapshot.club_id, at)
                for t in (state.home, state.away)
            }
            session.add(
                MatchDevelopment(
                    match_id=state.match_id,
                    policy_version=LEGACY_POLICY,
                    eligible=True,
                    staff_snapshot=staff,
                    locked_at=at,
                    results=[],
                )
            )

    monkeypatch.setattr(module, "pin_development", legacy_pin)
    fixture, at = await start_fixture(sessions, club)
    final = await completed_checkpoint(sessions, fixture, at)
    async with sessions.begin() as session:
        before = {p.person_id: p.form for p in (await session.scalars(select(Player))).all()}
        await finalize_result(session, fixture, at + timedelta(hours=1))
        after = {p.person_id: p.form for p in (await session.scalars(select(Player))).all()}
        assert before == after
        receipt = await session.get(MatchDevelopment, final.match_id)
        if legacy:
            assert receipt.policy_version == LEGACY_POLICY and receipt.terms_snapshot == {}
            assert receipt.results and all("form_after" not in r for r in receipt.results)
            report = await get_match_report(session, action(actor), fixture)
            own = "home" if report.home_club_id == club else "away"
            body = report_view(report, section=own)[0]
            assert "Опыт:" in body and "/100 (+" in body and "—/100" not in body
        else:
            assert receipt is None
