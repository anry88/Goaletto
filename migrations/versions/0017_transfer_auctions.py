"""Auctions, funded bids, atomic transfer receipts and retirement cancellation."""

from alembic import op

revision = "0017_transfer_auctions"
down_revision = "0016_stadium_experience"
branch_labels = None
depends_on = None

OLD_ACTION = (
    "kind IN ('club_claimed','club_opened','club_preview_opened',"
    "'lineup_opened','lineup_saved','match_command_accepted','mat"
    "ch_opened','match_report_opened','next_fixture_opened','noti"
    "fication_preferences','training_opened','training_plan_saved"
    "','language_preferences','finances_opened','stadium_opened',"
    "'stadium_expansion_ordered')"
)

NEW_ACTION = (
    "kind IN ('club_claimed','club_opened','club_preview_opened',"
    "'lineup_opened','lineup_saved','match_command_accepted','mat"
    "ch_opened','match_report_opened','next_fixture_opened','noti"
    "fication_preferences','training_opened','training_plan_saved"
    "','language_preferences','finances_opened','stadium_opened',"
    "'stadium_expansion_ordered','market_opened','market_listing_"
    "created','market_bid_accepted','market_listing_cancelled')"
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
    "er_retired','result_delivered','result_finalized','stadium_e"
    "xpansion_ordered','stadium_opened','start_accepted','trainin"
    "g_opened','training_plan_saved')"
)

NEW_JOURNEY = (
    "name IN ('action_rejected','bot_unavailable','bot_youth_repl"
    "acement_created','campaign_touched','club_claimed','club_off"
    "er_delivered','club_opened','club_ownership_ended','club_pre"
    "view_opened','experiment_exposed','feedback_answered','feedb"
    "ack_prompt_delivered','feedback_skipped','finances_opened','"
    "fixture_abandoned','fixture_finished','fixture_started','hel"
    "p_delivered','lineup_opened','lineup_saved','manager_started"
    "','market_bid_accepted','market_listing_cancelled','market_l"
    "isting_created','market_opened','match_command_accepted','ma"
    "tch_command_applied','match_lineup_locked','match_opened','m"
    "atch_report_opened','match_second_half_started','match_tick_"
    "committed','next_action_opened','next_fixture_opened','playe"
    "r_last_season_announced','player_retired','result_delivered'"
    ",'result_finalized','stadium_expansion_ordered','stadium_ope"
    "ned','start_accepted','training_opened','training_plan_saved"
    "')"
)


