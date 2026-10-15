"""Pin new market quotes to economy-v2; preserve all signed rates and ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0026_economy_market"
down_revision = "0025_player_recovery"
branch_labels = None
depends_on = None


def auction_guard(extra: bool) -> None:
    new, old = (",NEW.economy_policy", ",OLD.economy_policy") if extra else ("", "")
    op.execute(f"""CREATE OR REPLACE FUNCTION protect_auction_quote() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Auction history is immutable'; END IF;
        IF ROW(NEW.id,NEW.operation_id,NEW.person_id,NEW.seller_club_id,NEW.seller_ownership_id,
            NEW.season_id,NEW.league_set_id,NEW.policy_version,NEW.player_age,NEW.initial_price,
            NEW.weekly_rate,NEW.created_at{new}) IS DISTINCT FROM
           ROW(OLD.id,OLD.operation_id,OLD.person_id,OLD.seller_club_id,OLD.seller_ownership_id,
            OLD.season_id,OLD.league_set_id,OLD.policy_version,OLD.player_age,OLD.initial_price,
            OLD.weekly_rate,OLD.created_at{old})
          OR (OLD.status <> 'open' AND NEW IS DISTINCT FROM OLD)
          OR NEW.ends_at < OLD.ends_at
        THEN RAISE EXCEPTION 'Auction quote or terminal outcome is immutable'; END IF;
        RETURN NEW;
      END $$""")


def upgrade() -> None:
    for table in ("candidate_offers", "candidate_signings", "auctions"):
        op.add_column(
            table,
            sa.Column(
                "economy_policy", sa.String(32), nullable=False, server_default="economy-cycle-v1"
            ),
        )
        op.create_check_constraint(
            f"ck_{table}_economy",
            table,
            "economy_policy IN ('economy-cycle-v1','economy-cycle-v2')",
        )
    op.drop_constraint("ck_auction_money", "auctions", type_="check")
    op.create_check_constraint(
        "ck_auction_money",
        "auctions",
        "initial_price >= 100000 AND weekly_rate BETWEEN 1 AND 200000",
    )
    op.drop_constraint("ck_finance_policy", "finance_cycles", type_="check")
    op.create_check_constraint(
        "ck_finance_policy",
        "finance_cycles",
        "policy_version IN ('economy-cycle-v1','economy-cycle-v2')",
    )
    auction_guard(True)


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS
      (SELECT 1 FROM salary_contracts WHERE policy_version='economy-cycle-v2') OR EXISTS
      (SELECT 1 FROM finance_cycles WHERE policy_version='economy-cycle-v2') OR EXISTS
      (SELECT 1 FROM candidate_offers WHERE economy_policy='economy-cycle-v2') OR EXISTS
      (SELECT 1 FROM candidate_signings WHERE economy_policy='economy-cycle-v2') OR EXISTS
      (SELECT 1 FROM auctions WHERE economy_policy='economy-cycle-v2')
      THEN RAISE EXCEPTION 'Economy-v2 history prevents downgrade'; END IF; END $$""")
    auction_guard(False)
    for table in ("candidate_offers", "candidate_signings", "auctions"):
        op.drop_constraint(f"ck_{table}_economy", table, type_="check")
        op.drop_column(table, "economy_policy")
    op.drop_constraint("ck_auction_money", "auctions", type_="check")
    op.create_check_constraint(
        "ck_auction_money",
        "auctions",
        "initial_price >= 100000 AND weekly_rate BETWEEN 180 AND 1700",
    )
    op.drop_constraint("ck_finance_policy", "finance_cycles", type_="check")
    op.create_check_constraint(
        "ck_finance_policy", "finance_cycles", "policy_version = 'economy-cycle-v1'"
    )
