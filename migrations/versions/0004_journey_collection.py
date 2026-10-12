"""Frozen journey collector DDL; no coverage activation or game writes."""

import sqlalchemy as sa
from alembic import op

revision = "0004_journey_collection"
down_revision = "0003_world_seed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
CREATE TABLE analytics_coverage (
	version SMALLSERIAL NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	retention_days INTEGER NOT NULL, 
	PRIMARY KEY (version), 
	CONSTRAINT ck_coverage_version CHECK (version = 1), 
	CONSTRAINT ck_coverage_retention CHECK (retention_days BETWEEN 1 AND 730)
)
""")
    )
    op.execute(
        sa.text("""
CREATE TABLE analytics_exclusions (
	manager_id UUID NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	excluded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (manager_id), 
	CONSTRAINT ck_exclusion_kind CHECK (kind IN ('internal','test','operator','opt_out')), 
	FOREIGN KEY(manager_id) REFERENCES managers (id)
)
""")
    )
    op.execute(
        sa.text("""
CREATE TABLE journey_sessions (
	id UUID NOT NULL, 
	analytics_manager_id UUID NOT NULL, 
	first_action_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_action_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_session_time CHECK (last_action_at >= first_action_at), 
	CONSTRAINT uq_session_manager UNIQUE (id, analytics_manager_id), 
	FOREIGN KEY(analytics_manager_id) REFERENCES analytics_managers (id) ON DELETE CASCADE
)
""")
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_session_manager_time ON journey_sessions (analytics_manager_id, last_action_at)
""")
    )
    op.execute(
        sa.text("""
CREATE TABLE journey_events (
	id UUID NOT NULL, 
	version SMALLINT NOT NULL, 
	operation_id UUID NOT NULL, 
	effect_number SMALLINT NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	origin VARCHAR(16) NOT NULL, 
	analytics_manager_id UUID, 
	session_id UUID, 
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	user_activity BOOLEAN NOT NULL, 
	envelope JSONB NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_journey_effect UNIQUE (version, operation_id, effect_number), 
        CONSTRAINT fk_journey_session_manager FOREIGN KEY(session_id,  analytics_manager_id)
REFERENCES journey_sessions (id,  analytics_manager_id) ON DELETE CASCADE,
	CONSTRAINT ck_journey_version CHECK (version = 1 AND effect_number BETWEEN 0 AND 32767), 
	CONSTRAINT ck_journey_time CHECK (recorded_at >= occurred_at), 
	CONSTRAINT ck_journey_origin CHECK (origin IN ('user','system','automation','operator')), 
        CONSTRAINT ck_journey_session_origin CHECK ((origin = 'user' OR session_id IS NULL) AND
(NOT user_activity OR session_id IS NOT NULL)),
	CONSTRAINT ck_journey_activity CHECK (NOT user_activity OR origin = 'user'), 
	FOREIGN KEY(analytics_manager_id) REFERENCES analytics_managers (id) ON DELETE CASCADE
)
""")
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_journey_manager_time ON journey_events (analytics_manager_id, occurred_at)
""")
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_journey_retention ON journey_events (recorded_at)
""")
    )
    op.execute(
        sa.text("""
CREATE UNIQUE INDEX uq_journey_manager_start ON journey_events (analytics_manager_id) WHERE name
= 'manager_started'
""")
    )
    op.execute(
        sa.text("""
ALTER TABLE journey_events ADD CONSTRAINT ck_journey_closed_envelope CHECK
(jsonb_typeof(envelope) = 'object' AND envelope - ARRAY['event_id', 'name', 'occurred_at',
'recorded_at', 'origin', 'operation_id', 'effect_number', 'surface', 'version',
'analytics_manager_id', 'session_id', 'campaign_id', 'feedback_campaign_id', 'club_id',
'ownership_id', 'fixture_id', 'command_id', 'outbox_id', 'person_id', 'season_id',
'experiment_id', 'variant', 'reason', 'target']::text[] = '{}'::jsonb)
""")
    )
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
    op.execute(
        sa.text("""
CREATE TRIGGER tr_journey_event_immutable BEFORE UPDATE ON journey_events FOR EACH ROW EXECUTE
FUNCTION goaletto_guard_append_only()
""")
    )
    op.execute(
        sa.text("""
CREATE VIEW journey_export WITH (security_barrier=true) AS
SELECT e.id, e.version, e.name, e.origin, e.analytics_manager_id, e.session_id,
       e.occurred_at, e.recorded_at, e.user_activity
FROM journey_events e
JOIN analytics_managers a ON a.id = e.analytics_manager_id
JOIN managers m ON m.id = a.manager_id
WHERE NOT m.is_internal
AND NOT EXISTS (SELECT 1 FROM analytics_exclusions x WHERE x.manager_id = m.id)
""")
    )
    op.execute(
        "REVOKE ALL ON analytics_managers, analytics_exclusions, journey_sessions, "
        "journey_events, analytics_coverage, journey_export FROM PUBLIC"
    )


def downgrade() -> None:
    op.execute("DROP VIEW journey_export")
    op.drop_table("journey_events")
    op.drop_table("journey_sessions")
    op.drop_table("analytics_exclusions")
    op.drop_table("analytics_coverage")
