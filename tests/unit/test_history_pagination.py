"""Pagination-specific history tests: bounded queries, stable order, edge cases."""

from datetime import timedelta
from uuid import uuid4

import pytest

from goaletto.application.club_view import Actor
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.history import (
    HISTORY_PAGE_SIZE,
    get_club_results,
    get_club_seasons,
    get_match_report,
    get_player_matches,
    stored_events,
    stored_events_batch,
)
from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.infrastructure.models import MatchStage
from tests.unit.test_history import (
    NOW,
    STARTER_ID,
    STARTER_NAME,
    Recorder,
    session,
)

FINAL, _ = simulate_match(demo_match(7))
BENCH_ID = FINAL.home.snapshot.players[15].id
BENCH_NAME = FINAL.home.snapshot.players[15].name
OPPONENT_ID = FINAL.away.snapshot.players[0].id


async def test_empty_club_history_returns_empty_page() -> None:
    recorder = Recorder()
    recorder.fixtures = []
    recorder.matches = []
    recorder.receipts_by_match = {}
    page = await get_club_results(session(recorder), recorder.actor, 1)
    assert page.total == 0
    assert page.total_pages == 1
    assert page.entries == ()
    # Out-of-range page still rejected on empty archive.
    with pytest.raises(ValueError):
        await get_club_results(session(recorder), recorder.actor, 2)


async def test_club_results_page_boundaries() -> None:
    recorder = Recorder()
    # Build 6 fixtures -> 2 pages (5 + 1).
    for round_number, days_ago in ((4, 1), (2, 14), (1, 21), (5, 2), (6, 3)):
        fixture = recorder._fixture(round_number, days_ago)
        match = recorder._match(fixture)
        recorder.fixtures.append(fixture)
        recorder.matches.append(match)
        recorder.receipts_by_match[match.id] = recorder._receipt(fixture, match)
    first = await get_club_results(session(recorder), recorder.actor, 1)
    assert first.total == 6 and first.total_pages == 2
    assert len(first.entries) == HISTORY_PAGE_SIZE
    second = await get_club_results(session(recorder), recorder.actor, 2)
    assert len(second.entries) == 1
    with pytest.raises(ValueError):
        await get_club_results(session(recorder), recorder.actor, 0)
    with pytest.raises(ValueError):
        await get_club_results(session(recorder), recorder.actor, 3)
    with pytest.raises(ValueError):
        await get_club_results(session(recorder), recorder.actor, 10001)


async def test_club_results_stable_order_for_identical_dates() -> None:
    recorder = Recorder()
    same_day = NOW - timedelta(days=5)
    recorder.fixtures = []
    recorder.matches = []
    recorder.receipts_by_match = {}
    created = []
    for round_number in (1, 2, 3):
        fixture = recorder._fixture(round_number, days_ago=5)
        fixture.scheduled_start_at = same_day
        match = recorder._match(fixture)
        recorder.fixtures.append(fixture)
        recorder.matches.append(match)
        recorder.receipts_by_match[match.id] = recorder._receipt(fixture, match)
        created.append(fixture)
    page = await get_club_results(session(recorder), recorder.actor, 1)
    expected = sorted(created, key=lambda f: str(f.id))
    assert [e.fixture_id for e in page.entries] == [f.id for f in expected]


async def test_club_results_bounded_statements_do_not_grow_with_archive() -> None:
    small = Recorder()
    big = Recorder()
    for round_number in range(2, 13):
        fixture = big._fixture(round_number, days_ago=round_number)
        match = big._match(fixture)
        big.fixtures.append(fixture)
        big.matches.append(match)
        big.receipts_by_match[match.id] = big._receipt(fixture, match)
    await get_club_results(session(small), small.actor, 1)
    small_count = len(small.statements)
    await get_club_results(session(big), big.actor, 1)
    big_count = len(big.statements)
    # Bounded: same number of SQL statements for 1 vs 12 fixtures.
    assert big_count == small_count
    # No per-match session.get for receipts: receipts load via single IN query.
    big_page = await get_club_results(
        session(big), Actor(big.manager.id, 123, None, uuid4(), NOW), 1
    )
    assert big_page.total == 12
    assert len(big_page.entries) == HISTORY_PAGE_SIZE


async def test_player_bench_minutes_come_from_conditions_not_bench() -> None:
    recorder = Recorder()
    page = await get_player_matches(session(recorder), recorder.actor, BENCH_ID, 1)
    assert page.total == 1
    entry = page.entries[0]
    # Bench presence alone must not invent minutes; stored condition decides.
    assert entry.minutes == 0
    assert entry.goals == 0


async def test_player_history_empty_for_current_squad_member() -> None:
    recorder = Recorder()
    recorder.fixtures = []
    recorder.matches = []
    recorder.receipts_by_match = {}
    # Live squad member with no finished fixtures gets empty history, not denial.
    page = await get_player_matches(session(recorder), recorder.actor, STARTER_ID, 1)
    assert page.total == 0 and page.entries == ()
    with pytest.raises(ValueError, match="page"):
        await get_player_matches(session(recorder), recorder.actor, STARTER_ID, 2)


