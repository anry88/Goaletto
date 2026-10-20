"""Process-local technical telemetry. Never game state or per-person analytics."""

from collections.abc import Iterable
from typing import Literal

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
)
from prometheus_client.core import Metric

SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


class Snapshot:
    """Atomically replaced cache; exposition never performs database IO."""

    def __init__(self) -> None:
        self.families: tuple[Metric, ...] = ()

    def collect(self) -> Iterable[Metric]:
        return iter(self.families)


class Metrics:
    def __init__(self, *, resources: bool = False) -> None:
        self.registry = CollectorRegistry()
        if resources:
            ProcessCollector(registry=self.registry)
            GCCollector(registry=self.registry)
            PlatformCollector(registry=self.registry)
        self.snapshot = Snapshot()
        self.registry.register(self.snapshot)

        def counter(name: str, help_text: str, labels: tuple[str, ...] = ()) -> Counter:
            return Counter("goaletto_" + name, help_text, labels, registry=self.registry)

        def gauge(name: str, help_text: str, labels: tuple[str, ...] = ()) -> Gauge:
            return Gauge("goaletto_" + name, help_text, labels, registry=self.registry)

        def histogram(name: str, help_text: str, labels: tuple[str, ...] = ()) -> Histogram:
            return Histogram(
                "goaletto_" + name, help_text, labels, buckets=SECONDS, registry=self.registry
            )

        self.starts = counter(
            "updates_started_total",
            "Update handling started, including retries.",
            ("kind", "route"),
        )
        self.updates = counter(
            "updates_total",
            "Completed update attempts; not unique people or business successes.",
            ("kind", "route", "outcome"),
        )
        self.update_seconds = histogram(
            "update_duration_seconds",
            "Full handler time including DB and Telegram IO.",
            ("kind", "route"),
        )
        self.inflight = gauge("updates_inflight", "Updates currently being handled.", ("kind",))
        self.api = counter(
            "telegram_requests_total", "Observed Telegram API call outcomes.", ("method", "outcome")
        )
        self.api_seconds = histogram(
            "telegram_request_duration_seconds",
            "Telegram API time; getUpdates includes long polling.",
            ("method",),
        )
        self.retry_seconds = histogram(
            "telegram_retry_after_seconds", "Server-requested Telegram backoff.", ("method",)
        )
        self.transactions = counter(
            "database_transactions_total",
            "Transactions observed after exit/commit.",
            ("mode", "outcome"),
        )
        self.transaction_seconds = histogram(
            "database_transaction_duration_seconds",
            "Transaction wall time including acquisition/commit.",
            ("mode",),
        )
        self.sql = counter(
            "database_statements_total",
            "SQL execution attempts without statement text.",
            ("operation", "outcome"),
        )
        self.sql_seconds = histogram(
            "database_statement_duration_seconds", "SQL execution time.", ("operation",)
        )
        self.pool = gauge(
            "database_pool_connections", "Current application pool connections.", ("state",)
        )
        self.loops = counter(
            "worker_iterations_total",
            "Committed worker iterations and observed failures.",
            ("component", "outcome"),
        )
        self.loop_success = gauge(
            "worker_last_success_timestamp_seconds",
            "Last completed iteration or intentional sender backoff pulse.",
            ("component",),
        )
        self.loop_ok = gauge(
            "worker_last_iteration_ok",
            "Outcome of the last observed loop iteration.",
            ("component",),
        )
        self.loop_seconds = histogram(
            "worker_iteration_duration_seconds",
            "Worker processing time, excluding idle/backoff sleep.",
            ("component",),
        )
        self.jobs = counter(
            "jobs_total",
            "Job attempts after transaction commit; replay work is labelled separately.",
            ("kind", "outcome"),
        )
        self.job_lateness = histogram(
            "job_lateness_seconds",
            "Delay before a due job is processed, excluding historical world replay.",
            ("kind",),
        )
        self.deliveries = counter(
            "outbox_deliveries_total",
            "Outbox outcomes after the delivery receipt commits.",
            ("outcome",),
        )
        self.heartbeat_errors = counter(
            "heartbeat_write_errors_total",
            "Bounded durable heartbeat write failures.",
            ("component",),
        )
        self.lag = gauge("event_loop_lag_seconds", "Most recent event-loop scheduling lag.")
        self.lag_seconds = histogram(
            "event_loop_lag_observation_seconds", "Event-loop lag distribution."
        )
        self.tasks = gauge("asyncio_tasks", "Current number of asyncio tasks.")
        self.snapshot_ok = gauge(
            "db_snapshot_success",
            "Whether the most recent DB snapshot succeeded; old gauges disappear on failure.",
        )
        self.snapshot_at = gauge(
            "db_snapshot_timestamp_seconds", "Last successful DB snapshot, never backfilled."
        )
        self.snapshot_errors = counter(
            "db_snapshot_errors_total", "Bounded DB aggregate collection errors."
        )
        self.snapshot_seconds = histogram(
            "db_snapshot_duration_seconds", "Background aggregate collection duration."
        )
        self.role = gauge("service_info", "Enabled process role.", ("role",))

    def pulse(self, component: Literal["jobs", "sender"] | str, success: bool, now: float) -> None:
        self.loop_ok.labels(component).set(int(success))
        if success:
            self.loop_success.labels(component).set(now)


METRICS = Metrics(resources=True)
