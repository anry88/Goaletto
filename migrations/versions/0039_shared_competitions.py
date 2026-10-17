"""Shared champions eligibility and immutable tournament/allocation history."""

import sqlalchemy as sa
from alembic import op

revision = "0039_shared_competitions"
down_revision = "0038_academy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "season_contestants",
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), primary_key=True),
    )
    op.execute("INSERT INTO season_contestants SELECT season_id,club_id FROM season_clubs")
    op.execute("""CREATE FUNCTION register_season_contestant() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      INSERT INTO season_contestants VALUES (NEW.season_id,NEW.club_id) ON CONFLICT DO NOTHING;
      RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER tr_season_contestant AFTER INSERT ON season_clubs "
        "FOR EACH ROW EXECUTE FUNCTION register_season_contestant()"
    )
    for table, prefix in (("fixtures", "fixture"), ("match_results", "result")):
        for side in ("home", "away"):
            name = f"fk_{prefix}_{side}_season_club"
            op.drop_constraint(name, table, type_="foreignkey")
            op.create_foreign_key(
                name,
                table,
                "season_contestants",
                ["season_id", f"{side}_club_id"],
                ["season_id", "club_id"],
            )
    for table in ("season_allocation_receipts", "champions_draws", "champions_tournaments"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION protect_frozen_cup()"
        )
    op.create_table(
        "academy_intakes",
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), primary_key=True),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), primary_key=True),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("level BETWEEN 1 AND 5", name="ck_academy_intake_level"),
    )
    op.execute(
        "CREATE TRIGGER academy_intakes_immutable BEFORE UPDATE OR DELETE ON "
        "academy_intakes FOR EACH ROW EXECUTE FUNCTION protect_frozen_cup()"
    )
    op.execute("""CREATE FUNCTION protect_academy_candidate() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR (OLD.status<>'available' AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Academy history is immutable'; END IF;
      IF ROW(NEW.id,NEW.club_id,NEW.season_id,NEW.slot,NEW.policy_version,NEW.name,NEW.age,
             NEW.position,NEW.alternative_positions,NEW.skills,NEW.caps)
         IS DISTINCT FROM ROW(OLD.id,OLD.club_id,OLD.season_id,OLD.slot,OLD.policy_version,
             OLD.name,OLD.age,OLD.position,OLD.alternative_positions,OLD.skills,OLD.caps) THEN
        RAISE EXCEPTION 'Academy shortlist is immutable'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER academy_candidates_immutable BEFORE UPDATE OR DELETE ON "
        "academy_candidates FOR EACH ROW EXECUTE FUNCTION protect_academy_candidate()"
    )
    op.execute("""CREATE FUNCTION protect_friendly_invitation() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' OR (OLD.status<>'pending' AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Friendly booking history is immutable'; END IF;
      IF ROW(NEW.id,NEW.season_id,NEW.home_club_id,NEW.away_club_id,
             NEW.challenger_ownership_id,NEW.challenged_ownership_id,NEW.assigned_day,NEW.created_at)
         IS DISTINCT FROM ROW(OLD.id,OLD.season_id,OLD.home_club_id,OLD.away_club_id,
             OLD.challenger_ownership_id,OLD.challenged_ownership_id,OLD.assigned_day,OLD.created_at)
        THEN RAISE EXCEPTION 'Friendly invitation terms are immutable'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER friendly_invitations_immutable BEFORE UPDATE OR DELETE ON "
        "friendly_invitations FOR EACH ROW EXECUTE FUNCTION protect_friendly_invitation()"
    )
    # Champions ties use the same immutability rules as cup ties with their own terms.
    op.execute("""CREATE FUNCTION protect_champions_tie() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR (OLD.status='decided' AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Champions result is immutable'; END IF;
      IF ROW(NEW.id,NEW.tournament_id,NEW.season_id,NEW.round_number,NEW.tie_index,
          NEW.round_label,NEW.home_kind,NEW.home_tie_id,NEW.away_kind,NEW.away_tie_id,
          NEW.champions_cycles,NEW.neutral)
         IS DISTINCT FROM ROW(OLD.id,OLD.tournament_id,OLD.season_id,OLD.round_number,
          OLD.tie_index,OLD.round_label,OLD.home_kind,OLD.home_tie_id,OLD.away_kind,
          OLD.away_tie_id,OLD.champions_cycles,OLD.neutral)
        OR (OLD.home_kind='club' AND NEW.home_club_id IS DISTINCT FROM OLD.home_club_id)
        OR (OLD.away_kind='club' AND NEW.away_club_id IS DISTINCT FROM OLD.away_club_id)
        THEN RAISE EXCEPTION 'Champions draw is immutable'; END IF;
      IF (OLD.first_match_result IS NOT NULL
          AND NEW.first_match_result IS DISTINCT FROM OLD.first_match_result)
        OR (OLD.second_match_result IS NOT NULL
          AND NEW.second_match_result IS DISTINCT FROM OLD.second_match_result)
        OR (OLD.extra_time_result IS NOT NULL
          AND NEW.extra_time_result IS DISTINCT FROM OLD.extra_time_result)
        OR (OLD.shootout_result IS NOT NULL
          AND NEW.shootout_result IS DISTINCT FROM OLD.shootout_result)
        OR (OLD.status='in_progress' AND NEW.status='pending')
        THEN RAISE EXCEPTION 'Champions segment is immutable'; END IF;
      IF NEW.home_club_id IS DISTINCT FROM OLD.home_club_id AND NOT EXISTS
        (SELECT 1 FROM champions_ties t WHERE t.id=NEW.home_tie_id
          AND t.tournament_id=NEW.tournament_id AND t.status='decided'
          AND t.winner_club_id=NEW.home_club_id)
        THEN RAISE EXCEPTION 'Unresolved champions home feeder'; END IF;
      IF NEW.away_club_id IS DISTINCT FROM OLD.away_club_id AND NOT EXISTS
        (SELECT 1 FROM champions_ties t WHERE t.id=NEW.away_tie_id
          AND t.tournament_id=NEW.tournament_id AND t.status='decided'
          AND t.winner_club_id=NEW.away_club_id)
        THEN RAISE EXCEPTION 'Unresolved champions away feeder'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER champions_ties_immutable BEFORE UPDATE OR DELETE ON champions_ties "
        "FOR EACH ROW EXECUTE FUNCTION protect_champions_tie()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM champions_draws) OR
      EXISTS (SELECT 1 FROM season_allocation_receipts) OR
      EXISTS (SELECT 1 FROM academy_intakes) OR EXISTS (SELECT 1 FROM academy_candidates) OR
      EXISTS (SELECT 1 FROM friendly_invitations) THEN
      RAISE EXCEPTION 'Shared tournament history prevents downgrade'; END IF; END $$""")
    op.execute("DROP TRIGGER friendly_invitations_immutable ON friendly_invitations")
    op.execute("DROP FUNCTION protect_friendly_invitation()")
    op.execute("DROP TRIGGER academy_candidates_immutable ON academy_candidates")
    op.execute("DROP FUNCTION protect_academy_candidate()")
    op.drop_table("academy_intakes")
    op.execute("DROP TRIGGER champions_ties_immutable ON champions_ties")
    op.execute("DROP FUNCTION protect_champions_tie()")
    for table in ("season_allocation_receipts", "champions_draws", "champions_tournaments"):
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
    for table, prefix in (("fixtures", "fixture"), ("match_results", "result")):
        for side in ("home", "away"):
            name = f"fk_{prefix}_{side}_season_club"
            op.drop_constraint(name, table, type_="foreignkey")
            op.create_foreign_key(
                name,
                table,
                "season_clubs",
                ["season_id", f"{side}_club_id"],
                ["season_id", "club_id"],
            )
    op.execute("DROP TRIGGER tr_season_contestant ON season_clubs")
    op.execute("DROP FUNCTION register_season_contestant()")
    op.drop_table("season_contestants")
