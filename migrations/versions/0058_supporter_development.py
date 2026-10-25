"""Future supporter development and versioned match attendance; preserve history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058_supporter_development"
down_revision = "0057_active_form"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_crowd_policy", "match_crowds", type_="check")
    op.create_check_constraint(
        "ck_crowd_policy",
        "match_crowds",
        "policy_version IN ('stadium-demand-v1','neutral-final-v1',"
        "'stadium-demand-v2','neutral-final-v2')",
    )
    op.create_table(
        "supporter_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("match_id", sa.Uuid(), sa.ForeignKey("matches.id")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("before_count", sa.Integer(), nullable=False),
        sa.Column("after_count", sa.Integer(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("basis", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("club_id", "match_id", name="uq_supporter_match"),
        sa.CheckConstraint("policy_version = 'supporters-v1'", name="ck_supporter_policy"),
        sa.CheckConstraint(
            "before_count >= 0 AND after_count >= before_count", name="ck_supporter_counts"
        ),
        sa.CheckConstraint(
            "(kind = 'match' AND match_id IS NOT NULL) OR (kind = 'season' AND match_id IS NULL)",
            name="ck_supporter_source",
        ),
        sa.CheckConstraint("recorded_at >= effective_at", name="ck_supporter_dates"),
        sa.CheckConstraint("jsonb_typeof(basis) = 'object'", name="ck_supporter_basis"),
    )
    op.create_index(
        "uq_supporter_season",
        "supporter_changes",
        ["club_id", "season_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'season'"),
    )
    op.create_index(
        "ix_supporter_club_time", "supporter_changes", ["club_id", "effective_at", "id"]
    )
    op.execute("""CREATE FUNCTION protect_supporter_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Supporter history is immutable'; END $$""")
    op.execute(
        "CREATE TRIGGER supporter_change_immutable BEFORE UPDATE OR DELETE "
        "ON supporter_changes FOR EACH ROW EXECUTE FUNCTION protect_supporter_change()"
    )


def downgrade() -> None:
    raise RuntimeError("Supporter and attendance history require additive migrations")
