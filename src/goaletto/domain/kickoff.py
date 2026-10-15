"""Date-aware kickoff preferences. UTC tournament dates never move."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

KICKOFF_POLICY = "kickoff-preference-v1"
BASELINE_MINUTE = 18 * 60


def validate_zone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or len(name) > 64:
        raise ValueError("Invalid IANA timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Unknown IANA timezone") from error


def parse_local_time(value: str) -> int:
    if (
        len(value) != 5
        or value[2] != ":"
        or not value.replace(":", "").isascii()
        or not value.replace(":", "").isdigit()
    ):
        raise ValueError("Use HH:MM")
    hour, minute = map(int, value.split(":"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Invalid local time")
    return hour * 60 + minute


def search_zones(query: str, limit: int = 8) -> tuple[str, ...]:
    key = query.strip().casefold().replace(" ", "_")
    if not key:
        return ()
    return tuple(sorted(z for z in available_timezones() if key in z.casefold())[:limit])


@dataclass(frozen=True)
class KickoffPreference:
    zone: str
    minute: int

    def __post_init__(self) -> None:
        validate_zone(self.zone)
        if type(self.minute) is not int or not 0 <= self.minute < 1440:
            raise ValueError("Invalid preferred minute")


def preference_utc(day: date, preference: KickoffPreference) -> datetime:
    """Fold: earliest occurrence; gap: shift forward by the DST gap.

    Search adjacent local dates so +14/-12 offsets still land in the assigned
    UTC day. Round-tripping a nonexistent time with fold=0 shifts it forward.
    """
    zone = validate_zone(preference.zone)
    local_clock = time(*divmod(preference.minute, 60))
    exact: list[datetime] = []
    shifted: list[datetime] = []
    for offset in (-1, 0, 1):
        local = datetime.combine(day + timedelta(days=offset), local_clock)
        for fold in (0, 1):
            candidate = local.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            restored = candidate.astimezone(zone).replace(tzinfo=None)
            if candidate.date() != day:
                continue
            if restored == local:
                exact.append(candidate)
            elif restored > local:
                shifted.append(candidate)
    if exact:
        return min(exact)
    if shifted:
        return min(shifted)
    raise ValueError("Preferred local time has no occurrence on the assigned UTC day")


@dataclass(frozen=True)
class KickoffDecision:
    kickoff: datetime
    policy_version: str
    tie_break: int | None


def choose_kickoff(
    day: date, home: KickoffPreference | None, away: KickoffPreference | None, *, seed: int
) -> KickoffDecision:
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Seed must fit nonnegative BIGINT")
    if home is None and away is None:
        return KickoffDecision(
            datetime.combine(day, time(*divmod(BASELINE_MINUTE, 60)), UTC), KICKOFF_POLICY, None
        )
    if home is None or away is None:
        chosen = home if home is not None else away
        assert chosen is not None
        return KickoffDecision(preference_utc(day, chosen), KICKOFF_POLICY, None)
    clocks = sorted(
        value.hour * 60 + value.minute
        for value in (preference_utc(day, home), preference_utc(day, away))
    )
    left, right = clocks
    delta = right - left
    tie = seed % 2 if delta == 720 else None
    # Work in doubled minutes to preserve .5 until the half-hour rounding.
    middle2 = left + right
    if delta > 720 or (delta == 720 and tie == 1):
        middle2 += 1440
    # Exactly 15 minutes rounds forward on the UTC clock, including midnight.
    minute = ((middle2 + 30) // 60 * 30) % 1440
    return KickoffDecision(
        datetime.combine(day, time(*divmod(minute, 60)), UTC), KICKOFF_POLICY, tie
    )


def local_datetime(value: datetime, zone: str) -> str:
    local = value.astimezone(validate_zone(zone))
    result = local.strftime("%d.%m.%Y %H:%M %Z (UTC%z)")
    if local.date() != value.astimezone(UTC).date():
        result += " · UTC " + value.astimezone(UTC).strftime("%d.%m.%Y")
    return result
