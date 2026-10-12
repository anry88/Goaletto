"""Frozen operational action dedupe and reminder preferences. No ownership changes."""

import sqlalchemy as sa
from alembic import op

revision = "0008_club_entry"
down_revision = "0007_acquisition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managers",
        sa.Column("reminders_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "managers", sa.Column("bot_available", sa.Boolean(), nullable=False, server_default="true")
    )
    op.execute(
        sa.text("""
CREATE TABLE game_action_receipts (
        operation_id UUID NOT NULL,
        effect_number SMALLINT NOT NULL,
        manager_id UUID NOT NULL,
        ownership_id UUID,
        club_id UUID,
        kind VARCHAR(80) NOT NULL,
        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (operation_id, effect_number),
        CONSTRAINT fk_action_ownership FOREIGN KEY(ownership_id, manager_id, club_id) REFERENCES
ownership_periods (id, manager_id, club_id),
        CONSTRAINT ck_action_effect CHECK (effect_number BETWEEN 0 AND 32767),
        CONSTRAINT ck_action_club CHECK (ownership_id IS NULL OR club_id IS NOT NULL),
        FOREIGN KEY(manager_id) REFERENCES managers (id),
        FOREIGN KEY(club_id) REFERENCES clubs (id)
)
""")
    )
    op.execute(
        sa.text("""
ALTER TABLE game_action_receipts ADD CONSTRAINT ck_action_kind CHECK (kind IN ('club_claimed',
'club_opened', 'club_preview_opened', 'lineup_opened', 'lineup_saved', 'match_command_accepted',
'match_opened', 'match_report_opened', 'next_fixture_opened'))
""")
    )
    op.execute(
        sa.text("""
CREATE TRIGGER tr_game_action_receipt_immutable BEFORE UPDATE OR DELETE ON game_action_receipts
FOR EACH ROW EXECUTE FUNCTION goaletto_guard_append_only()
""")
    )
    op.execute(
        sa.text("""
REVOKE ALL ON game_action_receipts FROM PUBLIC
""")
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM game_action_receipts) THEN "
        "RAISE EXCEPTION 'Reconcile operational receipts before downgrade'; END IF; END $$"
    )
    op.drop_table("game_action_receipts")
    op.drop_column("managers", "bot_available")
    op.drop_column("managers", "reminders_enabled")
