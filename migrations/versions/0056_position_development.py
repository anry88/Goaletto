"""Positional experience, specialist roles and versioned match practice."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0056_position_development"
down_revision = "0055_academy_prospects"
branch_labels = None
depends_on = None

ROLES = (
    "'head_coach','goalkeeping_coach','defence_coach','attack_coach',"
    "'fitness_coach','physiotherapist','scout','midfield_coach','wing_coach'"
)


def upgrade() -> None:
    op.execute("""CREATE FUNCTION valid_position_experience(experience jsonb)
    RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
      SELECT jsonb_typeof(experience) = 'object' AND NOT EXISTS (
        SELECT 1 FROM jsonb_each(experience) AS entry
        WHERE key NOT IN ('GK','DL','DC','DR','DM','ML','MC','MR','AMC','AML','AMR','FC')
          OR jsonb_typeof(value) <> 'number' OR value::text::numeric < 0
          OR value::text::numeric <> trunc(value::text::numeric)
      );
    $$""")
    op.add_column(
        "players",
        sa.Column("position_experience", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    # Preserve old general points/fractions on the primary position. This is a
    # documented carry-forward rule, never invented historical positional minutes.
    op.execute("""UPDATE players p SET position_experience = jsonb_build_object(
      p.position, p.experience::bigint * 10000 + coalesce((
        SELECT (d.progress->>'experience')::bigint FROM player_development d
        WHERE d.person_id = p.person_id), 0))""")
    op.create_check_constraint(
        "ck_player_position_experience", "players", "valid_position_experience(position_experience)"
    )
    for table, name, definition in (
        ("staff", "ck_staff_role", f"role IS NULL OR role IN ({ROLES})"),
        ("staff_qualifications", "ck_qualification_role", f"role IN ({ROLES})"),
        (
            "candidate_pools",
            "ck_candidate_pool_scope",
            "(kind = 'player' AND profile = 'player' AND target = 12) OR "
            f"(kind = 'staff' AND profile IN ({ROLES}) AND target = 2)",
        ),
        ("staff_changes", "ck_staff_change_role", f"role IN ({ROLES})"),
        ("salary_contracts", "ck_salary_role", f"role IN ('player',{ROLES})"),
        (
            "staff_careers",
            "ck_staff_career_qualification",
            f"role IN ({ROLES}) AND skill BETWEEN 1 AND 20",
        ),
        (
            "staff_careers",
            "ck_staff_career_policy",
            "policy_version IN ('retired-staff-v1','retired-staff-v2')",
        ),
        (
            "match_development",
            "ck_match_development_policy",
            "policy_version IN ('match-development-v1','match-practice-v2',"
            "'match-practice-v3','match-practice-v4')",
        ),
    ):
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, definition)


def downgrade() -> None:
    raise RuntimeError("Positional experience and staff history require an additive migration")
