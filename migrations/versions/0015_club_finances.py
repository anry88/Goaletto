"""Club finance ledger and salary contracts

Revision ID: 0015_club_finances
Revises: 0014_live_recovery
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_club_finances"
down_revision: str | None = "0014_live_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTION = (
    "kind IN ('club_claimed','club_opened','club_preview_opened','line"
    "up_opened','lineup_saved','match_command_accepted','match_opened'"
    ",'match_report_opened','next_fixture_opened','notification_prefer"
    "ences','training_opened','training_plan_saved','language_preferen"
    "ces')"
)
NEW_ACTION = (
    "kind IN ('club_claimed','club_opened','club_preview_opened','line"
    "up_opened','lineup_saved','match_command_accepted','match_opened'"
    ",'match_report_opened','next_fixture_opened','notification_prefer"
    "ences','training_opened','training_plan_saved','language_preferen"
    "ces','finances_opened')"
)
OLD_JOURNEY = (
    "name IN ('action_rejected','bot_unavailable','bot_youth_replaceme"
    "nt_created','campaign_touched','club_claimed','club_offer_deliver"
    "ed','club_opened','club_ownership_ended','club_preview_opened','e"
    "xperiment_exposed','feedback_answered','feedback_prompt_delivered"
    "','feedback_skipped','fixture_abandoned','fixture_finished','fixt"
    "ure_started','help_delivered','lineup_opened','lineup_saved','man"
    "ager_started','match_command_accepted','match_command_applied','m"
    "atch_lineup_locked','match_opened','match_report_opened','match_s"
    "econd_half_started','match_tick_committed','next_action_opened','"
    "next_fixture_opened','player_last_season_announced','player_retir"
    "ed','result_delivered','result_finalized','start_accepted','train"
    "ing_opened','training_plan_saved')"
)
NEW_JOURNEY = (
    "name IN ('action_rejected','bot_unavailable','bot_youth_replaceme"
    "nt_created','campaign_touched','club_claimed','club_offer_deliver"
    "ed','club_opened','club_ownership_ended','club_preview_opened','e"
    "xperiment_exposed','feedback_answered','feedback_prompt_delivered"
    "','feedback_skipped','finances_opened','fixture_abandoned','fixtu"
    "re_finished','fixture_started','help_delivered','lineup_opened','"
    "lineup_saved','manager_started','match_command_accepted','match_c"
    "ommand_applied','match_lineup_locked','match_opened','match_repor"
    "t_opened','match_second_half_started','match_tick_committed','nex"
    "t_action_opened','next_fixture_opened','player_last_season_announ"
    "ced','player_retired','result_delivered','result_finalized','star"
    "t_accepted','training_opened','training_plan_saved')"
)


def upgrade() -> None:
    op.execute(
        "CREATE TABLE finance_accounts (\n\tclub_id UUID NOT NULL, \n\tre"
        "served BIGINT DEFAULT '0' NOT NULL, \n\tarrears BIGINT DEFAULT"
        " '0' NOT NULL, \n\tsequence INTEGER DEFAULT '1' NOT NULL, \n\tPR"
        "IMARY KEY (club_id), \n\tCONSTRAINT ck_finance_account CHECK ("
        "reserved >= 0 AND arrears >= 0 AND sequence >= 1), \n\tFOREIGN"
        " KEY(club_id) REFERENCES clubs (id)\n)"
    )
    op.execute(
        "CREATE TABLE finance_cycles (\n\tid UUID NOT NULL, \n\tclub_id U"
        "UID NOT NULL, \n\tweek_start TIMESTAMP WITH TIME ZONE NOT NULL"
        ", \n\tapplied_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tpolicy_v"
        "ersion VARCHAR(32) NOT NULL, \n\tsnapshot JSONB NOT NULL, \n\tPR"
        "IMARY KEY (id), \n\tCONSTRAINT uq_finance_week UNIQUE (club_id"
        ", week_start), \n\tCONSTRAINT ck_finance_policy CHECK (policy_"
        "version = 'economy-v1'), \n\tFOREIGN KEY(club_id) REFERENCES c"
        "lubs (id)\n)"
    )
    op.execute(
        "CREATE TABLE ledger_entries (\n\tid UUID NOT NULL, \n\tclub_id U"
        "UID NOT NULL, \n\toperation_key VARCHAR(180) NOT NULL, \n\tseque"
        "nce INTEGER NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tcash_de"
        "lta BIGINT NOT NULL, \n\treserved_delta BIGINT NOT NULL, \n\tarr"
        "ears_delta BIGINT NOT NULL, \n\tbalance_after BIGINT NOT NULL,"
        " \n\treserved_after BIGINT NOT NULL, \n\tarrears_after BIGINT NO"
        "T NULL, \n\toccurred_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tr"
        "equest JSONB NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_l"
        "edger_operation UNIQUE (club_id, operation_key), \n\tCONSTRAIN"
        "T uq_ledger_sequence UNIQUE (club_id, sequence), \n\tCONSTRAIN"
        "T ck_ledger_sequence CHECK (sequence >= 1), \n\tCONSTRAINT ck_"
        "ledger_balances CHECK (balance_after >= 0 AND reserved_after"
        " >= 0 AND reserved_after <= balance_after AND arrears_after "
        ">= 0), \n\tCONSTRAINT ck_ledger_effect CHECK (cash_delta <> 0 "
        "OR reserved_delta <> 0 OR arrears_delta <> 0 OR kind = 'open"
        "ing'), \n\tCONSTRAINT ck_ledger_kind CHECK (kind IN ('opening'"
        ",'grant','tickets','sponsor','merchandise','player_salary','"
        "staff_salary','stadium_upkeep','prize','reserve','release','"
        "transfer_in','transfer_out','arrears_paid','stadium_upgrade'"
        ")), \n\tFOREIGN KEY(club_id) REFERENCES clubs (id)\n)"
    )
    op.execute("CREATE INDEX ix_ledger_club_page ON ledger_entries (club_id, sequence)")
    op.execute(
        "CREATE TABLE money_reservations (\n\tid UUID NOT NULL, \n\tclub_"
        "id UUID NOT NULL, \n\tamount BIGINT NOT NULL, \n\tpurpose JSONB "
        "NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tcreated_at TIMEST"
        "AMP WITH TIME ZONE NOT NULL, \n\tclosed_at TIMESTAMP WITH TIME"
        " ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_reservation_amoun"
        "t CHECK (amount > 0), \n\tCONSTRAINT ck_reservation_status CHE"
        "CK (status IN ('active','released','settled')), \n\tCONSTRAINT"
        " ck_reservation_closed CHECK ((status = 'active') = (closed_"
        "at IS NULL)), \n\tFOREIGN KEY(club_id) REFERENCES clubs (id)\n)"
    )
    op.execute(
        "CREATE TABLE salary_contracts (\n\tid UUID NOT NULL, \n\tperson_"
        "id UUID NOT NULL, \n\tclub_id UUID NOT NULL, \n\trole VARCHAR(32"
        ") NOT NULL, \n\tweekly_rate BIGINT NOT NULL, \n\tpolicy_version "
        "VARCHAR(32) DEFAULT 'economy-v1' NOT NULL, \n\tstarted_at TIME"
        "STAMP WITH TIME ZONE NOT NULL, \n\tended_at TIMESTAMP WITH TIM"
        "E ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_salary_rate CHEC"
        "K (weekly_rate >= 0), \n\tCONSTRAINT ck_salary_dates CHECK (en"
        "ded_at IS NULL OR ended_at >= started_at), \n\tCONSTRAINT ck_s"
        "alary_role CHECK (role IN ('player','head_coach','goalkeepin"
        "g_coach','defence_coach','attack_coach','fitness_coach','phy"
        "siotherapist','scout')), \n\tFOREIGN KEY(person_id) REFERENCES"
        " persons (id), \n\tFOREIGN KEY(club_id) REFERENCES clubs (id)\n"
        ")"
    )
    op.execute("CREATE INDEX ix_salary_club_period ON salary_contracts (club_id, started_at)")
    op.execute(
        "CREATE UNIQUE INDEX uq_salary_active_person ON salary_contra"
        "cts (person_id) WHERE ended_at IS NULL"
    )
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", NEW_ACTION)
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint("ck_journey_name", "journey_events", NEW_JOURNEY)
    # Preserve existing cash exactly. Capital is a separate audited operator grant.
    op.execute("INSERT INTO finance_accounts (club_id) SELECT id FROM clubs")
    op.execute("""INSERT INTO ledger_entries
      (id,club_id,operation_key,sequence,kind,cash_delta,reserved_delta,arrears_delta,
       balance_after,reserved_after,arrears_after,occurred_at,request)
      SELECT gen_random_uuid(),id,'opening',1,'opening',budget,0,0,budget,0,0,now(),
      jsonb_build_object('kind','opening','amount',budget,'details','{}'::jsonb) FROM clubs""")
    op.execute("""INSERT INTO salary_contracts
      (id,person_id,club_id,role,weekly_rate,policy_version,started_at)
      SELECT gen_random_uuid(),person_id,club_id,'player',
      100+10*(goalkeeping+defending+playmaking+winger+attacking+set_pieces+stamina+pace),
      'economy-v1',now() FROM players WHERE status IN ('active','last_season')
      AND club_id IS NOT NULL""")
    op.execute("""INSERT INTO salary_contracts
      (id,person_id,club_id,role,weekly_rate,policy_version,started_at)
      SELECT gen_random_uuid(),person_id,club_id,role,
      CASE role WHEN 'head_coach' THEN 50000 WHEN 'physiotherapist' THEN 30000 ELSE 20000 END,
      'economy-v1',now() FROM staff WHERE status IN ('active','last_season')
      AND club_id IS NOT NULL""")
    op.execute("""INSERT INTO jobs(id,dedupe_key,kind,payload,due_at,status,attempts)
      SELECT gen_random_uuid(), 'finance:v1:' || id::text || ':' ||
        to_char(date_trunc('week',now() AT TIME ZONE 'UTC')+interval '7 days',
          'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00', 'finance.weekly',
        jsonb_build_object('version',1,'club_id',id::text,'week_start',
          to_char(date_trunc('week',now() AT TIME ZONE 'UTC')+interval '7 days',
          'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00','policy','economy-v1'),
        (date_trunc('week',now() AT TIME ZONE 'UTC')+interval '7 days') AT TIME ZONE 'UTC',
        'held',0 FROM clubs""")
    op.execute("""CREATE FUNCTION protect_finance_history() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Financial history is immutable'; END $$""")
    op.execute(
        "CREATE TRIGGER ledger_immutable BEFORE UPDATE OR DELETE ON ledger"
        "_entries FOR EACH ROW EXECUTE FUNCTION protect_finance_history()"
    )
    op.execute(
        "CREATE TRIGGER finance_cycle_immutable BEFORE UPDATE OR DELETE ON"
        " finance_cycles FOR EACH ROW EXECUTE FUNCTION protect_finance_his"
        "tory()"
    )
    op.execute("""CREATE FUNCTION protect_salary_contract() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Salary history is immutable'; END IF;
        IF ROW(NEW.id,NEW.person_id,NEW.club_id,NEW.role,NEW.weekly_rate,
          NEW.policy_version,NEW.started_at)
           IS DISTINCT FROM ROW(OLD.id,OLD.person_id,OLD.club_id,OLD.role,OLD.weekly_rate,
          OLD.policy_version,OLD.started_at)
           OR (OLD.ended_at IS NOT NULL AND NEW.ended_at IS DISTINCT FROM OLD.ended_at)
        THEN RAISE EXCEPTION 'Salary contract is immutable except its first close'; END IF;
        RETURN NEW;
      END $$""")
    op.execute(
        "CREATE TRIGGER salary_immutable BEFORE UPDATE OR DELETE ON salary"
        "_contracts FOR EACH ROW EXECUTE FUNCTION protect_salary_contract("
        ")"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF
      EXISTS (SELECT 1 FROM ledger_entries WHERE kind <> 'opening') OR
      EXISTS (SELECT 1 FROM finance_cycles) OR EXISTS (SELECT 1 FROM money_reservations) OR
      EXISTS (SELECT 1 FROM salary_contracts WHERE ended_at IS NOT NULL) OR
      EXISTS (SELECT 1 FROM game_action_receipts WHERE kind = 'finances_opened') OR
      EXISTS (SELECT 1 FROM journey_events WHERE name = 'finances_opened')
      THEN RAISE EXCEPTION 'Reconcile financial activity before downgrade'; END IF; END $$""")
    op.execute("DELETE FROM jobs WHERE kind = 'finance.weekly'")
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", OLD_ACTION)
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint("ck_journey_name", "journey_events", OLD_JOURNEY)
    op.execute("DROP TRIGGER ledger_immutable ON ledger_entries")
    op.execute("DROP TRIGGER finance_cycle_immutable ON finance_cycles")
    op.execute("DROP TRIGGER salary_immutable ON salary_contracts")
    op.execute("DROP FUNCTION protect_finance_history()")
    op.execute("DROP FUNCTION protect_salary_contract()")
    op.drop_index(
        "uq_salary_active_person",
        table_name="salary_contracts",
        postgresql_where="ended_at IS NULL",
    )
    op.drop_index("ix_salary_club_period", table_name="salary_contracts")
    op.drop_table("salary_contracts")
    op.drop_table("money_reservations")
    op.drop_index("ix_ledger_club_page", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_table("finance_cycles")
    op.drop_table("finance_accounts")
