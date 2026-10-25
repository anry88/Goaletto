"""Performance-sensitive form, genuine assists and durable elapsed inactivity."""

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from goaletto.application.match_commentary import football_line
from goaletto.application.match_runtime import event_documents
from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.match_codec import restore_state, state_document
from goaletto.domain.match_engine import start_match
from goaletto.domain.match_performance import player_of_match
from goaletto.domain.match_practice import CURRENT_POLICY, V3_POLICY, practice_form
from goaletto.domain.match_types import CoachSnapshot, EnginePolicy
from goaletto.domain.player_form import MatchPerformance, idle_form, performance_chances


def test_performance_minutes_fatigue_and_cards_change_form_in_the_expected_directions():
    normal = MatchPerformance(90, energy_bps=7000)
    up, down = performance_chances(6, normal)
    assert performance_chances(6, replace(normal, minutes=30))[0] < up
    for signal in ({"goals": 1}, {"assists": 1}, {"saves": 4}):
        assert performance_chances(6, replace(normal, **signal))[0] > up
    assert performance_chances(6, replace(normal, yellows=1))[1] > down
    assert performance_chances(6, replace(normal, dismissed=True))[1] > down
    assert performance_chances(6, replace(normal, energy_bps=1000))[1] > down
    star = practice_form(
        6,
        90,
        person_id=UUID(int=1),
        match_seed="star",
        policy=CURRENT_POLICY,
        performance=replace(normal, player_of_match=True),
    )
    assert star.form == 7 and star.chance == 10000
    exhausted_red = practice_form(
        6,
        90,
        person_id=UUID(int=1),
        match_seed="red",
        policy=CURRENT_POLICY,
        performance=replace(normal, energy_bps=0, dismissed=True, yellows=2),
    )
    assert exhausted_red.form == 5
    assert (
        practice_form(6, 0, person_id=UUID(int=1), match_seed="bench", policy=CURRENT_POLICY).form
        == 6
    )
    # The earlier pinned V3 law remains separate and reproducible.
    assert practice_form(
        6, 90, person_id=UUID(int=1), match_seed="star", policy=V3_POLICY
    ) == practice_form(6, 90, person_id=UUID(int=1), match_seed="star", policy=V3_POLICY)


def test_inactivity_depends_on_elapsed_time_not_number_of_views():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    assert idle_form(8, start, 0, start + timedelta(days=4)) == (8, 0)
    form, steps = idle_form(8, start, 0, start + timedelta(days=5))
    assert (form, steps) == (7, 1)
    assert idle_form(form, start, steps, start + timedelta(days=5)) == (7, 1)
    assert idle_form(form, start, steps, start + timedelta(days=7)) == (6, 2)
    assert idle_form(8, start, 0, start + timedelta(days=100))[0] == 1
    assert idle_form(8, start, 0, start - timedelta(days=1)) == (8, 0)


def test_v5_assists_match_actual_goal_events_and_performance_counters():
    original = demo_match()
    home, away = [
        replace(t.snapshot, coach=CoachSnapshot(None)) for t in (original.home, original.away)
    ]
    initial = start_match(
        UUID(int=701),
        7,
        home,
        away,
        engine_version="regulation-v5",
        policy=EnginePolicy(version="match-policy-v5"),
    )
    final, events = simulate_match(initial)
    goals = [event for event in events if event.kind == "goal"]
    assists = [event for event in goals if event.assist_player_id is not None]
    assert assists
    goal_counts = Counter(e.player_id for e in goals)
    assist_counts = Counter(e.assist_player_id for e in assists)
    save_counts = Counter(e.other_player_id for e in events if e.kind == "save")
    for event in assists:
        team = final.home if event.side.value == "home" else final.away
        assert event.assist_player_id != event.player_id
        assert event.assist_player_id in {p.id for p in team.snapshot.players}
        line = football_line(final, event_documents((event,))[0])
        assert " · пас " in line
    for team in (final.home, final.away):
        for condition in team.conditions:
            assert condition.goals == goal_counts[condition.player_id]
            assert condition.assists == assist_counts[condition.player_id]
            assert condition.saves == save_counts[condition.player_id]
    best = player_of_match(final)
    assert best and any(
        c.player_id == best and c.minutes_played > 0
        for t in (final.home, final.away)
        for c in t.conditions
    )
    assert restore_state(state_document(final)) == final
    old, old_events = simulate_match(original)
    assert player_of_match(old) is None
    assert not any(e.assist_player_id for e in old_events)
    assert not any("assist_player_id" in e for e in event_documents(old_events))
