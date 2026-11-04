"""Current card details, persisted participation and nationality projections."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from goaletto.application.candidates import CandidateItem, CandidateScreen
from goaletto.application.staff_cards import StaffCard
from goaletto.bot.candidate_views import candidate_list, candidate_quote, coach_capabilities
from goaletto.bot.club_cards import player_view
from goaletto.bot.lineup_views import TACTIC_LABELS
from goaletto.bot.staff_cards import staff_card_view
from goaletto.domain.match_types import CoachSnapshot, EnginePolicy
from goaletto.domain.nationalities import flag, legacy_nationality
from goaletto.infrastructure.models import CandidateOffer, MatchDevelopment, Person
from goaletto.presentation.i18n import using_locale
from tests.unit.test_club_cards import player
from tests.unit.test_history import NOW, Recorder, Rows, get_match_report, session


@pytest.mark.parametrize("locale", ["ru", "en"])
@pytest.mark.parametrize("skill", [1, 5, 9, 13, 20])
def test_coach_capabilities_match_runtime_in_card_market_and_quote(locale, skill):
    person = Person(id=uuid4(), name="Coach", age=55, country_code="RS")
    offer = CandidateOffer(
        person_id=person.id, kind="staff", profile="head_coach", fee=80000, weekly_rate=16000
    )
    item = CandidateItem(offer, person, (skill,))
    owner = uuid4()
    screen = CandidateScreen(owner, "Club", 100000, (item,), ())
    card = StaffCard(
        owner,
        person,
        "head_coach",
        {"skill": skill, "wage": 16000, "status": "active"},
        True,
        False,
        None,
        None,
        None,
        1,
        "#315B49",
        "#E2BF57",
        1,
    )
    with using_locale(locale):
        text = coach_capabilities(skill)
        abilities = CoachSnapshot(person.id, skill).abilities
        assert f"({len(abilities.tactics)})" in text
        assert all(TACTIC_LABELS[t] in text for t in abilities.tactics)
        assert all(
            str(limit) in text
            for limit in (
                abilities.tactic_changes,
                abilities.instruction_changes,
                abilities.mentality_changes,
            )
        )
        policy = EnginePolicy()
        assert str(policy.max_substitutions) in text and str(policy.max_windows) in text
        assert text in staff_card_view(card)[0]
        assert (
            text
            in staff_card_view(replace(card, employed=False, terms={**card.terms, "fee": 80000}))[0]
        )
        assert text in candidate_list(screen, "staff")[0]
        assert text in candidate_quote(item, owner)[0]


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_player_shows_declared_and_proven_positions_even_with_zero_xp(locale):
    card = replace(
        player(),
        position="DC",
        alternative_positions=("DR",),
        played_positions=("DC", "MC"),
        position_experience=(("DC", 0), ("DR", 0), ("MC", 0), ("FC", 500000)),
    )
    with using_locale(locale):
        text, _ = player_view(card)
        assert "DC 0% · DR 0% · MC 0%" in text
        assert "FC 50%" not in text and "GK 0%" not in text
        assert "Мораль" not in text and "Morale" not in text
        no_history = player_view(replace(card, played_positions=()))[0]
        assert "MC 0%" not in no_history
        assert "DC 0% · DR 0%" in no_history


@pytest.mark.parametrize("country", ["RS", "UA", "BR"])
async def test_report_reserve_outside_snapshot_uses_persisted_country(monkeypatch, country):
    recorder = Recorder()
    # Actual AND-239 person: legacy fallback incorrectly assigns Italy.
    pid = UUID("6bcf8509-84c0-52cc-bff9-1e2323212759")
    assert legacy_nationality(pid) == "IT"
    row = {
        "person_id": str(pid),
        "club_id": str(recorder.club.id),
        "name": "Filip Đorđević",
        "age": 17,
        "form": 6,
        "position": "DC",
        "experience_policy": "position-experience-v2",
        "position_experience": {},
    }
    development = MatchDevelopment(match_id=recorder.match.id, applied_at=NOW, results=[row])
    original_get, original_execute = recorder.get, recorder.execute

    async def get(model, key):
        return development if model is MatchDevelopment else await original_get(model, key)

    async def execute(statement, params=None):
        if "persons.country_code" in str(statement):
            requested = next(iter(statement.compile().params.values()))
            return Rows([(pid, country)] if pid in requested else [])
        return await original_execute(statement, params)

    async def report_rows(session, rows, at):
        return rows

    monkeypatch.setattr(recorder, "get", get)
    monkeypatch.setattr(recorder, "execute", execute)
    monkeypatch.setattr("goaletto.application.history.match_report_rows", report_rows)
    report = await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)
    reserve = next(p for p in report.home_lineup if p.person_id == pid)
    assert not reserve.started and reserve.minutes == 0 and reserve.country_code == country
    from goaletto.bot.history_cards import _player_progress

    for locale in ("ru", "en"):
        with using_locale(locale):
            assert flag(country) in _player_progress(reserve)
            assert "🇮🇹" not in _player_progress(reserve)
    assert development.results == [row]
