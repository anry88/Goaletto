"""Paid durable expansions and immutable match attendance basis."""

from alembic import op

revision = "0016_stadium_experience"
down_revision = "0015_club_finances"
branch_labels = None
depends_on = None

OLD_ACTION = (
    "kind IN ('club_claimed','club_opened','club_preview_opened',"
    "'lineup_opened','lineup_saved','match_command_accepted','mat"
    "ch_opened','match_report_opened','next_fixture_opened','noti"
    "fication_preferences','training_opened','training_plan_saved"
    "','language_preferences','finances_opened')"
)

NEW_ACTION = (
    "kind IN ('club_claimed','club_opened','club_preview_opened',"
    "'lineup_opened','lineup_saved','match_command_accepted','mat"
    "ch_opened','match_report_opened','next_fixture_opened','noti"
    "fication_preferences','training_opened','training_plan_saved"
    "','language_preferences','finances_opened','stadium_opened',"
    "'stadium_expansion_ordered')"
)

OLD_JOURNEY = (
    "name IN ('action_rejected','bot_unavailable','bot_youth_repl"
    "acement_created','campaign_touched','club_claimed','club_off"
    "er_delivered','club_opened','club_ownership_ended','club_pre"
    "view_opened','experiment_exposed','feedback_answered','feedb"
    "ack_prompt_delivered','feedback_skipped','finances_opened','"
    "fixture_abandoned','fixture_finished','fixture_started','hel"
    "p_delivered','lineup_opened','lineup_saved','manager_started"
    "','match_command_accepted','match_command_applied','match_li"
    "neup_locked','match_opened','match_report_opened','match_sec"
    "ond_half_started','match_tick_committed','next_action_opened"
    "','next_fixture_opened','player_last_season_announced','play"
    "er_retired','result_delivered','result_finalized','start_acc"
    "epted','training_opened','training_plan_saved')"
)

NEW_JOURNEY = (
    "name IN ('action_rejected','bot_unavailable','bot_youth_repl"
    "acement_created','campaign_touched','club_claimed','club_off"
    "er_delivered','club_opened','club_ownership_ended','club_pre"
    "view_opened','experiment_exposed','feedback_answered','feedb"
    "ack_prompt_delivered','feedback_skipped','finances_opened','"
    "fixture_abandoned','fixture_finished','fixture_started','hel"
    "p_delivered','lineup_opened','lineup_saved','manager_started"
    "','match_command_accepted','match_command_applied','match_li"
    "neup_locked','match_opened','match_report_opened','match_sec"
    "ond_half_started','match_tick_committed','next_action_opened"
    "','next_fixture_opened','player_last_season_announced','play"
    "er_retired','result_delivered','result_finalized','stadium_e"
    "xpansion_ordered','stadium_opened','start_accepted','trainin"
    "g_opened','training_plan_saved')"
)


def upgrade() -> None:
    op.execute(
        "CREATE TABLE stadium_expansions (\n\tid UUID NOT NULL, \n\tclub_"
        "id UUID NOT NULL, \n\townership_id UUID NOT NULL, \n\toperation_"
        "id UUID NOT NULL, \n\tfrom_capacity INTEGER NOT NULL, \n\tto_cap"
        "acity INTEGER NOT NULL, \n\tcost BIGINT NOT NULL, \n\tstarted_at"
        " TIMESTAMP WITH TIME ZONE NOT NULL, \n\tdue_at TIMESTAMP WITH "
        "TIME ZONE NOT NULL, \n\tcompleted_at TIMESTAMP WITH TIME ZONE,"
        " \n\tstatus VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTR"
        "AINT ck_expansion_dimensions CHECK (from_capacity IN (5000,1"
        "0000,20000,35000) AND to_capacity IN (10000,20000,35000,5000"
        "0) AND to_capacity > from_capacity AND cost > 0), \n\tCONSTRAI"
        "NT ck_expansion_dates CHECK (due_at > started_at AND (comple"
        "ted_at IS NULL OR completed_at >= due_at)), \n\tCONSTRAINT ck_"
        "expansion_state CHECK (status IN ('queued','finished') AND ("
        "status = 'finished') = (completed_at IS NOT NULL)), \n\tCONSTR"
        "AINT uq_expansion_operation UNIQUE (operation_id), \n\tFOREIGN"
        " KEY(club_id) REFERENCES clubs (id), \n\tFOREIGN KEY(ownership"
        "_id) REFERENCES ownership_periods (id)\n)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_expansion_pending_club ON stadium_exp"
        "ansions (club_id) WHERE status = 'queued'"
    )
    op.execute(
        "CREATE TABLE match_crowds (\n\tmatch_id UUID NOT NULL, \n\tpolic"
        "y_version VARCHAR(32) NOT NULL, \n\tlocked_at TIMESTAMP WITH T"
        "IME ZONE NOT NULL, \n\tbasis JSONB NOT NULL, \n\tPRIMARY KEY (ma"
        "tch_id), \n\tCONSTRAINT ck_crowd_policy CHECK (policy_version "
        "= 'stadium-demand-v1'), \n\tFOREIGN KEY(match_id) REFERENCES m"
        "atches (id)\n)"
    )
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", NEW_ACTION)
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint("ck_journey_name", "journey_events", NEW_JOURNEY)
    op.execute(
        "CREATE TRIGGER crowd_immutable BEFORE UPDATE OR DELETE ON match_crowds "
        "FOR EACH ROW EXECUTE FUNCTION protect_finance_history()"
    )
    op.execute("""CREATE FUNCTION protect_stadium_expansion() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Paid expansion history is immutable'; END IF;
        IF ROW(NEW.id,NEW.club_id,NEW.ownership_id,NEW.operation_id,NEW.from_capacity,
          NEW.to_capacity,NEW.cost,NEW.started_at,NEW.due_at) IS DISTINCT FROM
          ROW(OLD.id,OLD.club_id,OLD.ownership_id,OLD.operation_id,OLD.from_capacity,
          OLD.to_capacity,OLD.cost,OLD.started_at,OLD.due_at)
          OR (OLD.status = 'finished' AND ROW(NEW.status,NEW.completed_at)
            IS DISTINCT FROM ROW(OLD.status,OLD.completed_at))
        THEN RAISE EXCEPTION 'Paid expansion quote is immutable'; END IF;
        RETURN NEW;
      END $$""")
    op.execute(
        "CREATE TRIGGER expansion_immutable BEFORE UPDATE OR DELETE ON stadium_expansions "
        "FOR EACH ROW EXECUTE FUNCTION protect_stadium_expansion()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM stadium_expansions) OR
      EXISTS (SELECT 1 FROM match_crowds) OR EXISTS (SELECT 1 FROM game_action_receipts
        WHERE kind IN ('stadium_opened','stadium_expansion_ordered')) OR
      EXISTS (SELECT 1 FROM journey_events
        WHERE name IN ('stadium_opened','stadium_expansion_ordered'))
      THEN RAISE EXCEPTION 'Reconcile stadium activity before downgrade'; END IF; END $$""")
    op.execute("DELETE FROM jobs WHERE kind = 'stadium.complete'")
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", OLD_ACTION)
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint("ck_journey_name", "journey_events", OLD_JOURNEY)
    op.execute("DROP TRIGGER crowd_immutable ON match_crowds")
    op.execute("DROP TRIGGER expansion_immutable ON stadium_expansions")
    op.execute("DROP FUNCTION protect_stadium_expansion()")
    op.drop_table("match_crowds")
    op.drop_index("uq_expansion_pending_club", table_name="stadium_expansions")
    op.drop_table("stadium_expansions")
