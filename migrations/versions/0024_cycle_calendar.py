"""Four-day calendars and structural plans, applied after the approved dev reset."""

import sqlalchemy as sa
from alembic import op

revision = "0024_cycle_calendar"
down_revision = "0023_kickoff_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "seasons",
        sa.Column(
            "calendar_policy", sa.String(40), nullable=False, server_default="four-day-calendar-v1"
        ),
    )
    op.add_column(
        "seasons",
        sa.Column("calendar_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("seasons", sa.Column("cycle_epoch", sa.Date()))
    op.alter_column("salary_contracts", "policy_version", server_default="economy-cycle-v1")
    op.drop_constraint("ck_finance_policy", "finance_cycles", type_="check")
    op.create_check_constraint(
        "ck_finance_policy", "finance_cycles", "policy_version = 'economy-cycle-v1'"
    )
    op.create_table(
        "fixture_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("league_id", sa.Uuid(), sa.ForeignKey("leagues.id")),
        sa.Column("competition", sa.String(16), nullable=False, server_default="league"),
        sa.Column("round_number", sa.SmallInteger(), nullable=False),
        sa.Column("home_club_id", sa.Uuid(), sa.ForeignKey("clubs.id")),
        sa.Column("away_club_id", sa.Uuid(), sa.ForeignKey("clubs.id")),
        sa.Column("conditional_ref", sa.String(200)),
        sa.Column("assigned_day", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("announced_at", sa.DateTime(timezone=True)),
        sa.Column("planning_until", sa.DateTime(timezone=True)),
        sa.Column("blocked_reason", sa.String(40)),
        sa.CheckConstraint(
            "competition IN ('league','cup','champions','friendly')", name="ck_plan_competition"
        ),
        sa.CheckConstraint("revision > 0", name="ck_plan_revision"),
        sa.CheckConstraint(
            "home_club_id IS NULL OR away_club_id IS NULL OR home_club_id <> away_club_id",
            name="ck_plan_distinct",
        ),
        sa.CheckConstraint(
            "(home_club_id IS NOT NULL AND away_club_id IS NOT NULL) "
            "OR conditional_ref IS NOT NULL",
            name="ck_plan_participants",
        ),
    )
    op.create_index("ix_plan_day", "fixture_plans", ["assigned_day", "announced_at"])
    op.drop_constraint("ck_training_week", "training_plans", type_="check")
    op.drop_constraint("ck_training_policy", "training_plans", type_="check")
    op.create_check_constraint(
        "ck_training_policy",
        "training_plans",
        "policy_version = 'training-daily-v1'",
    )
    op.create_check_constraint(
        "ck_training_week",
        "training_plans",
        "due_at = week_start + interval '1 day' AND selected_at >= week_start "
        "AND selected_at < due_at",
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM fixture_plans) OR EXISTS
      (SELECT 1 FROM finance_cycles WHERE policy_version='economy-cycle-v1') OR EXISTS
      (SELECT 1 FROM training_plans WHERE policy_version='training-daily-v1')
      THEN RAISE EXCEPTION 'Cycle history prevents downgrade'; END IF; END $$""")
    op.drop_constraint("ck_training_week", "training_plans", type_="check")
    op.drop_constraint("ck_training_policy", "training_plans", type_="check")
    op.create_check_constraint(
        "ck_training_policy", "training_plans", "policy_version = 'training-v1'"
    )
    op.create_check_constraint(
        "ck_training_week",
        "training_plans",
        "due_at = week_start + interval '7 days' "
        "AND selected_at >= week_start AND selected_at < due_at",
    )
    op.drop_table("fixture_plans")
    op.drop_constraint("ck_finance_policy", "finance_cycles", type_="check")
    op.create_check_constraint(
        "ck_finance_policy", "finance_cycles", "policy_version = 'economy-v1'"
    )
    op.alter_column("salary_contracts", "policy_version", server_default="economy-v1")
    for column in ("cycle_epoch", "calendar_enabled", "calendar_policy"):
        op.drop_column("seasons", column)
