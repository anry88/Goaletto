"""History indexes and operational reminder settings receipts."""

from alembic import op

revision = "0011_agent_wave2"
down_revision = "0010_telegram_portrait_files"
branch_labels = None
depends_on = None

OLD_KINDS = (
    "kind IN ('club_claimed','club_opened','club_preview_opened','lineup_opened',"
    "'lineup_saved','match_command_accepted','match_opened','match_report_opened',"
    "'next_fixture_opened')"
)
NEW_KINDS = OLD_KINDS[:-1] + ",'notification_preferences')"


def upgrade() -> None:
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", NEW_KINDS)
    for side in ("home", "away"):
        op.create_index(
            f"ix_fixture_{side}_history",
            "fixtures",
            [f"{side}_club_id", "status", "scheduled_start_at", "id"],
        )
    op.create_index("ix_membership_club", "season_clubs", ["club_id"])
    op.create_index(
        "ix_match_snapshot_gin",
        "matches",
        ["starting_snapshot"],
        postgresql_using="gin",
        postgresql_ops={"starting_snapshot": "jsonb_path_ops"},
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM game_action_receipts "
        "WHERE kind='notification_preferences') THEN RAISE EXCEPTION "
        "'Reconcile notification settings receipts before downgrade'; END IF; END $$"
    )
    op.drop_index("ix_match_snapshot_gin", table_name="matches")
    op.drop_index("ix_membership_club", table_name="season_clubs")
    for side in ("home", "away"):
        op.drop_index(f"ix_fixture_{side}_history", table_name="fixtures")
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", OLD_KINDS)
