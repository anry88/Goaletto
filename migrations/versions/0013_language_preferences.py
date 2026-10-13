"""Durable RU/EN language selection receipts."""

from alembic import op

revision = "0013_language_preferences"
down_revision = "0012_weekly_training"
branch_labels = None
depends_on = None
OLD_KINDS = (
    "kind IN ('club_claimed','club_opened','club_preview_opened','lineup_opened',"
    "'lineup_saved','match_command_accepted','match_opened','match_report_opened',"
    "'next_fixture_opened','notification_preferences','training_opened','training_plan_saved')"
)
NEW_KINDS = OLD_KINDS[:-1] + ",'language_preferences')"


def upgrade() -> None:
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", NEW_KINDS)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM game_action_receipts WHERE kind = "
        "'language_preferences') THEN RAISE EXCEPTION 'Reconcile "
        "language receipts before downgrade'; END IF; END $$"
    )
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", OLD_KINDS)
