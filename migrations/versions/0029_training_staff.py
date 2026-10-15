"""Specialist training snapshots and an age-season growth budget; keep past receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029_training_staff"
down_revision = "0028_coaches_staff"
branch_labels = None
depends_on = None

ZERO_GAINS = (
    '{"goalkeeping": 0,"defending": 0,"playmaking": 0,"winger": 0,"attacking": 0,'
    '"set_pieces": 0,"stamina": 0,"pace": 0,"training": 0,"match": 0,"leadership": 0}'
)


def upgrade() -> None:
    op.add_column(
        "player_development",
        sa.Column("growth_age", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "player_development",
        sa.Column("season_gains", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "player_development",
        sa.Column("leadership_progress", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "player_development",
        sa.Column(
            "growth_policy", sa.String(32), nullable=False, server_default="development-budget-v1"
        ),
    )
    op.execute(
        "UPDATE player_development d SET growth_age=p.age, season_gains='"
        + ZERO_GAINS
        + "'::jsonb FROM persons p WHERE p.id=d.person_id"
    )
    op.create_check_constraint(
        "ck_development_season",
        "player_development",
        "growth_age BETWEEN 0 AND 150 AND leadership_progress BETWEEN 0 AND 9999",
    )
    op.create_check_constraint(
        "ck_development_gains", "player_development", "jsonb_typeof(season_gains) = 'object'"
    )
    op.create_check_constraint(
        "ck_development_growth_policy",
        "player_development",
        "growth_policy = 'development-budget-v1'",
    )
    op.add_column(
        "training_plans",
        sa.Column("staff_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    # Do not execute an old unrestricted choice under new specialist rules.
    op.execute(
        "UPDATE training_plans SET status='cancelled', "
        'results=\'[{"reason":"policy_updated"}]\'::jsonb '
        "WHERE status='scheduled'"
    )
    op.drop_constraint("ck_training_policy", "training_plans", type_="check")
    op.create_check_constraint(
        "ck_training_policy",
        "training_plans",
        "policy_version IN ('training-daily-v1','training-specialists-v2')",
    )
    op.execute("""CREATE FUNCTION protect_training_result() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' OR (OLD.status <> 'scheduled' AND NEW IS DISTINCT FROM OLD)
          THEN RAISE EXCEPTION 'Training result history is immutable'; END IF;
        IF ROW(NEW.id,NEW.club_id,NEW.week_start,NEW.due_at,NEW.policy_version)
          IS DISTINCT FROM ROW(OLD.id,OLD.club_id,OLD.week_start,OLD.due_at,OLD.policy_version)
          THEN RAISE EXCEPTION 'Training schedule is immutable'; END IF;
        RETURN NEW;
      END $$""")
    op.execute(
        "CREATE TRIGGER training_result_immutable BEFORE UPDATE OR DELETE ON training_plans "
        "FOR EACH ROW EXECUTE FUNCTION protect_training_result()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM training_plans WHERE policy_version='training-specialists-v2')
        OR EXISTS (SELECT 1 FROM player_development WHERE leadership_progress > 0
          OR (season_gains->>'training')::int > 0 OR (season_gains->>'match')::int > 0
          OR (season_gains->>'leadership')::int > 0)
        THEN RAISE EXCEPTION 'Development history prevents downgrade'; END IF;
    END $$""")
    op.execute("DROP TRIGGER training_result_immutable ON training_plans")
    op.execute("DROP FUNCTION protect_training_result()")
    op.drop_constraint("ck_training_policy", "training_plans", type_="check")
    op.create_check_constraint(
        "ck_training_policy", "training_plans", "policy_version = 'training-daily-v1'"
    )
    op.drop_column("training_plans", "staff_snapshot")
    for name in ("ck_development_season", "ck_development_gains", "ck_development_growth_policy"):
        op.drop_constraint(name, "player_development", type_="check")
    for column in ("growth_age", "season_gains", "leadership_progress", "growth_policy"):
        op.drop_column("player_development", column)
