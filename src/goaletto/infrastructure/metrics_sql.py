"""SQL timings without SQL text, parameters, DSNs or entity labels."""

from time import perf_counter
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from goaletto.infrastructure.metrics import METRICS


def install(engine: Engine) -> None:
    def before(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        word = statement.lstrip().split(maxsplit=1)[0].upper() if statement.strip() else ""
        context._goaletto_metrics = (
            perf_counter(),
            word
            if word in {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "SET", "SHOW"}
            else "OTHER",
        )

    def observe(context: Any, outcome: str) -> None:
        sample = getattr(context, "_goaletto_metrics", None)
        if sample is not None:
            started, operation = sample
            METRICS.sql.labels(operation, outcome).inc()
            METRICS.sql_seconds.labels(operation).observe(perf_counter() - started)
            context._goaletto_metrics = None

    def after(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        observe(context, "completed")

    def failed(error: Any) -> None:
        observe(error.execution_context, "error")

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "handle_error", failed)
