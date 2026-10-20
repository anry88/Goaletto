import pytest

from goaletto.domain.club_names import InvalidClubName, validate_name


@pytest.mark.parametrize("value", ["  FC   North  ", "St. John's-19", "Arsenal", "Scunthorpe"])
def test_names(value):
    assert validate_name(value) == " ".join(value.split())


@pytest.mark.parametrize(
    "value",
    [
        "A",
        "123",
        "a" * 31,
        "ФК",
        "North\nFC",
        "FC\u200bX",
        "<b>FC</b>",
        "F U C K",
        "sh1t",
        "P1ZDA",
        "FC Fuckers",
    ],
)
def test_rejected_names(value):
    with pytest.raises(InvalidClubName):
        validate_name(value)
