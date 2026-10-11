from datetime import UTC, datetime

import pytest

from goaletto.domain.ownership import ownership_deadline


@pytest.mark.parametrize(
    ("year", "month", "day", "expected"),
    [
        (2026, 8, 31, (2027, 2, 28)),
        (2027, 8, 31, (2028, 2, 29)),
        (2026, 10, 11, (2027, 4, 11)),
        (2026, 12, 31, (2027, 6, 30)),
        (2028, 2, 29, (2028, 8, 29)),
    ],
)
def test_six_calendar_months_preserve_utc_time(
    year: int,
    month: int,
    day: int,
    expected: tuple[int, int, int],
) -> None:
    action = datetime(year, month, day, 23, 42, 19, 12345, tzinfo=UTC)
    deadline = ownership_deadline(action)
    assert (deadline.year, deadline.month, deadline.day) == expected
    assert (deadline.hour, deadline.minute, deadline.second, deadline.microsecond) == (
        23,
        42,
        19,
        12345,
    )
    assert deadline.tzinfo is UTC


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="UTC"):
        ownership_deadline(datetime(2026, 10, 11))
