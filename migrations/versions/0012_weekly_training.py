"""Weekly training plans, durable choices and player development."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_weekly_training"
down_revision = "0011_agent_wave2"
branch_labels = None
depends_on = None

OLD_KINDS = (
    "kind IN ('club_claimed','club_opened','club_preview_opened','lineup_opened',"
    "'lineup_saved','match_command_accepted','match_opened','match_report_opened',"
    "'next_fixture_opened','notification_preferences')"
)
NEW_KINDS = OLD_KINDS[:-1] + ",'training_opened','training_plan_saved')"


OLD_EVENTS = (
    "action_rejected",
    "bot_unavailable",
    "bot_youth_replacement_created",
    "campaign_touched",
    "club_claimed",
    "club_offer_delivered",
    "club_opened",
    "club_ownership_ended",
    "club_preview_opened",
    "experiment_exposed",
    "feedback_answered",
    "feedback_prompt_delivered",
    "feedback_skipped",
    "fixture_abandoned",
    "fixture_finished",
    "fixture_started",
    "help_delivered",
    "lineup_opened",
    "lineup_saved",
    "manager_started",
    "match_command_accepted",
    "match_command_applied",
    "match_lineup_locked",
    "match_opened",
    "match_report_opened",
    "match_second_half_started",
    "match_tick_committed",
    "next_action_opened",
    "next_fixture_opened",
    "player_last_season_announced",
    "player_retired",
    "result_delivered",
    "result_finalized",
    "start_accepted",
)
NEW_EVENTS = (*OLD_EVENTS, "training_opened", "training_plan_saved")


def _event_check(names: tuple[str, ...]) -> str:
    return "name IN (" + ",".join(f"'{n}'" for n in sorted(names)) + ")"


def upgrade() -> None:
    op.create_table(
        "player_development",
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("persons.id"), primary_key=True),
        sa.Column("caps", postgresql.JSONB(), nullable=False),
        sa.Column("progress", postgresql.JSONB(), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(caps) = 'object' AND jsonb_typeof(progress) = 'object'",
            name="ck_development_objects",
        ),
        sa.CheckConstraint("policy_version = 'training-v1'", name="ck_development_policy"),
    )
    op.create_table(
        "training_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("ownership_id", sa.Uuid(), sa.ForeignKey("ownership_periods.id"), nullable=False),
        sa.Column("week_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("focus", sa.String(20), nullable=False),
        sa.Column("intensity", sa.String(16), nullable=False),
        sa.Column("executor_id", sa.Uuid(), sa.ForeignKey("persons.id")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("results", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("club_id", "week_start", name="uq_training_club_week"),
        sa.CheckConstraint(
            "focus IN ('goalkeeping','defending','playmaking','winger','attacking',"
            "'set_pieces','physical','team')",
            name="ck_training_focus",
        ),
        sa.CheckConstraint("intensity IN ('low','medium','high')", name="ck_training_intensity"),
        sa.CheckConstraint(
            "status IN ('scheduled','applied','cancelled')", name="ck_training_status"
        ),
        sa.CheckConstraint(
            "(status = 'applied') = (applied_at IS NOT NULL)", name="ck_training_applied"
        ),
        sa.CheckConstraint(
            "due_at = week_start + interval '7 days' AND selected_at >= week_start "
            "AND selected_at < due_at",
            name="ck_training_week",
        ),
        sa.CheckConstraint(
            "focus <> 'set_pieces' OR executor_id IS NOT NULL", name="ck_training_executor"
        ),
        sa.CheckConstraint("policy_version = 'training-v1'", name="ck_training_policy"),
    )
    op.create_table(
        "training_decisions",
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("training_plans.id"), nullable=False),
        sa.Column("ownership_id", sa.Uuid(), sa.ForeignKey("ownership_periods.id"), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    skills = (
        "goalkeeping",
        "defending",
        "playmaking",
        "winger",
        "attacking",
        "set_pieces",
        "stamina",
        "pace",
    )
    caps = ", ".join(
        f"'{k}', greatest(p.{k}, 12 + ((('x' || "
        f"substr(md5('training-caps:v1:' || p.person_id::text || chr(58) || '{k}'),1,8))"
        "::bit(32)::bigint) % 9))"
        for k in skills
    )
    progress = ", ".join(f"'{k}', 0" for k in (*skills, "experience"))
    op.execute(
        "INSERT INTO player_development(person_id,caps,progress,policy_version) "
        f"SELECT p.person_id,jsonb_build_object({caps}),jsonb_build_object({progress}),"
        "'training-v1' FROM players p"
    )
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", NEW_KINDS)
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint("ck_journey_name", "journey_events", _event_check(NEW_EVENTS))


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM training_plans) OR EXISTS "
        "(SELECT 1 FROM journey_events WHERE name IN "
        "('training_opened','training_plan_saved')) OR EXISTS "
        "(SELECT 1 FROM game_action_receipts WHERE kind IN "
        "('training_opened','training_plan_saved')) THEN RAISE EXCEPTION "
        "'Reconcile training plans and receipts before downgrade'; END IF; END $$"
    )
    op.drop_table("training_decisions")
    op.drop_table("training_plans")
    op.drop_table("player_development")
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", OLD_KINDS)
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint("ck_journey_name", "journey_events", _event_check(OLD_EVENTS))