async def test_player_history_rejects_foreign_and_requires_page_bounds() -> None:
    recorder = Recorder()
    with pytest.raises(PermissionDenied):
        await get_player_matches(session(recorder), recorder.actor, uuid4(), 1)
    with pytest.raises(ValueError):
        await get_player_matches(session(recorder), recorder.actor, STARTER_ID, 0)
    with pytest.raises(ValueError):
        await get_player_matches(session(recorder), recorder.actor, STARTER_ID, 2)


async def test_retired_player_stays_readable_without_live_row() -> None:
    recorder = Recorder()
    recorder.live_player = None
    page = await get_player_matches(session(recorder), recorder.actor, STARTER_ID, 1)
    assert page.total == 1 and page.person_name == STARTER_NAME


async def test_expired_ownership_gets_refusal_not_data() -> None:
    recorder = Recorder()
    recorder.ownership.expires_at = NOW - timedelta(seconds=1)
    with pytest.raises(PermissionDenied):
        await get_club_results(session(recorder), recorder.actor, 1)
    with pytest.raises(PermissionDenied):
        await get_player_matches(session(recorder), recorder.actor, STARTER_ID, 1)
    with pytest.raises(PermissionDenied):
        await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)


async def test_stored_events_batch_matches_single_and_dedups_halftime() -> None:
    recorder = Recorder()
    duplicate = recorder.ticks[0].events[0]
    substitution = {
        "id": str(uuid4()),
        "kind": "substitution",
        "tick": 15,
        "minute": 45,
        "side": "home",
        "player_id": str(STARTER_ID),
        "other_player_id": str(uuid4()),
        "detail": None,
    }
    recorder.stages = [
        MatchStage(
            match_id=recorder.match.id,
            stage="second_half_start",
            occurred_at=NOW,
            events=[duplicate, substitution],
            state_hash="0" * 64,
        )
    ]
    single = await stored_events(session(recorder), recorder.match.id)
    grouped = await stored_events_batch(session(recorder), [recorder.match.id])
    assert grouped[recorder.match.id] == single
    assert [e["kind"] for e in single] == ["goal", "yellow", "substitution"]
    # Batch for two matches uses two queries, not 2*N.
    second_fixture = recorder._fixture(9, 1)
    second_match = recorder._match(second_fixture)
    recorder.fixtures.append(second_fixture)
    recorder.matches.append(second_match)
    recorder.receipts_by_match[second_match.id] = recorder._receipt(second_fixture, second_match)
    before = len(recorder.statements)
    grouped2 = await stored_events_batch(session(recorder), [recorder.match.id, second_match.id])
    assert len(grouped2) == 2
    assert len(recorder.statements) - before == 2


async def test_frozen_names_survive_live_rename_for_results_and_player() -> None:
    recorder = Recorder()
    results = await get_club_results(session(recorder), recorder.actor, 1)
    assert results.entries[0].home_name == "Янтарь"
    assert results.club_name == "Переименованный клуб"
    player_page = await get_player_matches(
        session(recorder),
        Actor(recorder.manager.id, 123, None, uuid4(), NOW),
        STARTER_ID,
        1,
    )
    assert player_page.person_name == STARTER_NAME


async def test_season_list_uses_batched_queries_without_n_plus_one() -> None:
    recorder = Recorder()
    from goaletto.infrastructure.models import League, Season, SeasonClub

    second_season_id = uuid4()
    second_league_id = uuid4()
    recorder.memberships.append(
        SeasonClub(
            season_id=second_season_id,
            club_id=recorder.club.id,
            league_id=second_league_id,
            league_set_id=recorder.league.league_set_id,
            tie_break_order=1,
        )
    )
    recorder.seasons_list = [  # type: ignore[attr-defined]
        recorder.season,
        Season(
            id=second_season_id,
            league_set_id=recorder.league.league_set_id,
            number=3,
            status="active",
            seed=9,
            starts_at=NOW - timedelta(weeks=20),
            ends_at=NOW - timedelta(weeks=10),
            rollover_completed_at=None,
        ),
    ]
    recorder.leagues_list = [  # type: ignore[attr-defined]
        recorder.league,
        League(id=second_league_id, league_set_id=recorder.league.league_set_id, tier=4),
    ]
    before = len(recorder.statements)
    summaries = await get_club_seasons(
        session(recorder), Actor(recorder.manager.id, 123, None, uuid4(), NOW)
    )
    assert len(summaries) == 2
    assert [s.number for s in summaries] == [3, 2]
    # Memberships (1) + seasons IN (1) + leagues IN (1) + auth/receipt, no per-season gets.
    added = len(recorder.statements) - before
    assert added < 15
