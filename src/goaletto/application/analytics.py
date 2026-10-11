"""Version 1 journey contract. Validation only; no collector or database writes."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

CONTRACT_VERSION = 1


class Origin(StrEnum):
    USER = "user"
    SYSTEM = "system"
    AUTOMATION = "automation"
    OPERATOR = "operator"


class Surface(StrEnum):
    ONBOARDING = "onboarding"
    CLUB = "club"
    SQUAD = "squad"
    CALENDAR = "calendar"
    MATCH = "match"
    REPORT = "report"
    HELP = "help"
    FEEDBACK = "feedback"
    DELIVERY = "delivery"
    LIFECYCLE = "lifecycle"


class Reason(StrEnum):
    INVALID_INPUT = "invalid_input"
    STALE_ACTION = "stale_action"
    LINEUP_INVALID = "lineup_invalid"
    CLUB_UNAVAILABLE = "club_unavailable"
    PERMISSION_DENIED = "permission_denied"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    TECHNICAL_FAILURE = "technical_failure"
    BLOCKED = "blocked"
    CHAT_UNAVAILABLE = "chat_unavailable"
    UNCLEAR_NEXT_STEP = "unclear_next_step"
    DIFFICULT_LINEUP = "difficult_lineup"
    LONG_WAIT = "long_wait"
    INCONVENIENT_TIME = "inconvenient_time"
    UNINTERESTING_GAME = "uninteresting_game"
    OTHER = "other"


ERROR_REASONS = frozenset(
    {
        Reason.INVALID_INPUT,
        Reason.STALE_ACTION,
        Reason.LINEUP_INVALID,
        Reason.CLUB_UNAVAILABLE,
        Reason.PERMISSION_DENIED,
        Reason.INSUFFICIENT_FUNDS,
        Reason.TECHNICAL_FAILURE,
    }
)
FEEDBACK_REASONS = frozenset(
    {
        Reason.UNCLEAR_NEXT_STEP,
        Reason.DIFFICULT_LINEUP,
        Reason.LONG_WAIT,
        Reason.INCONVENIENT_TIME,
        Reason.UNINTERESTING_GAME,
        Reason.TECHNICAL_FAILURE,
        Reason.OTHER,
    }
)


@dataclass(frozen=True)
class EventSpec:
    origin: Origin
    required_refs: tuple[str, ...] = ()
    user_activity: bool = False
    ownership_activity: bool = False


def _user(*refs: str, ownership: bool = False, activity: bool = True) -> EventSpec:
    return EventSpec(Origin.USER, refs, activity, ownership)


EVENT_SPECS = MappingProxyType(
    {
        "manager_started": _user(),
        "campaign_touched": _user("campaign_id"),
        "club_offer_delivered": EventSpec(Origin.SYSTEM, ("analytics_manager_id", "outbox_id")),
        "club_preview_opened": _user("club_id"),
        "club_claimed": _user("club_id", "ownership_id", ownership=True),
        "club_opened": _user("club_id", "ownership_id", ownership=True),
        "lineup_opened": _user("club_id", "ownership_id", ownership=True),
        "lineup_saved": _user("club_id", "ownership_id", "command_id", ownership=True),
        "next_fixture_opened": _user("club_id", "ownership_id", "fixture_id", ownership=True),
        "match_opened": _user("club_id", "ownership_id", "fixture_id", ownership=True),
        "match_command_accepted": _user(
            "club_id", "ownership_id", "fixture_id", "command_id", ownership=True
        ),
        "match_command_applied": EventSpec(Origin.SYSTEM, ("fixture_id", "command_id")),
        "fixture_finished": EventSpec(Origin.SYSTEM, ("fixture_id",)),
        "result_delivered": EventSpec(
            Origin.SYSTEM, ("analytics_manager_id", "fixture_id", "outbox_id")
        ),
        "match_report_opened": _user("club_id", "ownership_id", "fixture_id", ownership=True),
        "next_action_opened": _user(),
        "action_rejected": _user(activity=False),
        "help_delivered": EventSpec(Origin.SYSTEM, ("analytics_manager_id", "outbox_id")),
        "feedback_prompt_delivered": EventSpec(
            Origin.SYSTEM, ("analytics_manager_id", "outbox_id", "feedback_campaign_id")
        ),
        "feedback_answered": _user("feedback_campaign_id"),
        "feedback_skipped": _user("feedback_campaign_id"),
        "experiment_exposed": EventSpec(
            Origin.SYSTEM, ("analytics_manager_id", "experiment_id", "outbox_id")
        ),
        "bot_unavailable": EventSpec(Origin.SYSTEM, ("analytics_manager_id", "outbox_id")),
        "club_ownership_ended": EventSpec(Origin.SYSTEM, ("club_id", "ownership_id")),
        "player_last_season_announced": EventSpec(Origin.SYSTEM, ("person_id", "season_id")),
        "player_retired": EventSpec(Origin.SYSTEM, ("person_id", "season_id")),
        "bot_youth_replacement_created": EventSpec(
            Origin.AUTOMATION, ("person_id", "season_id", "club_id")
        ),
    }
)


def require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Contract timestamps must be timezone-aware UTC")


@dataclass(frozen=True, kw_only=True)
class JourneyEvent:
    """Closed envelope: no free payload, names, Telegram IDs or comment text."""

    event_id: UUID
    name: str
    occurred_at: datetime
    recorded_at: datetime
    origin: Origin
    operation_id: UUID
    effect_number: int
    surface: Surface
    version: int = CONTRACT_VERSION
    analytics_manager_id: UUID | None = None
    session_id: UUID | None = None
    campaign_id: UUID | None = None
    feedback_campaign_id: UUID | None = None
    club_id: UUID | None = None
    ownership_id: UUID | None = None
    fixture_id: UUID | None = None
    command_id: UUID | None = None
    outbox_id: UUID | None = None
    person_id: UUID | None = None
    season_id: UUID | None = None
    experiment_id: UUID | None = None
    variant: str | None = None
    reason: Reason | None = None
    target: Surface | None = None

    def __post_init__(self) -> None:
        spec = EVENT_SPECS.get(self.name)
        if spec is None or type(self.version) is not int or self.version != CONTRACT_VERSION:
            raise ValueError("Unknown event or contract version")
        if not isinstance(self.origin, Origin) or self.origin != spec.origin:
            raise ValueError("Origin does not match event contract")
        if not isinstance(self.surface, Surface):
            raise ValueError("Unknown surface")
        require_utc(self.occurred_at)
        require_utc(self.recorded_at)
        if self.recorded_at < self.occurred_at:
            raise ValueError("Recorded timestamp precedes event time")
        if type(self.effect_number) is not int or not 0 <= self.effect_number <= 32767:
            raise ValueError("Effect number must be a bounded integer")
        for field in (
            "event_id",
            "operation_id",
            "analytics_manager_id",
            "session_id",
            "campaign_id",
            "feedback_campaign_id",
            "club_id",
            "ownership_id",
            "fixture_id",
            "command_id",
            "outbox_id",
            "person_id",
            "season_id",
            "experiment_id",
        ):
            value = getattr(self, field)
            if value is not None and not isinstance(value, UUID):
                raise ValueError("References must be UUIDs")
        if self.event_id is None or self.operation_id is None:
            raise ValueError("Event and operation IDs are required")
        if self.origin == Origin.USER:
            if self.analytics_manager_id is None or self.session_id is None:
                raise ValueError("User events need manager and explicit-action session")
        elif self.session_id is not None:
            raise ValueError("Background events must not extend a user session")
        for ref in spec.required_refs:
            if getattr(self, ref) is None:
                raise ValueError(f"Missing required reference: {ref}")
        if self.reason is not None and not isinstance(self.reason, Reason):
            raise ValueError("Unknown reason")
        if self.reason is not None and self.name not in {
            "action_rejected",
            "feedback_answered",
            "bot_unavailable",
        }:
            raise ValueError("This event has no reason field")
        if self.name == "action_rejected" and self.reason not in ERROR_REASONS:
            raise ValueError("Rejection needs an allowed error reason")
        if self.name == "feedback_answered" and self.reason not in FEEDBACK_REASONS:
            raise ValueError("Feedback needs an allowed response reason")
        if self.name == "bot_unavailable" and self.reason not in {
            Reason.BLOCKED,
            Reason.CHAT_UNAVAILABLE,
        }:
            raise ValueError("Bot availability needs a confirmed delivery reason")
        if self.target is not None and not isinstance(self.target, Surface):
            raise ValueError("Unknown next-action target")
        if self.name == "next_action_opened" and self.target is None:
            raise ValueError("Next action needs an explicit bounded target")
        if self.variant not in {None, "control", "a", "b"}:
            raise ValueError("Unknown experiment variant")
        if self.name == "experiment_exposed" and self.variant is None:
            raise ValueError("Exposure requires a delivered variant")

    @property
    def dedupe_key(self) -> str:
        return f"journey:v{self.version}:{self.operation_id}:{self.effect_number}"

    def counts_as_activity(self, *, internal: bool) -> bool:
        return not internal and EVENT_SPECS[self.name].user_activity

    def extends_ownership(self) -> bool:
        return EVENT_SPECS[self.name].ownership_activity


def retention_window(started_at: datetime, day: int) -> tuple[datetime, datetime]:
    require_utc(started_at)
    if type(day) is not int or day not in {1, 3, 7, 30}:
        raise ValueError("Supported retention windows are D1/D3/D7/D30")
    return started_at + timedelta(days=day), started_at + timedelta(days=day + 1)


def match_activity_window(
    actual_start: datetime, actual_finish: datetime
) -> tuple[datetime, datetime]:
    require_utc(actual_start)
    require_utc(actual_finish)
    if actual_finish < actual_start:
        raise ValueError("Match finish precedes start")
    return actual_start - timedelta(hours=24), actual_finish + timedelta(hours=24)


def window_is_mature(end: datetime, report_at: datetime) -> bool:
    require_utc(end)
    require_utc(report_at)
    return report_at >= end


def continues_session(previous_action_at: datetime, action_at: datetime) -> bool:
    require_utc(previous_action_at)
    require_utc(action_at)
    return timedelta(0) <= action_at - previous_action_at < timedelta(minutes=30)
