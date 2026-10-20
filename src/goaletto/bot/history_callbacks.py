"""Compact hx: callbacks for history screens. No database or network access.

Separate namespace from vc: (cards) and table:/report: (result_views): our
router ignores those and they ignore ours, so old buttons stay safe.
"""

from dataclasses import dataclass
from uuid import UUID

from goaletto.bot.result_views import compact_id, expand_id

MAX_PAGE = 10000


def results_callback(page: int) -> str:
    if type(page) is not int or not 1 <= page <= MAX_PAGE:
        raise ValueError("Invalid history page")
    value = f"hx:r:{page}"
    assert len(value.encode()) <= 64
    return value


def season_results_callback(season_id: UUID, page: int = 1) -> str:
    if not isinstance(season_id, UUID):
        raise ValueError("Invalid season identity")
    results_callback(page)
    return f"hx:sr:{compact_id(season_id)}:{page}"


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


def archive_callback(season_id: UUID, page: int = 1) -> str:
    if not isinstance(season_id, UUID):
        raise ValueError("Invalid season identity")
    seasons_callback(page)
    value = f"hx:a:{compact_id(season_id)}" + (f":{page}" if page != 1 else "")
    assert len(value.encode()) <= 64
    return value


def parse_history_callback(value: str) -> tuple[str, UUID | None, int]:
    """Return (kind, identity, page). Kinds: results/match/player/seasons/archive."""
    if type(value) is not str:
        raise ValueError("Invalid history callback")
    parts = value.split(":")
    if len(parts) == 4 and parts[:2] == ["hx", "sr"]:
        identity, page = expand_id(parts[2]), int(parts[3])
        if season_results_callback(identity, page) != value:
            raise ValueError("Noncanonical season results callback")
        return ("season_results", identity, page)
    if len(parts) == 4 and parts[:2] == ["hx", "a"]:
        identity, page = expand_id(parts[2]), int(parts[3])
        if archive_callback(identity, page) != value:
            raise ValueError("Noncanonical archive callback")
        return ("archive", identity, page)
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


REPORT_SECTIONS = frozenset({"events", "home", "away"})


def report_callback(fixture_id: UUID, section: str, page: int = 1, return_page: int = 1) -> str:
    if not isinstance(fixture_id, UUID) or section not in REPORT_SECTIONS:
        raise ValueError("Invalid report section")
    if any(type(p) is not int or not 1 <= p <= MAX_PAGE for p in (page, return_page)):
        raise ValueError("Invalid report page")
    return f"hr:{compact_id(fixture_id)}:{section}:{page}:{return_page}"


def parse_report_callback(value: str) -> tuple[UUID, str, int, int]:
    parts = value.split(":")
    if len(parts) != 5 or parts[0] != "hr" or len(value.encode()) > 64:
        raise ValueError("Invalid report callback")
    if not all(p.isascii() and p.isdigit() for p in parts[3:]):
        raise ValueError("Invalid report page")
    fixture_id, section, page, return_page = (
        expand_id(parts[1]),
        parts[2],
        int(parts[3]),
        int(parts[4]),
    )
    if report_callback(fixture_id, section, page, return_page) != value:
        raise ValueError("Noncanonical report callback")
    return fixture_id, section, page, return_page


@dataclass(frozen=True)
class ReportOrigin:
    """Compact return address; every target rechecks current ownership."""

    kind: str  # player / season
    identity: UUID
    page: int = 1

    def callback(self) -> str:
        if self.kind == "player":
            return player_history_callback(self.identity, self.page)
        if self.kind == "season":
            return season_results_callback(self.identity, self.page)
        raise ValueError("Invalid report origin")


def context_report_callback(
    fixture_id: UUID, origin: ReportOrigin, section: str = "events", page: int = 1
) -> str:
    # Two compact UUIDs, single-letter sections and origin fit even at page 10000.
    report_callback(fixture_id, section, page, origin.page)
    origin.callback()
    code = "p" if origin.kind == "player" else "s"
    value = (
        f"ht:{compact_id(fixture_id)}:{section[0]}:{page}:"
        f"{code}{compact_id(origin.identity)}:{origin.page}"
    )
    if len(value.encode()) > 64:
        raise ValueError("Report callback too long")
    return value


def parse_context_report_callback(value: str) -> tuple[UUID, str, int, ReportOrigin]:
    parts = value.split(":")
    if len(parts) != 6 or parts[0] != "ht" or len(value.encode()) > 64:
        raise ValueError("Invalid contextual report callback")
    sections = {"e": "events", "h": "home", "a": "away"}
    kinds = {"p": "player", "s": "season"}
    if parts[2] not in sections or parts[4][:1] not in kinds:
        raise ValueError("Invalid report context")
    fixture_id = expand_id(parts[1])
    section, page = sections[parts[2]], int(parts[3])
    origin = ReportOrigin(kinds[parts[4][0]], expand_id(parts[4][1:]), int(parts[5]))
    if context_report_callback(fixture_id, origin, section, page) != value:
        raise ValueError("Noncanonical contextual report callback")
    return fixture_id, section, page, origin
