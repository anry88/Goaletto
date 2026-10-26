"""Persist new match injury periods without rewriting finished matches."""

import sqlalchemy as sa
from alembic import op

revision = "0059_match_injuries"
down_revision = "0058_supporter_development"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_injuries",
        sa.Column("match_id", sa.Uuid(), sa.ForeignKey("matches.id"), primary_key=True),
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("persons.id"), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("days", sa.SmallInteger(), nullable=False),
        sa.Column("minutes_played", sa.SmallInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unavailable_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_until", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "policy_version = 'match-injury-v1' AND days = 7", name="ck_match_injury_policy"
        ),
        sa.CheckConstraint("minutes_played BETWEEN 1 AND 120", name="ck_match_injury_minutes"),
        sa.CheckConstraint("source IN ('runtime','repair')", name="ck_match_injury_source"),
        sa.CheckConstraint(
            "unavailable_until = started_at + days * interval '1 day' AND applied_at >= started_at",
            name="ck_match_injury_dates",
        ),
    )
    op.create_index("ix_match_injuries_club_id", "match_injuries", ["club_id"])
    op.execute("""CREATE FUNCTION protect_match_injury() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Match injury history is immutable'; END $$""")
    op.execute(
        "CREATE TRIGGER match_injury_immutable BEFORE UPDATE OR DELETE ON match_injuries "
        "FOR EACH ROW EXECUTE FUNCTION protect_match_injury()"
    )


def downgrade() -> None:
    raise RuntimeError("Match injury history requires additive migrations")
