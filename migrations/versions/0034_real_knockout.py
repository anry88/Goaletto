"""Real knockout jobs, immutable draws, booked neutral arenas and league guards."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034_real_knockout"
down_revision = "0033_cup_tournament_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_crowd_policy", "match_crowds", type_="check")
    op.create_check_constraint(
        "ck_crowd_policy",
        "match_crowds",
        "policy_version IN ('stadium-demand-v1','neutral-final-v1')",
    )
    op.alter_column("matches", "phase", type_=sa.String(32), existing_type=sa.String(16))
    op.drop_constraint("ck_recovery_tick", "match_recoveries", type_="check")
    op.create_check_constraint(
        "ck_recovery_tick", "match_recoveries", "checkpoint_tick BETWEEN 0 AND 40"
    )
    op.add_column("cup_brackets", sa.Column("entry_snapshot", postgresql.JSONB(), nullable=True))
    op.execute("""UPDATE cup_brackets b SET entry_snapshot=(
      SELECT jsonb_agg(jsonb_build_object('league_id',e.id,'tier',e.tier,'order',e.clubs)
        ORDER BY e.tier)
      FROM (SELECT l.id,l.tier,jsonb_agg(sc.club_id ORDER BY sc.tie_break_order,sc.club_id) clubs
      FROM season_clubs sc JOIN leagues l ON l.id=sc.league_id
      WHERE sc.season_id=b.season_id GROUP BY l.id,l.tier) e)""")
    op.alter_column("cup_brackets", "entry_snapshot", nullable=False)
    op.create_check_constraint(
        "ck_cup_bracket_policy",
        "cup_brackets",
        "policy_version = 'cup-bracket-v1' AND jsonb_typeof(entry_snapshot) = 'array' "
        "AND jsonb_array_length(entry_snapshot) = 5",
    )
    for table, prefix in [("fixtures", "fixture"), ("match_results", "result")]:
        for side in ("home", "away"):
            op.create_foreign_key(
                f"fk_{prefix}_{side}_membership",
                table,
                "season_clubs",
                ["season_id", f"{side}_club_id", "league_id"],
                ["season_id", "club_id", "league_id"],
            )
    op.drop_constraint("ck_cup_tie_winner", "cup_ties", type_="check")
    op.create_check_constraint(
        "ck_cup_tie_winner",
        "cup_ties",
        "(status = 'decided') = (winner_club_id IS NOT NULL) AND "
        "(status = 'decided') = (decided_at IS NOT NULL) AND "
        "(winner_club_id IS NULL OR (home_club_id IS NOT NULL AND away_club_id IS NOT NULL "
        "AND winner_club_id IN (home_club_id,away_club_id)))",
    )
    op.drop_constraint("ck_match_phase", "matches", type_="check")
    op.create_check_constraint(
        "ck_match_phase",
        "matches",
        "phase IN ('first_half','half_time','second_half','full_time','abandoned','paused',"
        "'extra_break','extra_first_half','extra_half_time','extra_second_half','penalties')",
    )
    op.drop_constraint("ck_command_target_tick", "accepted_commands", type_="check")
    op.create_check_constraint(
        "ck_command_target_tick",
        "accepted_commands",
        "target_tick_number IS NULL OR target_tick_number BETWEEN 1 AND 40",
    )
    op.alter_column("match_stages", "stage", type_=sa.String(80), existing_type=sa.String(16))
    op.drop_constraint("ck_match_stage", "match_stages", type_="check")
    op.create_check_constraint(
        "ck_match_stage",
        "match_stages",
        "stage IN ('locked','started','second_half','finished','abandoned','extra_first',"
        "'extra_second') OR stage ~ '^penalty:[1-9][0-9]*$'",
    )
    op.create_table(
        "neutral_final_bookings",
        sa.Column("fixture_id", sa.Uuid(), sa.ForeignKey("fixtures.id"), primary_key=True),
        sa.Column("venue_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("venue_name", sa.String(80), nullable=False),
        sa.Column("assigned_day", sa.Date(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("basis", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("venue_club_id", "assigned_day", name="uq_neutral_venue_day"),
        sa.CheckConstraint("jsonb_typeof(basis) = 'object'", name="ck_neutral_booking_basis"),
    )
    op.execute("""CREATE FUNCTION protect_frozen_cup() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Frozen cup history is immutable'; END IF;
      RETURN NEW;
    END $$""")
    for table in ("cup_brackets", "neutral_final_bookings"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION protect_frozen_cup()"
        )
    op.execute("""CREATE FUNCTION protect_cup_tie() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR (OLD.status='decided' AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Cup result is immutable'; END IF;
      IF ROW(NEW.id,NEW.season_id,NEW.round_key,NEW.round_label,NEW.home_kind,NEW.home_tie_id,
          NEW.away_kind,NEW.away_tie_id,NEW.cup_days,NEW.neutral)
        IS DISTINCT FROM ROW(OLD.id,OLD.season_id,OLD.round_key,OLD.round_label,
          OLD.home_kind,OLD.home_tie_id,OLD.away_kind,OLD.away_tie_id,OLD.cup_days,OLD.neutral)
        OR (OLD.home_kind='club' AND NEW.home_club_id IS DISTINCT FROM OLD.home_club_id)
        OR (OLD.away_kind='club' AND NEW.away_club_id IS DISTINCT FROM OLD.away_club_id)
        THEN RAISE EXCEPTION 'Cup draw is immutable'; END IF;
      IF (OLD.first_match_result IS NOT NULL
          AND NEW.first_match_result IS DISTINCT FROM OLD.first_match_result)
        OR (OLD.second_match_result IS NOT NULL
          AND NEW.second_match_result IS DISTINCT FROM OLD.second_match_result)
        OR (OLD.extra_time_result IS NOT NULL
          AND NEW.extra_time_result IS DISTINCT FROM OLD.extra_time_result)
        OR (OLD.shootout_result IS NOT NULL
          AND NEW.shootout_result IS DISTINCT FROM OLD.shootout_result)
        OR (OLD.status='in_progress' AND NEW.status='pending')
        THEN RAISE EXCEPTION 'Cup segments are immutable'; END IF;
      IF NEW.home_club_id IS DISTINCT FROM OLD.home_club_id AND NOT EXISTS
        (SELECT 1 FROM cup_ties t WHERE t.id=NEW.home_tie_id AND t.season_id=NEW.season_id
          AND t.status='decided' AND t.winner_club_id=NEW.home_club_id)
        THEN RAISE EXCEPTION 'Unresolved cup home feeder'; END IF;
      IF NEW.away_club_id IS DISTINCT FROM OLD.away_club_id AND NOT EXISTS
        (SELECT 1 FROM cup_ties t WHERE t.id=NEW.away_tie_id AND t.season_id=NEW.season_id
          AND t.status='decided' AND t.winner_club_id=NEW.away_club_id)
        THEN RAISE EXCEPTION 'Unresolved cup away feeder'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER cup_ties_immutable BEFORE UPDATE OR DELETE ON cup_ties "
        "FOR EACH ROW EXECUTE FUNCTION protect_cup_tie()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM matches WHERE engine_version='regulation-v4')
        OR EXISTS (SELECT 1 FROM neutral_final_bookings) THEN
        RAISE EXCEPTION 'Knockout history prevents downgrade'; END IF;
    END $$""")
    op.drop_constraint("ck_recovery_tick", "match_recoveries", type_="check")
    op.create_check_constraint(
        "ck_recovery_tick", "match_recoveries", "checkpoint_tick BETWEEN 0 AND 29"
    )
    op.drop_constraint("ck_crowd_policy", "match_crowds", type_="check")
    op.create_check_constraint(
        "ck_crowd_policy", "match_crowds", "policy_version = 'stadium-demand-v1'"
    )
    op.execute("DROP TRIGGER cup_ties_immutable ON cup_ties")
    op.execute("DROP FUNCTION protect_cup_tie()")
    op.execute("DROP TRIGGER cup_brackets_immutable ON cup_brackets")
    op.drop_table("neutral_final_bookings")
    op.execute("DROP FUNCTION protect_frozen_cup()")
    op.drop_constraint("ck_match_stage", "match_stages", type_="check")
    op.create_check_constraint(
        "ck_match_stage",
        "match_stages",
        "stage IN ('locked','started','second_half','finished','abandoned')",
    )
    op.alter_column("match_stages", "stage", type_=sa.String(16), existing_type=sa.String(80))
    op.drop_constraint("ck_command_target_tick", "accepted_commands", type_="check")
    op.create_check_constraint(
        "ck_command_target_tick",
        "accepted_commands",
        "target_tick_number IS NULL OR target_tick_number BETWEEN 1 AND 30",
    )
    op.drop_constraint("ck_match_phase", "matches", type_="check")
    op.create_check_constraint(
        "ck_match_phase",
        "matches",
        "phase IN ('first_half','half_time','second_half','full_time','abandoned','paused',"
        "'extra_first_half','extra_half_time','extra_second_half','penalties')",
    )
    op.drop_constraint("ck_cup_tie_winner", "cup_ties", type_="check")
    op.create_check_constraint(
        "ck_cup_tie_winner",
        "cup_ties",
        "(status = 'decided') = (winner_club_id IS NOT NULL AND decided_at IS NOT NULL)",
    )
    for table, prefix in [("fixtures", "fixture"), ("match_results", "result")]:
        for side in ("home", "away"):
            op.drop_constraint(f"fk_{prefix}_{side}_membership", table, type_="foreignkey")
    op.drop_constraint("ck_cup_bracket_policy", "cup_brackets", type_="check")
    op.drop_column("cup_brackets", "entry_snapshot")
    op.alter_column("matches", "phase", type_=sa.String(16), existing_type=sa.String(32))
