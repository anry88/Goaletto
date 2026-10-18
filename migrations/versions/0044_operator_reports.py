"""Restricted aggregate-only SQL interface for private Grafana."""

from pathlib import Path

from alembic import op

revision = "0044_operator_reports"
down_revision = "0043_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Frozen SQL travels with the migration, never imports mutable application queries.
    source = Path(__file__).parents[1] / "sql" / "0044_operator.sql"
    # asyncpg accepts one statement per execute; function body contains semicolons.
    schema, function = source.read_text().split("CREATE FUNCTION", 1)
    for statement in schema.split(";"):
        if statement.strip():
            op.execute(statement)
    definition, revoke = function.split("$function$;", 1)
    op.execute("CREATE FUNCTION" + definition + "$function$;")
    op.execute(revoke)


def downgrade() -> None:
    op.execute("DROP FUNCTION goaletto_operator.report(text,timestamptz,timestamptz,jsonb)")
    op.execute("DROP SCHEMA goaletto_operator")
