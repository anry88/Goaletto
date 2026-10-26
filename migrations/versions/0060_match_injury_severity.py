"""Allow varied new match injuries without changing existing injury history."""

import sqlalchemy as sa
from alembic import op

revision = "0060_match_injury_severity"
down_revision = "0059_match_injuries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("match_injuries", sa.Column("severity", sa.String(16)))
    op.drop_constraint("ck_match_injury_policy", "match_injuries", type_="check")
    op.create_check_constraint(
        "ck_match_injury_policy",
        "match_injuries",
        "(policy_version = 'match-injury-v1' AND days = 7 AND severity IS NULL) OR "
        "(policy_version = 'match-injury-v2' AND severity IS NOT NULL AND "
        "((severity = 'light' AND days BETWEEN 1 AND 3) OR "
        "(severity = 'moderate' AND days BETWEEN 4 AND 7) OR "
        "(severity = 'severe' AND days BETWEEN 8 AND 14)))",
    )


def downgrade() -> None:
    raise RuntimeError("Match injury history requires additive migrations")
