"""Frozen opaque campaign registry and start attribution; no cohorts or seeded codes."""

import sqlalchemy as sa
from alembic import op

revision = "0007_acquisition"
down_revision = "0006_match_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
CREATE TABLE acquisition_campaigns (
        id UUID NOT NULL,
        code VARCHAR(64) NOT NULL,
        source VARCHAR(24) NOT NULL,
        placement VARCHAR(80) NOT NULL,
        published_on DATE NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT ck_campaign_code CHECK (code ~ '^[A-Za-z0-9_-]{1,64}$'),
        CONSTRAINT ck_campaign_source CHECK (source IN
('telegram_channel','telegram_ads','community','referral','other')),
        CONSTRAINT ck_campaign_placement CHECK (placement ~ '^[a-z0-9_-]{1,80}$'),
        UNIQUE (code)
)
""")
    )
    op.execute(
        sa.text("""
CREATE TRIGGER tr_acquisition_campaigns_immutable BEFORE UPDATE OR DELETE ON
acquisition_campaigns FOR EACH ROW EXECUTE FUNCTION goaletto_guard_append_only()
""")
    )
    op.execute(
        sa.text("""
CREATE TABLE acquisition_first_touches (
        analytics_manager_id UUID NOT NULL,
        operation_id UUID NOT NULL,
        kind VARCHAR(16) NOT NULL,
        campaign_id UUID,
        started_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (analytics_manager_id),
        CONSTRAINT ck_first_touch_kind CHECK (kind IN ('direct','unknown','known')),
        CONSTRAINT ck_first_touch_campaign CHECK ((kind = 'known') = (campaign_id IS NOT NULL)),
        FOREIGN KEY(analytics_manager_id) REFERENCES analytics_managers (id) ON DELETE CASCADE,
        UNIQUE (operation_id),
        FOREIGN KEY(campaign_id) REFERENCES acquisition_campaigns (id)
)
""")
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_first_touch_time ON acquisition_first_touches (started_at)
""")
    )
    op.execute(
        sa.text("""
CREATE TRIGGER tr_acquisition_first_touches_immutable BEFORE UPDATE ON acquisition_first_touches
FOR EACH ROW EXECUTE FUNCTION goaletto_guard_append_only()
""")
    )
    op.execute(
        sa.text("""
CREATE TABLE acquisition_touches (
        operation_id UUID NOT NULL,
        analytics_manager_id UUID NOT NULL,
        kind VARCHAR(16) NOT NULL,
        campaign_id UUID,
        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
        recorded_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (operation_id),
        CONSTRAINT ck_touch_kind CHECK (kind IN ('direct','unknown','known')),
        CONSTRAINT ck_touch_campaign CHECK ((kind = 'known') = (campaign_id IS NOT NULL)),
        CONSTRAINT ck_touch_time CHECK (recorded_at >= occurred_at),
        FOREIGN KEY(analytics_manager_id) REFERENCES analytics_managers (id) ON DELETE CASCADE,
        FOREIGN KEY(campaign_id) REFERENCES acquisition_campaigns (id)
)
""")
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_touch_retention ON acquisition_touches (recorded_at)
""")
    )
    op.execute(
        sa.text("""
CREATE TRIGGER tr_acquisition_touches_immutable BEFORE UPDATE ON acquisition_touches FOR EACH
ROW EXECUTE FUNCTION goaletto_guard_append_only()
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
'start_accepted', 'campaign_touched', 'club_offer_delivered', 'club_preview_opened',
'club_claimed', 'club_opened', 'lineup_opened', 'lineup_saved', 'next_fixture_opened',
'match_opened', 'match_command_accepted', 'match_command_applied', 'fixture_finished',
'fixture_started', 'match_lineup_locked', 'match_tick_committed', 'match_second_half_started',
'fixture_abandoned', 'result_finalized', 'result_delivered', 'match_report_opened',
'next_action_opened', 'action_rejected', 'help_delivered', 'feedback_prompt_delivered',
'feedback_answered', 'feedback_skipped', 'experiment_exposed', 'bot_unavailable',
'club_ownership_ended', 'player_last_season_announced', 'player_retired',
'bot_youth_replacement_created'))
""")
    )
    op.execute(
        sa.text("""
REVOKE ALL ON acquisition_campaigns, acquisition_first_touches, acquisition_touches FROM PUBLIC
""")
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM acquisition_touches) THEN "
        "RAISE EXCEPTION 'Reconcile attribution before downgrade'; END IF; END $$"
    )
    op.drop_table("acquisition_touches")
    op.drop_table("acquisition_first_touches")
    op.drop_table("acquisition_campaigns")
    op.execute("ALTER TABLE journey_events DROP CONSTRAINT ck_journey_name")
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