def upgrade() -> None:
    op.execute(
        "CREATE TABLE auctions (\n\tid UUID NOT NULL, \n\toperation_id UU"
        "ID NOT NULL, \n\tperson_id UUID NOT NULL, \n\tseller_club_id UUI"
        "D NOT NULL, \n\tseller_ownership_id UUID NOT NULL, \n\tseason_id"
        " UUID NOT NULL, \n\tleague_set_id UUID NOT NULL, \n\tpolicy_vers"
        "ion VARCHAR(32) DEFAULT 'auction-v1' NOT NULL, \n\tplayer_age "
        "SMALLINT NOT NULL, \n\tinitial_price BIGINT NOT NULL, \n\tweekly"
        "_rate BIGINT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE"
        " NOT NULL, \n\tends_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tst"
        "atus VARCHAR(16) NOT NULL, \n\thighest_bid_id UUID, \n\tclosed_a"
        "t TIMESTAMP WITH TIME ZONE, \n\tclose_reason VARCHAR(32), \n\tPR"
        "IMARY KEY (id), \n\tCONSTRAINT uq_auction_operation UNIQUE (op"
        "eration_id), \n\tCONSTRAINT ck_auction_policy CHECK (policy_ve"
        "rsion = 'auction-v1'), \n\tCONSTRAINT ck_auction_money CHECK ("
        "initial_price >= 100000 AND weekly_rate BETWEEN 180 AND 1700"
        "), \n\tCONSTRAINT ck_auction_dates CHECK (player_age BETWEEN 1"
        "7 AND 38 AND ends_at >= created_at + interval '48 hours'), \n"
        "\tCONSTRAINT ck_auction_status CHECK (status IN ('open','sold"
        "','expired','cancelled') AND (status = 'open') = (closed_at "
        "IS NULL)), \n\tCONSTRAINT ck_auction_reason_state CHECK ((stat"
        "us = 'open') = (close_reason IS NULL)), \n\tCONSTRAINT ck_auct"
        "ion_reason CHECK (close_reason IS NULL OR close_reason IN ('"
        "sold','no_bids','last_season','retired','ownership_changed',"
        "'roster_changed','calendar_changed','withdrawn','operator','"
        "funds_changed')), \n\tFOREIGN KEY(person_id) REFERENCES player"
        "s (person_id), \n\tFOREIGN KEY(seller_club_id) REFERENCES club"
        "s (id), \n\tFOREIGN KEY(seller_ownership_id) REFERENCES owners"
        "hip_periods (id), \n\tFOREIGN KEY(season_id) REFERENCES season"
        "s (id), \n\tFOREIGN KEY(league_set_id) REFERENCES league_sets "
        "(id)\n)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_auction_open_player ON auctions (person_id) WHERE status = 'open'"
    )
    op.execute(
        "CREATE TABLE auction_bids (\n\tid UUID NOT NULL, \n\toperation_i"
        "d UUID NOT NULL, \n\tauction_id UUID NOT NULL, \n\tbuyer_club_id"
        " UUID NOT NULL, \n\tbuyer_ownership_id UUID NOT NULL, \n\treserv"
        "ation_id UUID NOT NULL, \n\tamount BIGINT NOT NULL, \n\taccepted"
        "_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT uq_bid_operation UNIQUE (operation_id), \n\tCONSTR"
        "AINT uq_bid_auction_identity UNIQUE (auction_id, id), \n\tCONS"
        "TRAINT uq_bid_reservation UNIQUE (reservation_id), \n\tCONSTRA"
        "INT ck_bid_amount CHECK (amount >= 100000), \n\tFOREIGN KEY(au"
        "ction_id) REFERENCES auctions (id), \n\tFOREIGN KEY(buyer_club"
        "_id) REFERENCES clubs (id), \n\tFOREIGN KEY(buyer_ownership_id"
        ") REFERENCES ownership_periods (id), \n\tFOREIGN KEY(reservati"
        "on_id) REFERENCES money_reservations (id)\n)"
    )
    op.execute(
        "CREATE TABLE transfer_receipts (\n\tid UUID NOT NULL, \n\tauctio"
        "n_id UUID NOT NULL, \n\tperson_id UUID NOT NULL, \n\tseller_club"
        "_id UUID NOT NULL, \n\tbuyer_club_id UUID NOT NULL, \n\tprice BI"
        "GINT NOT NULL, \n\ttransferred_at TIMESTAMP WITH TIME ZONE NOT"
        " NULL, \n\tsnapshot JSONB NOT NULL, \n\tPRIMARY KEY (id), \n\tCONS"
        "TRAINT uq_transfer_auction UNIQUE (auction_id), \n\tCONSTRAINT"
        " ck_transfer_price CHECK (price >= 100000), \n\tFOREIGN KEY(au"
        "ction_id) REFERENCES auctions (id), \n\tFOREIGN KEY(person_id)"
        " REFERENCES persons (id), \n\tFOREIGN KEY(seller_club_id) REFE"
        "RENCES clubs (id), \n\tFOREIGN KEY(buyer_club_id) REFERENCES c"
        "lubs (id)\n)"
    )
    op.create_foreign_key(
        "fk_auction_highest_bid",
        "auctions",
        "auction_bids",
        ["id", "highest_bid_id"],
        ["auction_id", "id"],
    )
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", NEW_ACTION)
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint("ck_journey_name", "journey_events", NEW_JOURNEY)
    op.execute(
        "CREATE TRIGGER bid_immutable BEFORE UPDATE OR DELETE ON auction_bids "
        "FOR EACH ROW EXECUTE FUNCTION protect_finance_history()"
    )
    op.execute(
        "CREATE TRIGGER transfer_immutable BEFORE UPDATE OR DELETE ON transfer_receipts "
        "FOR EACH ROW EXECUTE FUNCTION protect_finance_history()"
    )
    op.execute("""CREATE FUNCTION protect_auction_quote() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Auction history is immutable'; END IF;
        IF ROW(NEW.id,NEW.operation_id,NEW.person_id,NEW.seller_club_id,NEW.seller_ownership_id,
            NEW.season_id,NEW.league_set_id,NEW.policy_version,NEW.player_age,NEW.initial_price,
            NEW.weekly_rate,NEW.created_at) IS DISTINCT FROM
           ROW(OLD.id,OLD.operation_id,OLD.person_id,OLD.seller_club_id,OLD.seller_ownership_id,
            OLD.season_id,OLD.league_set_id,OLD.policy_version,OLD.player_age,OLD.initial_price,
            OLD.weekly_rate,OLD.created_at)
          OR (OLD.status <> 'open' AND NEW IS DISTINCT FROM OLD)
          OR NEW.ends_at < OLD.ends_at
        THEN RAISE EXCEPTION 'Auction quote or terminal outcome is immutable'; END IF;
        RETURN NEW;
      END $$""")
    op.execute(
        "CREATE TRIGGER auction_quote_immutable BEFORE UPDATE OR DELETE ON auctions "
        "FOR EACH ROW EXECUTE FUNCTION protect_auction_quote()"
    )
    # Any persisted retirement flag, including future rollover code, enqueues
    # cancellation in the same transaction. Bid/settlement also recheck under lock.
    op.execute("""CREATE FUNCTION enqueue_player_trade_cancellation() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.status IN ('last_season','retired') AND
          ROW(NEW.status,NEW.last_season_id) IS DISTINCT FROM ROW(OLD.status,OLD.last_season_id)
        THEN INSERT INTO jobs(id,dedupe_key,kind,payload,due_at,status,attempts)
          VALUES(gen_random_uuid(),'market:prohibit:v1:' || NEW.person_id::text || ':' ||
            NEW.status || ':' || coalesce(NEW.last_season_id::text,'-'), 'market.cancel_player',
            jsonb_build_object('version',1,'person_id',NEW.person_id::text),now(),'pending',0)
          ON CONFLICT(dedupe_key) DO NOTHING;
        END IF;
        RETURN NEW;
      END $$""")
    op.execute(
        "CREATE TRIGGER player_trade_prohibited AFTER UPDATE OF status,last_season_id "
        "ON players FOR EACH ROW EXECUTE FUNCTION enqueue_player_trade_cancellation()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM auctions) OR
      EXISTS (SELECT 1 FROM auction_bids) OR EXISTS (SELECT 1 FROM transfer_receipts) OR
      EXISTS (SELECT 1 FROM game_action_receipts WHERE kind LIKE 'market_%') OR
      EXISTS (SELECT 1 FROM journey_events WHERE name LIKE 'market_%')
      THEN RAISE EXCEPTION 'Reconcile market activity before downgrade'; END IF; END $$""")
    op.execute("DELETE FROM jobs WHERE kind IN ('market.close','market.cancel_player')")
    op.execute("DROP TRIGGER player_trade_prohibited ON players")
    op.execute("DROP FUNCTION enqueue_player_trade_cancellation()")
    op.execute("DROP TRIGGER bid_immutable ON auction_bids")
    op.execute("DROP TRIGGER transfer_immutable ON transfer_receipts")
    op.execute("DROP TRIGGER auction_quote_immutable ON auctions")
    op.execute("DROP FUNCTION protect_auction_quote()")
    op.drop_constraint("ck_action_kind", "game_action_receipts", type_="check")
    op.create_check_constraint("ck_action_kind", "game_action_receipts", OLD_ACTION)
    op.drop_constraint("ck_journey_name", "journey_events", type_="check")
    op.create_check_constraint("ck_journey_name", "journey_events", OLD_JOURNEY)
    op.drop_constraint("fk_auction_highest_bid", "auctions", type_="foreignkey")
    op.drop_table("transfer_receipts")
    op.drop_table("auction_bids")
    op.drop_index("uq_auction_open_player", table_name="auctions")
    op.drop_table("auctions")
