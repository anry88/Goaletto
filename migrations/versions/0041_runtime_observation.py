"""Real request stages and outbox queue/render clocks, without historical backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0041_runtime_observation"
down_revision = "0040_world_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_messages", sa.Column("enqueued_at", sa.DateTime(timezone=True)))
    # Only future inserts get a physical enqueue timestamp. due_at is a delivery
    # schedule and historical rows must not acquire invented queue measurements.
    op.execute("ALTER TABLE outbox_messages ALTER COLUMN enqueued_at SET DEFAULT clock_timestamp()")
    for field in ("available_at", "render_started_at", "rendered_at"):
        op.add_column("outbox_delivery_attempts", sa.Column(field, sa.DateTime(timezone=True)))
    for name, table, column in (
        ("ix_delivery_attempt_started", "outbox_delivery_attempts", "started_at"),
        ("ix_operation_attempt_started", "operation_attempts", "started_at"),
        ("ix_accepted_command_time", "accepted_commands", "accepted_at"),
    ):
        op.create_index(name, table, [column])
    op.create_table(
        "runtime_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("route", sa.String(24), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), sa.ForeignKey("fixtures.id")),
        sa.Column("command_id", sa.Uuid(), sa.ForeignKey("accepted_commands.id")),
        *(
            sa.Column(field, sa.DateTime(timezone=True), nullable=field != "received_at")
            for field in (
                "received_at",
                "committed_at",
                "render_started_at",
                "rendered_at",
                "network_started_at",
                "finished_at",
            )
        ),
        sa.Column("business_outcome", sa.String(16), nullable=False),
        sa.Column("delivery_outcome", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "route IN ('match_open','match_callback','history')", name="ck_request_route"
        ),
        sa.CheckConstraint(
            "business_outcome IN ('unknown','committed','rejected')", name="ck_request_business"
        ),
        sa.CheckConstraint(
            "delivery_outcome IN ('not_started','sending','accepted','rejected','unknown')",
            name="ck_request_delivery",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= received_at", name="ck_request_time"
        ),
    )
    op.create_index("ix_runtime_request_time", "runtime_requests", ["received_at"])
    op.create_index("ix_runtime_request_operation", "runtime_requests", ["operation_id"])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM runtime_requests) OR EXISTS "
        "(SELECT 1 FROM outbox_delivery_attempts WHERE rendered_at IS NOT NULL "
        "OR available_at IS NOT NULL) OR EXISTS "
        "(SELECT 1 FROM outbox_messages WHERE enqueued_at IS NOT NULL) THEN "
        "RAISE EXCEPTION 'Runtime observation history prevents downgrade'; END IF; END $$"
    )
    op.drop_index("ix_delivery_attempt_started", table_name="outbox_delivery_attempts")
    op.drop_index("ix_operation_attempt_started", table_name="operation_attempts")
    op.drop_index("ix_accepted_command_time", table_name="accepted_commands")
    op.drop_table("runtime_requests")
    op.drop_column("outbox_delivery_attempts", "rendered_at")
    op.drop_column("outbox_delivery_attempts", "render_started_at")
    op.drop_column("outbox_delivery_attempts", "available_at")
    op.drop_column("outbox_messages", "enqueued_at")
