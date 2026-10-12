"""Frozen durable regulation runtime DDL. No job release or coverage activation."""

import sqlalchemy as sa
from alembic import op

revision = "0005_match_runtime"
down_revision = "0004_journey_collection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
CREATE FUNCTION goaletto_guard_match_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF NEW.id IS DISTINCT FROM OLD.id OR NEW.fixture_id IS DISTINCT FROM OLD.fixture_id
OR NEW.seed IS DISTINCT FROM OLD.seed OR NEW.engine_version IS DISTINCT FROM OLD.engine_version
OR NEW.starting_snapshot IS DISTINCT FROM OLD.starting_snapshot THEN
RAISE EXCEPTION 'Match starting inputs are immutable';
END IF;
RETURN NEW;
END $$
""")
    )
    op.execute(
        "CREATE TRIGGER tr_match_snapshot BEFORE UPDATE ON matches FOR EACH ROW "
        "EXECUTE FUNCTION goaletto_guard_match_snapshot()"
    )
    op.add_column(
        "clubs", sa.Column("stadium_capacity", sa.Integer(), nullable=False, server_default="5000")
    )
    op.add_column(
        "clubs", sa.Column("supporters", sa.Integer(), nullable=False, server_default="2500")
    )
    op.add_column(
        "matches", sa.Column("paused_seconds", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("matches", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_club_crowd", "clubs", "stadium_capacity >= 0 AND supporters >= 0"
    )
    op.drop_constraint("ck_fixture_status", "fixtures", type_="check")
    op.create_check_constraint(
        "ck_fixture_status",
        "fixtures",
        "status IN ('scheduled','live','paused','finished','abandoned','cancelled')",
    )
    op.drop_constraint("ck_fixture_finish", "fixtures", type_="check")
    op.create_check_constraint(
        "ck_fixture_finish",
        "fixtures",
        "(status IN ('finished','abandoned')) = (actual_finish_at IS NOT NULL)",
    )
    op.drop_constraint("ck_fixture_started", "fixtures", type_="check")
    op.create_check_constraint(
        "ck_fixture_started",
        "fixtures",
        "status NOT IN ('live','paused','finished','abandoned') OR actual_start_at IS NOT NULL",
    )
    op.drop_constraint("ck_match_phase", "matches", type_="check")
    op.create_check_constraint(
        "ck_match_phase",
        "matches",
        "phase IN ('first_half','half_time','second_half','full_time','abandoned','paused')",
    )
    op.create_check_constraint(
        "ck_match_pause",
        "matches",
        "paused_seconds >= 0 AND (phase = 'paused') = (paused_at IS NOT NULL)",
    )
    op.drop_constraint("ck_tick_game_minute", "match_ticks", type_="check")
    op.create_check_constraint(
        "ck_tick_game_minute",
        "match_ticks",
        "game_minute BETWEEN tick_number * 3 - 2 AND tick_number * 3",
    )
    op.drop_constraint("ck_command_kind", "accepted_commands", type_="check")
    op.create_check_constraint(
        "ck_command_kind",
        "accepted_commands",
        "kind IN ('lineup','tactics','substitution','instruction','mentality')",
    )
    op.execute(
        sa.text("""
CREATE TABLE operation_attempts (
        id UUID NOT NULL,
        job_id UUID NOT NULL,
        operation_id UUID NOT NULL,
        fixture_id UUID,
        retry_number INTEGER NOT NULL,
        stage VARCHAR(80) NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE NOT NULL,
        finished_at TIMESTAMP WITH TIME ZONE NOT NULL,
        due_at TIMESTAMP WITH TIME ZONE NOT NULL,
        outcome VARCHAR(16) NOT NULL,
        failure_class VARCHAR(32),
        PRIMARY KEY (id),
        CONSTRAINT uq_attempt_job_retry UNIQUE (job_id, retry_number),
        CONSTRAINT ck_attempt_time CHECK (retry_number > 0 AND finished_at >= started_at),
        CONSTRAINT ck_attempt_outcome CHECK (outcome IN ('committed','deferred','failed')),
        CONSTRAINT ck_attempt_failure CHECK (failure_class IS NULL OR failure_class IN
('not_ready','invalid_state','unsupported_job_kind')),
        FOREIGN KEY(job_id) REFERENCES jobs (id),
        FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
)
""")
    )
    op.execute(
        sa.text("""
