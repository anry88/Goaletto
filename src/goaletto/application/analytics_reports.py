"""Operator journey/delivery aggregates. No public API, no new events, no Grafana.

Three read-only reports mirror the SQL in
``infrastructure.analytics_reports_queries`` and the definitions in
``docs/analytics/measurement-plan.md`` and ``docs/analytics/contract-v1.md``:

* entry funnel ``start -> claim -> manual lineup_saved`` within 24 hours;
* ``D1/D3/D7/D30`` explicit manager actions;
* outbox attempts with known/unknown delivery outcomes.

Every metric carries ``n/N``, its UTC window, eligibility, coverage,
waiting and unknown components. Mature cohorts only: immature managers
stay in ``waiting``, never in ``N``. Late ``recorded_at`` rows are
excluded as-of ``report_at`` and surfaced as ``unknown_late`` so a later
``report_at`` recounts the same windows. Internal accounts and analytics
opt-out are excluded from journey reports. Automation, simulation ticks,
delivery and passive boards are never user retention. ``network_started_at``
is the committed preflight instant before the Telegram call, not proof of
receipt. A missing outcome stays pending/unknown, never success/failure.

Journey reports are pseudonymised aggregates under journey retention
(default 180 days) and opt-out cascade deletion. Operational delivery
history (``outbox_delivery_attempts``) is independent of journey
retention/opt-out and is kept for operations; see
``docs/analytics/reports.md``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from goaletto.application.analytics import require_utc

ENTRY_WINDOW = timedelta(hours=24)
RETENTION_DAYS: tuple[int, ...] = (1, 3, 7, 30)

DELIVERY_SUCCESS = frozenset({"sent", "edited", "duplicate"})
DELIVERY_FAILURE_KNOWN = frozenset(
    {"blocked", "chat_unavailable", "invalid_request", "skip_stale", "skip_unavailable"}
)
DELIVERY_RETRYABLE_KNOWN = frozenset({"rate_limited", "transient"})
DELIVERY_UNKNOWN = "unknown"
DELIVERY_WAITING = frozenset({"claimed", "sending"})


@dataclass(frozen=True)
class Coverage:
    coverage_started_at: datetime
    retention_days: int
    requested_from: datetime
    available_from: datetime
    report_at: datetime
    coverage_limited: bool


def resolve_coverage(
    *,
    coverage_started_at: datetime,
    retention_days: int,
    requested_from: datetime,
    report_at: datetime,
) -> Coverage:
    require_utc(coverage_started_at)
    require_utc(requested_from)
    require_utc(report_at)
    if type(retention_days) is not int or not 1 <= retention_days <= 730:
        raise ValueError("Invalid retention policy")
    if requested_from > report_at:
        raise ValueError("Cohort starts after the observation time")
    available_from = max(
        requested_from,
        coverage_started_at,
        report_at - timedelta(days=retention_days),
    )
    return Coverage(
        coverage_started_at,
        retention_days,
        requested_from,
        available_from,
        report_at,
        available_from > requested_from,
    )


@dataclass(frozen=True)
class EntryFunnel:
    coverage: Coverage
    starts: int
    eligible: int
    waiting: int
    claimed: int
    ready: int
    unknown_claimed_late: int
    unknown_ready_late: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage_started_at": self.coverage.coverage_started_at.isoformat(),
            "retention_days": self.coverage.retention_days,
            "requested_from": self.coverage.requested_from.isoformat(),
            "available_from": self.coverage.available_from.isoformat(),
            "report_at": self.coverage.report_at.isoformat(),
            "coverage_limited": self.coverage.coverage_limited,
            "utc_window": (
                f"[{self.coverage.available_from.isoformat()}, "
                f"{self.coverage.report_at.isoformat()}]"
            ),
            "per_manager_window": "[started_at, started_at+24h)",
            "starts": self.starts,
            "eligible_N": self.eligible,
            "waiting": self.waiting,
            "claimed_n": self.claimed,
            "ready_n": self.ready,
            "unknown_claimed_late": self.unknown_claimed_late,
            "unknown_ready_late": self.unknown_ready_late,
        }


def validate_entry_funnel(data: dict[str, Any]) -> None:
    counts = [
        data[k]
        for k in (
            "starts",
            "eligible_N",
            "waiting",
            "claimed_n",
            "ready_n",
            "unknown_claimed_late",
            "unknown_ready_late",
        )
    ]
    if any(type(v) is not int or v < 0 for v in counts):
        raise ValueError("Entry funnel counts must be nonnegative integers")
    if data["eligible_N"] + data["waiting"] != data["starts"]:
        raise ValueError("Eligible/waiting do not reconcile with starts")
    if not 0 <= data["ready_n"] <= data["claimed_n"] <= data["eligible_N"]:
        raise ValueError("Entry funnel numerator/denominator do not reconcile")
    if not 0 <= data["unknown_claimed_late"] <= data["eligible_N"]:
        raise ValueError("Late unknown exceeds the mature cohort")
    if not 0 <= data["unknown_ready_late"] <= data["eligible_N"]:
        raise ValueError("Late unknown exceeds the mature cohort")


@dataclass(frozen=True)
class RetentionRow:
    day: int
    eligible_N: int
    waiting: int
    retained_n: int
    unknown_late: int


@dataclass(frozen=True)
class RetentionReport:
    coverage: Coverage
    starts: int
    rows: tuple[RetentionRow, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage_started_at": self.coverage.coverage_started_at.isoformat(),
            "retention_days": self.coverage.retention_days,
            "requested_from": self.coverage.requested_from.isoformat(),
            "available_from": self.coverage.available_from.isoformat(),
            "report_at": self.coverage.report_at.isoformat(),
            "coverage_limited": self.coverage.coverage_limited,
            "utc_window": (
                f"[{self.coverage.available_from.isoformat()}, "
                f"{self.coverage.report_at.isoformat()}]"
            ),
            "starts": self.starts,
            "cohorts": [
                {
                    "day": row.day,
                    "utc_window_per_manager": (
                        f"[started_at+{row.day}d, started_at+{row.day + 1}d)"
                    ),
                    "eligible_N": row.eligible_N,
                    "waiting": row.waiting,
                    "retained_n": row.retained_n,
                    "unknown_late": row.unknown_late,
                }
                for row in self.rows
            ],
        }


def validate_retention_report(data: dict[str, Any]) -> None:
    if type(data["starts"]) is not int or data["starts"] < 0:
        raise ValueError("Retention starts must be a nonnegative integer")
    days = [row["day"] for row in data["cohorts"]]
    if sorted(days) != [1, 3, 7, 30]:
        raise ValueError("Retention needs exact D1/D3/D7/D30 cohorts")
    for row in data["cohorts"]:
        counts = (row["eligible_N"], row["waiting"], row["retained_n"], row["unknown_late"])
        if any(type(v) is not int or v < 0 for v in counts):
            raise ValueError("Retention counts must be nonnegative integers")
        if row["eligible_N"] + row["waiting"] != data["starts"]:
            raise ValueError("Retention eligible/waiting do not reconcile with starts")
        if not 0 <= row["retained_n"] <= row["eligible_N"]:
            raise ValueError("Retention numerator exceeds mature cohort")
        if not 0 <= row["unknown_late"] <= row["eligible_N"]:
            raise ValueError("Retention late unknown exceeds mature cohort")


@dataclass(frozen=True)
class DeliveryKindRow:
    kind: str
    total: int
    delivered: int
    failed_known: int
    failed_unknown: int
    pending: int


@dataclass(frozen=True)
class DeliveryReport:
    window_from: datetime
    report_at: datetime
    messages_total_N: int
    messages_delivered_n: int
    messages_failed_known: int
    messages_failed_unknown: int
    messages_pending_waiting: int
    messages_unknown: int
    attempts_total: int
    attempts_by_outcome: tuple[tuple[str, int], ...]
    attempts_unknown: int
    attempts_waiting: int
    kinds: tuple[DeliveryKindRow, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "utc_window": f"[{self.window_from.isoformat()}, {self.report_at.isoformat()}]",
            "window_from": self.window_from.isoformat(),
            "report_at": self.report_at.isoformat(),
            "state_semantics": "current_message_and_attempt_state_in_consistent_snapshot",
            "historical_outcomes_available": False,
            "eligibility": (
                "messages with due_at <= report_at are due; "
                "pending without a terminal outcome stays waiting"
            ),
            "coverage": (
                "operational outbox history is independent of journey "
                "retention/opt-out; no journey clipping applies; the window filters current "
                "due_at/attempt start times, not a reconstruction of outcomes at report_at"
            ),
            "messages_total_N": self.messages_total_N,
            "messages_delivered_n": self.messages_delivered_n,
            "messages_failed_known": self.messages_failed_known,
            "messages_failed_unknown": self.messages_failed_unknown,
            "messages_pending_waiting": self.messages_pending_waiting,
            "messages_unknown": self.messages_unknown,
            "attempts_total": self.attempts_total,
            "attempts_by_outcome": [
                {"outcome": outcome, "count": count} for outcome, count in self.attempts_by_outcome
            ],
            "attempts_unknown": self.attempts_unknown,
            "attempts_waiting": self.attempts_waiting,
            "kinds": [
                {
                    "kind": row.kind,
                    "total": row.total,
                    "delivered": row.delivered,
                    "failed_known": row.failed_known,
                    "failed_unknown": row.failed_unknown,
                    "pending": row.pending,
                }
                for row in self.kinds
            ],
        }


def validate_delivery_report(data: dict[str, Any]) -> None:
    counts = [
        data[k]
        for k in (
            "messages_total_N",
            "messages_delivered_n",
            "messages_failed_known",
            "messages_failed_unknown",
            "messages_pending_waiting",
            "messages_unknown",
            "attempts_total",
            "attempts_unknown",
            "attempts_waiting",
        )
    ]
    if any(type(v) is not int or v < 0 for v in counts):
        raise ValueError("Delivery counts must be nonnegative integers")
    if (
        data["messages_delivered_n"]
        + data["messages_failed_known"]
        + data["messages_failed_unknown"]
        + data["messages_pending_waiting"]
        != data["messages_total_N"]
    ):
        raise ValueError("Delivery message outcomes do not reconcile with total")
    if data["messages_unknown"] > data["messages_total_N"] - data["messages_delivered_n"]:
        raise ValueError("Delivery unknown exceeds non-delivered messages")
    if data["attempts_unknown"] + data["attempts_waiting"] > data["attempts_total"]:
        raise ValueError("Delivery attempt unknown/waiting exceeds total")
    listed = sum(row["count"] for row in data["attempts_by_outcome"])
    if listed != data["attempts_total"]:
        raise ValueError("Delivery attempt breakdown does not reconcile")
    for row in data["attempts_by_outcome"]:
        if type(row["count"]) is not int or row["count"] < 0:
            raise ValueError("Delivery attempt counts must be nonnegative integers")
    kind_total = sum(row["total"] for row in data["kinds"])
    if kind_total != data["messages_total_N"]:
        raise ValueError("Delivery per-kind totals do not reconcile")
    for row in data["kinds"]:
        kind_counts: tuple[Any, ...] = (
            row["total"],
            row["delivered"],
            row["failed_known"],
            row["failed_unknown"],
            row["pending"],
        )
        if any(type(v) is not int or v < 0 for v in kind_counts):
            raise ValueError("Delivery per-kind counts must be nonnegative integers")
        if (
            row["delivered"] + row["failed_known"] + row["failed_unknown"] + row["pending"]
            != (row["total"])
        ):
            raise ValueError("Delivery per-kind outcomes do not reconcile")


@dataclass(frozen=True)
class SyntheticStart:
    analytics_manager_id: UUID
    started_at: datetime
    is_internal: bool = False
    excluded: bool = False


@dataclass(frozen=True)
class SyntheticEvent:
    analytics_manager_id: UUID | None
    name: str
    origin: str
    occurred_at: datetime
    recorded_at: datetime
    ownership_id: UUID | None = None
    user_activity: bool = False
    version: int = 1


@dataclass(frozen=True)
class SyntheticMessage:
    id: UUID
    kind: str
    due_at: datetime
    status: str
    last_error: str | None = None


@dataclass(frozen=True)
class SyntheticAttempt:
    outbox_id: UUID
    outcome: str
    started_at: datetime
    network_started_at: datetime | None = None
    finished_at: datetime | None = None


def _cohort_starts(
    starts: tuple[SyntheticStart, ...], coverage: Coverage
) -> tuple[SyntheticStart, ...]:
    return tuple(
        s
        for s in starts
        if not s.is_internal
        and not s.excluded
        and s.started_at >= coverage.available_from
        and s.started_at <= coverage.report_at
    )


def _claim_in_window(
    events: tuple[SyntheticEvent, ...],
    manager_id: UUID,
    started_at: datetime,
    report_at: datetime,
    *,
    as_of: bool,
) -> list[SyntheticEvent]:
    window_end = started_at + ENTRY_WINDOW
    result: list[SyntheticEvent] = []
    for event in events:
        if (
            event.analytics_manager_id != manager_id
            or event.name != "club_claimed"
            or event.origin != "user"
            or event.version != 1
            or event.ownership_id is None
        ):
            continue
        if not started_at <= event.occurred_at < window_end:
            continue
        if as_of and event.recorded_at > report_at:
            continue
        result.append(event)
    return result


def _ready_in_window(
    events: tuple[SyntheticEvent, ...],
    manager_id: UUID,
    started_at: datetime,
    report_at: datetime,
    *,
    as_of: bool,
) -> bool:
    window_end = started_at + ENTRY_WINDOW
    claims = _claim_in_window(events, manager_id, started_at, report_at, as_of=as_of)
    for claim in claims:
        for saved in events:
            if (
                saved.analytics_manager_id != manager_id
                or saved.name != "lineup_saved"
                or saved.origin != "user"
                or saved.version != 1
                or saved.ownership_id is None
                or saved.ownership_id != claim.ownership_id
            ):
                continue
            if not claim.occurred_at <= saved.occurred_at < window_end:
                continue
            if as_of and saved.recorded_at > report_at:
                continue
            return True
    return False


def evaluate_entry_funnel(
    starts: tuple[SyntheticStart, ...],
    events: tuple[SyntheticEvent, ...],
    coverage: Coverage,
) -> EntryFunnel:
    cohort = _cohort_starts(starts, coverage)
    eligible = tuple(s for s in cohort if s.started_at + ENTRY_WINDOW <= coverage.report_at)
    claimed = sum(
        1
        for s in eligible
        if _claim_in_window(
            events, s.analytics_manager_id, s.started_at, coverage.report_at, as_of=True
        )
    )
    ready = sum(
        1
        for s in eligible
        if _ready_in_window(
            events, s.analytics_manager_id, s.started_at, coverage.report_at, as_of=True
        )
    )
    unknown_claimed_late = sum(
        1
        for s in eligible
        if not _claim_in_window(
            events, s.analytics_manager_id, s.started_at, coverage.report_at, as_of=True
        )
        and _claim_in_window(
            events, s.analytics_manager_id, s.started_at, coverage.report_at, as_of=False
        )
    )
    unknown_ready_late = sum(
        1
        for s in eligible
        if not _ready_in_window(
            events, s.analytics_manager_id, s.started_at, coverage.report_at, as_of=True
        )
        and _ready_in_window(
            events, s.analytics_manager_id, s.started_at, coverage.report_at, as_of=False
        )
    )
    return EntryFunnel(
        coverage=coverage,
        starts=len(cohort),
        eligible=len(eligible),
        waiting=len(cohort) - len(eligible),
        claimed=claimed,
        ready=ready,
        unknown_claimed_late=unknown_claimed_late,
        unknown_ready_late=unknown_ready_late,
    )


def _retained_in_window(
    events: tuple[SyntheticEvent, ...],
    manager_id: UUID,
    window_start: datetime,
    window_end: datetime,
    report_at: datetime,
    *,
    as_of: bool,
) -> bool:
    for event in events:
        if (
            event.analytics_manager_id != manager_id
            or event.origin != "user"
            or not event.user_activity
            or event.version != 1
        ):
            continue
        if not window_start <= event.occurred_at < window_end:
            continue
        if as_of and event.recorded_at > report_at:
            continue
        return True
    return False


def evaluate_retention(
    starts: tuple[SyntheticStart, ...],
    events: tuple[SyntheticEvent, ...],
    coverage: Coverage,
) -> RetentionReport:
    cohort = _cohort_starts(starts, coverage)
    rows: list[RetentionRow] = []
    for day in RETENTION_DAYS:
        eligible = tuple(
            s for s in cohort if s.started_at + timedelta(days=day + 1) <= coverage.report_at
        )
        retained = 0
        unknown_late = 0
        for item in eligible:
            window_start = item.started_at + timedelta(days=day)
            window_end = item.started_at + timedelta(days=day + 1)
            as_of = _retained_in_window(
                events,
                item.analytics_manager_id,
                window_start,
                window_end,
                coverage.report_at,
                as_of=True,
            )
            eventual = _retained_in_window(
                events,
                item.analytics_manager_id,
                window_start,
                window_end,
                coverage.report_at,
                as_of=False,
            )
            if as_of:
                retained += 1
            elif eventual:
                unknown_late += 1
        rows.append(
            RetentionRow(
                day=day,
                eligible_N=len(eligible),
                waiting=len(cohort) - len(eligible),
                retained_n=retained,
                unknown_late=unknown_late,
            )
        )
    return RetentionReport(coverage=coverage, starts=len(cohort), rows=tuple(rows))


def evaluate_delivery(
    messages: tuple[SyntheticMessage, ...],
    attempts: tuple[SyntheticAttempt, ...],
    *,
    window_from: datetime,
    report_at: datetime,
) -> DeliveryReport:
    require_utc(window_from)
    require_utc(report_at)
    if window_from > report_at:
        raise ValueError("Delivery window starts after the observation time")
    cohort = tuple(m for m in messages if window_from <= m.due_at <= report_at)
    cohort_ids = {m.id for m in cohort}
    delivered = sum(1 for m in cohort if m.status == "sent")
    failed_unknown = sum(
        1 for m in cohort if m.status == "failed" and m.last_error == "unknown_outcome"
    )
    failed_known = sum(
        1 for m in cohort if m.status == "failed" and m.last_error != "unknown_outcome"
    )
    pending = sum(1 for m in cohort if m.status == "pending")
    if delivered + failed_known + failed_unknown + pending != len(cohort):
        raise ValueError("Synthetic delivery messages have an unknown status")
    attempts_in_window = tuple(
        a
        for a in attempts
        if a.outbox_id in cohort_ids and window_from <= a.started_at <= report_at
    )
    by_outcome = Counter(a.outcome for a in attempts_in_window)
    unknown_attempt_ids = {a.outbox_id for a in attempts_in_window if a.outcome == "unknown"}
    messages_unknown = len(
        {
            m.id
            for m in cohort
            if m.status != "sent"
            and (m.id in unknown_attempt_ids or m.last_error == "unknown_outcome")
        }
    )
    kind_groups: dict[str, list[SyntheticMessage]] = {}
    for message in cohort:
        kind_groups.setdefault(message.kind, []).append(message)
    kind_rows: list[DeliveryKindRow] = []
    for kind in sorted(kind_groups):
        group = kind_groups[kind]
        kind_rows.append(
            DeliveryKindRow(
                kind=kind,
                total=len(group),
                delivered=sum(1 for m in group if m.status == "sent"),
                failed_known=sum(
                    1 for m in group if m.status == "failed" and m.last_error != "unknown_outcome"
                ),
                failed_unknown=sum(
                    1 for m in group if m.status == "failed" and m.last_error == "unknown_outcome"
                ),
                pending=sum(1 for m in group if m.status == "pending"),
            )
        )
    return DeliveryReport(
        window_from=window_from,
        report_at=report_at,
        messages_total_N=len(cohort),
        messages_delivered_n=delivered,
        messages_failed_known=failed_known,
        messages_failed_unknown=failed_unknown,
        messages_pending_waiting=pending,
        messages_unknown=messages_unknown,
        attempts_total=len(attempts_in_window),
        attempts_by_outcome=tuple(sorted(by_outcome.items())),
        attempts_unknown=by_outcome.get("unknown", 0),
        attempts_waiting=sum(by_outcome.get(name, 0) for name in DELIVERY_WAITING),
        kinds=tuple(kind_rows),
    )
