"""Preferred daily training, global opponent search and confirmed player release."""

import sqlalchemy as sa
from alembic import op

revision = "0051_club_management"
down_revision = "0050_club_country"
branch_labels = None
depends_on = None

CK_TRAINING_WEEK = (
    "((schedule_policy = 'training-midnight-v1' AND due_at = week_start + interval '1"
    " day') OR (schedule_policy = 'training-preference-v1' AND due_at >= week_start "
    "AND due_at < week_start + interval '1 day')) AND selected_at >= week_start - "
    "interval '9 days' AND selected_at < due_at"
)
CK_LEDGER_KIND = (
    "kind IN "
    "('opening','grant','tickets','sponsor','merchandise','player_salary','staff_salary','stadium_upkeep','prize','reserve','release','transfer_in','transfer_out','arrears_paid','stadium_upgrade','signing_fee','scout_search','academy_upkeep','academy_upgrade','debt_conversion','player_release')"
)
CK_JOURNEY_NAME = (
    "name IN "
    "('friendly_opened','friendly_scheduled','friendly_invited','friendly_responded','academy_candidate_signed','academy_opened','academy_upgraded','action_rejected','bot_unavailable','bot_youth_replacement_created','campaign_touched','candidate_signed','player_contract_released','club_claimed','club_offer_delivered','club_opened','club_ownership_ended','club_preview_opened','experiment_exposed','feedback_answered','feedback_prompt_delivered','feedback_skipped','finances_opened','fixture_abandoned','fixture_finished','fixture_started','help_delivered','lineup_opened','lineup_saved','manager_started','market_bid_accepted','market_listing_cancelled','market_listing_created','market_opened','match_command_accepted','match_command_applied','match_lineup_locked','match_opened','match_report_opened','match_second_half_started','match_tick_committed','next_action_opened','next_fixture_opened','player_last_season_announced','player_retired','result_delivered','result_finalized','stadium_expansion_ordered','stadium_opened','start_accepted','training_opened','training_plan_saved','training_plan_cancelled','scout_search_started','scout_search_cancelled')"
)
CK_ACTION_KIND = (
    "kind IN "
    "('club_claimed','club_opened','club_preview_opened','lineup_opened','lineup_saved','match_command_accepted','match_opened','match_report_opened','next_fixture_opened','notification_preferences','training_opened','training_plan_saved','language_preferences','finances_opened','stadium_opened','stadium_expansion_ordered','market_opened','market_listing_created','market_bid_accepted','market_listing_cancelled','candidate_signed','scout_search_started','scout_search_cancelled','academy_opened','academy_upgraded','training_plan_cancelled','player_contract_released','academy_candidate_signed','friendly_opened','friendly_scheduled','friendly_invited','friendly_responded','entry_opened')"
)


def upgrade() -> None:
    op.add_column(
        "training_plans",
        sa.Column(
            "schedule_policy", sa.String(32), nullable=False, server_default="training-midnight-v1"
        ),
    )
    op.drop_constraint("uq_training_club_week", "training_plans", type_="unique")
    op.create_index(
        "uq_training_current_day",
        "training_plans",
        ["club_id", "week_start"],
        unique=True,
        postgresql_where=sa.text("status <> 'cancelled'"),
    )
    op.drop_constraint("ck_training_week", "training_plans", type_="check")
    op.create_check_constraint("ck_training_week", "training_plans", CK_TRAINING_WEEK)
    op.execute("""CREATE OR REPLACE FUNCTION protect_training_result() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' OR (OLD.status <> 'scheduled' AND NEW IS DISTINCT FROM OLD)
          THEN RAISE EXCEPTION 'Training result history is immutable'; END IF;
        IF ROW(NEW.id,NEW.club_id,NEW.week_start,NEW.policy_version)
          IS DISTINCT FROM ROW(OLD.id,OLD.club_id,OLD.week_start,OLD.policy_version)
          THEN RAISE EXCEPTION 'Training identity is immutable'; END IF;
        IF ROW(NEW.due_at,NEW.schedule_policy) IS DISTINCT FROM ROW(OLD.due_at,OLD.schedule_policy)
          AND NOT (OLD.status='scheduled' AND NEW.status='scheduled'
            AND NEW.schedule_policy='training-preference-v1' AND NEW.selected_at>OLD.selected_at)
          THEN RAISE EXCEPTION 'Training time needs a new explicit selection'; END IF;
        RETURN NEW; END $$""")
    op.create_table(
        "friendly_searches",
        sa.Column(
            "ownership_id", sa.Uuid(), sa.ForeignKey("ownership_periods.id"), primary_key=True
        ),
        sa.Column("assigned_day", sa.Date(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prompt_message_id", sa.BigInteger()),
    )
    op.create_table(
        "player_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("ownership_id", sa.Uuid(), sa.ForeignKey("ownership_periods.id"), nullable=False),
        sa.Column("salary_id", sa.Uuid(), sa.ForeignKey("salary_contracts.id"), nullable=False),
        sa.Column("compensation", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("compensation >= 0", name="ck_player_release_compensation"),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR confirmed_at >= created_at", name="ck_player_release_time"
        ),
    )
    op.execute("""CREATE FUNCTION protect_player_release() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' OR (OLD.confirmed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD)
          OR ROW(NEW.id,NEW.person_id,NEW.club_id,NEW.ownership_id,NEW.salary_id,
                 NEW.compensation,NEW.name,NEW.created_at)
          IS DISTINCT FROM ROW(OLD.id,OLD.person_id,OLD.club_id,OLD.ownership_id,OLD.salary_id,
                 OLD.compensation,OLD.name,OLD.created_at)
          THEN RAISE EXCEPTION 'Player release terms/history are immutable'; END IF;
        RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER player_releases_immutable BEFORE UPDATE OR DELETE ON player_releases "
        "FOR EACH ROW EXECUTE FUNCTION protect_player_release()"
    )
    op.drop_index("uq_candidate_player_signing", table_name="candidate_signings")
    op.create_index("ix_candidate_person_signing", "candidate_signings", ["person_id", "signed_at"])
    op.drop_constraint("ck_ledger_kind", "ledger_entries", type_="check")
    op.create_check_constraint("ck_ledger_kind", "ledger_entries", CK_LEDGER_KIND)
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint("ck_journey_name", "journey_events", CK_JOURNEY_NAME)
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", CK_ACTION_KIND)


def downgrade() -> None:
    # Preserve the additive schema and use an application version supporting both schedules.
    # Automatic downgrade may not erase quotes, releases, replies or scheduled times.
    raise RuntimeError("Club-management history must be preserved; use an additive migration")
