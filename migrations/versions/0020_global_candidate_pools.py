"""Bounded global free player/staff supply and atomic signing receipts."""

from alembic import op

revision = "0020_global_candidate_pools"
down_revision = "0019_season_rollover"
branch_labels = None
depends_on = None
ROLES = (
    "'head_coach','goalkeeping_coach','defence_coach','attack_coach',"
    "'fitness_coach','physiotherapist','scout'"
)


def upgrade() -> None:
    op.execute(
        "\nCREATE TABLE candidate_pools (\n\tkind VARCHAR(16) NOT NULL, \n\tprofile "
        "VARCHAR(32) NOT NULL, \n\thome_league_set_id UUID NOT NULL, \n\ttarget SMA"
        "LLINT NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tPRIMARY KEY (kind, pr"
        "ofile), \n\tCONSTRAINT ck_candidate_pool_scope CHECK ((kind = 'player' A"
        "ND profile = 'player' AND target = 12) OR (kind = 'staff' AND profile "
        "IN ('head_coach','goalkeeping_coach','defence_coach','attack_coach','f"
        "itness_coach','physiotherapist','scout') AND target = 2)), \n\tCONSTRAIN"
        "T ck_candidate_generation CHECK (generation >= 0), \n\tFOREIGN KEY(home_"
        "league_set_id) REFERENCES league_sets (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE candidate_offers (\n\tperson_id UUID NOT NULL, \n\tkind VARC"
        "HAR(16) NOT NULL, \n\tpool_profile VARCHAR(32) NOT NULL, \n\tprofile VARCH"
        "AR(32) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tfee BIGINT NOT NULL,"
        " \n\tweekly_rate INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE"
        " NOT NULL, \n\tPRIMARY KEY (person_id), \n\tCONSTRAINT fk_candidate_pool F"
        "OREIGN KEY(kind, pool_profile) REFERENCES candidate_pools (kind, profi"
        "le), \n\tCONSTRAINT ck_candidate_status CHECK (status IN ('available','s"
        "igned','retired')), \n\tCONSTRAINT ck_candidate_price CHECK (fee > 0 AND"
        " weekly_rate > 0), \n\tFOREIGN KEY(person_id) REFERENCES persons (id)\n)\n"
        "\n"
    )
    op.execute(
        "\nCREATE TABLE candidate_signings (\n\toperation_id UUID NOT NULL, \n\tpers"
        "on_id UUID NOT NULL, \n\tclub_id UUID NOT NULL, \n\townership_id UUID NOT "
        "NULL, \n\tfee BIGINT NOT NULL, \n\tweekly_rate INTEGER NOT NULL, \n\tsigned_"
        "at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (operation_id), \n\t"
        "CONSTRAINT ck_signing_price CHECK (fee > 0 AND weekly_rate > 0), \n\tUNI"
        "QUE (person_id), \n\tFOREIGN KEY(person_id) REFERENCES candidate_offers "
        "(person_id), \n\tFOREIGN KEY(club_id) REFERENCES clubs (id), \n\tFOREIGN K"
        "EY(ownership_id) REFERENCES ownership_periods (id)\n)\n\n"
    )
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint(
        "ck_action_kind",
        "game_action_receipts",
        "kind IN ('club_claimed','club_opened','club_preview_opened','lineup_op"
        "ened','lineup_saved','match_command_accepted','match_opened','match_re"
        "port_opened','next_fixture_opened','notification_preferences','trainin"
        "g_opened','training_plan_saved','language_preferences','finances_opene"
        "d','stadium_opened','stadium_expansion_ordered','market_opened','marke"
        "t_listing_created','market_bid_accepted','market_listing_cancelled','c"
        "andidate_signed')",
    )
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint(
        "ck_journey_name",
        "journey_events",
        "name IN ('action_rejected','bot_unavailable','bot_youth_replacement_cr"
        "eated','campaign_touched','candidate_signed','club_claimed','club_offe"
        "r_delivered','club_opened','club_ownership_ended','club_preview_opened"
        "','experiment_exposed','feedback_answered','feedback_prompt_delivered'"
        ",'feedback_skipped','finances_opened','fixture_abandoned','fixture_fin"
        "ished','fixture_started','help_delivered','lineup_opened','lineup_save"
        "d','manager_started','market_bid_accepted','market_listing_cancelled',"
        "'market_listing_created','market_opened','match_command_accepted','mat"
        "ch_command_applied','match_lineup_locked','match_opened','match_report"
        "_opened','match_second_half_started','match_tick_committed','next_acti"
        "on_opened','next_fixture_opened','player_last_season_announced','playe"
        "r_retired','result_delivered','result_finalized','stadium_expansion_or"
        "dered','stadium_opened','start_accepted','training_opened','training_p"
        "lan_saved')",
    )
    op.drop_constraint("ck_ledger_kind", "ledger_entries", type_="check")
    op.create_check_constraint(
        "ck_ledger_kind",
        "ledger_entries",
        "kind IN ('opening','grant','tickets','sponsor','merchandise',"
        "'player_salary','staff_salary',"
        "'stadium_upkeep','prize','reserve','release','transfer_in','transfer_out','arrears_paid',"
        "'stadium_upgrade','signing_fee')",
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM candidate_pools) OR
        EXISTS (SELECT 1 FROM candidate_offers) OR EXISTS (SELECT 1 FROM candidate_signings) THEN
        RAISE EXCEPTION 'Initialized candidate pools prevent downgrade'; END IF; END $$""")
    op.drop_table("candidate_signings")
    op.drop_table("candidate_offers")
    op.drop_table("candidate_pools")
    op.drop_constraint("ck_ledger_kind", "ledger_entries", type_="check")
    op.create_check_constraint(
        "ck_ledger_kind",
        "ledger_entries",
        "kind IN ('opening','grant','tickets','sponsor','merchandise',"
        "'player_salary','staff_salary',"
        "'stadium_upkeep','prize','reserve','release','transfer_in','transfer_out',"
        "'arrears_paid','stadium_upgrade')",
    )

    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint(
        "ck_action_kind",
        "game_action_receipts",
        "kind IN ('club_claimed','club_opened','club_preview_opened','lineup_opened',"
        "'lineup_saved','match_command_accepted','match_opened','match_report_opened',"
        "'next_fixture_opened','notification_preferences','training_opened',"
        "'training_plan_saved','language_preferences','finances_opened',"
        "'stadium_opened','stadium_expansion_ordered','market_opened',"
        "'market_listing_created','market_bid_accepted','market_listing_cancelled')",
    )
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint(
        "ck_journey_name",
        "journey_events",
        "name IN ('action_rejected','bot_unavailable','bot_youth_replacement_created',"
        "'campaign_touched','club_claimed','club_offer_delivered','club_opened',"
        "'club_ownership_ended','club_preview_opened','experiment_exposed',"
        "'feedback_answered','feedback_prompt_delivered','feedback_skipped',"
        "'finances_opened','fixture_abandoned','fixture_finished','fixture_started',"
        "'help_delivered','lineup_opened','lineup_saved','manager_started',"
        "'market_bid_accepted','market_listing_cancelled','market_listing_created',"
        "'market_opened','match_command_accepted','match_command_applied',"
        "'match_lineup_locked','match_opened','match_report_opened',"
        "'match_second_half_started','match_tick_committed','next_action_opened',"
        "'next_fixture_opened','player_last_season_announced','player_retired',"
        "'result_delivered','result_finalized','stadium_expansion_ordered',"
        "'stadium_opened','start_accepted','training_opened','training_plan_saved')",
    )
