"""Manager-local timezone and kickoff preference; fixtures remain immutable."""

import sqlalchemy as sa
from alembic import op

revision = "0023_kickoff_preferences"
down_revision = "0022_staff_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managers", sa.Column("timezone_name", sa.String(64), nullable=False, server_default="UTC")
    )
    op.add_column("managers", sa.Column("preferred_kickoff_minute", sa.SmallInteger()))
    op.create_check_constraint(
        "ck_manager_kickoff_minute", "managers", "preferred_kickoff_minute BETWEEN 0 AND 1439"
    )

    op.create_table(
        "kickoff_preference_receipts",
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("manager_id", sa.Uuid(), sa.ForeignKey("managers.id"), nullable=False),
        sa.Column("timezone_name", sa.String(64)),
        sa.Column("preferred_minute", sa.SmallInteger()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "timezone_name IS NOT NULL OR preferred_minute IS NOT NULL",
            name="ck_kickoff_receipt_value",
        ),
        sa.CheckConstraint("preferred_minute BETWEEN 0 AND 1439", name="ck_kickoff_receipt_minute"),
    )

    op.create_table(
        "kickoff_decision_receipts",
        sa.Column("fixture_key", sa.Uuid(), primary_key=True),
        sa.Column("home_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("away_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("assigned_day", sa.Date(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("kickoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("tie_break", sa.SmallInteger()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("seed >= 0", name="ck_kickoff_decision_seed"),
        sa.CheckConstraint("tie_break IN (0, 1)", name="ck_kickoff_decision_tie"),
        sa.CheckConstraint(
            "(kickoff AT TIME ZONE 'UTC')::date = assigned_day", name="ck_kickoff_decision_day"
        ),
    )


def downgrade() -> None:
    op.drop_table("kickoff_decision_receipts")
    op.drop_table("kickoff_preference_receipts")
    op.drop_constraint("ck_manager_kickoff_minute", "managers", type_="check")
    op.drop_column("managers", "preferred_kickoff_minute")
    op.drop_column("managers", "timezone_name")
