"""Read-only runtime diagnostics for jobs, matches and the notification outbox.

The package never writes to the database, never claims work, never extends
ownership and never creates journey/user effects. It only runs bounded SELECT
statements (see :mod:`goaletto.diagnostics.queries`) and evaluates them with
pure functions (see :mod:`goaletto.diagnostics.checks`).

No Telegram IDs, tokens, DSNs, message contents or raw exception text ever
appear in a diagnostic report: snapshots carry routing UUIDs and bounded
outcome classes only.
"""

from goaletto.diagnostics.checks import Finding, Report, collect_report
from goaletto.diagnostics.snapshot import Snapshot

__all__ = ["Finding", "Report", "Snapshot", "collect_report"]
