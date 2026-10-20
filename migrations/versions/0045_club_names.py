"""Persist one-time club names without rewriting historical match documents."""

import sqlalchemy as sa
from alembic import op

revision = "0045_club_names"
down_revision = "0044_operator_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "club_name_choices",
        sa.Column(
            "ownership_id", sa.Uuid(), sa.ForeignKey("ownership_periods.id"), primary_key=True
        ),
        sa.Column("state", sa.String(8), nullable=False),
        sa.Column("draft_id", sa.Uuid()),
        sa.Column("candidate", sa.String(30)),
        sa.Column("original_name", sa.String(80)),
        sa.Column("declined_at", sa.DateTime(timezone=True)),
        sa.Column("changed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('offer','input','preview','declined','done')", name="ck_club_name_state"
        ),
        sa.CheckConstraint(
            "(state IN ('preview','done')) = (candidate IS NOT NULL)", name="ck_club_name_candidate"
        ),
        sa.CheckConstraint(
            "(state = 'done') = (changed_at IS NOT NULL AND original_name IS NOT NULL)",
            name="ck_club_name_completed",
        ),
    )
    op.create_table(
        "club_name_inputs",
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("ownership_id", sa.Uuid(), sa.ForeignKey("ownership_periods.id"), nullable=False),
        sa.Column("value", sa.String(200), nullable=False),
    )
    op.create_index("ix_club_name_inputs_ownership_id", "club_name_inputs", ["ownership_id"])
    op.execute("REVOKE ALL ON club_name_choices, club_name_inputs FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM club_name_choices) THEN "
        "RAISE EXCEPTION 'Used club names must be preserved'; END IF; END $$"
    )
    op.drop_table("club_name_inputs")
    op.drop_table("club_name_choices")
