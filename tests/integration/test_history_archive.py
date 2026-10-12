"""Large-archive history checks on the single Goaletto development DB.

Builds a minimal synthetic archive (10 clubs, 2 seasons, 18 finished
fixtures for the owned club) and verifies bounded pagination, query counts
and EXPLAIN plans. No new databases; truncates via the shared fixture.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from goaletto.application.history import (
    HISTORY_PAGE_SIZE,
    get_club_results,
    get_player_matches,
)
from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.match_codec import state_document, state_hash

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
KICKOFF = demo_match(7)
FINAL, _ = simulate_match(KICKOFF)
INITIAL_DOC = state_document(KICKOFF)
TERMINAL_DOC = state_document(FINAL)
HOME_ID = FINAL.home.snapshot.club_id
AWAY_ID = FINAL.away.snapshot.club_id
STARTER_ID = FINAL.home.snapshot.players[0].id
BENCH_ID = FINAL.home.snapshot.players[15].id
OPPONENT_ID = FINAL.away.snapshot.players[0].id


async def _build_archive(sessions):
    from goaletto.domain.ownership import ownership_deadline
    from goaletto.infrastructure.models import (
        Club,
        Fixture,
        League,
        LeagueSet,
        Manager,
        Match,
        MatchResult,
        MatchTick,
        OwnershipPeriod,
        Person,
        Player,
        Season,
        SeasonClub,
        Standing,
    )

    league_set_id = uuid4()
    league_id = uuid4()
    season_ids = [uuid4(), uuid4()]
    club_ids = [HOME_ID] + [uuid4() for _ in range(8)] + [AWAY_ID]
    manager_id = uuid4()
    async with sessions.begin() as session:
        session.add(LeagueSet(id=league_set_id, name="synthetic-set"))
        await session.flush()
        session.add(League(id=league_id, league_set_id=league_set_id, tier=5))
        await session.flush()
        for idx, cid in enumerate(club_ids):
            session.add(
                Club(
                    id=cid,
                    league_id=league_id,
                    name=f"Club {idx}",
                    owner_telegram_id=123 if cid == HOME_ID else None,
                    budget=1000,
                    kit_primary="#743D42",
                    kit_secondary="#F2E9D8",
                    kit_version=1,
                    stadium_capacity=5000,
                    supporters=2500,
                )
            )
        await session.flush()
        for number, season_id in enumerate(season_ids, start=1):
            session.add(
                Season(
                    id=season_id,
                    league_set_id=league_set_id,
                    number=number,
                    status="active" if number == 2 else "finished",
                    seed=7,
                    starts_at=NOW - timedelta(weeks=20),
                    ends_at=NOW + timedelta(weeks=20),
                    rollover_completed_at=None,
                )
            )
        await session.flush()
        for season_id in season_ids:
            for order, cid in enumerate(club_ids, start=1):
                session.add(
                    SeasonClub(
                        season_id=season_id,
                        club_id=cid,
                        league_id=league_id,
                        league_set_id=league_set_id,
                        tie_break_order=order,
                    )
                )
        await session.flush()
        for season_id in season_ids:
            for cid in club_ids:
                session.add(
                    Standing(
                        season_id=season_id,
                        club_id=cid,
                        played=0,
                        won=0,
                        drawn=0,
                        lost=0,
                        goals_for=0,
                        goals_against=0,
                        points=0,
                    )
                )
        await session.flush()
        session.add(
            Manager(id=manager_id, telegram_id=123, reminders_enabled=True, bot_available=True)
        )
        await session.flush()
        last = NOW - timedelta(days=1)
        session.add(
            OwnershipPeriod(
                id=uuid4(),
                operation_key="synthetic-op",
                manager_id=manager_id,
                club_id=HOME_ID,
                claimed_at=last - timedelta(days=9),
                last_game_action_at=last,
                expires_at=ownership_deadline(last),
                ended_at=None,
                end_reason=None,
            )
        )
        from goaletto.infrastructure.models import AppearanceIdentity, AppearanceVariant

        session.add(AppearanceIdentity(id="face-1", origin_bank="players", catalog_version=1))
        await session.flush()
        session.add(
            AppearanceVariant(
                id="face-1-v",
                identity_id="face-1",
                bank="players",
                stage="adult",
                asset_path="face-1.png",
                mask_path="face-1-mask.png",
            )
        )
        await session.flush()
        session.add(
            Person(
                id=STARTER_ID,
                creation_key="synthetic-starter",
                name=FINAL.home.snapshot.players[0].name,
                age=25,
                career="player",
                appearance_identity_id="face-1",
                appearance_variant_id="face-1-v",
                home_league_set_id=None,
                age_applied_for_season_id=None,
            )
        )
        await session.flush()
        session.add(
            Player(
                person_id=STARTER_ID,
                club_id=HOME_ID,
                position="MC",
                alternative_positions=[],
                goalkeeping=1,
                defending=9,
                playmaking=12,
                winger=12,
                attacking=9,
                set_pieces=10,
                stamina=11,
                pace=10,
                leadership=10,
                loyalty=10,
                form=6,
                experience=0,
                morale=50,
                status="active",
                last_season_id=None,
                retired_after_season_id=None,
                injured_until=None,
            )
        )
        await session.flush()
        # 9 home fixtures per season for the owned club (18 total).
        fixture_ids: list[object] = []
        base = NOW - timedelta(days=1)
        for season_id in season_ids:
            opponents = [cid for cid in club_ids if cid != HOME_ID][:9]
            for idx, opponent in enumerate(opponents):
                at = base - timedelta(days=len(fixture_ids))
                fixture_id = uuid4()
                fixture_ids.append(fixture_id)
                session.add(
                    Fixture(
                        id=fixture_id,
                        season_id=season_id,
                        league_id=league_id,
                        home_club_id=HOME_ID,
                        away_club_id=opponent,
                        round_number=idx + 1,
                        scheduled_start_at=at,
                        lineup_lock_at=at - timedelta(minutes=1),
                        actual_start_at=at,
                        actual_finish_at=at + timedelta(minutes=33),
                        status="finished",
                    )
                )
        await session.flush()
        # Matches, receipts and ticks only for the page-independent archive.
        for fixture_id in fixture_ids:
            fixture = await session.get(Fixture, fixture_id)
            assert fixture is not None
            match_id = uuid4()
            session.add(
                Match(
                    id=match_id,
                    fixture_id=fixture.id,
                    seed=7,
                    engine_version="regulation-v1",
                    state_version=1,
                    state=TERMINAL_DOC,
                    starting_snapshot=INITIAL_DOC,
                    completed_tick=30,
                    phase="full_time",
                    home_goals=FINAL.home.stats.goals,
                    away_goals=FINAL.away.stats.goals,
                    next_due_at=None,
                    paused_at=None,
                    paused_seconds=0,
                )
            )
            session.add(
                MatchResult(
                    match_id=match_id,
                    fixture_id=fixture.id,
                    season_id=fixture.season_id,
                    league_id=fixture.league_id,
                    home_club_id=fixture.home_club_id,
                    away_club_id=fixture.away_club_id,
                    version=1,
                    home_goals=FINAL.home.stats.goals,
                    away_goals=FINAL.away.stats.goals,
                    actual_start_at=fixture.actual_start_at,
                    actual_finish_at=fixture.actual_finish_at,
                    recorded_at=NOW,
                    state_hash=state_hash(FINAL),
                )
            )
            session.add(
                MatchTick(
                    match_id=match_id,
                    tick_number=5,
                    game_minute=15,
                    due_at=NOW,
                    committed_at=NOW,
                    events=[
                        {
                            "id": str(uuid4()),
                            "kind": "goal",
                            "tick": 5,
                            "minute": 15,
                            "side": "home",
                            "player_id": str(STARTER_ID),
                            "other_player_id": None,
                            "detail": "center",
                        }
                    ],
                    state_hash="0" * 64,
                )
            )
    return manager_id, HOME_ID


async def test_synthetic_archive_bounded_queries_and_snapshots(sessions):
    from dataclasses import replace

    from goaletto.application.club_view import Actor
    from goaletto.infrastructure.models import Fixture, Match

    manager_id, club_id = await _build_archive(sessions)
    statements: list[str] = []
    snapshots_loaded = 0
    original_restore = None
    import goaletto.application.history as history_module

    original_restore = history_module.restore_state

    def counting_restore(doc):
        nonlocal snapshots_loaded
        snapshots_loaded += 1
        return original_restore(doc)

    history_module.restore_state = counting_restore  # type: ignore[assignment]
    try:
        async with sessions.begin() as session:
            actor = Actor(manager_id, 123, None, uuid4(), NOW)
            page = await get_club_results(session, actor, 1)
            assert page.total == 18
            assert page.total_pages == 4
            assert len(page.entries) == HISTORY_PAGE_SIZE
            first_count = snapshots_loaded
            # Only the requested page snapshots decode (5 matches x 1 snapshot).
            assert first_count == HISTORY_PAGE_SIZE
            actor2 = replace(actor, operation_id=uuid4())
            player_page = await get_player_matches(session, actor2, STARTER_ID, 1)
            assert player_page.total == 18
            assert len(player_page.entries) == HISTORY_PAGE_SIZE
            assert player_page.entries[0].minutes == 90
            # Bench player has 0 minutes, still listed.
            actor3 = replace(actor, operation_id=uuid4())
            bench_page = await get_player_matches(session, actor3, BENCH_ID, 1)
            assert bench_page.total == 18
            assert bench_page.entries[0].minutes == 0
            # Opponent never counts for the owned club.
            from goaletto.application.game_actions import PermissionDenied

            try:
                await get_player_matches(
                    session, replace(actor, operation_id=uuid4()), OPPONENT_ID, 1
                )
                raise AssertionError("opponent should be denied")
            except PermissionDenied:
                pass
            # Total finished fixtures for the club stays 18.
            total = await session.scalar(
                select(func.count())
                .select_from(Fixture)
                .where(Fixture.home_club_id == club_id, Fixture.status == "finished")
            )
            assert total == 18
            match_total = await session.scalar(select(func.count()).select_from(Match))
            assert match_total == 18
            _ = statements
    finally:
        history_module.restore_state = original_restore  # type: ignore[assignment]


async def test_synthetic_archive_explain_uses_bounded_plan(sessions):
    await _build_archive(sessions)
    async with sessions.begin() as session:
        # EXPLAIN for COUNT and bounded page; output saved for docs/history-pagination.md.
        count_plan = (
            await session.execute(
                text(
                    "EXPLAIN (COSTS OFF) SELECT count(*) FROM fixtures "
                    "WHERE (home_club_id = :club OR away_club_id = :club) "
                    "AND status = 'finished'"
                ),
                {"club": str(HOME_ID)},
            )
        ).all()
        page_plan = (
            await session.execute(
                text(
                    "EXPLAIN (COSTS OFF) SELECT id FROM fixtures "
                    "WHERE (home_club_id = :club OR away_club_id = :club) "
                    "AND status = 'finished' "
                    "ORDER BY scheduled_start_at DESC, id LIMIT 5 OFFSET 0"
                ),
                {"club": str(HOME_ID)},
            )
        ).all()
        player_plan = (
            await session.execute(
                text(
                    "EXPLAIN (COSTS OFF) SELECT count(*) FROM fixtures "
                    "JOIN matches ON matches.fixture_id = fixtures.id "
                    "WHERE (fixtures.home_club_id = :club OR fixtures.away_club_id = :club) "
                    "AND fixtures.status = 'finished' "
                    "AND (matches.starting_snapshot @> :home OR matches.starting_snapshot @> :away)"
                ),
                {
                    "club": str(HOME_ID),
                    "home": (
                        '{"home": {"snapshot": {"club_id": "'
                        + str(HOME_ID)
                        + '", "players": [{"id": "'
                        + str(STARTER_ID)
                        + '"}]}}}'
                    ),
                    "away": (
                        '{"away": {"snapshot": {"club_id": "'
                        + str(HOME_ID)
                        + '", "players": [{"id": "'
                        + str(STARTER_ID)
                        + '"}]}}}'
                    ),
                },
            )
        ).all()
        assert count_plan and page_plan and player_plan
