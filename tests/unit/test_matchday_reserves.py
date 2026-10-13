"""A club can keep 25 players while selecting at most 20 for one match."""

from dataclasses import replace
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.lineup import load_lineup
from goaletto.bot import lineup_views as views
from goaletto.domain.lineup import LineupInvalid, RosterPlayer, validate_lineup
from goaletto.presentation.i18n import using_locale
from tests.unit.test_language import assert_english
from tests.unit.test_lineup import Recorder, bench, roster, xi


def test_25_player_roster_allows_unselected_reserves_but_not_foreign_or_26():
    rec = Recorder()
    reserves = tuple(RosterPlayer(uuid4(), "FC", (), "active", False) for _ in range(5))
    args = dict(
        roster=(*roster(rec), *reserves),
        starters=xi(rec),
        bench=bench(rec),
        formation="4-4-2",
        captain_id=rec.club_players[5].person_id,
        tactic="normal",
        mentality="balanced",
    )
    assert len(validate_lineup(**args).bench) == 9
    assert len(validate_lineup(**{**args, "bench": ()}).bench) == 0
    with pytest.raises(LineupInvalid) as err:
        validate_lineup(
            **{
                **args,
                "roster": (*args["roster"], RosterPlayer(uuid4(), "FC", (), "active", False)),
            }
        )
    assert err.value.code == "count"
    with pytest.raises(LineupInvalid) as err:
        validate_lineup(**{**args, "bench": (*bench(rec)[:-1], uuid4())})
    assert err.value.code == "foreign"


async def test_reserve_picker_worst_revision_fits_and_english_main_shows_outside_squad():
    rec = Recorder()
    data = await load_lineup(cast(AsyncSession, rec), rec.actor)
    reserves = tuple(
        replace(data.bench[-1], person_id=uuid4(), name=f"Reserve Player {i}") for i in range(5)
    )
    data = replace(data, reserves=reserves, version=2**31 - 1)
    with using_locale("en"):
        assert_english((views.render_main(data), views.main_keyboard(data)))
        assert "20/25" in views.render_main(
            data
        ) and "Outside the match squad" in views.render_main(data)
        for keyboard in (
            views.swap_keyboard(data, 11),
            views.bench_slots_keyboard(data),
            views.bench_choices_keyboard(data, 20),
        ):
            for row in keyboard.inline_keyboard:
                for button in row:
                    if button.callback_data.startswith("ln:"):
                        views.parse_callback(button.callback_data)
                        assert len(button.callback_data.encode()) <= 64
        data = replace(data, bench=data.bench[:-1])
        assert any(
            b.text == "Add to bench"
            for row in views.bench_slots_keyboard(data).inline_keyboard
            for b in row
        )
        views.bench_choices_keyboard(data, 20)
