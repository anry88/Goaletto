"""Private world admission, proactive stock and durable entry dedupe."""

import sqlalchemy as sa
from alembic import op

revision = "0040_world_admission"
down_revision = "0039_shared_competitions"
branch_labels = None
depends_on = None

OLD_ACTIONS = (
    "'club_claimed','club_opened','club_preview_opened','lineup_opened','lineup_saved',"
    "'match_command_accepted','match_opened','match_report_opened','next_fixture_opened',"
    "'notification_preferences','training_opened','training_plan_saved','language_preferences',"
    "'finances_opened','stadium_opened','stadium_expansion_ordered','market_opened',"
    "'market_listing_created','market_bid_accepted','market_listing_cancelled','candidate_signed',"
    "'scout_search_started','scout_search_cancelled','academy_opened','academy_upgraded',"
    "'academy_candidate_signed','friendly_opened','friendly_scheduled','friendly_invited',"
    "'friendly_responded'"
)


def upgrade() -> None:
    op.create_table(
        "world_admissions",
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), primary_key=True),
        sa.Column("league_set_id", sa.Uuid(), sa.ForeignKey("league_sets.id"), nullable=False),
        sa.Column("reference_season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=True),
        sa.Column("replay_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("league_set_id", name="uq_admission_set"),
        sa.CheckConstraint("replay_from <= requested_at", name="ck_admission_replay"),
        sa.CheckConstraint(
            "ready_at IS NULL OR ready_at >= requested_at", name="ck_admission_ready"
        ),
    )
    op.execute("""CREATE FUNCTION guard_world_admission() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME = 'world_admissions' THEN
        IF TG_OP = 'INSERT' THEN
          IF NEW.ready_at IS NOT NULL OR EXISTS (
            SELECT 1 FROM clubs c JOIN leagues l ON l.id = c.league_id
            WHERE l.league_set_id = NEW.league_set_id AND c.owner_telegram_id IS NOT NULL)
            OR EXISTS (SELECT 1 FROM ownership_periods o JOIN clubs c ON c.id = o.club_id
              JOIN leagues l ON l.id = c.league_id WHERE l.league_set_id = NEW.league_set_id)
          THEN RAISE EXCEPTION 'Historical replay requires a new unowned set'; END IF;
          IF NOT EXISTS (SELECT 1 FROM seasons s WHERE s.id=NEW.season_id
            AND s.league_set_id=NEW.league_set_id AND NOT EXISTS (
              SELECT 1 FROM seasons older WHERE older.league_set_id=s.league_set_id
              AND older.id<>s.id))
          THEN RAISE EXCEPTION 'Historical replay requires the initial season'; END IF;
          IF NEW.reference_season_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM seasons s JOIN seasons reference ON reference.id=NEW.reference_season_id
            WHERE s.id=NEW.season_id AND s.cycle_epoch=reference.cycle_epoch
              AND s.number=reference.number AND s.ends_at=reference.ends_at
              AND s.league_set_id<>reference.league_set_id)
          THEN RAISE EXCEPTION 'Admission calendars must agree'; END IF;
          RETURN NEW;
        END IF;
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'World admission history is durable'; END IF;
        IF OLD.season_id IS DISTINCT FROM NEW.season_id
          OR OLD.league_set_id IS DISTINCT FROM NEW.league_set_id
          OR OLD.reference_season_id IS DISTINCT FROM NEW.reference_season_id
          OR OLD.replay_from IS DISTINCT FROM NEW.replay_from
          OR OLD.requested_at IS DISTINCT FROM NEW.requested_at
          OR (OLD.ready_at IS NOT NULL AND OLD.ready_at IS DISTINCT FROM NEW.ready_at)
        THEN RAISE EXCEPTION 'World admission terms are immutable'; END IF;
        RETURN NEW;
      END IF;
      IF TG_TABLE_NAME = 'clubs' THEN
        IF NEW.owner_telegram_id IS NULL THEN RETURN NEW; END IF;
        IF EXISTS (SELECT 1 FROM world_admissions a JOIN leagues l
          ON l.league_set_id = a.league_set_id
          WHERE l.id = NEW.league_id AND a.ready_at IS NULL)
        THEN RAISE EXCEPTION 'Club is still preparing its bot history'; END IF;
      ELSE
        IF EXISTS (SELECT 1 FROM world_admissions a JOIN leagues l
          ON l.league_set_id = a.league_set_id JOIN clubs c ON c.league_id = l.id
          WHERE c.id = NEW.club_id AND a.ready_at IS NULL)
        THEN RAISE EXCEPTION 'Club is still preparing its bot history'; END IF;
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER tr_admission_frozen BEFORE INSERT OR UPDATE OR DELETE ON world_admissions "
        "FOR EACH ROW EXECUTE FUNCTION guard_world_admission()"
    )
    op.execute(
        "CREATE TRIGGER tr_club_entry_ready BEFORE UPDATE OF owner_telegram_id ON clubs "
        "FOR EACH ROW EXECUTE FUNCTION guard_world_admission()"
    )
    op.execute(
        "CREATE TRIGGER tr_ownership_entry_ready BEFORE INSERT ON ownership_periods "
        "FOR EACH ROW EXECUTE FUNCTION guard_world_admission()"
    )
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint(
        "ck_action_kind", "game_action_receipts", f"kind IN ({OLD_ACTIONS},'entry_opened')"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM world_admissions) OR EXISTS "
        "(SELECT 1 FROM game_action_receipts WHERE kind='entry_opened') THEN "
        "RAISE EXCEPTION 'World admission history prevents downgrade'; END IF; END $$"
    )
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", f"kind IN ({OLD_ACTIONS})")
    op.execute("DROP TRIGGER tr_ownership_entry_ready ON ownership_periods")
    op.execute("DROP TRIGGER tr_club_entry_ready ON clubs")
    op.drop_table("world_admissions")
    op.execute("DROP FUNCTION guard_world_admission()")
