"""Seed receipt and held calendar jobs. No game population or job activation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_world_seed"
down_revision = "0002_game_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "world_seed_receipts",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column(
            "league_set_id", sa.Uuid(), sa.ForeignKey("league_sets.id"), nullable=False, unique=True
        ),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False, unique=True),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(config_hash) = 64", name="ck_world_seed_hash"),
    )
    op.execute(
        "CREATE TRIGGER tr_world_seed_receipt_immutable BEFORE UPDATE OR DELETE "
        "ON world_seed_receipts FOR EACH ROW EXECUTE FUNCTION goaletto_guard_append_only()"
    )
    op.drop_constraint("ck_job_status", "jobs", type_="check")
    op.create_check_constraint(
        "ck_job_status", "jobs", "status IN ('held', 'pending', 'done', 'failed')"
    )


def downgrade() -> None:
    # Never silently turn held, unsupported work into executable pending jobs.
    op.execute("DELETE FROM jobs WHERE status = 'held' AND dedupe_key LIKE 'calendar:v1:%'")
    op.drop_constraint("ck_job_status", "jobs", type_="check")
    op.create_check_constraint("ck_job_status", "jobs", "status IN ('pending', 'done', 'failed')")
    op.drop_table("world_seed_receipts")
