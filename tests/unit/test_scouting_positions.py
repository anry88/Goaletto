from collections import Counter
from uuid import UUID, uuid4

import pytest

from goaletto.application.outbox_delivery import render_text, valid_payload
from goaletto.application.scouting import ScoutScreen
from goaletto.bot import scouting_views as views
from goaletto.domain.finances import player_fee, player_wage
from goaletto.domain.market_players import market_player
from goaletto.domain.player_positions import ADJACENT, alternative_positions
from goaletto.domain.scouting import discovery, findings_count, potential_range
from goaletto.domain.world import SKILL_NAMES
from goaletto.infrastructure.models import Person
from goaletto.presentation.i18n import using_locale
from tests.unit.test_language import assert_english


def test_seeded_universals_are_adjacent_and_keep_keeper_separate():
    values = [alternative_positions(UUID(int=n), "DC") for n in range(2000)]
    counts = Counter(map(len, values))
    assert 1050 < counts[0] < 1350 and 500 < counts[1] < 700 and 120 < counts[2] < 280
    for p, nearby in ADJACENT.items():
        assert all(p in ADJACENT[n] for n in nearby)
        for n in range(100):
            a = alternative_positions(UUID(int=n), p)
            assert len(a) <= 2 and len(set(a)) == len(a) and p not in a
            assert all(x in nearby for x in a)
            if p == "GK":
                assert a == ()
    assert values == [alternative_positions(UUID(int=n), "DC") for n in range(2000)]
    assert "DL" in ADJACENT["DC"] and "AMC" in ADJACENT["FC"]
    profiles = [market_player(UUID(int=n), "FC") for n in range(100)]
    assert any(len(p.alternatives) == 2 for p in profiles)
    p = profiles[0]
    assert player_fee(p.skills[:8], p.age, "FC", 3) > player_fee(p.skills[:8], p.age, "FC", 1)
    assert player_wage(p.skills[:8], p.age, "FC", 3) > player_wage(p.skills[:8], p.age, "FC", 1)


def test_individual_youth_profiles_caps_and_scout_quality_do_not_guarantee_elite():
    weak = [discovery(UUID(int=n), "AMC", 1) for n in range(2000)]
    strong = [discovery(UUID(int=n), "AMC", 20) for n in range(2000)]
    assert {p.age for p in strong} == set(range(17, 22))
    assert len({p.skills for p in strong}) > 1500
    assert sum(p.skills[4] for p in strong) > sum(p.skills[4] for p in weak)
    assert sum(p.caps["attacking"] for p in strong) > sum(p.caps["attacking"] for p in weak)
    assert any(p.skills[4] < 8 for p in strong)
    assert any(p.skills[4] >= 17 for p in strong)
    for p in strong + weak:
        assert all(1 <= s <= 20 for s in p.skills)
        assert all(s <= p.caps[k] <= 20 for k, s in zip(SKILL_NAMES[:8], p.skills[:8], strict=True))
        assert p.skills[0] <= 3
    assert discovery(UUID(int=1), "AMC", 20) == strong[1]
    assert potential_range(16, 1) == (11, 20) and potential_range(16, 20) == (16, 16)
    assert [findings_count(d) for d in (4, 8, 12, 16)] == [1, 2, 3, 4]
    with pytest.raises(ValueError):
        findings_count(True)


def test_search_screens_in_both_languages_and_callbacks_fit_telegram():
    owner, scout = uuid4(), uuid4()
    screen = ScoutScreen(owner, Person(id=scout, name="Name <b>", age=52), 20, 3920, 600000, (), ())
    for lang in ("ru", "en"):
        with using_locale(lang):
            outputs = [views.view(screen), views.positions(owner)]
            for position in ADJACENT:
                d = views.durations(screen, position)
                outputs.append(d)
                for row in d[1].inline_keyboard[:-1]:
                    callback = row[0].callback_data
                    p = callback.split(":")
                    outputs.append(
                        views.search_quote(owner, scout, position, int(p[5]), 500000, callback)
                    )
            outputs.append(views.cancel_quote(owner, uuid4()))
            for output in outputs:
                assert len(output[0]) < 4096
                for row in output[1].inline_keyboard:
                    for b in row:
                        assert len(b.callback_data.encode()) <= 64
                if lang == "en":
                    assert_english(output)
    payload = {
        "version": 1,
        "kind": "scout_report",
        "ownership_id": str(owner),
        "club_id": str(uuid4()),
        "mission_id": str(uuid4()),
        "count": 4,
    }
    assert valid_payload(payload)
    assert not valid_payload({**payload, "count": True})
    for lang in ("ru", "en"):
        with using_locale(lang):
            text = render_text(payload)
            assert "/scout" in text
            if lang == "en":
                assert "Search complete" in text
