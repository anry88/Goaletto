"""Bound only earned positional experience and retain an immutable transition audit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063_percent_position_experience"
down_revision = "0062_teamwide_training"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE FUNCTION valid_percent_position_experience(experience jsonb)
    RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
      SELECT valid_position_experience(experience) AND NOT EXISTS (
        SELECT 1 FROM jsonb_each(experience) e WHERE e.value::text::numeric > 1000000
      ); $$""")
    op.add_column(
        "players",
        sa.Column(
            "position_experience_policy",
            sa.String(32),
            nullable=False,
            server_default="position-experience-v1",
        ),
    )
    op.create_table(
        "position_experience_transitions",
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("players.person_id"), primary_key=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("before_values", postgresql.JSONB(), nullable=False),
        sa.Column("after_values", postgresql.JSONB(), nullable=False),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "policy_version='position-experience-v2'", name="ck_position_transition_policy"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(before_values)='object' AND "
            "valid_percent_position_experience(after_values)",
            name="ck_position_transition_values",
        ),
    )
    op.execute("""INSERT INTO position_experience_transitions
      SELECT p.person_id,'position-experience-v2',p.position_experience,
        coalesce((SELECT jsonb_object_agg(e.key,least(e.value::text::numeric * 100,1000000)::bigint)
                  FROM jsonb_each(p.position_experience) e),'{}'::jsonb),CURRENT_TIMESTAMP
      FROM players p""")
    op.execute("""UPDATE players p SET position_experience=t.after_values,
      position_experience_policy='position-experience-v2'
      FROM position_experience_transitions t WHERE t.person_id=p.person_id""")
    op.alter_column(
        "players", "position_experience_policy", server_default="position-experience-v2"
    )
    op.create_check_constraint(
        "ck_player_experience_policy",
        "players",
        "position_experience_policy IN ('position-experience-v1','position-experience-v2')",
    )
    op.create_check_constraint(
        "ck_player_experience_percent",
        "players",
        "position_experience_policy <> 'position-experience-v2' OR "
        "valid_percent_position_experience(position_experience)",
    )
    op.execute("""CREATE FUNCTION protect_position_experience_transition() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Position experience transition is immutable'; END $$""")
    op.execute("""CREATE TRIGGER position_experience_transition_immutable BEFORE UPDATE OR DELETE
      ON position_experience_transitions FOR EACH ROW
      EXECUTE FUNCTION protect_position_experience_transition()""")
    op.execute("REVOKE ALL ON position_experience_transitions FROM PUBLIC")
    op.drop_constraint("ck_match_development_policy", "match_development", type_="check")
    op.create_check_constraint(
        "ck_match_development_policy",
        "match_development",
        "policy_version IN ('match-development-v1','match-practice-v2','match-practice-v3',"
        "'match-practice-v4','match-practice-v5')",
    )


def downgrade() -> None:
    raise RuntimeError("Percent experience and transition history require an additive migration")
