import pytest

from goaletto.domain.clock import MatchClock, MatchPhase


def test_regulation_has_exact_halves_and_three_minute_break() -> None:
    clock = MatchClock()
    assert clock.tick_elapsed_seconds(1) == 60
    assert clock.tick_elapsed_seconds(15) == 900
    assert clock.game_minute(15) == 45
    assert clock.tick_elapsed_seconds(16) == 1140
    assert clock.tick_elapsed_seconds(30) == 1980
    assert clock.game_minute(30) == 90
    assert clock.duration_seconds == 33 * 60


@pytest.mark.parametrize(
    ("seconds", "phase"),
    [
        (0, MatchPhase.FIRST_HALF),
        (899, MatchPhase.FIRST_HALF),
        (900, MatchPhase.HALF_TIME),
        (1079, MatchPhase.HALF_TIME),
        (1080, MatchPhase.SECOND_HALF),
        (1979, MatchPhase.SECOND_HALF),
        (1980, MatchPhase.FULL_TIME),
    ],
)
def test_phase_boundaries(seconds: int, phase: MatchPhase) -> None:
    assert MatchClock().phase_at(seconds) == phase


@pytest.mark.parametrize("tick", [-1, 0, 31])
def test_invalid_tick_is_rejected(tick: int) -> None:
    with pytest.raises(ValueError):
        MatchClock().tick_elapsed_seconds(tick)
