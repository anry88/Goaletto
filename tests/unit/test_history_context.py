"""History return addresses survive report tabs, paging and fresh requests."""

from dataclasses import replace
from uuid import uuid4

import pytest

from goaletto.application.history import SeasonArchive, get_club_results, get_player_matches
from goaletto.bot.history_callbacks import (
    ReportOrigin,
    archive_callback,
    context_report_callback,
    parse_context_report_callback,
    parse_history_callback,
    player_history_callback,
    season_results_callback,
    seasons_callback,
)
from goaletto.bot.history_cards import (
    archive_view,
    player_history_view,
    report_view,
    results_view,
    seasons_view,
)
from goaletto.presentation.i18n import using_locale
from tests.unit.test_history import STARTER_ID, Recorder, get_match_report, session


def callbacks(view):
    return [b.callback_data for row in view[1].inline_keyboard for b in row]


@pytest.mark.parametrize("kind", ["player", "season"])
@pytest.mark.parametrize("section", ["events", "home", "away"])
def test_context_callbacks_fit_telegram_and_reject_noncanonical(kind, section):
    fixture_id, identity = uuid4(), uuid4()
    origin = ReportOrigin(kind, identity, 10000)
    value = context_report_callback(fixture_id, origin, section, 10000)
    assert len(value.encode()) <= 64
    assert parse_context_report_callback(value) == (fixture_id, section, 10000, origin)
    for bad in (
        value + ":x",
        value.replace(":10000:", ":010000:"),
        value.rsplit(":", 1)[0] + ":10001",
        value.replace(":10000:", ":0:"),
        value.replace(":10000:", ":True:"),
        value.replace("ht:", "hr:"),
    ):
        with pytest.raises(ValueError):
            parse_context_report_callback(bad)


async def test_player_report_keeps_original_page_in_all_tabs():
    recorder = Recorder()
    page = await get_player_matches(session(recorder), recorder.actor, STARTER_ID, 1)
    page = replace(page, page=3, total_pages=4)
    value = next(c for c in callbacks(player_history_view(page)) if c.startswith("ht:"))
    fixture_id, section, number, origin = parse_context_report_callback(value)
    report = await get_match_report(
        session(recorder), replace(recorder.actor, operation_id=uuid4()), fixture_id
    )
    assert origin == ReportOrigin("player", STARTER_ID, 3)
    for section in ("events", "home", "away"):
        values = callbacks(report_view(report, section=section, origin=origin))
        assert player_history_callback(STARTER_ID, 3) in values
        for child in values:
            if child.startswith("ht:"):
                assert parse_context_report_callback(child)[3] == origin
        assert "hx:r:1" not in values


async def test_season_archive_results_and_report_navigation_in_both_languages():
    recorder = Recorder()
    from goaletto.domain.standings import LeagueStanding

    archive = SeasonArchive(
        recorder.season_id,
        2,
        5,
        recorder.league_id,
        tuple(LeagueStanding(uuid4(), f"Club {i}", i, 1, 1, 0, 0, 2, 0) for i in range(1, 11)),
        1,
        1,
        1,
        0,
        0,
        2,
        0,
        3,
    )
    page = await get_club_results(session(recorder), recorder.actor, 1)
    page = replace(page, page=2, total_pages=3)
    report = await get_match_report(
        session(recorder), replace(recorder.actor, operation_id=uuid4()), recorder.fixture.id
    )
    for locale in ("ru", "en"):
        with using_locale(locale):
            assert archive_callback(archive.season_id, 3) in callbacks(
                seasons_view((recorder_summary(archive),), page=3, total_pages=3)
            )
            values = callbacks(archive_view(archive, 3))
            archive_text = archive_view(archive, 3)[0]
            for place in range(1, 11):
                assert archive_text.count(f"<b>{place}. ") == 1
            assert seasons_callback(3) in values
            assert season_results_callback(archive.season_id) in values
            values = callbacks(results_view(page, season_id=archive.season_id))
            assert season_results_callback(archive.season_id, 1) in values
            assert season_results_callback(archive.season_id, 3) in values
            value = next(c for c in values if c.startswith("ht:"))
            _, _, _, origin = parse_context_report_callback(value)
            assert origin == ReportOrigin("season", archive.season_id, 2)
            assert season_results_callback(archive.season_id, 2) in callbacks(
                report_view(report, origin=origin)
            )


def recorder_summary(archive):
    from goaletto.application.history import SeasonSummary

    return SeasonSummary(archive.season_id, archive.number, archive.tier)


def test_season_callbacks_roundtrip_and_validate_bounds():
    season_id = uuid4()
    assert parse_history_callback(archive_callback(season_id)) == ("archive", season_id, 1)
    assert parse_history_callback(archive_callback(season_id, 5)) == ("archive", season_id, 5)
    assert parse_history_callback(season_results_callback(season_id, 2)) == (
        "season_results",
        season_id,
        2,
    )
    for value in (
        season_results_callback(season_id).replace(":1", ":01"),
        season_results_callback(season_id).replace(":1", ":10001"),
        archive_callback(season_id, 2).replace(":2", ":0"),
    ):
        with pytest.raises(ValueError):
            parse_history_callback(value)
