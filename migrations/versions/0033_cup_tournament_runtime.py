"""Cup tournament persistence, bracket progression and cross-competition fixtures.

Revision ID: 0033_cup_tournament_runtime
Revises: 0032_match_practice
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_cup_tournament_runtime"
down_revision = "0032_match_practice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create cup_brackets table
    op.create_table(
        "cup_brackets",
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), primary_key=True),
        sa.Column("league_set_id", sa.Uuid(), sa.ForeignKey("league_sets.id"), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False, server_default="cup-bracket-v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("seed >= 0", name="ck_cup_bracket_seed"),
    )

    # 2. Create cup_ties table
    op.create_table(
        "cup_ties",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("round_key", sa.String(40), nullable=False),
        sa.Column("round_label", sa.String(80), nullable=False),
        sa.Column("home_kind", sa.String(16), nullable=False),
        sa.Column("home_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=True),
        sa.Column("home_tie_id", sa.Uuid(), sa.ForeignKey("cup_ties.id"), nullable=True),
        sa.Column("away_kind", sa.String(16), nullable=False),
        sa.Column("away_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=True),
        sa.Column("away_tie_id", sa.Uuid(), sa.ForeignKey("cup_ties.id"), nullable=True),
        sa.Column("cup_days", postgresql.JSONB(), nullable=False),
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
            name="ck_cup_tie_home_slot",
        ),
        sa.CheckConstraint(
            "(away_kind = 'club' AND away_club_id IS NOT NULL AND away_tie_id IS NULL) "
            "OR (away_kind = 'winner' AND away_tie_id IS NOT NULL)",
            name="ck_cup_tie_away_slot",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'decided')",
            name="ck_cup_tie_status",
        ),
        sa.CheckConstraint(
            "(status = 'decided') = (winner_club_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_cup_tie_winner",
        ),
    )
    op.create_index("ix_cup_ties_season", "cup_ties", ["season_id"])

    # 3. Update fixtures table
    op.add_column("fixtures", sa.Column("competition", sa.String(16), nullable=False, server_default="league"))
    op.add_column("fixtures", sa.Column("stage", sa.String(80), nullable=True))
    op.add_column("fixtures", sa.Column("leg", sa.SmallInteger(), nullable=True))
    op.add_column("fixtures", sa.Column("neutral", sa.Boolean(), nullable=False, server_default="false"))
    op.alter_column("fixtures", "league_id", nullable=True)

    # Replace fixture constraints
    op.drop_constraint("fk_fixture_home_membership", "fixtures", type_="foreignkey")
    op.drop_constraint("fk_fixture_away_membership", "fixtures", type_="foreignkey")
    op.create_foreign_key(
        "fk_fixture_home_season_club",
        "fixtures",
        "season_clubs",
        ["season_id", "home_club_id"],
        ["season_id", "club_id"],
    )
    op.create_foreign_key(
        "fk_fixture_away_season_club",
        "fixtures",
        "season_clubs",
        ["season_id", "away_club_id"],
        ["season_id", "club_id"],
    )

    op.drop_constraint("uq_fixture_pair", "fixtures", type_="unique")
    op.create_index(
        "uq_fixture_league_pair",
        "fixtures",
        ["season_id", "home_club_id", "away_club_id"],
        unique=True,
        postgresql_where=sa.text("competition = 'league'"),
    )
    op.create_index(
        "uq_fixture_competition_leg",
        "fixtures",
        ["season_id", "competition", "stage", sa.text("COALESCE(leg, 0)"), "home_club_id", "away_club_id"],
        unique=True,
        postgresql_where=sa.text("competition <> 'league'"),
    )

    op.create_check_constraint(
        "ck_fixture_competition",
        "fixtures",
        "competition IN ('league', 'cup', 'champions', 'friendly')",
    )
    op.create_check_constraint(
        "ck_fixture_league",
        "fixtures",
        "competition <> 'league' OR league_id IS NOT NULL",
    )

    # 4. Update match_results table
    op.add_column("match_results", sa.Column("competition", sa.String(16), nullable=False, server_default="league"))
    op.alter_column("match_results", "league_id", nullable=True)

    op.drop_constraint("fk_result_home_membership", "match_results", type_="foreignkey")
    op.drop_constraint("fk_result_away_membership", "match_results", type_="foreignkey")
    op.create_foreign_key(
        "fk_result_home_season_club",
        "match_results",
        "season_clubs",
        ["season_id", "home_club_id"],
        ["season_id", "club_id"],
    )
    op.create_foreign_key(
        "fk_result_away_season_club",
        "match_results",
        "season_clubs",
        ["season_id", "away_club_id"],
        ["season_id", "club_id"],
    )

    op.create_check_constraint(
        "ck_result_competition",
        "match_results",
        "competition IN ('league', 'cup', 'champions', 'friendly')",
    )
    op.create_check_constraint(
        "ck_result_league",
        "match_results",
        "competition <> 'league' OR league_id IS NOT NULL",
    )

    # 5. Extend matches check constraints for extra time
    op.drop_constraint("ck_match_tick", "matches", type_="check")
    op.create_check_constraint("ck_match_tick", "matches", "completed_tick BETWEEN 0 AND 40")

    op.drop_constraint("ck_match_phase", "matches", type_="check")
    op.create_check_constraint(
        "ck_match_phase",
        "matches",
        "phase IN ('first_half','half_time','second_half','full_time','abandoned','paused',"
        "'extra_first_half','extra_half_time','extra_second_half','penalties')",
    )

    op.drop_constraint("ck_match_full_time", "matches", type_="check")
    op.create_check_constraint(
        "ck_match_full_time",
        "matches",
        "phase <> 'full_time' OR completed_tick IN (30, 40)",
    )

    # 6. Extend match_ticks check constraint
    op.drop_constraint("ck_tick_number", "match_ticks", type_="check")
    op.create_check_constraint("ck_tick_number", "match_ticks", "tick_number BETWEEN 1 AND 40")


def downgrade() -> None:
    # Check if any cup data exists before downgrade
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM fixtures WHERE competition <> 'league')
         OR EXISTS (SELECT 1 FROM cup_brackets)
         OR EXISTS (SELECT 1 FROM matches WHERE completed_tick > 30) THEN
        RAISE EXCEPTION 'Cup tournament history prevents downgrade';
      END IF;
    END $$""")

    op.drop_constraint("ck_tick_number", "match_ticks", type_="check")
    op.create_check_constraint("ck_tick_number", "match_ticks", "tick_number BETWEEN 1 AND 30")

    op.drop_constraint("ck_match_full_time", "matches", type_="check")
    op.create_check_constraint("ck_match_full_time", "matches", "phase <> 'full_time' OR completed_tick = 30")

    op.drop_constraint("ck_match_phase", "matches", type_="check")
    op.create_check_constraint(
        "ck_match_phase",
        "matches",
        "phase IN ('first_half','half_time','second_half','full_time','abandoned','paused')",
    )

    op.drop_constraint("ck_match_tick", "matches", type_="check")
    op.create_check_constraint("ck_match_tick", "matches", "completed_tick BETWEEN 0 AND 30")

    op.drop_constraint("ck_result_league", "match_results", type_="check")
    op.drop_constraint("ck_result_competition", "match_results", type_="check")
    op.drop_constraint("fk_result_away_season_club", "match_results", type_="foreignkey")
    op.drop_constraint("fk_result_home_season_club", "match_results", type_="foreignkey")
    op.create_foreign_key(
        "fk_result_home_membership",
        "match_results",
        "season_clubs",
        ["season_id", "home_club_id", "league_id"],
        ["season_id", "club_id", "league_id"],
    )
    op.create_foreign_key(
        "fk_result_away_membership",
        "match_results",
        "season_clubs",
        ["season_id", "away_club_id", "league_id"],
        ["season_id", "club_id", "league_id"],
    )
    op.alter_column("match_results", "league_id", nullable=False)
    op.drop_column("match_results", "competition")

    op.drop_constraint("ck_fixture_league", "fixtures", type_="check")
    op.drop_constraint("ck_fixture_competition", "fixtures", type_="check")
    op.drop_index("uq_fixture_competition_leg", table_name="fixtures")
    op.drop_index("uq_fixture_league_pair", table_name="fixtures")
    op.create_unique_constraint("uq_fixture_pair", "fixtures", ["season_id", "home_club_id", "away_club_id"])

    op.drop_constraint("fk_fixture_away_season_club", "fixtures", type_="foreignkey")
    op.drop_constraint("fk_fixture_home_season_club", "fixtures", type_="foreignkey")
    op.create_foreign_key(
        "fk_fixture_home_membership",
        "fixtures",
        "season_clubs",
        ["season_id", "home_club_id", "league_id"],
        ["season_id", "club_id", "league_id"],
    )
    op.create_foreign_key(
        "fk_fixture_away_membership",
        "fixtures",
        "season_clubs",
        ["season_id", "away_club_id", "league_id"],
        ["season_id", "club_id", "league_id"],
    )
    op.alter_column("fixtures", "league_id", nullable=False)
    op.drop_column("fixtures", "neutral")
    op.drop_column("fixtures", "leg")
    op.drop_column("fixtures", "stage")
    op.drop_column("fixtures", "competition")

    op.drop_table("cup_ties")
    op.drop_table("cup_brackets")