CREATE TABLE match_stages (
        match_id UUID NOT NULL,
        stage VARCHAR(16) NOT NULL,
        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
        due_at TIMESTAMP WITH TIME ZONE NOT NULL,
        events JSONB NOT NULL,
        state_hash VARCHAR(64) NOT NULL,
        PRIMARY KEY (match_id, stage),
        CONSTRAINT ck_match_stage CHECK (stage IN
('locked','started','second_half','finished','abandoned')),
        FOREIGN KEY(match_id) REFERENCES matches (id)
)
""")
    )
    op.execute(
        sa.text("""
CREATE TRIGGER tr_match_stage_immutable BEFORE UPDATE OR DELETE ON match_stages FOR EACH ROW
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
'result_delivered', 'match_report_opened', 'next_action_opened', 'action_rejected',
'help_delivered', 'feedback_prompt_delivered', 'feedback_answered', 'feedback_skipped',
'experiment_exposed', 'bot_unavailable', 'club_ownership_ended', 'player_last_season_announced',
'player_retired', 'bot_youth_replacement_created'))
""")
    )


def downgrade() -> None:
    # Runtime state is immutable history; require explicit reconciliation before rollback.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM matches) THEN "
        "RAISE EXCEPTION 'Reconcile runtime matches before downgrade'; END IF; END $$"
    )
    op.drop_table("operation_attempts")
    op.execute("DROP TRIGGER tr_match_snapshot ON matches")
    op.execute("DROP FUNCTION goaletto_guard_match_snapshot()")
    op.drop_table("match_stages")
    op.drop_constraint("ck_club_crowd", "clubs", type_="check")
    op.drop_constraint("ck_fixture_status", "fixtures", type_="check")
    op.create_check_constraint(
        "ck_fixture_status",
        "fixtures",
        "status IN ('scheduled','live','paused','finished','cancelled')",
    )
    op.drop_constraint("ck_fixture_finish", "fixtures", type_="check")
    op.create_check_constraint(
        "ck_fixture_finish", "fixtures", "(status = 'finished') = (actual_finish_at IS NOT NULL)"
    )
    op.drop_constraint("ck_fixture_started", "fixtures", type_="check")
    op.create_check_constraint(
        "ck_fixture_started",
        "fixtures",
        "status NOT IN ('live','paused','finished') OR actual_start_at IS NOT NULL",
    )
    op.drop_constraint("ck_match_phase", "matches", type_="check")
    op.create_check_constraint(
        "ck_match_phase",
        "matches",
        "phase IN ('first_half','half_time','second_half','full_time','paused')",
    )
    op.drop_constraint("ck_match_pause", "matches", type_="check")
    op.drop_constraint("ck_tick_game_minute", "match_ticks", type_="check")
    op.create_check_constraint(
        "ck_tick_game_minute", "match_ticks", "game_minute = tick_number * 3"
    )
    op.drop_constraint("ck_command_kind", "accepted_commands", type_="check")
    op.create_check_constraint(
        "ck_command_kind",
        "accepted_commands",
        "kind IN ('lineup','tactics','substitution','instruction')",
    )
    op.drop_column("clubs", "stadium_capacity")
    op.drop_column("clubs", "supporters")
    op.drop_column("matches", "paused_seconds")
    op.drop_column("matches", "paused_at")
    op.execute("ALTER TABLE journey_events DROP CONSTRAINT ck_journey_name")
    op.execute(
        sa.text("""
ALTER TABLE journey_events ADD CONSTRAINT ck_journey_name CHECK (name IN ('manager_started',
'campaign_touched', 'club_offer_delivered', 'club_preview_opened', 'club_claimed',
'club_opened', 'lineup_opened', 'lineup_saved', 'next_fixture_opened', 'match_opened',
'match_command_accepted', 'match_command_applied', 'fixture_finished', 'result_delivered',
'match_report_opened', 'next_action_opened', 'action_rejected', 'help_delivered',
'feedback_prompt_delivered', 'feedback_answered', 'feedback_skipped', 'experiment_exposed',
'bot_unavailable', 'club_ownership_ended', 'player_last_season_announced', 'player_retired',
'bot_youth_replacement_created'))
""")
    )
