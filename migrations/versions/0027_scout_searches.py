"""Private paid scout missions and immutable findings, no world conversions."""

from alembic import op

revision = "0027_scout_searches"
down_revision = "0026_economy_market"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "\nCREATE TABLE scout_missions (\n\tid UUID NOT NULL, \n\toperatio"
        "n_id UUID NOT NULL, \n\tclub_id UUID NOT NULL, \n\townership_id "
        "UUID NOT NULL, \n\tscout_id UUID NOT NULL, \n\tskill SMALLINT NO"
        "T NULL, \n\tposition VARCHAR(4) NOT NULL, \n\tdays SMALLINT NOT "
        "NULL, \n\tfee BIGINT NOT NULL, \n\tpolicy_version VARCHAR(32) NO"
        "T NULL, \n\teconomy_policy VARCHAR(32) NOT NULL, \n\tstarted_at "
        "TIMESTAMP WITH TIME ZONE NOT NULL, \n\tdue_at TIMESTAMP WITH T"
        "IME ZONE NOT NULL, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n"
        "\texpires_at TIMESTAMP WITH TIME ZONE, \n\tstatus VARCHAR(16) N"
        "OT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_scout_start_ope"
        "ration UNIQUE (operation_id), \n\tCONSTRAINT ck_scout_position"
        " CHECK (position IN ('GK','DL','DC','DR','DM','ML','MC','MR'"
        ",'AMC','AML','AMR','FC')), \n\tCONSTRAINT ck_scout_terms CHECK"
        " (skill BETWEEN 1 AND 20 AND days IN (4,8,12,16) AND fee > 0"
        "), \n\tCONSTRAINT ck_scout_policy CHECK (policy_version = 'sco"
        "uting-v1' AND economy_policy = 'economy-cycle-v2'), \n\tCONSTR"
        "AINT ck_scout_duration CHECK (due_at = started_at + days * i"
        "nterval '1 day'), \n\tCONSTRAINT ck_scout_state CHECK (status "
        "IN ('active','completed','cancelled','expired') AND (status "
        "= 'active') = (finished_at IS NULL)), \n\tCONSTRAINT ck_scout_"
        "finish CHECK (finished_at IS NULL OR finished_at >= started_"
        "at), \n\tCONSTRAINT ck_scout_expiry CHECK ((status IN ('comple"
        "ted','expired')) = (expires_at IS NOT NULL)), \n\tFOREIGN KEY("
        "club_id) REFERENCES clubs (id), \n\tFOREIGN KEY(ownership_id) "
        "REFERENCES ownership_periods (id), \n\tFOREIGN KEY(scout_id) R"
        "EFERENCES staff (person_id)\n)\n\n",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_scout_active_club ON scout_missions ("
        "club_id) WHERE status = 'active'",
    )
    op.execute(
        "\nCREATE TABLE scout_discoveries (\n\tperson_id UUID NOT NULL, "
        "\n\tmission_id UUID NOT NULL, \n\trank SMALLINT NOT NULL, \n\tfee "
        "BIGINT NOT NULL, \n\tcycle_rate BIGINT NOT NULL, \n\tpotential_l"
        "ow SMALLINT NOT NULL, \n\tpotential_high SMALLINT NOT NULL, \n\t"
        "signed_at TIMESTAMP WITH TIME ZONE, \n\tdeclined_at TIMESTAMP "
        "WITH TIME ZONE, \n\tPRIMARY KEY (person_id), \n\tCONSTRAINT uq_s"
        "cout_rank UNIQUE (mission_id, rank), \n\tCONSTRAINT ck_scout_p"
        "otential CHECK (potential_low BETWEEN 1 AND 20 AND potential"
        "_high BETWEEN potential_low AND 20 AND NOT (signed_at IS NOT"
        " NULL AND declined_at IS NOT NULL)), \n\tCONSTRAINT ck_scout_d"
        "iscovery CHECK (rank BETWEEN 1 AND 4 AND fee > 0 AND cycle_r"
        "ate > 0), \n\tFOREIGN KEY(person_id) REFERENCES players (perso"
        "n_id), \n\tFOREIGN KEY(mission_id) REFERENCES scout_missions ("
        "id)\n)\n\n",
    )
    op.execute("CREATE INDEX ix_scout_discoveries_mission_id ON scout_discoveries (mission_id)")
    op.execute(
        "\nCREATE TABLE scout_signings (\n\toperation_id UUID NOT NULL, "
        "\n\tperson_id UUID NOT NULL, \n\tclub_id UUID NOT NULL, \n\towners"
        "hip_id UUID NOT NULL, \n\tfee BIGINT NOT NULL, \n\tcycle_rate BI"
        "GINT NOT NULL, \n\teconomy_policy VARCHAR(32) NOT NULL, \n\tsign"
        "ed_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (oper"
        "ation_id), \n\tCONSTRAINT uq_scout_person_signing UNIQUE (pers"
        "on_id), \n\tCONSTRAINT ck_scout_signing_money CHECK (fee > 0 A"
        "ND cycle_rate > 0), \n\tFOREIGN KEY(person_id) REFERENCES scou"
        "t_discoveries (person_id), \n\tFOREIGN KEY(club_id) REFERENCES"
        " clubs (id), \n\tFOREIGN KEY(ownership_id) REFERENCES ownershi"
        "p_periods (id)\n)\n\n",
    )
    op.drop_constraint("ck_ledger_kind", "ledger_entries", type_="check")
    op.create_check_constraint(
        "ck_ledger_kind",
        "ledger_entries",
        "kind IN ('opening','grant','tickets','sponsor','merchandise'"
        ",'player_salary','staff_salary','stadium_upkeep','prize','re"
        "serve','release','transfer_in','transfer_out','arrears_paid'"
        ",'stadium_upgrade','signing_fee','scout_search')",
    )
    op.execute(
        "CREATE FUNCTION protect_scout_mission() RETURNS trigger LANG"
        "UAGE plpgsql AS $$ BEGIN\n      IF TG_OP='DELETE' THEN RAISE "
        "EXCEPTION 'Scout history is immutable'; END IF;\n      IF ROW"
        "(NEW.id,NEW.operation_id,NEW.club_id,NEW.ownership_id,NEW.sc"
        "out_id,NEW.skill,\n        NEW.position,NEW.days,NEW.fee,NEW."
        "policy_version,NEW.economy_policy,NEW.started_at,NEW.due_at)"
        "\n        IS DISTINCT FROM ROW(OLD.id,OLD.operation_id,OLD.cl"
        "ub_id,OLD.ownership_id,OLD.scout_id,OLD.skill,\n        OLD.p"
        "osition,OLD.days,OLD.fee,OLD.policy_version,OLD.economy_poli"
        "cy,OLD.started_at,OLD.due_at)\n        OR (OLD.finished_at IS"
        " NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_"
        "at)\n        OR (OLD.expires_at IS NOT NULL AND NEW.expires_a"
        "t IS DISTINCT FROM OLD.expires_at)\n        OR (OLD.status IN"
        " ('cancelled','expired') AND NEW IS DISTINCT FROM OLD)\n     "
        "   OR (OLD.status='completed' AND NEW.status NOT IN ('comple"
        "ted','expired'))\n      THEN RAISE EXCEPTION 'Scout quote or "
        "terminal outcome is immutable'; END IF;\n      RETURN NEW;\n  "
        "  END $$",
    )
    op.execute(
        "CREATE TRIGGER scout_mission_immutable BEFORE UPDATE OR DELE"
        "TE ON scout_missions FOR EACH ROW EXECUTE FUNCTION protect_s"
        "cout_mission()",
    )
    op.execute(
        "CREATE FUNCTION protect_scout_discovery() RETURNS trigger LA"
        "NGUAGE plpgsql AS $$ BEGIN\n      IF TG_OP='DELETE' THEN RAIS"
        "E EXCEPTION 'Scout history is immutable'; END IF;\n      IF R"
        "OW(NEW.person_id,NEW.mission_id,NEW.rank,NEW.fee,NEW.cycle_r"
        "ate,NEW.potential_low,NEW.potential_high)\n        IS DISTINC"
        "T FROM ROW(OLD.person_id,OLD.mission_id,OLD.rank,OLD.fee,OLD"
        ".cycle_rate,OLD.potential_low,OLD.potential_high)\n        OR"
        " (OLD.signed_at IS NOT NULL AND NEW.signed_at IS DISTINCT FR"
        "OM OLD.signed_at)\n        OR (OLD.declined_at IS NOT NULL AN"
        "D NEW.declined_at IS DISTINCT FROM OLD.declined_at)\n      TH"
        "EN RAISE EXCEPTION 'Scout finding is immutable'; END IF;\n   "
        "   RETURN NEW;\n    END $$",
    )
    op.execute(
        "CREATE TRIGGER scout_discovery_immutable BEFORE UPDATE OR DE"
        "LETE ON scout_discoveries FOR EACH ROW EXECUTE FUNCTION prot"
        "ect_scout_discovery()",
    )
    op.execute(
        "CREATE FUNCTION protect_scout_signing() RETURNS trigger LANG"
        "UAGE plpgsql AS $$ BEGIN\n      RAISE EXCEPTION 'Scout signin"
        "g history is immutable'; END $$",
    )
    op.execute(
        "CREATE TRIGGER scout_signing_immutable BEFORE UPDATE OR DELE"
        "TE ON scout_signings FOR EACH ROW EXECUTE FUNCTION protect_s"
        "cout_signing()",
    )

    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint(
        "ck_action_kind",
        "game_action_receipts",
        "kind IN ('club_claimed','club_opened','club_preview_opened',"
        "'lineup_opened','lineup_saved','match_command_accepted','mat"
        "ch_opened','match_report_opened','next_fixture_opened','noti"
        "fication_preferences','training_opened','training_plan_saved"
        "','language_preferences','finances_opened','stadium_opened',"
        "'stadium_expansion_ordered','market_opened','market_listing_"
        "created','market_bid_accepted','market_listing_cancelled','c"
        "andidate_signed','scout_search_started','scout_search_cancel"
        "led')",
    )
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint(
        "ck_journey_name",
        "journey_events",
        "name IN ('action_rejected','bot_unavailable','bot_youth_repl"
        "acement_created','campaign_touched','candidate_signed','club"
        "_claimed','club_offer_delivered','club_opened','club_ownersh"
        "ip_ended','club_preview_opened','experiment_exposed','feedba"
        "ck_answered','feedback_prompt_delivered','feedback_skipped',"
        "'finances_opened','fixture_abandoned','fixture_finished','fi"
        "xture_started','help_delivered','lineup_opened','lineup_save"
        "d','manager_started','market_bid_accepted','market_listing_c"
        "ancelled','market_listing_created','market_opened','match_co"
        "mmand_accepted','match_command_applied','match_lineup_locked"
        "','match_opened','match_report_opened','match_second_half_st"
        "arted','match_tick_committed','next_action_opened','next_fix"
        "ture_opened','player_last_season_announced','player_retired'"
        ",'result_delivered','result_finalized','stadium_expansion_or"
        "dered','stadium_opened','start_accepted','training_opened','"
        "training_plan_saved','scout_search_started','scout_search_ca"
        "ncelled')",
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM scout_missions) THEN RA"
        "ISE EXCEPTION 'Scout history prevents downgrade'; END IF; EN"
        "D $$",
    )
    for table in ("scout_signings", "scout_discoveries", "scout_missions"):
        op.drop_table(table)
    for function in ("protect_scout_signing", "protect_scout_discovery", "protect_scout_mission"):
        op.execute("DROP FUNCTION " + function + "()")
    op.drop_constraint("ck_ledger_kind", "ledger_entries", type_="check")
    op.create_check_constraint(
        "ck_ledger_kind",
        "ledger_entries",
        "kind IN ('opening','grant','tickets','sponsor','merchandise'"
        ",'player_salary','staff_salary','stadium_upkeep','prize','re"
        "serve','release','transfer_in','transfer_out','arrears_paid'"
        ",'stadium_upgrade','signing_fee')",
    )

    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint(
        "ck_action_kind",
        "game_action_receipts",
        "kind IN ('club_claimed','club_opened','club_preview_opened',"
        "'lineup_opened','lineup_saved','match_command_accepted','mat"
        "ch_opened','match_report_opened','next_fixture_opened','noti"
        "fication_preferences','training_opened','training_plan_saved"
        "','language_preferences','finances_opened','stadium_opened',"
        "'stadium_expansion_ordered','market_opened','market_listing_"
        "created','market_bid_accepted','market_listing_cancelled','c"
        "andidate_signed')",
    )
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint(
        "ck_journey_name",
        "journey_events",
        "name IN ('action_rejected','bot_unavailable','bot_youth_repl"
        "acement_created','campaign_touched','candidate_signed','club"
        "_claimed','club_offer_delivered','club_opened','club_ownersh"
        "ip_ended','club_preview_opened','experiment_exposed','feedba"
        "ck_answered','feedback_prompt_delivered','feedback_skipped',"
        "'finances_opened','fixture_abandoned','fixture_finished','fi"
        "xture_started','help_delivered','lineup_opened','lineup_save"
        "d','manager_started','market_bid_accepted','market_listing_c"
        "ancelled','market_listing_created','market_opened','match_co"
        "mmand_accepted','match_command_applied','match_lineup_locked"
        "','match_opened','match_report_opened','match_second_half_st"
        "arted','match_tick_committed','next_action_opened','next_fix"
        "ture_opened','player_last_season_announced','player_retired'"
        ",'result_delivered','result_finalized','stadium_expansion_or"
        "dered','stadium_opened','start_accepted','training_opened','"
        "training_plan_saved')",
    )
