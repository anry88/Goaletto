"""Backfill qualification tiers from skills, without changing people or salaries."""

import sqlalchemy as sa
from alembic import op

revision = "0022_staff_quality"
down_revision = "0021_candidate_career_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "staff_qualifications",
        sa.Column(
            "quality_tier", sa.SmallInteger(), sa.Computed("((skill - 1) / 4) + 1", persisted=True)
        ),
    )
    op.add_column(
        "staff_qualifications",
        sa.Column(
            "policy_version", sa.String(40), nullable=False, server_default="staff-quality-v1"
        ),
    )


def downgrade() -> None:
    op.drop_column("staff_qualifications", "policy_version")
    op.drop_column("staff_qualifications", "quality_tier")
