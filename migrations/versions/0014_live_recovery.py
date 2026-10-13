"""Audited restoration of a live decision window after worker lag."""

import sqlalchemy as sa
from alembic import op

revision = "0014_live_recovery"
down_revision = "0013_language_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_recoveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("match_id", sa.Uuid(), sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("checkpoint_tick", sa.SmallInteger(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("previous_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(24), nullable=False),
        sa.UniqueConstraint("match_id", "previous_due_at", name="uq_recovery_window"),
        sa.CheckConstraint("checkpoint_tick BETWEEN 0 AND 29", name="ck_recovery_tick"),
        sa.CheckConstraint(
            "next_due_at > previous_due_at AND next_due_at > recovered_at", name="ck_recovery_due"
        ),
        sa.CheckConstraint("reason IN ('worker_lag','late_resume')", name="ck_recovery_reason"),
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM match_recoveries) THEN RAISE "
        "EXCEPTION 'Reconcile recovery records before downgrade'; END IF; END $$"
    )
    op.drop_table("match_recoveries")
