"""Extend current form to ten without rewriting stored values or pinned V2 practice."""

from alembic import op

revision = "0054_form_ten"
down_revision = "0053_starter_rebalance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_player_form", "players", type_="check")
    op.create_check_constraint("ck_player_form", "players", "form BETWEEN 1 AND 10")
    op.drop_constraint("ck_match_development_policy", "match_development", type_="check")
    op.create_check_constraint(
        "ck_match_development_policy",
        "match_development",
        "policy_version IN ('match-development-v1','match-practice-v2','match-practice-v3')",
    )


def downgrade() -> None:
    raise RuntimeError("Form and pinned V3 history must be preserved; use an additive migration")
