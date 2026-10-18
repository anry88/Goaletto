"""Internal feedback trigger scope and operational match action references."""

import sqlalchemy as sa
from alembic import op

revision = "0042_feedback_triggers"
down_revision = "0041_runtime_observation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("game_action_receipts", sa.Column("fixture_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_action_fixture", "game_action_receipts", "fixtures", ["fixture_id"], ["id"]
    )
    for column in (
        sa.Column("trigger_kind", sa.String(24)),
        sa.Column("ownership_id", sa.Uuid()),
        sa.Column("fixture_id", sa.Uuid()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("feedback_responses", column)
    op.create_foreign_key(
        "fk_feedback_ownership", "feedback_responses", "ownership_periods", ["ownership_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_feedback_fixture", "feedback_responses", "fixtures", ["fixture_id"], ["id"]
    )
    op.create_check_constraint(
        "ck_feedback_trigger",
        "feedback_responses",
        "trigger_kind IS NULL OR (trigger_kind IN ('entry_help','missed_match') "
        "AND expires_at IS NOT NULL AND expires_at > prompted_at AND "
        "(trigger_kind <> 'missed_match' OR (ownership_id IS NOT NULL "
        "AND fixture_id IS NOT NULL)))",
    )
    op.create_index("ix_action_manager_time", "game_action_receipts", ["manager_id", "occurred_at"])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM feedback_responses WHERE trigger_kind IS NOT NULL) "
        "OR EXISTS (SELECT 1 FROM game_action_receipts WHERE fixture_id IS NOT NULL) THEN "
        "RAISE EXCEPTION 'Feedback trigger history prevents downgrade'; END IF; END $$"
    )
    op.drop_index("ix_action_manager_time", table_name="game_action_receipts")
    op.drop_constraint("ck_feedback_trigger", "feedback_responses")
    op.drop_constraint("fk_feedback_fixture", "feedback_responses", type_="foreignkey")
    op.drop_constraint("fk_feedback_ownership", "feedback_responses", type_="foreignkey")
    for column in ("expires_at", "fixture_id", "ownership_id", "trigger_kind"):
        op.drop_column("feedback_responses", column)
    op.drop_constraint("fk_action_fixture", "game_action_receipts", type_="foreignkey")
    op.drop_column("game_action_receipts", "fixture_id")
