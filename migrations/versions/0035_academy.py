"""Club academy level, durable season shortlists and academy economy kinds."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_academy"
down_revision = "0034_real_knockout"
branch_labels = None
depends_on = None

POSITIONS = "'GK','DL','DC','DR','DM','ML','MC','MR','AMC','AML','AMR','FC'"

LEDGER_KINDS_OLD = (
    "'opening','grant','tickets','sponsor','merchandise','player_salary','staff_salary',"
    "'stadium_upkeep','prize','reserve','release','transfer_in','transfer_out','arrears_paid',"
    "'stadium_upgrade','signing_fee','scout_search'"
)
LEDGER_KINDS_NEW = LEDGER_KINDS_OLD + ",'academy_upkeep','academy_upgrade'"

ACTION_KINDS_OLD = (
    "'club_claimed','club_opened','club_preview_opened','lineup_opened','lineup_saved',"
    "'match_command_accepted','match_opened','match_report_opened','next_fixture_opened',"
    "'notification_preferences','training_opened','training_plan_saved','language_preferences',"
    "'finances_opened','stadium_opened','stadium_expansion_ordered','market_opened',"
    "'market_listing_created','market_bid_accepted','market_listing_cancelled','candidate_signed',"
    "'scout_search_started','scout_search_cancelled'"
)
ACTION_KINDS_NEW = (
    ACTION_KINDS_OLD + ",'academy_opened','academy_upgraded','academy_candidate_signed'"
)

JOURNEY_NAMES_OLD = (
    "'action_rejected','bot_unavailable','bot_youth_replacement_created','campaign_touched',"
    "'candidate_signed','club_claimed','club_offer_delivered','club_opened',"
    "'club_ownership_ended','club_preview_opened','experiment_exposed','feedback_answered',"
    "'feedback_prompt_delivered','feedback_skipped','finances_opened','fixture_abandoned',"
    "'fixture_finished','fixture_started','help_delivered','lineup_opened','lineup_saved',"
    "'manager_started','market_bid_accepted','market_listing_cancelled','market_listing_created',"
    "'market_opened','match_command_accepted','match_command_applied','match_lineup_locked',"
    "'match_opened','match_report_opened','match_second_half_started','match_tick_committed',"
    "'next_action_opened','next_fixture_opened','player_last_season_announced','player_retired',"
    "'result_delivered','result_finalized','stadium_expansion_ordered','stadium_opened',"
    "'start_accepted','training_opened','training_plan_saved','scout_search_started',"
    "'scout_search_cancelled'"
)
JOURNEY_NAMES_NEW = (
    "'academy_candidate_signed','academy_opened','academy_upgraded'," + JOURNEY_NAMES_OLD
)


def upgrade() -> None:
    op.add_column(
        "clubs", sa.Column("academy_level", sa.SmallInteger(), server_default="1", nullable=False)
    )
    op.create_check_constraint("ck_club_academy_level", "clubs", "academy_level BETWEEN 1 AND 5")
    op.create_table(
        "academy_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        sa.Column("policy_version", sa.String(32), server_default="academy-v1", nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("age", sa.SmallInteger(), nullable=False),
        sa.Column("position", sa.String(3), nullable=False),
        sa.Column("alternative_positions", postgresql.ARRAY(sa.String(3)), nullable=False),
        sa.Column("skills", postgresql.JSONB(), nullable=False),
        sa.Column("caps", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("club_id", "season_id", "slot", name="uq_academy_club_season_slot"),
        sa.CheckConstraint("policy_version = 'academy-v1'", name="ck_academy_policy"),
        sa.CheckConstraint("slot >= 1", name="ck_academy_slot"),
        sa.CheckConstraint("age BETWEEN 17 AND 18", name="ck_academy_age"),
        sa.CheckConstraint(f"position IN ({POSITIONS})", name="ck_academy_position"),
        sa.CheckConstraint(
            "cardinality(alternative_positions) <= 2 AND "
            f"alternative_positions <@ ARRAY[{POSITIONS}]::varchar[] AND "
            "NOT position = ANY(alternative_positions) AND "
            "array_position(alternative_positions, NULL) IS NULL AND "
            "(cardinality(alternative_positions) < 2 OR "
            "alternative_positions[1] <> alternative_positions[2])",
            name="ck_academy_alternatives",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(skills) = 'object' AND jsonb_typeof(caps) = 'object'",
            name="ck_academy_profiles",
        ),
        sa.CheckConstraint("status IN ('available','signed','expired')", name="ck_academy_status"),
        sa.CheckConstraint(
            "(status = 'signed') = (signed_at IS NOT NULL)", name="ck_academy_signed"
        ),
    )
    op.create_index("ix_academy_candidates_club_id", "academy_candidates", ["club_id"])
    op.drop_constraint("ck_ledger_kind", "ledger_entries", type_="check")
    op.create_check_constraint("ck_ledger_kind", "ledger_entries", f"kind IN ({LEDGER_KINDS_NEW})")
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint(
        "ck_action_kind", "game_action_receipts", f"kind IN ({ACTION_KINDS_NEW})"
    )
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint(
        "ck_journey_name", "journey_events", f"name IN ({JOURNEY_NAMES_NEW})"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM academy_candidates) THEN "
        "RAISE EXCEPTION 'Academy history prevents downgrade'; END IF; END $$"
    )
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint(
        "ck_journey_name", "journey_events", f"name IN ({JOURNEY_NAMES_OLD})"
    )
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint(
        "ck_action_kind", "game_action_receipts", f"kind IN ({ACTION_KINDS_OLD})"
    )
    op.drop_constraint("ck_ledger_kind", "ledger_entries", type_="check")
    op.create_check_constraint("ck_ledger_kind", "ledger_entries", f"kind IN ({LEDGER_KINDS_OLD})")
    op.drop_index("ix_academy_candidates_club_id", table_name="academy_candidates")
    op.drop_table("academy_candidates")
    op.drop_constraint("ck_club_academy_level", "clubs", type_="check")
    op.drop_column("clubs", "academy_level")
