"""Durable champions-cup draws, tournaments and ties (single set, AND-126)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_champions_tournaments"
down_revision = "0034_real_knockout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "champions_draws",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("qualifying_season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("league_set_id", sa.Uuid(), sa.ForeignKey("league_sets.id"), nullable=False),
        sa.Column("cohort_key", sa.String(128), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("snapshots", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("season_id", name="uq_champions_draw_season"),
        sa.CheckConstraint(
            "policy_version = 'champions-groups-v1'", name="ck_champions_draw_policy"
        ),
        sa.CheckConstraint("seed >= 0", name="ck_champions_draw_seed"),
        sa.CheckConstraint(
            "jsonb_typeof(snapshots) = 'array' AND jsonb_array_length(snapshots) = 5",
            name="ck_champions_draw_snapshots",
        ),
    )
    op.create_table(
        "champions_tournaments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("qualifying_season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("group_index", sa.Integer(), nullable=False),
        sa.Column("participant_count", sa.Integer(), nullable=False),
        sa.Column("cohort_key", sa.String(128), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("bracket_seed", sa.BigInteger(), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("season_id", "tier", "group_index", name="uq_champions_tournament"),
        sa.CheckConstraint("tier BETWEEN 1 AND 5", name="ck_champions_tournament_tier"),
        sa.CheckConstraint("group_index >= 0", name="ck_champions_tournament_group"),
        sa.CheckConstraint(
            "participant_count IN (8,16,32,64,128,256,512)",
            name="ck_champions_tournament_size",
        ),
        sa.CheckConstraint(
            "policy_version = 'champions-bracket-v1'", name="ck_champions_tournament_policy"
        ),
        sa.CheckConstraint("seed >= 0 AND bracket_seed >= 0", name="ck_champions_tournament_seed"),
    )
    op.create_index("ix_champions_tournaments_season", "champions_tournaments", ["season_id"])
    op.create_table(
        "champions_ties",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tournament_id",
            sa.Uuid(),
            sa.ForeignKey("champions_tournaments.id"),
            nullable=False,
        ),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("round_number", sa.SmallInteger(), nullable=False),
        sa.Column("tie_index", sa.SmallInteger(), nullable=False),
        sa.Column("round_label", sa.String(40), nullable=False),
        sa.Column("home_kind", sa.String(16), nullable=False),
        sa.Column("home_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=True),
        sa.Column("home_tie_id", sa.Uuid(), sa.ForeignKey("champions_ties.id"), nullable=True),
        sa.Column("away_kind", sa.String(16), nullable=False),
        sa.Column("away_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=True),
        sa.Column("away_tie_id", sa.Uuid(), sa.ForeignKey("champions_ties.id"), nullable=True),
        sa.Column("champions_cycles", postgresql.JSONB(), nullable=False),
        sa.Column("neutral", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("winner_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=True),
        sa.Column("first_match_result", postgresql.JSONB(), nullable=True),
        sa.Column("second_match_result", postgresql.JSONB(), nullable=True),
        sa.Column("extra_time_result", postgresql.JSONB(), nullable=True),
        sa.Column("shootout_result", postgresql.JSONB(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(home_kind = 'club' AND home_club_id IS NOT NULL AND home_tie_id IS NULL) "
            "OR (home_kind = 'winner' AND home_tie_id IS NOT NULL)",
            name="ck_champions_tie_home_slot",
        ),
        sa.CheckConstraint(
            "(away_kind = 'club' AND away_club_id IS NOT NULL AND away_tie_id IS NULL) "
            "OR (away_kind = 'winner' AND away_tie_id IS NOT NULL)",
            name="ck_champions_tie_away_slot",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'decided')",
            name="ck_champions_tie_status",
        ),
        sa.CheckConstraint(
            "(status = 'decided') = (winner_club_id IS NOT NULL) AND "
            "(status = 'decided') = (decided_at IS NOT NULL) AND "
            "(winner_club_id IS NULL OR (home_club_id IS NOT NULL AND away_club_id IS NOT NULL "
            "AND winner_club_id IN (home_club_id,away_club_id)))",
            name="ck_champions_tie_winner",
        ),
    )
    op.create_index("ix_champions_ties_season", "champions_ties", ["season_id"])
    op.create_index("ix_champions_ties_tournament", "champions_ties", ["tournament_id"])


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM champions_ties WHERE status = 'decided') THEN
        RAISE EXCEPTION 'Decided champions ties prevent downgrade'; END IF;
    END $$""")
    op.drop_index("ix_champions_ties_tournament", table_name="champions_ties")
    op.drop_index("ix_champions_ties_season", table_name="champions_ties")
    op.drop_table("champions_ties")
    op.drop_index("ix_champions_tournaments_season", table_name="champions_tournaments")
    op.drop_table("champions_tournaments")
    op.drop_table("champions_draws")
