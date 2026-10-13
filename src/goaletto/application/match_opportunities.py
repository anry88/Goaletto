"""First and second match-opportunity participation calculator (AND-77/79/82 calc part).

Pure typed calculator for two weekly KPIs: participation in the first available
match and return to the second. No ORM, no network, no wall-clock reads: all
times (including ``report_at``) are passed explicitly as timezone-aware UTC.

Definitions mirror ``docs/analytics/measurement-plan.md`` and
``docs/analytics/contract-v1.md`` (Three KPIs):

* Opportunity window uses *actual* times: ``[actual_start-24h, actual_finish+24h)``.
* Maturity: ``actual_finish+24h <= report_at`` (see ``window_is_mature``).
* Claim at or before ``lineup_lock`` admits the fixture; claim after skips it.
* Ownership must still hold at the window end (``ended_at`` is ``None`` or
  ``>= window_end``). Lost ownership is a separate ``interrupted`` group, never
  a continuation by a new owner.
* Only ``league`` fixtures with ``finished`` status are supported opportunities.
  ``cancelled`` and non-league (``cup``/``friendly``) fixtures are excluded
  (skipped, separate route). ``abandoned`` and unknown statuses/types are
  ``unsupported``/``unavailable`` until a central mapping proves an official
  result; they never produce a false KPI.
* Earlier unfinished candidates block later mature ones: the first candidate in
  ``(lineup_lock, scheduled_start, fixture_id)`` order determines the status.
* ``n`` counts distinct managers with an eligible visible effect
  (``match_opened`` / ``match_report_opened`` / ``match_command_accepted``,
  ``origin=user``) for their own fixture/ownership inside the mature window.
  ``result_delivered``, ``match_command_applied``, automation/system effects,
  passive board updates and repeats never count. ``recorded_at > report_at``
  is ``unknown_late``, not success.
* Zero mature ``N`` yields ``rate=None``, never ``0%``.
* Insufficient coverage never equals proven absence: without complete
  calendar/ownership/event history the first observed fixture must not be
  declared the first historical opportunity.

Eligibility predicates are documented for the future SQL reader (see
``docs/analytics/match-opportunities.md``). This module only evaluates the
projections it is given; SQL projection/emission/wiring is a separate central
integration. ``EVENT_SPECS`` is not extended and entry/Dk/source/delivery
evaluators are not duplicated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from goaletto.application.analytics import (
    match_activity_window,
    require_utc,
    window_is_mature,
)

ELIGIBLE_USER_ACTIONS: frozenset[str] = frozenset(
    {"match_opened", "match_report_opened", "match_command_accepted"}
)
"""User effects that prove explicit participation. ``match_command_applied``,
``result_delivered``, automation/system and passive board updates are excluded."""

SUPPORTED_FIXTURE_TYPE = "league"
NON_LEAGUE_TYPES: frozenset[str] = frozenset({"cup", "friendly"})
CANCELLED_STATUS = "cancelled"
SUPPORTED_FINAL_STATUS = "finished"
PENDING_STATUSES: frozenset[str] = frozenset({"scheduled", "live", "paused"})

FIRST_STATUSES: frozenset[str] = frozenset(
    {
        "mature",
        "waiting",
        "interrupted",
        "coverage",
        "unsupported",
        "unavailable",
        "no_calendar",
        "excluded",
    }
)
SECOND_STATUSES: frozenset[str] = frozenset(
    {
        "mature",
        "waiting",
        "interrupted",
        "coverage",
        "unsupported",
        "unavailable",
        "no_calendar",
        "no_first",
        "excluded",
    }
)


@dataclass(frozen=True)
class ManagerProjection:
    """Eligibility projection for one manager.

    SQL must prove: ``managers.is_internal``, bot/test flag, and
    analytics-enabled (no row in ``analytics_exclusions`` for
    internal/test/operator/opt-out, tombstone respected). Bot managers,
    internal accounts and analytics-disabled managers are ``excluded`` and
    never enter ``N``.
    """

    manager_id: UUID
    is_internal: bool
    analytics_enabled: bool
    is_bot: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.manager_id, UUID):
            raise ValueError("manager_id must be a UUID")
        if type(self.is_internal) is not bool:
            raise ValueError("is_internal must be a bool")
        if type(self.analytics_enabled) is not bool:
            raise ValueError("analytics_enabled must be a bool")
        if type(self.is_bot) is not bool:
            raise ValueError("is_bot must be a bool")

    @property
    def excluded(self) -> bool:
        return self.is_internal or (not self.analytics_enabled) or self.is_bot


@dataclass(frozen=True)
class OwnershipProjection:
    """One ownership period.

    SQL must prove ``ownership_id``, ``manager_id``, ``club_id``,
    ``claimed_at`` (period start) and ``ended_at`` (``None`` while active)
    with complete history (no truncated/retention-limited gaps). Ownership
    refs in effects are checked against these periods.
    """

    ownership_id: UUID
    manager_id: UUID
    club_id: UUID
    claimed_at: datetime
    ended_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.ownership_id, UUID):
            raise ValueError("ownership_id must be a UUID")
        if not isinstance(self.manager_id, UUID):
            raise ValueError("ownership manager_id must be a UUID")
        if not isinstance(self.club_id, UUID):
            raise ValueError("ownership club_id must be a UUID")
        require_utc(self.claimed_at)
        if self.ended_at is not None:
            require_utc(self.ended_at)
            if self.ended_at < self.claimed_at:
                raise ValueError("Ownership end precedes claim")


@dataclass(frozen=True)
class FixtureProjection:
    """One fixture from the calendar perspective of the owning club.

    Fields for future SQL: ``fixture_id``, ``club_id`` (the owned club taking
    part), ``fixture_type`` (only ``league`` is supported; ``cup``/``friendly``
    are a separate route and are skipped), ``status``, calendar
    ``scheduled_start``, ``lineup_lock``, and actual ``actual_start`` /
    ``actual_finish`` (postponements/pauses shift the window via actual times).
    ``has_official_result`` records whether an official result was proven; it
    is carried for the future central mapping of ``abandoned`` and does not
    yet admit any abandoned fixture (all are ``unsupported``).
    """

    fixture_id: UUID
    club_id: UUID
    fixture_type: str
    status: str
    scheduled_start: datetime
    lineup_lock: datetime
    actual_start: datetime | None
    actual_finish: datetime | None
    has_official_result: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, UUID):
            raise ValueError("fixture_id must be a UUID")
        if not isinstance(self.club_id, UUID):
            raise ValueError("fixture club_id must be a UUID")
        if type(self.fixture_type) is not str or not self.fixture_type:
            raise ValueError("fixture_type must be a nonempty string")
        if type(self.status) is not str or not self.status:
            raise ValueError("fixture status must be a nonempty string")
        require_utc(self.scheduled_start)
        require_utc(self.lineup_lock)
        if self.actual_start is not None:
            require_utc(self.actual_start)
        if self.actual_finish is not None:
            require_utc(self.actual_finish)
        if (
            self.actual_start is not None
            and self.actual_finish is not None
            and self.actual_finish < self.actual_start
        ):
            raise ValueError("Fixture finish precedes start")
        if type(self.has_official_result) is not bool:
            raise ValueError("has_official_result must be a bool")


@dataclass(frozen=True)
class UserEffectProjection:
    """One eligible-candidate user effect (deduplicated by ``effect_key``).

    SQL must prove ``fixture_id``/``ownership_id`` refs, ``origin``,
    ``occurred_at``/``recorded_at`` and a stable effect key
    (``journey:v1:{operation_id}:{effect_number}``), plus ``version=1`` and the
    contract name. Only ``match_opened`` / ``match_report_opened`` /
    ``match_command_accepted`` with ``origin=user`` inside the opportunity
    window count; wrong fixture/ownership, other names/origins and repeats do
    not increase ``n``. ``recorded_at > report_at`` is ``unknown_late``.
    """

    effect_key: str
    manager_id: UUID
    fixture_id: UUID
    ownership_id: UUID
    name: str
    origin: str
    occurred_at: datetime
    recorded_at: datetime
    version: int = 1

    def __post_init__(self) -> None:
        if type(self.effect_key) is not str or not self.effect_key:
            raise ValueError("effect_key must be a nonempty string")
        if not isinstance(self.manager_id, UUID):
            raise ValueError("effect manager_id must be a UUID")
        if not isinstance(self.fixture_id, UUID):
            raise ValueError("effect fixture_id must be a UUID")
        if not isinstance(self.ownership_id, UUID):
            raise ValueError("effect ownership_id must be a UUID")
        if type(self.name) is not str or not self.name:
            raise ValueError("effect name must be a nonempty string")
        if type(self.origin) is not str or not self.origin:
            raise ValueError("effect origin must be a nonempty string")
        require_utc(self.occurred_at)
        require_utc(self.recorded_at)
        if self.recorded_at < self.occurred_at:
            raise ValueError("Effect recorded_at precedes occurred_at")
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Effect version must be 1")


@dataclass(frozen=True)
class CoverageState:
    """Explicit projection coverage.

    * ``calendar_complete``: the fixture list for the evaluated clubs/period is
      complete (no missing earlier rounds, no truncated calendar).
    * ``ownership_history_complete``: ownership periods are complete (no
      truncated/retention-limited history that could hide an earlier claim).
    * ``events_complete``: user effects are complete (absence of an effect is
      proven absence, not a retention gap).

    Incomplete coverage never equals proven absence: without full history the
    first observed fixture must not be declared the first historical
    opportunity (``coverage`` group, never a false ``N``/``n``).
    SQL must prove each flag (coverage start, retention window, gaps).
    """

    calendar_complete: bool
    ownership_history_complete: bool
    events_complete: bool

    def __post_init__(self) -> None:
        if type(self.calendar_complete) is not bool:
            raise ValueError("calendar_complete must be a bool")
        if type(self.ownership_history_complete) is not bool:
            raise ValueError("ownership_history_complete must be a bool")
        if type(self.events_complete) is not bool:
            raise ValueError("events_complete must be a bool")

    @property
    def history_complete(self) -> bool:
        return self.calendar_complete and self.ownership_history_complete

    @property
    def fully_complete(self) -> bool:
        return self.history_complete and self.events_complete


@dataclass(frozen=True)
class ManagerOpportunityDetail:
    """Per-manager first/second outcome (deterministic, sorted by manager)."""

    manager_id: UUID
    first_fixture_id: UUID | None
    first_status: str
    first_participated: bool
    first_unknown_late: bool
    second_fixture_id: UUID | None
    second_status: str
    second_participated: bool
    second_unknown_late: bool

    def __post_init__(self) -> None:
        if not isinstance(self.manager_id, UUID):
            raise ValueError("detail manager_id must be a UUID")
        if self.first_fixture_id is not None and not isinstance(self.first_fixture_id, UUID):
            raise ValueError("first_fixture_id must be a UUID or None")
        if self.second_fixture_id is not None and not isinstance(self.second_fixture_id, UUID):
            raise ValueError("second_fixture_id must be a UUID or None")
        if self.first_status not in FIRST_STATUSES:
            raise ValueError("Unknown first status")
        if self.second_status not in SECOND_STATUSES:
            raise ValueError("Unknown second status")
        if type(self.first_participated) is not bool:
            raise ValueError("first_participated must be a bool")
        if type(self.first_unknown_late) is not bool:
            raise ValueError("first_unknown_late must be a bool")
        if type(self.second_participated) is not bool:
            raise ValueError("second_participated must be a bool")
        if type(self.second_unknown_late) is not bool:
            raise ValueError("second_unknown_late must be a bool")
        if self.first_status != "mature" and self.first_participated:
            raise ValueError("Only mature first can show participation")
        if self.second_status != "mature" and self.second_participated:
            raise ValueError("Only mature second can show participation")
        if self.first_participated and self.first_unknown_late:
            raise ValueError("Participation and late unknown are exclusive")
        if self.second_participated and self.second_unknown_late:
            raise ValueError("Participation and late unknown are exclusive")


@dataclass(frozen=True)
class OpportunityCounts:
    """Aggregate ``n/N`` with waiting and separate groups.

    ``rate`` is ``None`` when ``eligible_N == 0`` (zero mature ``N`` never
    becomes ``0%``).
    """

    eligible_N: int
    participated_n: int
    waiting: int
    interrupted: int
    coverage: int
    unsupported: int
    unavailable: int
    no_calendar: int
    unknown_late: int
    rate: float | None

    def __post_init__(self) -> None:
        counts = (
            self.eligible_N,
            self.participated_n,
            self.waiting,
            self.interrupted,
            self.coverage,
            self.unsupported,
            self.unavailable,
            self.no_calendar,
            self.unknown_late,
        )
        if any(type(v) is not int or v < 0 for v in counts):
            raise ValueError("Opportunity counts must be nonnegative integers")
        if not 0 <= self.participated_n <= self.eligible_N:
            raise ValueError("Opportunity numerator exceeds mature cohort")
        if not 0 <= self.unknown_late <= self.eligible_N - self.participated_n:
            raise ValueError("Late unknown exceeds mature non-participants")
        if self.rate is not None:
            if not isinstance(self.rate, float):
                raise ValueError("Rate must be a float or None")
            if self.eligible_N == 0:
                raise ValueError("Rate must be None without a mature cohort")
            expected = self.participated_n / self.eligible_N
            if abs(self.rate - expected) > 1e-12:
                raise ValueError("Rate does not reconcile with n/N")
        elif self.eligible_N != 0:
            raise ValueError("Rate must be set with a mature cohort")


@dataclass(frozen=True)
class MatchOpportunityReport:
    """Deterministic report for both KPIs at one ``report_at``."""

    report_at: datetime
    coverage_limited: bool
    total_managers: int
    excluded_managers: int
    first: OpportunityCounts
    second: OpportunityCounts
    second_no_first: int
    details: tuple[ManagerOpportunityDetail, ...]

    def __post_init__(self) -> None:
        require_utc(self.report_at)
        if type(self.coverage_limited) is not bool:
            raise ValueError("coverage_limited must be a bool")
        if type(self.total_managers) is not int or self.total_managers < 0:
            raise ValueError("total_managers must be a nonnegative integer")
        if type(self.excluded_managers) is not int or self.excluded_managers < 0:
            raise ValueError("excluded_managers must be a nonnegative integer")
        if type(self.second_no_first) is not int or self.second_no_first < 0:
            raise ValueError("second_no_first must be a nonnegative integer")
        if not isinstance(self.details, tuple):
            raise ValueError("details must be a tuple")
        if len(self.details) != self.total_managers:
            raise ValueError("details do not reconcile with total_managers")
        lanes = sorted(d.manager_id.hex for d in self.details)
        if lanes != [d.manager_id.hex for d in self.details]:
            raise ValueError("details must be sorted deterministically by manager_id")
        first_total = (
            self.first.eligible_N
            + self.first.waiting
            + self.first.interrupted
            + self.first.coverage
            + self.first.unsupported
            + self.first.unavailable
            + self.first.no_calendar
        )
        if first_total + self.excluded_managers != self.total_managers:
            raise ValueError("First groups do not reconcile with total_managers")
        second_total = (
            self.second.eligible_N
            + self.second.waiting
            + self.second.interrupted
            + self.second.coverage
            + self.second.unsupported
            + self.second.unavailable
            + self.second.no_calendar
        )
        if second_total + self.second_no_first != self.total_managers:
            raise ValueError("Second groups do not reconcile with total_managers")


def _dedupe_effects(
    effects: tuple[UserEffectProjection, ...],
) -> tuple[UserEffectProjection, ...]:
    seen: dict[str, UserEffectProjection] = {}
    for effect in sorted(
        effects,
        key=lambda e: (
            e.effect_key,
            e.occurred_at,
            e.recorded_at,
            e.manager_id.hex,
            e.fixture_id.hex,
        ),
    ):
        if effect.effect_key not in seen:
            seen[effect.effect_key] = effect
    return tuple(seen[key] for key in sorted(seen))


def _qualifying_ownership(
    ownerships: list[OwnershipProjection],
    club_id: UUID,
    lineup_lock: datetime,
) -> OwnershipProjection | None:
    candidates = [
        own for own in ownerships if own.club_id == club_id and own.claimed_at <= lineup_lock
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: (o.claimed_at, o.ownership_id.hex))
    return candidates[-1]


def _candidate_fixtures(
    fixtures: tuple[FixtureProjection, ...],
    ownerships: list[OwnershipProjection],
) -> list[tuple[FixtureProjection, OwnershipProjection]]:
    """League, non-cancelled fixtures with a qualifying claim, in order."""
    picked: list[tuple[FixtureProjection, OwnershipProjection]] = []
    for fixture in fixtures:
        if fixture.fixture_type in NON_LEAGUE_TYPES:
            continue
        if fixture.fixture_type != SUPPORTED_FIXTURE_TYPE:
            # Unknown competition: keep as blocking unsupported candidate.
            pass
        if fixture.status == CANCELLED_STATUS:
            continue
        ownership = _qualifying_ownership(ownerships, fixture.club_id, fixture.lineup_lock)
        if ownership is None:
            continue
        # Keep unknown-type fixtures so they block as unsupported instead of
        # being silently skipped like cup/friendly/cancelled.
        picked.append((fixture, ownership))
    picked.sort(
        key=lambda item: (
            item[0].lineup_lock,
            item[0].scheduled_start,
            item[0].fixture_id.hex,
        )
    )
    return picked


def _participation(
    *,
    effects: tuple[UserEffectProjection, ...],
    manager_id: UUID,
    fixture_id: UUID,
    ownership_id: UUID,
    window_start: datetime,
    window_end: datetime,
    report_at: datetime,
) -> tuple[bool, bool]:
    visible = False
    late = False
    for effect in effects:
        if effect.manager_id != manager_id:
            continue
        if effect.fixture_id != fixture_id:
            continue
        if effect.ownership_id != ownership_id:
            continue
        if effect.name not in ELIGIBLE_USER_ACTIONS:
            continue
        if effect.origin != "user":
            continue
        if effect.version != 1:
            continue
        if not window_start <= effect.occurred_at < window_end:
            continue
        if effect.recorded_at <= report_at:
            visible = True
        else:
            late = True
    if visible:
        return True, False
    if late:
        return False, True
    return False, False


def _classify_candidate(
    fixture: FixtureProjection,
    ownership: OwnershipProjection,
    report_at: datetime,
) -> tuple[str, datetime | None, datetime | None]:
    """Return (status, window_start, window_end) for one ordered candidate.

    ``mature`` means finished, ownership held through the window end, and the
    window closed at ``report_at``. ``waiting`` covers unfinished/future
    fixtures. ``interrupted`` is lost ownership. ``unsupported`` is abandoned
    or unknown competition/status without a central official-result mapping.
    ``unavailable`` is finished without usable actual times.
    """
    if fixture.fixture_type != SUPPORTED_FIXTURE_TYPE:
        return "unsupported", None, None
    if fixture.status in PENDING_STATUSES:
        return "waiting", None, None
    if fixture.status != SUPPORTED_FINAL_STATUS:
        # Includes abandoned (with or without has_official_result: no central
        # mapping yet) and any unknown final status.
        return "unsupported", None, None
    if fixture.actual_start is None or fixture.actual_finish is None:
        return "unavailable", None, None
    window_start, window_end = match_activity_window(fixture.actual_start, fixture.actual_finish)
    if ownership.ended_at is not None and ownership.ended_at < window_end:
        return "interrupted", window_start, window_end
    if not window_is_mature(window_end, report_at):
        return "waiting", window_start, window_end
    return "mature", window_start, window_end


def evaluate_match_opportunities(
    *,
    managers: tuple[ManagerProjection, ...],
    ownerships: tuple[OwnershipProjection, ...],
    fixtures: tuple[FixtureProjection, ...],
    effects: tuple[UserEffectProjection, ...],
    coverage: CoverageState,
    report_at: datetime,
) -> MatchOpportunityReport:
    """Evaluate first/second match-opportunity KPIs for explicit projections."""
    require_utc(report_at)
    if not isinstance(managers, tuple):
        raise ValueError("managers must be a tuple")
    if not isinstance(ownerships, tuple):
        raise ValueError("ownerships must be a tuple")
    if not isinstance(fixtures, tuple):
        raise ValueError("fixtures must be a tuple")
    if not isinstance(effects, tuple):
        raise ValueError("effects must be a tuple")
    if not isinstance(coverage, CoverageState):
        raise ValueError("coverage must be a CoverageState")
    for manager_item in managers:
        if not isinstance(manager_item, ManagerProjection):
            raise ValueError("managers must hold ManagerProjection")
    for ownership_item in ownerships:
        if not isinstance(ownership_item, OwnershipProjection):
            raise ValueError("ownerships must hold OwnershipProjection")
    for fixture_item in fixtures:
        if not isinstance(fixture_item, FixtureProjection):
            raise ValueError("fixtures must hold FixtureProjection")
    for effect_item in effects:
        if not isinstance(effect_item, UserEffectProjection):
            raise ValueError("effects must hold UserEffectProjection")

    manager_ids = [m.manager_id for m in managers]
    if len(set(manager_ids)) != len(manager_ids):
        raise ValueError("Duplicate manager_id")
    ownership_ids = [o.ownership_id for o in ownerships]
    if len(set(ownership_ids)) != len(ownership_ids):
        raise ValueError("Duplicate ownership_id")
    fixture_ids = [f.fixture_id for f in fixtures]
    if len(set(fixture_ids)) != len(fixture_ids):
        raise ValueError("Duplicate fixture_id")

    manager_index = {m.manager_id: m for m in managers}
    ownerships_by_manager: dict[UUID, list[OwnershipProjection]] = {
        m.manager_id: [] for m in managers
    }
    for own in ownerships:
        if own.manager_id not in manager_index:
            raise ValueError("Ownership references an unknown manager")
        ownerships_by_manager[own.manager_id].append(own)

    deduped_effects = _dedupe_effects(effects)

    details: list[ManagerOpportunityDetail] = []
    for manager in sorted(managers, key=lambda m: m.manager_id.hex):
        if manager.excluded:
            details.append(
                ManagerOpportunityDetail(
                    manager_id=manager.manager_id,
                    first_fixture_id=None,
                    first_status="excluded",
                    first_participated=False,
                    first_unknown_late=False,
                    second_fixture_id=None,
                    second_status="excluded",
                    second_participated=False,
                    second_unknown_late=False,
                )
            )
            continue

        own_list = sorted(
            ownerships_by_manager[manager.manager_id],
            key=lambda o: (o.claimed_at, o.ownership_id.hex),
        )
        owned_clubs = {o.club_id for o in own_list}
        relevant = tuple(f for f in fixtures if f.club_id in owned_clubs) if owned_clubs else ()
        candidates = _candidate_fixtures(relevant, own_list)

        if not candidates:
            if not coverage.history_complete:
                first_status = "coverage"
            elif not owned_clubs or not relevant:
                # Complete history and truly no fixture for owned clubs.
                # Distinguish "no ownership at all" from "no calendar": both
                # are explicit no_calendar (never a false zero rate).
                first_status = "no_calendar"
            else:
                # Owned clubs exist but none of their fixtures had a
                # qualifying claim (all claims after lock): the manager simply
                # has no opportunity yet with complete coverage.
                first_status = "no_calendar"
            # Coverage for events does not change an empty calendar: there is
            # no window whose absence of actions could be misread.
            details.append(
                ManagerOpportunityDetail(
                    manager_id=manager.manager_id,
                    first_fixture_id=None,
                    first_status=first_status,
                    first_participated=False,
                    first_unknown_late=False,
                    second_fixture_id=None,
                    second_status=("coverage" if first_status == "coverage" else "no_first"),
                    second_participated=False,
                    second_unknown_late=False,
                )
            )
            continue

        # First opportunity: earliest candidate blocks later ones.
        first_idx: int | None = None
        first_status = "waiting"
        first_window: tuple[datetime | None, datetime | None] = (None, None)
        first_pair: tuple[FixtureProjection, OwnershipProjection] | None = None
        for idx, (fixture, ownership) in enumerate(candidates):
            status, wstart, wend = _classify_candidate(fixture, ownership, report_at)
            if status == "mature":
                first_idx = idx
                first_status = "mature"
                first_window = (wstart, wend)
                first_pair = (fixture, ownership)
                break
            # waiting/interrupted/unsupported/unavailable all block: the
            # manager cannot skip an earlier unresolved candidate for a later
            # mature fixture.
            first_idx = idx
            first_status = status
            first_window = (wstart, wend)
            first_pair = (fixture, ownership)
            break

        assert first_pair is not None and first_idx is not None
        first_fixture, first_ownership = first_pair

        # Coverage override: without complete history the first observed
        # fixture must not be declared the first historical opportunity.
        if first_status in {"mature", "interrupted", "unsupported", "unavailable"}:
            if not coverage.history_complete:
                first_status = "coverage"
        if first_status == "no_calendar":
            if not coverage.history_complete:  # defensive; handled above
                first_status = "coverage"
        # Without complete events, a mature window must not be reported as a
        # proven (non-)participation.
        if first_status == "mature" and not coverage.events_complete:
            first_status = "coverage"

        first_participated = False
        first_late = False
        if first_status == "mature":
            assert first_window[0] is not None and first_window[1] is not None
            first_participated, first_late = _participation(
                effects=deduped_effects,
                manager_id=manager.manager_id,
                fixture_id=first_fixture.fixture_id,
                ownership_id=first_ownership.ownership_id,
                window_start=first_window[0],
                window_end=first_window[1],
                report_at=report_at,
            )

        # Second opportunity: only first participants have a denominator.
        if not first_participated or first_status != "mature":
            if first_status == "coverage":
                second_status = "coverage"
            elif first_status == "excluded":
                second_status = "excluded"
            else:
                second_status = "no_first"
            details.append(
                ManagerOpportunityDetail(
                    manager_id=manager.manager_id,
                    first_fixture_id=first_fixture.fixture_id,
                    first_status=first_status,
                    first_participated=first_participated,
                    first_unknown_late=first_late,
                    second_fixture_id=None,
                    second_status=second_status,
                    second_participated=False,
                    second_unknown_late=False,
                )
            )
            continue

        rest = candidates[first_idx + 1 :]
        if not rest:
            if not coverage.history_complete:
                second_status = "coverage"
            else:
                second_status = "waiting"
            details.append(
                ManagerOpportunityDetail(
                    manager_id=manager.manager_id,
                    first_fixture_id=first_fixture.fixture_id,
                    first_status=first_status,
                    first_participated=True,
                    first_unknown_late=False,
                    second_fixture_id=None,
                    second_status=second_status,
                    second_participated=False,
                    second_unknown_late=False,
                )
            )
            continue

        second_pair = rest[0]
        second_fixture, second_ownership = second_pair
        raw_status, swstart, swend = _classify_candidate(
            second_fixture, second_ownership, report_at
        )
        second_status = raw_status
        # Same blocking rule: only the immediate next candidate counts; a
        # pending/interrupted/unsupported next fixture blocks skipping.
        if second_status in {"mature", "interrupted", "unsupported", "unavailable"}:
            if not coverage.history_complete:
                second_status = "coverage"
        if second_status == "mature" and not coverage.events_complete:
            second_status = "coverage"

        second_participated = False
        second_late = False
        if second_status == "mature":
            assert swstart is not None and swend is not None
            second_participated, second_late = _participation(
                effects=deduped_effects,
                manager_id=manager.manager_id,
                fixture_id=second_fixture.fixture_id,
                ownership_id=second_ownership.ownership_id,
                window_start=swstart,
                window_end=swend,
                report_at=report_at,
            )

        details.append(
            ManagerOpportunityDetail(
                manager_id=manager.manager_id,
                first_fixture_id=first_fixture.fixture_id,
                first_status=first_status,
                first_participated=first_participated,
                first_unknown_late=first_late,
                second_fixture_id=second_fixture.fixture_id,
                second_status=second_status,
                second_participated=second_participated,
                second_unknown_late=second_late,
            )
        )

    details_t = tuple(sorted(details, key=lambda d: d.manager_id.hex))

    def _counts(kind: str) -> OpportunityCounts:
        if kind == "first":
            mature = sum(1 for d in details_t if d.first_status == "mature")
            part = sum(1 for d in details_t if d.first_status == "mature" and d.first_participated)
            late = sum(
                1
                for d in details_t
                if d.first_status == "mature" and not d.first_participated and d.first_unknown_late
            )
            waiting = sum(1 for d in details_t if d.first_status == "waiting")
            interrupted = sum(1 for d in details_t if d.first_status == "interrupted")
            cov = sum(1 for d in details_t if d.first_status == "coverage")
            unsup = sum(1 for d in details_t if d.first_status == "unsupported")
            unav = sum(1 for d in details_t if d.first_status == "unavailable")
            nocal = sum(1 for d in details_t if d.first_status == "no_calendar")
        else:
            mature = sum(1 for d in details_t if d.second_status == "mature")
            part = sum(
                1 for d in details_t if d.second_status == "mature" and d.second_participated
            )
            late = sum(
                1
                for d in details_t
                if d.second_status == "mature"
                and not d.second_participated
                and d.second_unknown_late
            )
            waiting = sum(1 for d in details_t if d.second_status == "waiting")
            interrupted = sum(1 for d in details_t if d.second_status == "interrupted")
            cov = sum(1 for d in details_t if d.second_status == "coverage")
            unsup = sum(1 for d in details_t if d.second_status == "unsupported")
            unav = sum(1 for d in details_t if d.second_status == "unavailable")
            nocal = sum(1 for d in details_t if d.second_status == "no_calendar")
        rate = (float(part) / float(mature)) if mature else None
        return OpportunityCounts(
            eligible_N=mature,
            participated_n=part,
            waiting=waiting,
            interrupted=interrupted,
            coverage=cov,
            unsupported=unsup,
            unavailable=unav,
            no_calendar=nocal,
            unknown_late=late,
            rate=rate,
        )

    first_counts = _counts("first")
    second_counts = _counts("second")
    excluded = sum(1 for d in details_t if d.first_status == "excluded")
    no_first = sum(1 for d in details_t if d.second_status in {"no_first", "excluded"})
    # Managers whose first is coverage carry second coverage, not no_first;
    # managers with waiting/interrupted/etc. first carry no_first. Both are
    # explicit and reconciled below.

    return MatchOpportunityReport(
        report_at=report_at,
        coverage_limited=not coverage.fully_complete,
        total_managers=len(details_t),
        excluded_managers=excluded,
        first=first_counts,
        second=second_counts,
        second_no_first=no_first,
        details=details_t,
    )


def validate_match_opportunity_report(data: dict[str, Any]) -> None:
    """Strict reconciliation check for a serialized report dict."""
    first_raw = data.get("first")
    second_raw = data.get("second")
    if not isinstance(first_raw, dict) or not isinstance(second_raw, dict):
        raise ValueError("Report needs first/second sections")
    for section in (first_raw, second_raw):
        for key in (
            "eligible_N",
            "participated_n",
            "waiting",
            "interrupted",
            "coverage",
            "unsupported",
            "unavailable",
            "no_calendar",
            "unknown_late",
        ):
            value = section.get(key)
            if type(value) is not int or value < 0:
                raise ValueError("Match opportunity counts must be nonnegative integers")
        eligible = section["eligible_N"]
        participated = section["participated_n"]
        unknown_late = section["unknown_late"]
        assert isinstance(eligible, int)
        assert isinstance(participated, int)
        assert isinstance(unknown_late, int)
        if not 0 <= participated <= eligible:
            raise ValueError("Opportunity numerator exceeds mature cohort")
        if not 0 <= unknown_late <= eligible - participated:
            raise ValueError("Late unknown exceeds mature non-participants")
    total = data.get("total_managers")
    excluded = data.get("excluded_managers")
    no_first = data.get("second_no_first")
    if type(total) is not int or total < 0:
        raise ValueError("total_managers must be a nonnegative integer")
    if type(excluded) is not int or excluded < 0:
        raise ValueError("excluded_managers must be a nonnegative integer")
    if type(no_first) is not int or no_first < 0:
        raise ValueError("second_no_first must be a nonnegative integer")
