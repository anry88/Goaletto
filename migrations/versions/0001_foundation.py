"""Foundation topology and durable jobs/outbox. Explicit immutable migration."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "league_sets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "leagues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("league_set_id", sa.Uuid(), sa.ForeignKey("league_sets.id"), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.UniqueConstraint("league_set_id", "tier", name="uq_league_set_tier"),
        sa.CheckConstraint("tier BETWEEN 1 AND 5", name="ck_league_tier"),
    )
    op.create_index("ix_leagues_league_set_id", "leagues", ["league_set_id"])
    op.create_table(
        "clubs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("league_id", sa.Uuid(), sa.ForeignKey("leagues.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=True, unique=True),
        sa.Column("budget", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("budget >= 0", name="ck_club_budget"),
    )
    op.create_index("ix_clubs_league_id", "clubs", ["league_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dedupe_key", sa.String(200), nullable=False, unique=True),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name="ck_job_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_job_attempts"),
    )
    op.create_index("ix_jobs_due", "jobs", ["status", "due_at"])
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dedupe_key", sa.String(200), nullable=False, unique=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_outbox_status"),
    )
    op.create_index("ix_outbox_due", "outbox_messages", ["status", "due_at"])


def downgrade() -> None:
    op.drop_table("outbox_messages")
    op.drop_table("jobs")
    op.drop_table("clubs")
    op.drop_table("leagues")
    op.drop_table("league_sets")
