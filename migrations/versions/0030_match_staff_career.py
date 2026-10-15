"""Pinned appearance development and one retirement career with a private first offer."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030_match_staff_career"
down_revision = "0029_training_staff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_development",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("staff_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("results", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "policy_version = 'match-development-v1'", name="ck_match_development_policy"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(staff_snapshot) = 'object' AND jsonb_typeof(results) = 'array'",
            name="ck_match_development_documents",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
        ),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_table(
        "staff_careers",
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("origin_club_id", sa.Uuid(), nullable=True),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("skill", sa.SmallInteger(), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('head_coach','goalkeeping_coach','defence_coach','attack_coach',"
            "'fitness_coach','physiotherapist','scout') AND skill BETWEEN 1 AND 20",
            name="ck_staff_career_qualification",
        ),
        sa.CheckConstraint("policy_version = 'retired-staff-v1'", name="ck_staff_career_policy"),
        sa.CheckConstraint(
            "status IN ('pending','public','accepted')", name="ck_staff_career_status"
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND ((status = 'pending') = (resolved_at IS NULL))",
            name="ck_staff_career_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["staff.person_id"],
        ),
        sa.ForeignKeyConstraint(
            ["origin_club_id"],
            ["clubs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.id"],
        ),
        sa.PrimaryKeyConstraint("person_id"),
    )
    op.drop_constraint("ck_candidate_status", "candidate_offers", type_="check")
    op.create_check_constraint(
        "ck_candidate_status",
        "candidate_offers",
        "status IN ('available','private','signed','retired')",
    )
    op.execute("""CREATE FUNCTION protect_match_development() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' OR (OLD.applied_at IS NOT NULL AND NEW IS DISTINCT FROM OLD)
        THEN RAISE EXCEPTION 'Match development history is immutable'; END IF;
      IF ROW(NEW.match_id,NEW.policy_version,NEW.eligible,NEW.staff_snapshot,NEW.locked_at)
        IS DISTINCT FROM ROW(OLD.match_id,OLD.policy_version,OLD.eligible,
        OLD.staff_snapshot,OLD.locked_at)
        THEN RAISE EXCEPTION 'Match development terms are immutable'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER match_development_immutable BEFORE UPDATE OR DELETE ON "
        "match_development FOR EACH ROW EXECUTE FUNCTION protect_match_development()"
    )
    op.execute("""CREATE FUNCTION protect_staff_career() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' OR (OLD.status <> 'pending' AND NEW IS DISTINCT FROM OLD)
        THEN RAISE EXCEPTION 'Staff career history is immutable'; END IF;
      IF ROW(NEW.person_id,NEW.origin_club_id,NEW.season_id,NEW.role,NEW.skill,
        NEW.policy_version,NEW.created_at,NEW.expires_at)
        IS DISTINCT FROM ROW(OLD.person_id,OLD.origin_club_id,OLD.season_id,OLD.role,
        OLD.skill,OLD.policy_version,OLD.created_at,OLD.expires_at)
        THEN RAISE EXCEPTION 'Staff career outcome is immutable'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER staff_career_immutable BEFORE UPDATE OR DELETE ON "
        "staff_careers FOR EACH ROW EXECUTE FUNCTION protect_staff_career()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM staff_careers) OR EXISTS
        (SELECT 1 FROM match_development WHERE applied_at IS NOT NULL)
        THEN RAISE EXCEPTION 'Career or development history prevents downgrade'; END IF;
    END $$""")
    op.drop_table("staff_careers")
    op.drop_table("match_development")
    op.execute("DROP FUNCTION protect_staff_career()")
    op.execute("DROP FUNCTION protect_match_development()")
    op.drop_constraint("ck_candidate_status", "candidate_offers", type_="check")
    op.create_check_constraint(
        "ck_candidate_status", "candidate_offers", "status IN ('available','signed','retired')"
    )
