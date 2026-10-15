"""Freeze match practice terms while preserving all legacy appearance receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_match_practice"
down_revision = "0031_season_sponsors"
branch_labels = None
depends_on = None


def protect_terms(*, practice: bool) -> None:
    new_terms = ",NEW.terms_snapshot" if practice else ""
    old_terms = ",OLD.terms_snapshot" if practice else ""
    op.execute(f"""CREATE OR REPLACE FUNCTION protect_match_development() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' OR (OLD.applied_at IS NOT NULL AND NEW IS DISTINCT FROM OLD)
        THEN RAISE EXCEPTION 'Match development history is immutable'; END IF;
      IF ROW(NEW.match_id,NEW.policy_version,NEW.eligible,
        NEW.staff_snapshot,NEW.locked_at{new_terms})
        IS DISTINCT FROM ROW(OLD.match_id,OLD.policy_version,OLD.eligible,
        OLD.staff_snapshot,OLD.locked_at{old_terms})
        THEN RAISE EXCEPTION 'Match development terms are immutable'; END IF;
      RETURN NEW;
    END $$""")


def upgrade() -> None:
    op.add_column(
        "match_development",
        sa.Column("terms_snapshot", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.drop_constraint("ck_match_development_policy", "match_development", type_="check")
    op.create_check_constraint(
        "ck_match_development_policy",
        "match_development",
        "policy_version IN ('match-development-v1','match-practice-v2')",
    )
    op.create_check_constraint(
        "ck_match_development_terms",
        "match_development",
        "jsonb_typeof(terms_snapshot) = 'object' AND "
        "(policy_version = 'match-development-v1' OR ("
        "terms_snapshot ?& ARRAY['competition','fixture_id','season_id','plan_revision',"
        "'match_seed','friendly_skill_factor','friendly_experience_factor','players'] AND "
        "terms_snapshot->>'competition' IN ('league','cup','champions','friendly') AND "
        "jsonb_typeof(terms_snapshot->'players') = 'object' AND "
        "jsonb_typeof(terms_snapshot->'match_seed') = 'string' AND "
        "length(terms_snapshot->>'match_seed') > 0 AND "
        "(terms_snapshot->>'friendly_skill_factor')::int BETWEEN 1 AND 9999 AND "
        "(terms_snapshot->>'friendly_experience_factor')::int BETWEEN 1 AND 9999) IS TRUE)",
    )
    protect_terms(practice=True)


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM match_development WHERE policy_version='match-practice-v2')
        THEN RAISE EXCEPTION 'Pinned practice history prevents downgrade'; END IF;
    END $$""")
    protect_terms(practice=False)
    op.drop_constraint("ck_match_development_terms", "match_development", type_="check")
    op.drop_constraint("ck_match_development_policy", "match_development", type_="check")
    op.create_check_constraint(
        "ck_match_development_policy",
        "match_development",
        "policy_version='match-development-v1'",
    )
    op.drop_column("match_development", "terms_snapshot")
