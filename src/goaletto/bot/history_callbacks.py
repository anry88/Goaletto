"""Compact hx: callbacks for history screens. No database or network access.

Separate namespace from vc: (cards) and table:/report: (result_views): our
router ignores those and they ignore ours, so old buttons stay safe.
"""

from uuid import UUID

from goaletto.bot.result_views import compact_id, expand_id

MAX_PAGE = 10000


def results_callback(page: int) -> str:
    if type(page) is not int or not 1 <= page <= MAX_PAGE:
        raise ValueError("Invalid history page")
    value = f"hx:r:{page}"
    assert len(value.encode()) <= 64
    return value


def match_callback(fixture_id: UUID, page: int = 1) -> str:
    if not isinstance(fixture_id, UUID):
        raise ValueError("Invalid fixture identity")
    if type(page) is not int or not 1 <= page <= MAX_PAGE:
        raise ValueError("Invalid return page")
    value = f"hx:m:{compact_id(fixture_id)}:{page}"
    assert len(value.encode()) <= 64
    return value


def player_history_callback(person_id: UUID, page: int = 1) -> str:
    if not isinstance(person_id, UUID):
        raise ValueError("Invalid player identity")
    if type(page) is not int or not 1 <= page <= MAX_PAGE:
        raise ValueError("Invalid return page")
    value = f"hx:pm:{compact_id(person_id)}:{page}"
    assert len(value.encode()) <= 64
    return value


def seasons_callback(page: int = 1) -> str:
    if type(page) is not int or not 1 <= page <= MAX_PAGE:
        raise ValueError("Invalid history page")
    value = f"hx:s:{page}"
    assert len(value.encode()) <= 64
    return value


def archive_callback(season_id: UUID) -> str:
    if not isinstance(season_id, UUID):
        raise ValueError("Invalid season identity")
    value = f"hx:a:{compact_id(season_id)}"
    assert len(value.encode()) <= 64
    return value


def parse_history_callback(value: str) -> tuple[str, UUID | None, int]:
    """Return (kind, identity, page). Kinds: results/match/player/seasons/archive."""
    if type(value) is not str:
        raise ValueError("Invalid history callback")
    parts = value.split(":")
    if len(parts) == 3 and parts[0] == "hx" and parts[1] == "r" and parts[2].isdigit():
        page = int(parts[2])
        if 1 <= page <= MAX_PAGE:
            return ("results", None, page)
    if len(parts) == 3 and parts[0] == "hx" and parts[1] == "s" and parts[2].isdigit():
        page = int(parts[2])
        if 1 <= page <= MAX_PAGE:
            return ("seasons", None, page)
    if len(parts) == 3 and parts[0] == "hx" and parts[1] == "a":
        return ("archive", expand_id(parts[2]), 1)
    if (
        len(parts) == 4
        and parts[0] == "hx"
        and parts[1] == "m"
        and parts[3].isdigit()
        and 1 <= int(parts[3]) <= MAX_PAGE
    ):
        return ("match", expand_id(parts[2]), int(parts[3]))
    if (
        len(parts) == 4
        and parts[0] == "hx"
        and parts[1] == "pm"
        and parts[3].isdigit()
        and 1 <= int(parts[3]) <= MAX_PAGE
    ):
        return ("player", expand_id(parts[2]), int(parts[3]))
    raise ValueError("Invalid history callback")


def is_history_callback(value: str | None) -> bool:
    return type(value) is str and value.startswith("hx:")
