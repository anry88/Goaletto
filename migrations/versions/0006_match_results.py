"""Frozen result receipts and lifecycle name; no score/standings backfill."""

import sqlalchemy as sa
from alembic import op

revision = "0006_match_results"
down_revision = "0005_match_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_match_fixture_identity", "matches", ["id", "fixture_id"])
    op.execute(
        sa.text("""
CREATE TABLE match_results (
        match_id UUID NOT NULL,
        fixture_id UUID NOT NULL,
        season_id UUID NOT NULL,
        league_id UUID NOT NULL,
        home_club_id UUID NOT NULL,
        away_club_id UUID NOT NULL,
        version SMALLINT NOT NULL,
        home_goals SMALLINT NOT NULL,
        away_goals SMALLINT NOT NULL,
        actual_start_at TIMESTAMP WITH TIME ZONE NOT NULL,
        actual_finish_at TIMESTAMP WITH TIME ZONE NOT NULL,
        recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
        state_hash VARCHAR(64) NOT NULL,
        PRIMARY KEY (match_id),
        CONSTRAINT uq_result_fixture UNIQUE (fixture_id),
        CONSTRAINT fk_result_match_fixture FOREIGN KEY(match_id, fixture_id) REFERENCES matches
(id, fixture_id),
        CONSTRAINT fk_result_home_membership FOREIGN KEY(season_id, home_club_id, league_id)
REFERENCES season_clubs (season_id, club_id, league_id),
        CONSTRAINT fk_result_away_membership FOREIGN KEY(season_id, away_club_id, league_id)
REFERENCES season_clubs (season_id, club_id, league_id),
        CONSTRAINT ck_result_score CHECK (version = 1 AND home_goals >= 0 AND away_goals >= 0
AND home_club_id <> away_club_id),
        CONSTRAINT ck_result_dates CHECK (actual_finish_at >= actual_start_at AND recorded_at >=
actual_finish_at),
        CONSTRAINT ck_result_hash CHECK (length(state_hash) = 64),
        FOREIGN KEY(match_id) REFERENCES matches (id),
        FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
)
""")
    )
    op.execute(
        sa.text("""
CREATE TRIGGER tr_match_result_immutable BEFORE UPDATE OR DELETE ON match_results FOR EACH ROW
EXECUTE FUNCTION goaletto_guard_append_only()
""")
    )
    op.execute(
        sa.text("""
ALTER TABLE journey_events DROP CONSTRAINT ck_journey_name
""")
    )
    op.execute(
        sa.text("""
ALTER TABLE journey_events ADD CONSTRAINT ck_journey_name CHECK (name IN ('manager_started',
'campaign_touched', 'club_offer_delivered', 'club_preview_opened', 'club_claimed',
'club_opened', 'lineup_opened', 'lineup_saved', 'next_fixture_opened', 'match_opened',
'match_command_accepted', 'match_command_applied', 'fixture_finished', 'fixture_started',
'match_lineup_locked', 'match_tick_committed', 'match_second_half_started', 'fixture_abandoned',
'result_finalized', 'result_delivered', 'match_report_opened', 'next_action_opened',
'action_rejected', 'help_delivered', 'feedback_prompt_delivered', 'feedback_answered',
'feedback_skipped', 'experiment_exposed', 'bot_unavailable', 'club_ownership_ended',
'player_last_season_announced', 'player_retired', 'bot_youth_replacement_created'))
""")
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM match_results) THEN "
        "RAISE EXCEPTION 'Reconcile result receipts before downgrade'; END IF; END $$"
    )
    op.drop_table("match_results")
    op.drop_constraint("uq_match_fixture_identity", "matches", type_="unique")
    op.execute("ALTER TABLE journey_events DROP CONSTRAINT ck_journey_name")
    op.execute(
        sa.text("""
ALTER TABLE journey_events ADD CONSTRAINT ck_journey_name CHECK (name IN ('manager_started',
'campaign_touched', 'club_offer_delivered', 'club_preview_opened', 'club_claimed',
'club_opened', 'lineup_opened', 'lineup_saved', 'next_fixture_opened', 'match_opened',
'match_command_accepted', 'match_command_applied', 'fixture_finished', 'fixture_started',
'match_lineup_locked', 'match_tick_committed', 'match_second_half_started', 'fixture_abandoned',
'result_delivered', 'match_report_opened', 'next_action_opened', 'action_rejected',
'help_delivered', 'feedback_prompt_delivered', 'feedback_answered', 'feedback_skipped',
'experiment_exposed', 'bot_unavailable', 'club_ownership_ended', 'player_last_season_announced',
'player_retired', 'bot_youth_replacement_created'))
""")
    )
