"""Game foundation. Frozen PostgreSQL DDL; no imports of mutable ORM models.

No seed, gameplay or analytics collection is executed by this migration.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_game_foundation"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clubs", sa.Column("kit_primary", sa.String(7), nullable=False, server_default="#743D42")
    )
    op.add_column(
        "clubs", sa.Column("kit_secondary", sa.String(7), nullable=False, server_default="#F2E9D8")
    )
    op.add_column(
        "clubs", sa.Column("kit_version", sa.Integer(), nullable=False, server_default="1")
    )
    op.create_check_constraint(
        "ck_club_primary_color", "clubs", "kit_primary ~ '^#[0-9A-Fa-f]{6}$'"
    )
    op.create_check_constraint(
        "ck_club_secondary_color", "clubs", "kit_secondary ~ '^#[0-9A-Fa-f]{6}$'"
    )
    op.create_check_constraint("ck_club_kit_version", "clubs", "kit_version > 0")
    op.create_unique_constraint("uq_league_id_set", "leagues", ["id", "league_set_id"])
    op.execute(
        sa.text(
            """
CREATE TABLE appearance_identities (
id VARCHAR(100) NOT NULL,
origin_bank VARCHAR(16) NOT NULL,
catalog_version INTEGER NOT NULL,
PRIMARY KEY (id),
CONSTRAINT ck_appearance_bank CHECK (origin_bank IN ('players', 'staff')),
CONSTRAINT ck_appearance_catalog CHECK (catalog_version > 0)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE INDEX ix_appearance_identities_origin_bank ON appearance_identities (origin_bank)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE managers (
id UUID NOT NULL,
telegram_id BIGINT NOT NULL,
locale VARCHAR(16) DEFAULT 'ru' NOT NULL,
is_internal BOOLEAN DEFAULT 'false' NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
first_game_start_at TIMESTAMP WITH TIME ZONE,
PRIMARY KEY (id),
CONSTRAINT ck_manager_telegram_id CHECK (telegram_id > 0),
UNIQUE (telegram_id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE analytics_managers (
id UUID NOT NULL,
manager_id UUID NOT NULL,
created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
PRIMARY KEY (id),
UNIQUE (manager_id),
FOREIGN KEY(manager_id) REFERENCES managers (id) ON DELETE CASCADE
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE appearance_variants (
id VARCHAR(100) NOT NULL,
identity_id VARCHAR(100) NOT NULL,
bank VARCHAR(16) NOT NULL,
stage VARCHAR(32),
asset_path VARCHAR(240) NOT NULL,
mask_path VARCHAR(240) NOT NULL,
PRIMARY KEY (id),
CONSTRAINT uq_variant_identity UNIQUE (id, identity_id),
CONSTRAINT ck_variant_bank CHECK (bank IN ('players', 'staff')),
FOREIGN KEY(identity_id) REFERENCES appearance_identities (id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE INDEX ix_appearance_variants_identity_id ON appearance_variants (identity_id)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE seasons (
id UUID NOT NULL,
league_set_id UUID NOT NULL,
number INTEGER NOT NULL,
status VARCHAR(16) NOT NULL,
seed BIGINT NOT NULL,
starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
rollover_completed_at TIMESTAMP WITH TIME ZONE,
PRIMARY KEY (id),
CONSTRAINT uq_season_set_number UNIQUE (league_set_id, number),
CONSTRAINT uq_season_id_set UNIQUE (id, league_set_id),
CONSTRAINT ck_season_number CHECK (number > 0),
CONSTRAINT ck_season_status CHECK (status IN ('draft', 'scheduled', 'active', 'finished',
'rolled_over')),
CONSTRAINT ck_season_dates CHECK (ends_at > starts_at),
CONSTRAINT ck_season_rollover CHECK ((status = 'rolled_over') = (rollover_completed_at IS NOT
NULL)),
FOREIGN KEY(league_set_id) REFERENCES league_sets (id)
)
            """
        )
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_seasons_league_set_id ON seasons (league_set_id)
            """)
    )
    op.execute(
        sa.text(
            """
CREATE UNIQUE INDEX uq_season_active_set ON seasons (league_set_id) WHERE status = 'active'
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE persons (
id UUID NOT NULL,
creation_key VARCHAR(200) NOT NULL,
name VARCHAR(120) NOT NULL,
age SMALLINT NOT NULL,
career VARCHAR(16) NOT NULL,
appearance_identity_id VARCHAR(100) NOT NULL,
appearance_variant_id VARCHAR(100) NOT NULL,
home_league_set_id UUID,
age_applied_for_season_id UUID,
PRIMARY KEY (id),
CONSTRAINT fk_person_same_face_variant FOREIGN KEY(appearance_variant_id,
appearance_identity_id) REFERENCES appearance_variants (id, identity_id),
CONSTRAINT ck_person_age CHECK (age BETWEEN 0 AND 150),
CONSTRAINT ck_person_career CHECK (career IN ('player', 'staff', 'retired')),
UNIQUE (creation_key),
UNIQUE (appearance_identity_id),
FOREIGN KEY(appearance_identity_id) REFERENCES appearance_identities (id),
FOREIGN KEY(home_league_set_id) REFERENCES league_sets (id),
FOREIGN KEY(age_applied_for_season_id) REFERENCES seasons (id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE club_lineups (
club_id UUID NOT NULL,
formation VARCHAR(32) NOT NULL,
version INTEGER NOT NULL,
tactics_version INTEGER NOT NULL,
tactics JSONB NOT NULL,
PRIMARY KEY (club_id),
CONSTRAINT ck_lineup_versions CHECK (version > 0 AND tactics_version > 0),
FOREIGN KEY(club_id) REFERENCES clubs (id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE ownership_periods (
id UUID NOT NULL,
operation_key VARCHAR(200) NOT NULL,
club_id UUID NOT NULL,
manager_id UUID NOT NULL,
claimed_at TIMESTAMP WITH TIME ZONE NOT NULL,
last_game_action_at TIMESTAMP WITH TIME ZONE NOT NULL,
expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
ended_at TIMESTAMP WITH TIME ZONE,
end_reason VARCHAR(16),
PRIMARY KEY (id),
CONSTRAINT uq_ownership_identity UNIQUE (id, manager_id, club_id),
CONSTRAINT ck_ownership_last_action CHECK (last_game_action_at >= claimed_at),
CONSTRAINT ck_ownership_deadline CHECK (expires_at > last_game_action_at),
CONSTRAINT ck_ownership_calendar_deadline CHECK (expires_at = ((last_game_action_at AT TIME
ZONE 'UTC') + INTERVAL '6 months') AT TIME ZONE 'UTC'),
CONSTRAINT ck_ownership_end CHECK (ended_at IS NULL OR ended_at >= claimed_at),
CONSTRAINT ck_ownership_end_reason CHECK ((ended_at IS NULL AND end_reason IS NULL) OR
(ended_at IS NOT NULL AND end_reason IS NOT NULL AND end_reason IN ('inactive', 'operator',
'voluntary'))),
UNIQUE (operation_key),
FOREIGN KEY(club_id) REFERENCES clubs (id),
FOREIGN KEY(manager_id) REFERENCES managers (id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE INDEX ix_ownership_expiry ON ownership_periods (expires_at) WHERE ended_at IS NULL
            """
        )
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_ownership_periods_club_id ON ownership_periods (club_id)
            """)
    )
    op.execute(
        sa.text(
            """
CREATE INDEX ix_ownership_periods_manager_id ON ownership_periods (manager_id)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE UNIQUE INDEX uq_ownership_active_club ON ownership_periods (club_id) WHERE ended_at IS
NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE UNIQUE INDEX uq_ownership_active_manager ON ownership_periods (manager_id) WHERE
ended_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE person_season_events (
id UUID NOT NULL,
person_id UUID NOT NULL,
season_id UUID NOT NULL,
stage VARCHAR(32) NOT NULL,
age SMALLINT NOT NULL,
policy_version VARCHAR(80) NOT NULL,
probability_bps SMALLINT,
draw SMALLINT,
is_last_season BOOLEAN,
related_person_id UUID,
occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
PRIMARY KEY (id),
CONSTRAINT uq_person_season_stage UNIQUE (person_id, season_id, stage),
CONSTRAINT ck_person_event_stage CHECK (stage IN
('age_applied','player_last_decision','player_retired','staff_last_decision','staff_retired','career_assigned','youth_replacement')),
CONSTRAINT ck_person_event_age CHECK (age BETWEEN 0 AND 150),
CONSTRAINT ck_person_event_probability CHECK (probability_bps IS NULL OR probability_bps
BETWEEN 0 AND 10000),
CONSTRAINT ck_person_event_draw CHECK (draw IS NULL OR draw BETWEEN 0 AND 9999),
CONSTRAINT ck_person_event_decision CHECK (stage NOT IN
('player_last_decision','staff_last_decision') OR (is_last_season IS NOT NULL AND
probability_bps IS NOT NULL)),
FOREIGN KEY(person_id) REFERENCES persons (id),
FOREIGN KEY(season_id) REFERENCES seasons (id),
FOREIGN KEY(related_person_id) REFERENCES persons (id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE INDEX ix_person_season_events_person_id ON person_season_events (person_id)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE players (
person_id UUID NOT NULL,
club_id UUID,
position VARCHAR(3) NOT NULL,
alternative_positions VARCHAR(3)[] NOT NULL,
goalkeeping SMALLINT NOT NULL,
defending SMALLINT NOT NULL,
playmaking SMALLINT NOT NULL,
winger SMALLINT NOT NULL,
attacking SMALLINT NOT NULL,
set_pieces SMALLINT NOT NULL,
stamina SMALLINT NOT NULL,
pace SMALLINT NOT NULL,
leadership SMALLINT NOT NULL,
loyalty SMALLINT NOT NULL,
form SMALLINT NOT NULL,
experience INTEGER NOT NULL,
morale SMALLINT NOT NULL,
status VARCHAR(16) NOT NULL,
last_season_id UUID,
retired_after_season_id UUID,
injured_until TIMESTAMP WITH TIME ZONE,
PRIMARY KEY (person_id),
CONSTRAINT uq_player_person_club UNIQUE (person_id, club_id),
CONSTRAINT ck_player_position CHECK (position IN
('GK','DL','DC','DR','DM','ML','MC','MR','AMC','AML','AMR','FC')),
CONSTRAINT ck_player_alternatives CHECK (cardinality(alternative_positions) <= 2 AND
alternative_positions <@
ARRAY['GK','DL','DC','DR','DM','ML','MC','MR','AMC','AML','AMR','FC']::varchar[] AND NOT
position = ANY(alternative_positions) AND array_position(alternative_positions, NULL) IS NULL
AND (cardinality(alternative_positions) < 2 OR alternative_positions[1] <>
alternative_positions[2])),
CONSTRAINT ck_player_goalkeeping CHECK (goalkeeping BETWEEN 1 AND 20),
CONSTRAINT ck_player_defending CHECK (defending BETWEEN 1 AND 20),
CONSTRAINT ck_player_playmaking CHECK (playmaking BETWEEN 1 AND 20),
CONSTRAINT ck_player_winger CHECK (winger BETWEEN 1 AND 20),
CONSTRAINT ck_player_attacking CHECK (attacking BETWEEN 1 AND 20),
CONSTRAINT ck_player_set_pieces CHECK (set_pieces BETWEEN 1 AND 20),
CONSTRAINT ck_player_stamina CHECK (stamina BETWEEN 1 AND 20),
CONSTRAINT ck_player_pace CHECK (pace BETWEEN 1 AND 20),
CONSTRAINT ck_player_leadership CHECK (leadership BETWEEN 1 AND 20),
CONSTRAINT ck_player_loyalty CHECK (loyalty BETWEEN 1 AND 20),
CONSTRAINT ck_player_form CHECK (form BETWEEN 1 AND 8),
CONSTRAINT ck_player_experience CHECK (experience >= 0),
CONSTRAINT ck_player_morale CHECK (morale BETWEEN 0 AND 100),
CONSTRAINT ck_player_status CHECK (status IN ('active', 'last_season', 'retired')),
CONSTRAINT ck_player_retirement CHECK ((status = 'active' AND last_season_id IS NULL AND
retired_after_season_id IS NULL) OR (status = 'last_season' AND last_season_id IS NOT NULL AND
retired_after_season_id IS NULL) OR (status = 'retired' AND last_season_id IS NOT NULL AND
retired_after_season_id IS NOT NULL AND retired_after_season_id = last_season_id AND club_id IS
NULL)),
FOREIGN KEY(person_id) REFERENCES persons (id),
FOREIGN KEY(club_id) REFERENCES clubs (id),
FOREIGN KEY(last_season_id) REFERENCES seasons (id),
FOREIGN KEY(retired_after_season_id) REFERENCES seasons (id)
)
            """
        )
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_players_club_id ON players (club_id)
            """)
    )
    op.execute(
        sa.text(
            """
CREATE TABLE season_clubs (
season_id UUID NOT NULL,
club_id UUID NOT NULL,
league_id UUID NOT NULL,
league_set_id UUID NOT NULL,
tie_break_order SMALLINT NOT NULL,
PRIMARY KEY (season_id, club_id),
CONSTRAINT fk_membership_season_set FOREIGN KEY(season_id, league_set_id) REFERENCES seasons
(id, league_set_id),
CONSTRAINT fk_membership_league_set FOREIGN KEY(league_id, league_set_id) REFERENCES leagues
(id, league_set_id),
CONSTRAINT uq_membership_club_league UNIQUE (season_id, club_id, league_id),
CONSTRAINT uq_membership_draw UNIQUE (season_id, league_id, tie_break_order),
CONSTRAINT ck_membership_draw CHECK (tie_break_order BETWEEN 1 AND 10),
FOREIGN KEY(club_id) REFERENCES clubs (id)
)
            """
        )
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_membership_league ON season_clubs (season_id, league_id)
            """)
    )
    op.execute(
        sa.text(
            """
CREATE TABLE staff (
person_id UUID NOT NULL,
club_id UUID,
role VARCHAR(32),
status VARCHAR(16) NOT NULL,
last_season_id UUID,
retired_after_season_id UUID,
PRIMARY KEY (person_id),
CONSTRAINT ck_staff_role CHECK (role IS NULL OR role IN
('head_coach','goalkeeping_coach','defence_coach','attack_coach','fitness_coach','physiotherapist','scout')),
CONSTRAINT ck_staff_employment CHECK ((club_id IS NULL) = (role IS NULL)),
CONSTRAINT ck_staff_status CHECK (status IN ('active', 'last_season', 'retired')),
CONSTRAINT ck_staff_retirement CHECK ((status = 'active' AND last_season_id IS NULL AND
retired_after_season_id IS NULL) OR (status = 'last_season' AND last_season_id IS NOT NULL AND
retired_after_season_id IS NULL) OR (status = 'retired' AND last_season_id IS NOT NULL AND
retired_after_season_id IS NOT NULL AND retired_after_season_id = last_season_id AND club_id IS
NULL)),
FOREIGN KEY(person_id) REFERENCES persons (id),
FOREIGN KEY(club_id) REFERENCES clubs (id),
FOREIGN KEY(last_season_id) REFERENCES seasons (id),
FOREIGN KEY(retired_after_season_id) REFERENCES seasons (id)
)
            """
        )
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_staff_club_id ON staff (club_id)
            """)
    )
    op.execute(
        sa.text(
            """
CREATE TABLE fixtures (
id UUID NOT NULL,
season_id UUID NOT NULL,
league_id UUID NOT NULL,
home_club_id UUID NOT NULL,
away_club_id UUID NOT NULL,
round_number SMALLINT NOT NULL,
scheduled_start_at TIMESTAMP WITH TIME ZONE NOT NULL,
lineup_lock_at TIMESTAMP WITH TIME ZONE NOT NULL,
actual_start_at TIMESTAMP WITH TIME ZONE,
actual_finish_at TIMESTAMP WITH TIME ZONE,
status VARCHAR(16) NOT NULL,
PRIMARY KEY (id),
CONSTRAINT fk_fixture_home_membership FOREIGN KEY(season_id, home_club_id, league_id)
REFERENCES season_clubs (season_id, club_id, league_id),
CONSTRAINT fk_fixture_away_membership FOREIGN KEY(season_id, away_club_id, league_id)
REFERENCES season_clubs (season_id, club_id, league_id),
CONSTRAINT uq_fixture_pair UNIQUE (season_id, home_club_id, away_club_id),
CONSTRAINT ck_fixture_distinct_clubs CHECK (home_club_id <> away_club_id),
CONSTRAINT ck_fixture_round CHECK (round_number BETWEEN 1 AND 18),
CONSTRAINT ck_fixture_lock CHECK (lineup_lock_at <= scheduled_start_at),
CONSTRAINT ck_fixture_status CHECK (status IN
('scheduled','live','paused','finished','cancelled')),
CONSTRAINT ck_fixture_finish CHECK ((status = 'finished') = (actual_finish_at IS NOT NULL)),
CONSTRAINT ck_fixture_started CHECK (status NOT IN ('live','paused','finished') OR
actual_start_at IS NOT NULL),
CONSTRAINT ck_fixture_actual_dates CHECK (actual_finish_at IS NULL OR (actual_start_at IS NOT
NULL AND actual_finish_at >= actual_start_at))
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE INDEX ix_fixture_league_round ON fixtures (season_id, league_id, round_number)
            """
        )
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_fixture_schedule ON fixtures (status, scheduled_start_at)
            """)
    )
    op.execute(
        sa.text(
            """
CREATE TABLE lineup_slots (
club_id UUID NOT NULL,
slot SMALLINT NOT NULL,
person_id UUID NOT NULL,
position VARCHAR(3) NOT NULL,
PRIMARY KEY (club_id, slot),
CONSTRAINT fk_lineup_player_club FOREIGN KEY(person_id, club_id) REFERENCES players (person_id,
club_id),
CONSTRAINT uq_lineup_player UNIQUE (club_id, person_id),
CONSTRAINT ck_lineup_slot CHECK (slot BETWEEN 1 AND 20),
CONSTRAINT ck_lineup_position CHECK (position IN
('GK','DL','DC','DR','DM','ML','MC','MR','AMC','AML','AMR','FC')),
FOREIGN KEY(club_id) REFERENCES club_lineups (club_id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE staff_qualifications (
person_id UUID NOT NULL,
role VARCHAR(32) NOT NULL,
skill SMALLINT NOT NULL,
qualified_for_season_id UUID,
PRIMARY KEY (person_id, role),
CONSTRAINT ck_qualification_role CHECK (role IN
('head_coach','goalkeeping_coach','defence_coach','attack_coach','fitness_coach','physiotherapist','scout')),
CONSTRAINT ck_qualification_skill CHECK (skill BETWEEN 1 AND 20),
FOREIGN KEY(person_id) REFERENCES staff (person_id),
FOREIGN KEY(qualified_for_season_id) REFERENCES seasons (id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE standings (
season_id UUID NOT NULL,
club_id UUID NOT NULL,
played SMALLINT NOT NULL,
won SMALLINT NOT NULL,
drawn SMALLINT NOT NULL,
lost SMALLINT NOT NULL,
goals_for INTEGER NOT NULL,
goals_against INTEGER NOT NULL,
points SMALLINT NOT NULL,
PRIMARY KEY (season_id, club_id),
CONSTRAINT fk_standing_membership FOREIGN KEY(season_id, club_id) REFERENCES season_clubs
(season_id, club_id),
CONSTRAINT ck_standing_played CHECK (played = won + drawn + lost),
CONSTRAINT ck_standing_points CHECK (points = won * 3 + drawn),
CONSTRAINT ck_standing_counters CHECK (played BETWEEN 0 AND 18 AND won >= 0 AND drawn >= 0 AND
lost >= 0 AND goals_for >= 0 AND goals_against >= 0)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE matches (
id UUID NOT NULL,
fixture_id UUID NOT NULL,
seed BIGINT NOT NULL,
engine_version VARCHAR(80) NOT NULL,
state_version INTEGER NOT NULL,
state JSONB NOT NULL,
starting_snapshot JSONB NOT NULL,
completed_tick SMALLINT NOT NULL,
phase VARCHAR(16) NOT NULL,
home_goals SMALLINT NOT NULL,
away_goals SMALLINT NOT NULL,
next_due_at TIMESTAMP WITH TIME ZONE,
PRIMARY KEY (id),
CONSTRAINT ck_match_tick CHECK (completed_tick BETWEEN 0 AND 30),
CONSTRAINT ck_match_score CHECK (home_goals >= 0 AND away_goals >= 0),
CONSTRAINT ck_match_state_version CHECK (state_version > 0),
CONSTRAINT ck_match_phase CHECK (phase IN
('first_half','half_time','second_half','full_time','paused')),
CONSTRAINT ck_match_full_time CHECK (phase <> 'full_time' OR completed_tick = 30),
UNIQUE (fixture_id),
FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
)
            """
        )
    )
    op.execute(
        sa.text("""
CREATE INDEX ix_matches_next_due_at ON matches (next_due_at)
            """)
    )
    op.execute(
        sa.text(
            """
CREATE TABLE match_ticks (
match_id UUID NOT NULL,
tick_number SMALLINT NOT NULL,
game_minute SMALLINT NOT NULL,
due_at TIMESTAMP WITH TIME ZONE NOT NULL,
committed_at TIMESTAMP WITH TIME ZONE NOT NULL,
events JSONB NOT NULL,
state_hash VARCHAR(64) NOT NULL,
PRIMARY KEY (match_id, tick_number),
CONSTRAINT ck_tick_number CHECK (tick_number BETWEEN 1 AND 30),
CONSTRAINT ck_tick_game_minute CHECK (game_minute = tick_number * 3),
FOREIGN KEY(match_id) REFERENCES matches (id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TABLE accepted_commands (
id UUID NOT NULL,
operation_key VARCHAR(200) NOT NULL,
telegram_update_id BIGINT NOT NULL,
effect_number SMALLINT NOT NULL,
manager_id UUID NOT NULL,
ownership_id UUID NOT NULL,
club_id UUID NOT NULL,
match_id UUID,
kind VARCHAR(16) NOT NULL,
payload_version INTEGER NOT NULL,
payload JSONB NOT NULL,
status VARCHAR(16) NOT NULL,
target_tick_number SMALLINT,
applied_tick_number SMALLINT,
accepted_at TIMESTAMP WITH TIME ZONE NOT NULL,
applied_at TIMESTAMP WITH TIME ZONE,
PRIMARY KEY (id),
CONSTRAINT uq_command_update_effect UNIQUE (telegram_update_id, effect_number),
CONSTRAINT fk_command_applied_tick FOREIGN KEY(match_id, applied_tick_number) REFERENCES
match_ticks (match_id, tick_number),
CONSTRAINT fk_command_owner_club FOREIGN KEY(ownership_id, manager_id, club_id) REFERENCES
ownership_periods (id, manager_id, club_id),
CONSTRAINT ck_command_effect CHECK (effect_number >= 0),
CONSTRAINT ck_command_payload_version CHECK (payload_version > 0),
CONSTRAINT ck_command_status CHECK (status IN ('accepted','applied','cancelled')),
CONSTRAINT ck_command_kind CHECK (kind IN ('lineup','tactics','substitution','instruction')),
CONSTRAINT ck_command_match_required CHECK (kind = 'lineup' OR match_id IS NOT NULL),
CONSTRAINT ck_command_target_tick CHECK (target_tick_number IS NULL OR target_tick_number
BETWEEN 1 AND 30),
CONSTRAINT ck_command_applied_tick CHECK (applied_tick_number IS NULL OR (status = 'applied'
AND applied_at IS NOT NULL)),
CONSTRAINT ck_command_applied_at CHECK ((status = 'applied') = (applied_at IS NOT NULL)),
UNIQUE (operation_key),
FOREIGN KEY(manager_id) REFERENCES managers (id),
FOREIGN KEY(ownership_id) REFERENCES ownership_periods (id),
FOREIGN KEY(club_id) REFERENCES clubs (id),
FOREIGN KEY(match_id) REFERENCES matches (id)
)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE INDEX ix_command_match_pending ON accepted_commands (match_id, status,
target_tick_number)
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE FUNCTION goaletto_guard_person_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
IF TG_OP = 'DELETE' THEN
RAISE EXCEPTION 'Person history and appearance reservation must be retained';
END IF;
IF NEW.id IS DISTINCT FROM OLD.id OR
NEW.creation_key IS DISTINCT FROM OLD.creation_key OR
NEW.appearance_identity_id IS DISTINCT FROM OLD.appearance_identity_id THEN
RAISE EXCEPTION 'Person identity and appearance reservation are immutable';
END IF;
RETURN NEW;
END;
$$
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TRIGGER tr_person_identity BEFORE UPDATE OR DELETE ON persons
FOR EACH ROW EXECUTE FUNCTION goaletto_guard_person_identity()
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE FUNCTION goaletto_guard_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
RAISE EXCEPTION 'Committed history and portrait catalog entries are append-only';
END;
$$
            """
        )
    )
    op.execute(
        sa.text(
            """
CREATE TRIGGER tr_person_event_immutable BEFORE UPDATE OR DELETE ON person_season_events
FOR EACH ROW EXECUTE FUNCTION goaletto_guard_append_only()
            """
        )
    )
    op.execute(
        "CREATE TRIGGER tr_appearance_identity_immutable BEFORE UPDATE OR DELETE "
        "ON appearance_identities FOR EACH ROW EXECUTE FUNCTION goaletto_guard_append_only()"
    )
    op.execute(
        "CREATE TRIGGER tr_appearance_variant_immutable BEFORE UPDATE OR DELETE "
        "ON appearance_variants FOR EACH ROW EXECUTE FUNCTION goaletto_guard_append_only()"
    )
    op.execute(
        sa.text(
            """
CREATE TRIGGER tr_match_tick_immutable BEFORE UPDATE OR DELETE ON match_ticks
FOR EACH ROW EXECUTE FUNCTION goaletto_guard_append_only()
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION goaletto_guard_append_only() CASCADE")
    op.execute("DROP FUNCTION goaletto_guard_person_identity() CASCADE")
    op.drop_table("accepted_commands")
    op.drop_table("match_ticks")
    op.drop_table("matches")
    op.drop_table("standings")
    op.drop_table("staff_qualifications")
    op.drop_table("lineup_slots")
    op.drop_table("fixtures")
    op.drop_table("staff")
    op.drop_table("season_clubs")
    op.drop_table("players")
    op.drop_table("person_season_events")
    op.drop_table("ownership_periods")
    op.drop_table("club_lineups")
    op.drop_table("persons")
    op.drop_table("seasons")
    op.drop_table("appearance_variants")
    op.drop_table("analytics_managers")
    op.drop_table("managers")
    op.drop_table("appearance_identities")
    op.drop_constraint("uq_league_id_set", "leagues", type_="unique")
    op.drop_constraint("ck_club_kit_version", "clubs", type_="check")
    op.drop_constraint("ck_club_secondary_color", "clubs", type_="check")
    op.drop_constraint("ck_club_primary_color", "clubs", type_="check")
    op.drop_column("clubs", "kit_version")
    op.drop_column("clubs", "kit_secondary")
    op.drop_column("clubs", "kit_primary")
