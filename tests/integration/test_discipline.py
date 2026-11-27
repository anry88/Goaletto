"""Real worker locks/finalization on the one disposable LOCAL application DB."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.calendar_planning import plan_horizon
from goaletto.application.discipline import apply_discipline, tournament_scope
from goaletto.application.history import get_match_report
from goaletto.application.lineup import LineupInput, load_lineup, save_lineup
from goaletto.application.match_runtime import handle_calendar_job
from goaletto.application.results import finalize_result
from goaletto.bot.history_cards import report_view
from goaletto.bot.lineup_views import render_main
from goaletto.domain.lineup import LineupInvalid, StarterPick
from goaletto.domain.match_codec import restore_state
from goaletto.domain.match_types import EnginePolicy, MatchEvent, Side
from goaletto.infrastructure.models import (
    DisciplineReceipt,
    Fixture,
    Job,
    LineupSlot,
    Match,
    MatchDiscipline,
    MatchResult,
    Player,
    PlayerDiscipline,
)
from tests.integration.test_agent_wave import NOW, job, setup_world
from tests.integration.test_cup_runtime import next_job, play_fixture

pytestmark = pytest.mark.integration


def controlled_cards(monkeypatch, target, club_id, *, red=False):
    """Use the real engine with deterministic injected incidents for this test."""
    import goaletto.application.match_runtime as runtime
    from goaletto.domain.match_engine import advance_tick

    monkeypatch.setattr(
        runtime,
        "EnginePolicy",
        lambda **kw: replace(EnginePolicy(**kw), yellow_bps=0, direct_red_bps=0, injury_bps=0),
    )

    def advance(state, number, *args, **kwargs):
        result = advance_tick(state, number, *args, **kwargs)
        new = result.state
        side = Side.HOME if new.home.snapshot.club_id == club_id else Side.AWAY
        team = new.home if side == Side.HOME else new.away
        if (
            number != 1
            or team.snapshot.club_id != club_id
            or target not in {s.player_id for s in team.lineup}
        ):
            return result
        team = replace(
            team,
            conditions=tuple(
                replace(c, yellows=0 if red else 1, dismissed=red) if c.player_id == target else c
                for c in team.conditions
            ),
        )
        if red:
            team = replace(
                team,
                lineup=tuple(s for s in team.lineup if s.player_id != target),
                removed=team.removed + (target,),
            )
        new = replace(new, **{side.value: team})
        event = MatchEvent(
            uuid5(state.match_id, "test-card"),
            "red" if red else "yellow",
            1,
            3,
            side,
            target,
            None,
            "direct_red" if red else None,
        )
        return replace(result, state=new, events=(*result.events, event))

    monkeypatch.setattr(runtime, "advance_tick", advance)


async def fixture_list(sessions, season_id, league_id, round_number):
    async with sessions.begin() as session:
        first = await session.scalar(
            select(Fixture).where(
                Fixture.season_id == season_id,
                Fixture.league_id == league_id,
                Fixture.round_number == 1,
            )
        )
        await plan_horizon(
            session, season_id, first.scheduled_start_at + timedelta(days=4 * (round_number - 1))
        )
        return list(
            await session.scalars(
                select(Fixture.id)
                .where(
                    Fixture.season_id == season_id,
                    Fixture.league_id == league_id,
                    Fixture.round_number == round_number,
                )
                .order_by(Fixture.scheduled_start_at, Fixture.id)
            )
        )


async def test_two_yellows_ban_next_same_tournament_rollback_retry_and_lineup(
    sessions, monkeypatch
):
    world, actor, club_id = await setup_world(sessions)
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        target = await session.scalar(
            select(LineupSlot.person_id).where(LineupSlot.club_id == club_id, LineupSlot.slot == 2)
        )
        first = await session.scalar(
            select(Fixture)
            .where(
                Fixture.season_id == world.season_id,
                Fixture.competition == "league",
                (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
            )
            .order_by(Fixture.round_number)
        )
        league_id = first.league_id
        scope = await tournament_scope(session, first)
    controlled_cards(monkeypatch, target, club_id)
    for fixture_id in await fixture_list(sessions, world.season_id, league_id, 1):
        await play_fixture(sessions, fixture_id)
    async with sessions.begin() as session:
        account = await session.get(PlayerDiscipline, (scope, target))
        assert (account.yellows, account.bans) == (1, 0)

    import goaletto.application.match_development as development

    original = development.apply_development

    async def fail_after_discipline(session, state, at):
        if target in {c.player_id for t in (state.home, state.away) for c in t.conditions}:
            raise RuntimeError("rollback after discipline")
        await original(session, state, at)

    for fixture_id in await fixture_list(sessions, world.season_id, league_id, 2):
        async with sessions.begin() as session:
            fixture = await session.get(Fixture, fixture_id)
            ours = club_id in (fixture.home_club_id, fixture.away_club_id)
        if ours:
            monkeypatch.setattr(development, "apply_development", fail_after_discipline)
            with pytest.raises(RuntimeError, match="rollback after discipline"):
                await play_fixture(sessions, fixture_id, separate=True)
            async with sessions.begin() as session:
                account = await session.get(PlayerDiscipline, (scope, target))
                assert (account.yellows, account.bans) == (1, 0)
                match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
                assert await session.get(MatchResult, match.id) is None
                assert (await session.get(MatchDiscipline, match.id)).applied_at is None
            monkeypatch.setattr(development, "apply_development", original)
        await play_fixture(sessions, fixture_id, separate=True)
        if ours:
            second_id = fixture_id
    async with sessions.begin() as session:
        account = await session.get(PlayerDiscipline, (scope, target))
        assert (account.yellows, account.bans) == (0, 1)
        second = await session.get(Fixture, second_id)
        assert not await finalize_result(session, second.id, second.actual_finish_at)
        report = await get_match_report(
            session, replace(actor, at=second.actual_finish_at, operation_id=uuid4()), second.id
        )
        assert any(r[2] == 1 for r in report.discipline)
        assert "пропуск следующего матча" in report_view(report, section="events", page=1)[0]

    fixtures = await fixture_list(sessions, world.season_id, league_id, 3)
    async with sessions.begin() as session:
        third = await session.scalar(
            select(Fixture).where(
                Fixture.id.in_(fixtures),
                (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
            )
        )
        third_id = third.id
        # Other competitions have their own balance and cannot serve this ban.
        cup = await session.scalar(
            select(Fixture).where(
                Fixture.competition == "cup",
                (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
            )
        )
        cup_scope = await tournament_scope(session, cup)
        assert cup_scope != scope
        assert await session.get(PlayerDiscipline, (cup_scope, target)) is None
    for fixture_id in fixtures:
        if fixture_id == third_id:
            async with sessions.begin() as session:
                row = await session.scalar(
                    select(Job).where(
                        Job.kind == "match.lock_lineup",
                        Job.payload["fixture_id"].astext == str(fixture_id),
                    )
                )
                await handle_calendar_job(session, row, row.due_at)
                match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
                state = restore_state(match.starting_snapshot)
                assert target not in {
                    p.id for t in (state.home, state.away) for p in t.snapshot.players
                }
                team = state.home if state.home.snapshot.club_id == club_id else state.away
                assert len(team.lineup) == 11 and team.substitutions == 0
                assert (
                    await session.scalar(
                        select(LineupSlot.person_id).where(
                            LineupSlot.club_id == club_id, LineupSlot.slot == 2
                        )
                    )
                    == target
                )
                assert (await session.get(PlayerDiscipline, (scope, target))).bans == 1
        await play_fixture(sessions, fixture_id)
    async with sessions.begin() as session:
        assert (await session.get(PlayerDiscipline, (scope, target))).bans == 0
        match = await session.scalar(select(Match).where(Match.fixture_id == third_id))
        receipt = await session.get(DisciplineReceipt, (match.id, target))
        assert receipt.served and not receipt.awarded
        third = await session.get(Fixture, third_id)
        await apply_discipline(session, restore_state(match.state), third.actual_finish_at)
        assert (await session.get(PlayerDiscipline, (scope, target))).bans == 0


async def test_red_card_ban_and_immutable_receipts(sessions, monkeypatch):
    world, actor, club_id = await setup_world(sessions)
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        # Earliest actual fixture may be a cup: exercise its own scope and runtime.
        fixture = await session.scalar(
            select(Fixture)
            .where((Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id))
            .order_by(Fixture.scheduled_start_at)
        )
        target = await session.scalar(
            select(LineupSlot.person_id).where(LineupSlot.club_id == club_id, LineupSlot.slot == 2)
        )
        fixture_id = fixture.id
        scope = await tournament_scope(session, fixture)
    controlled_cards(monkeypatch, target, club_id, red=True)
    match_id = await play_fixture(sessions, fixture_id)
    async with sessions.begin() as session:
        account = await session.get(PlayerDiscipline, (scope, target))
        assert account.bans == 1
        receipt = await session.get(DisciplineReceipt, (match_id, target))
        assert receipt.dismissed and receipt.awarded == 1
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    text("UPDATE discipline_receipts SET awarded=0 WHERE match_id=:id"),
                    {"id": match_id},
                )


async def test_lineup_previews_replacement_and_rejects_stale_banned_pick(sessions):
    _, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        fixture = await session.scalar(
            select(Fixture)
            .where((Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id))
            .order_by(Fixture.scheduled_start_at)
        )
        target = await session.scalar(
            select(LineupSlot.person_id).where(LineupSlot.club_id == club_id, LineupSlot.slot == 2)
        )
        scope = await tournament_scope(session, fixture)
        session.add(PlayerDiscipline(scope=scope, person_id=target, bans=1, yellows=1))
        await session.flush()
        data = await load_lineup(session, actor)
        assert target not in {p.person_id for p in (*data.starters, *data.bench)}
        member = next(p for p in data.reserves if p.person_id == target)
        assert member.suspended and member.tournament_yellows == 1
        assert "⛔" in render_main(data) and "🟨1/2" in render_main(data)
        picks = tuple(StarterPick(p.person_id, p.slot_position) for p in data.starters)
        draft = LineupInput(
            picks,
            tuple(p.person_id for p in data.bench),
            data.formation,
            data.starters[0].person_id,
            data.tactic,
            data.mentality,
            data.version,
        )
        bad = replace(
            draft, starters=(picks[0], StarterPick(target, picks[1].slot_position), *picks[2:])
        )
        with pytest.raises(LineupInvalid, match="suspended"):
            await save_lineup(
                session, replace(actor, operation_id=uuid4(), update_id=700), bad, clock=lambda: NOW
            )
        good = await save_lineup(
            session, replace(actor, operation_id=uuid4(), update_id=701), draft, clock=lambda: NOW
        )
        assert good.version == data.version + 1


async def test_friendly_does_not_serve_or_add_official_cards(sessions, monkeypatch):
    from goaletto.application.friendly import schedule_friendly
    from tests.integration.test_friendly import _free_day_and_opponent

    _, actor, club_id = await setup_world(sessions)
    await job(sessions, "season.open")
    _, opponent, day = await _free_day_and_opponent(sessions, club_id)
    async with sessions.begin() as session:
        target = await session.scalar(
            select(LineupSlot.person_id).where(LineupSlot.club_id == club_id, LineupSlot.slot == 2)
        )
        league = await session.scalar(
            select(Fixture).where(
                Fixture.competition == "league",
                (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
            )
        )
        scope = await tournament_scope(session, league)
        session.add(PlayerDiscipline(scope=scope, person_id=target, bans=1, yellows=1))
        plan = await schedule_friendly(
            session, replace(actor, operation_id=uuid4(), update_id=702), opponent, day
        )
        fixture_id = plan.id
    controlled_cards(monkeypatch, target, club_id, red=True)
    match_id = await play_fixture(sessions, fixture_id)
    async with sessions.begin() as session:
        account = await session.get(PlayerDiscipline, (scope, target))
        assert (account.yellows, account.bans) == (1, 1)
        assert await session.get(MatchDiscipline, match_id) is None
        match = await session.get(Match, match_id)
        assert any(
            c.player_id == target and c.dismissed
            for t in (restore_state(match.state).home, restore_state(match.state).away)
            for c in t.conditions
        )


async def test_cup_suspension_allows_league_appearance_without_serving_cup_ban(
    sessions, monkeypatch
):
    from goaletto.application.club_view import get_squad_page
    from goaletto.application.discipline import SquadDiscipline

    _, actor, club_id = await setup_world(sessions)
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        target = await session.scalar(
            select(LineupSlot.person_id).where(LineupSlot.club_id == club_id, LineupSlot.slot == 2)
        )
        cup = await session.scalar(
            select(Fixture).where(
                Fixture.competition == "cup",
                (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
            )
        )
        league = await session.scalar(
            select(Fixture)
            .where(
                Fixture.competition == "league",
                (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
            )
            .order_by(Fixture.scheduled_start_at)
        )
        cup_scope = await tournament_scope(session, cup)
        league_scope = await tournament_scope(session, league)
        league_id = league.id
        session.add(PlayerDiscipline(scope=cup_scope, person_id=target, bans=1, yellows=0))
    controlled_cards(monkeypatch, target, club_id)
    match_id = await play_fixture(sessions, league_id)
    async with sessions.begin() as session:
        assert (await session.get(PlayerDiscipline, (cup_scope, target))).bans == 1
        league_account = await session.get(PlayerDiscipline, (league_scope, target))
        assert (league_account.yellows, league_account.bans) == (1, 0)
        squad = await get_squad_page(
            session, replace(actor, operation_id=uuid4(), update_id=998), 1
        )
        assert squad.discipline == (
            SquadDiscipline("league", (target,), ()),
            SquadDiscipline("cup", (), ((target, 1),)),
        )
        match = await session.get(Match, match_id)
        state = restore_state(match.state)
        assert any(
            c.player_id == target and c.minutes_played == 90
            for t in (state.home, state.away)
            for c in t.conditions
        )
        assert not any(
            r["person_id"] == str(target)
            for r in (await session.get(MatchDiscipline, match_id)).excluded
        )


async def test_suspended_keeper_uses_existing_outfield_reserve_when_other_keeper_is_injured(
    sessions, monkeypatch
):
    _, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        fixture = await session.scalar(
            select(Fixture)
            .where((Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id))
            .order_by(Fixture.scheduled_start_at)
        )
        fixture_id = fixture.id
        scope = await tournament_scope(session, fixture)
        keeper = await session.scalar(
            select(LineupSlot.person_id).where(LineupSlot.club_id == club_id, LineupSlot.slot == 1)
        )
        for p in await session.scalars(
            select(Player).where(
                Player.club_id == club_id, Player.position == "GK", Player.person_id != keeper
            )
        ):
            p.injured_until = fixture.scheduled_start_at + timedelta(days=1)
        session.add(PlayerDiscipline(scope=scope, person_id=keeper, bans=1, yellows=0))
        await session.flush()
        data = await load_lineup(session, actor)
        replacement = next(p for p in data.starters if p.slot_position == "GK")
        assert replacement.position != "GK" and replacement.person_id != keeper
        draft = LineupInput(
            tuple(StarterPick(p.person_id, p.slot_position) for p in data.starters),
            tuple(p.person_id for p in data.bench),
            data.formation,
            replacement.person_id,
            data.tactic,
            data.mentality,
            data.version,
        )
        await save_lineup(
            session, replace(actor, operation_id=uuid4(), update_id=703), draft, clock=lambda: NOW
        )
    await job(sessions, "season.open")
    controlled_cards(monkeypatch, keeper, club_id)
    match_id = await play_fixture(sessions, fixture_id)
    async with sessions.begin() as session:
        state = restore_state((await session.get(Match, match_id)).starting_snapshot)
        team = state.home if state.home.snapshot.club_id == club_id else state.away
        assert len(team.lineup) == 11 and team.substitutions == 0
        assert next(s.player_id for s in team.lineup if s.position == "GK") == replacement.person_id
        assert (await session.get(PlayerDiscipline, (scope, keeper))).bans == 0


async def test_retry_refreshes_a_cached_pending_discipline_receipt(sessions, monkeypatch):
    _, _, club_id = await setup_world(sessions)
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        fixture = await session.scalar(
            select(Fixture)
            .where((Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id))
            .order_by(Fixture.scheduled_start_at)
        )
        target = await session.scalar(
            select(LineupSlot.person_id).where(LineupSlot.club_id == club_id, LineupSlot.slot == 2)
        )
        fixture_id = fixture.id
        scope = await tournament_scope(session, fixture)
    controlled_cards(monkeypatch, target, club_id, red=True)
    await job(sessions, "match.lock_lineup", fixture_id)
    async with sessions.begin() as stale:
        match = await stale.scalar(select(Match).where(Match.fixture_id == fixture_id))
        header = await stale.get(MatchDiscipline, match.id)
        assert header.applied_at is None
        await play_fixture(sessions, fixture_id, separate=True)
        assert header.applied_at is None
        await stale.refresh(match)
        finished = await stale.get(Fixture, fixture_id)
        await apply_discipline(stale, restore_state(match.state), finished.actual_finish_at)
        assert header.applied_at is not None
        assert (await stale.get(PlayerDiscipline, (scope, target))).bans == 1


@pytest.mark.parametrize("second_yellow", [False, True])
async def test_abandoned_match_records_reds_but_does_not_serve_existing_ban(
    sessions, monkeypatch, second_yellow
):
    import goaletto.application.match_runtime as runtime

    _, _, club_id = await setup_world(sessions)
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        fixture = await session.scalar(
            select(Fixture)
            .where((Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id))
            .order_by(Fixture.scheduled_start_at)
        )
        fixture_id = fixture.id
        target = await session.scalar(
            select(LineupSlot.person_id).where(LineupSlot.club_id == club_id, LineupSlot.slot == 12)
        )
        scope = await tournament_scope(session, fixture)
        session.add(PlayerDiscipline(scope=scope, person_id=target, bans=1, yellows=0))
    monkeypatch.setattr(
        runtime,
        "EnginePolicy",
        lambda **kw: replace(
            EnginePolicy(**kw),
            yellow_bps=10000 if second_yellow else 0,
            direct_red_bps=0 if second_yellow else 10000,
            injury_bps=0,
        ),
    )
    async with sessions.begin() as session:
        while (row := await next_job(session, fixture_id)) is not None:
            await handle_calendar_job(session, row, row.due_at)
            row.status = "done"
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, fixture_id)
        assert fixture.status == "abandoned"
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        assert await session.get(MatchResult, match.id) is None
        assert (await session.get(MatchDiscipline, match.id)).applied_at is not None
        assert (await session.get(PlayerDiscipline, (scope, target))).bans == 1
        rows = list(
            await session.scalars(
                select(DisciplineReceipt).where(DisciplineReceipt.match_id == match.id)
            )
        )
        assert any(r.dismissed and r.awarded == 1 for r in rows)
        if second_yellow:
            assert any(r.yellows == 2 and r.awarded == 1 for r in rows)
        assert not any(r.served for r in rows)
        await apply_discipline(session, restore_state(match.state), fixture.actual_finish_at)


async def test_squad_shows_the_guest_clubs_league_cup_discipline(sessions):
    from goaletto.application.club_view import get_squad_page
    from goaletto.application.discipline import SquadDiscipline
    from goaletto.application.world import initialize_world
    from goaletto.infrastructure.models import (
        ChampionsDraw,
        ChampionsTie,
        ChampionsTournament,
        SeasonClub,
    )
    from tests.integration.test_agent_wave import OPTIONS

    world, actor, club_id = await setup_world(sessions)
    tournament_id, unrelated = uuid4(), uuid4()
    async with sessions.begin() as session:
        host = await initialize_world(
            session, replace(OPTIONS, key="champions-discipline-host", seed=1), initialized_at=NOW
        )
        opponent = await session.scalar(
            select(SeasonClub.club_id).where(SeasonClub.season_id == host.season_id).limit(1)
        )
        session.add_all(
            [
                ChampionsDraw(
                    id=uuid4(),
                    season_id=world.season_id,
                    qualifying_season_id=world.season_id,
                    league_set_id=world.league_set_id,
                    cohort_key="squad-test-host",
                    seed=0,
                    policy_version="champions-groups-v1",
                    snapshots=[{}] * 5,
                    created_at=NOW,
                ),
                ChampionsTournament(
                    id=tournament_id,
                    season_id=host.season_id,
                    qualifying_season_id=world.season_id,
                    tier=5,
                    group_index=0,
                    participant_count=8,
                    cohort_key="squad-test-host",
                    seed=0,
                    bracket_seed=0,
                    policy_version="champions-bracket-v1",
                    created_at=NOW,
                ),
            ]
        )
        await session.flush()
        session.add(
            ChampionsTie(
                id=uuid4(),
                tournament_id=tournament_id,
                season_id=host.season_id,
                round_number=1,
                tie_index=0,
                round_label="quarterfinal",
                home_kind="club",
                home_club_id=opponent,
                away_kind="club",
                away_club_id=club_id,
                champions_cycles=[1, 2],
                neutral=False,
                status="pending",
            )
        )
        target = await session.scalar(
            select(LineupSlot.person_id).where(LineupSlot.club_id == club_id, LineupSlot.slot == 2)
        )
        session.add_all(
            [
                PlayerDiscipline(
                    scope=f"champions:{tournament_id}", person_id=target, bans=2, yellows=1
                ),
                PlayerDiscipline(
                    scope=f"champions:{unrelated}", person_id=target, bans=9, yellows=0
                ),
            ]
        )
        await session.flush()
        squad = await get_squad_page(
            session, replace(actor, operation_id=uuid4(), update_id=999), 1
        )
        assert squad.discipline == (SquadDiscipline("champions", (target,), ((target, 2),)),)
