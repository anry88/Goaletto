"""Audit targeted starter-squad-v2 data migration; existing history is preserved."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0053_starter_rebalance"
down_revision = "0052_tournament_discipline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "starter_rebalance_receipts",
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("persons.id"), primary_key=True),
        sa.Column(
            "world_key", sa.String(64), sa.ForeignKey("world_seed_receipts.key"), nullable=False
        ),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("before_skills", postgresql.JSONB(), nullable=False),
        sa.Column("after_skills", postgresql.JSONB(), nullable=False),
        sa.Column("old_baseline", postgresql.JSONB(), nullable=False),
        sa.Column("new_baseline", postgresql.JSONB(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "policy_version = 'starter-squad-v2'", name="ck_starter_rebalance_policy"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(before_skills) = 'object' AND jsonb_typeof(after_skills) = 'object' "
            "AND jsonb_typeof(old_baseline) = 'object' AND jsonb_typeof(new_baseline) = 'object'",
            name="ck_starter_rebalance_skills",
        ),
    )
    op.execute("""CREATE FUNCTION protect_starter_rebalance() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Starter rebalancing history is immutable'; END $$""")
    op.execute(
        "CREATE TRIGGER starter_rebalance_immutable BEFORE UPDATE OR DELETE "
        "ON starter_rebalance_receipts FOR EACH ROW "
        "EXECUTE FUNCTION protect_starter_rebalance()"
    )


def downgrade() -> None:
    raise RuntimeError("Starter rebalancing history must be preserved; use an additive migration")
