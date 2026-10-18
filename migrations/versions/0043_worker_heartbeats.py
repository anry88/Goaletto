"""Operational worker-loop health, independent of game/analytics history."""

import sqlalchemy as sa
from alembic import op

revision = "0043_worker_heartbeats"
down_revision = "0042_feedback_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("instance_id", sa.Uuid(), primary_key=True),
        sa.Column("component", sa.String(8), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("succeeded_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(8), nullable=False),
        sa.CheckConstraint("component IN ('jobs','sender')", name="ck_heartbeat_component"),
        sa.CheckConstraint("outcome IN ('starting','ok','error')", name="ck_heartbeat_outcome"),
        sa.CheckConstraint("observed_at >= started_at", name="ck_heartbeat_time"),
        sa.CheckConstraint(
            "succeeded_at IS NULL OR succeeded_at <= observed_at", name="ck_heartbeat_success"
        ),
    )
    op.create_index("ix_heartbeat_observed", "worker_heartbeats", ["observed_at"])
    op.execute("REVOKE ALL ON worker_heartbeats FROM PUBLIC")


def downgrade() -> None:
    # Heartbeats are disposable monitoring samples, never gameplay receipts.
    op.drop_table("worker_heartbeats")
