"""Persistent next-offseason academy prospects and monotonic unsigned upgrades."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055_academy_prospects"
down_revision = "0054_form_ten"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academy_prospects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("position", sa.String(3), nullable=False),
        sa.Column("alternative_positions", postgresql.ARRAY(sa.String(3)), nullable=False),
        sa.Column("skills", postgresql.JSONB(), nullable=False),
        sa.Column("caps", postgresql.JSONB(), nullable=False),
        sa.Column("graduation_season_id", sa.Uuid(), sa.ForeignKey("seasons.id")),
        sa.UniqueConstraint("club_id", "season_id", "slot", name="uq_academy_prospect_slot"),
        sa.CheckConstraint("level BETWEEN 1 AND 5", name="ck_academy_prospect_level"),
        sa.CheckConstraint("slot BETWEEN 1 AND 5", name="ck_academy_prospect_slot"),
        sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="ck_academy_prospect_country"),
        sa.CheckConstraint(
            "position IN ('GK','DL','DC','DR','DM','ML','MC','MR','AMC','AML','AMR','FC')",
            name="ck_academy_prospect_position",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(skills) = 'object' AND jsonb_typeof(caps) = 'object'",
            name="ck_academy_prospect_profiles",
        ),
        sa.CheckConstraint(
            "graduation_season_id IS NULL OR graduation_season_id <> season_id",
            name="ck_academy_prospect_graduation",
        ),
    )
    op.create_index("ix_academy_prospects_season_id", "academy_prospects", ["season_id"])
    op.execute("""CREATE FUNCTION academy_profile_improves(
      old_skills jsonb, new_skills jsonb, old_caps jsonb, new_caps jsonb
    ) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
      SELECT NOT EXISTS (
        SELECT 1 FROM jsonb_each(old_skills) o FULL JOIN jsonb_each(new_skills) n USING (key)
        WHERE o.key IS NULL OR n.key IS NULL OR jsonb_typeof(n.value) <> 'number'
          OR n.value::text::integer < o.value::text::integer
          OR n.value::text::integer > 20
      ) AND NOT EXISTS (
        SELECT 1 FROM jsonb_each(old_caps) o FULL JOIN jsonb_each(new_caps) n USING (key)
        WHERE o.key IS NULL OR n.key IS NULL OR jsonb_typeof(n.value) <> 'number'
          OR n.value::text::integer < o.value::text::integer
          OR n.value::text::integer < (new_skills->>n.key)::integer
          OR n.value::text::integer > 20
      );
    $$""")
    op.execute("""CREATE FUNCTION protect_academy_prospect() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP = 'DELETE' OR
        (OLD.graduation_season_id IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Academy prospect history is immutable'; END IF;
      IF ROW(NEW.id,NEW.club_id,NEW.season_id,NEW.slot,NEW.name,NEW.country_code,
             NEW.position,NEW.alternative_positions)
        IS DISTINCT FROM ROW(OLD.id,OLD.club_id,OLD.season_id,OLD.slot,OLD.name,OLD.country_code,
             OLD.position,OLD.alternative_positions) OR NEW.level < OLD.level THEN
        RAISE EXCEPTION 'Academy prospect identity is immutable'; END IF;
      IF ROW(NEW.skills,NEW.caps) IS DISTINCT FROM ROW(OLD.skills,OLD.caps) AND
        (NEW.level <= OLD.level OR NOT academy_profile_improves(
          OLD.skills,NEW.skills,OLD.caps,NEW.caps)) THEN
        RAISE EXCEPTION 'Academy prospects only improve with a level upgrade'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER academy_prospects_immutable BEFORE UPDATE OR DELETE ON "
        "academy_prospects FOR EACH ROW EXECUTE FUNCTION protect_academy_prospect()"
    )
    op.execute(
        "CREATE TRIGGER tr_production_reset BEFORE TRUNCATE ON academy_prospects "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_production_reset()"
    )
    op.execute("DROP TRIGGER academy_intakes_immutable ON academy_intakes")
    op.execute("""CREATE FUNCTION protect_academy_intake() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP = 'DELETE' OR NEW.club_id <> OLD.club_id OR NEW.season_id <> OLD.season_id
         OR NEW.level < OLD.level OR (NEW.level <> OLD.level AND
           (SELECT status FROM seasons WHERE id = OLD.season_id) <> 'scheduled') THEN
        RAISE EXCEPTION 'Academy intake history is immutable'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER academy_intakes_immutable BEFORE UPDATE OR DELETE ON "
        "academy_intakes FOR EACH ROW EXECUTE FUNCTION protect_academy_intake()"
    )
    op.execute("""CREATE OR REPLACE FUNCTION protect_academy_candidate() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP = 'DELETE' OR (OLD.status <> 'available' AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Academy history is immutable'; END IF;
      IF ROW(NEW.id,NEW.club_id,NEW.season_id,NEW.slot,NEW.policy_version,NEW.name,NEW.age,
             NEW.country_code,NEW.position,NEW.alternative_positions)
        IS DISTINCT FROM ROW(OLD.id,OLD.club_id,OLD.season_id,OLD.slot,OLD.policy_version,
             OLD.name,OLD.age,OLD.country_code,OLD.position,OLD.alternative_positions) THEN
        RAISE EXCEPTION 'Academy shortlist identity is immutable'; END IF;
      IF ROW(NEW.skills,NEW.caps) IS DISTINCT FROM ROW(OLD.skills,OLD.caps) AND
        (NEW.status <> 'available' OR
         (SELECT status FROM seasons WHERE id = OLD.season_id) <> 'scheduled' OR
         NOT academy_profile_improves(OLD.skills,NEW.skills,OLD.caps,NEW.caps)) THEN
        RAISE EXCEPTION 'Only unsigned academy graduates may improve'; END IF;
      RETURN NEW;
    END $$""")


def downgrade() -> None:
    raise RuntimeError("Academy prospects and signed history require an additive migration")
