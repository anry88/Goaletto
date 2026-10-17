"""Durable cross-set promotion/relegation draws (AND-119)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_allocation_receipts"
down_revision = "0034_real_knockout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "season_allocation_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cohort_key", sa.String(128), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("season_ids", postgresql.JSONB(), nullable=False),
        sa.Column("assignments", postgresql.JSONB(), nullable=False),
        sa.Column("draws", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cohort_key", name="uq_allocation_cohort"),
        sa.CheckConstraint("policy_version = 'league-allocation-v1'", name="ck_allocation_policy"),
        sa.CheckConstraint("seed >= 0", name="ck_allocation_seed"),
        sa.CheckConstraint("jsonb_typeof(season_ids) = 'array'", name="ck_allocation_seasons"),
        sa.CheckConstraint("jsonb_typeof(assignments) = 'array'", name="ck_allocation_assignments"),
        sa.CheckConstraint("jsonb_typeof(draws) = 'array'", name="ck_allocation_draws"),
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM season_allocation_receipts) THEN
        RAISE EXCEPTION 'Persisted allocation draws prevent downgrade'; END IF;
    END $$""")
    op.drop_table("season_allocation_receipts")
