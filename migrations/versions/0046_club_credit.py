"""Signed balances and durable insolvency, preserving previous ledger entries."""

import sqlalchemy as sa
from alembic import op

revision = "0046_club_credit"
down_revision = "0045_club_names"
branch_labels = None
depends_on = None

OLD_KINDS = (
    "kind IN ('opening','grant','tickets','sponsor','merchandise',"
    "'player_salary','staff_salary','stadium_upkeep','prize','reserve','release',"
    "'transfer_in','transfer_out','arrears_paid','stadium_upgrade','signing_fee',"
    "'scout_search','academy_upkeep','academy_upgrade')"
)
NEW_KINDS = OLD_KINDS[:-1] + ",'debt_conversion')"
OLD_END = (
    "(ended_at IS NULL AND end_reason IS NULL) OR "
    "(ended_at IS NOT NULL AND end_reason IS NOT NULL AND "
    "end_reason IN ('inactive', 'operator', 'voluntary'))"
)


def upgrade() -> None:
    op.add_column("clubs", sa.Column("bankrupt_at", sa.DateTime(timezone=True)))
    op.add_column("managers", sa.Column("bankruptcy_until", sa.DateTime(timezone=True)))
    op.add_column(
        "finance_accounts",
        sa.Column("credit_limit", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "finance_accounts", sa.Column("valuation", sa.JSON(), server_default="{}", nullable=False)
    )
    # JSONB matches the application and supports ordinary operator inspection.
    op.execute(
        "ALTER TABLE finance_accounts ALTER COLUMN valuation TYPE jsonb USING valuation::jsonb"
    )
    for table, name, condition in (
        ("clubs", "ck_club_budget", "budget >= -9223372036854775807"),
        (
            "ledger_entries",
            "ck_ledger_balances",
            "balance_after >= -9223372036854775807 AND reserved_after >= 0 AND arrears_after >= 0",
        ),
        ("ledger_entries", "ck_ledger_kind", NEW_KINDS),
        (
            "finance_accounts",
            "ck_finance_account",
            "credit_limit >= 0 AND reserved >= 0 AND arrears >= 0 AND sequence >= 1",
        ),
        (
            "ownership_periods",
            "ck_ownership_end_reason",
            OLD_END.replace("'voluntary'", "'voluntary', 'bankrupt'"),
        ),
    ):
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, condition)
    op.create_table(
        "bankruptcy_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("ownership_id", sa.Uuid(), sa.ForeignKey("ownership_periods.id"), unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("balance", sa.BigInteger(), nullable=False),
        sa.Column("assets", sa.BigInteger(), nullable=False),
        sa.Column("policy", sa.String(32), nullable=False),
        sa.Column("valuation", sa.dialects.postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_bankruptcy_receipts_club_id", "bankruptcy_receipts", ["club_id"])
    op.execute("REVOKE ALL ON bankruptcy_receipts FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER bankruptcy_immutable BEFORE UPDATE OR DELETE ON bankruptcy_receipts "
        "FOR EACH ROW EXECUTE FUNCTION protect_finance_history()"
    )
    # Debt conversion is a new immutable movement, never a rewrite of history.
    op.execute("""
        INSERT INTO ledger_entries
          (id, club_id, operation_key, sequence, kind, cash_delta, reserved_delta,
           arrears_delta, balance_after, reserved_after, arrears_after, occurred_at, request)
        SELECT md5(a.club_id::text || '-debt-v1-conversion')::uuid,
          a.club_id, 'debt-v1-conversion', a.sequence + 1, 'debt_conversion',
          -a.arrears, 0, -a.arrears, c.budget - a.arrears, a.reserved, 0,
          CURRENT_TIMESTAMP, jsonb_build_object('kind', 'debt_conversion', 'amount', a.arrears,
          'details', jsonb_build_object('policy', 'club-assets-v1'))
        FROM finance_accounts a JOIN clubs c ON c.id=a.club_id WHERE a.arrears > 0
    """)
    op.execute(
        "UPDATE clubs c SET budget=c.budget-a.arrears FROM finance_accounts a "
        "WHERE a.club_id=c.id AND a.arrears > 0"
    )
    op.execute("UPDATE finance_accounts SET sequence=sequence+1, arrears=0 WHERE arrears > 0")


def downgrade() -> None:
    # No lossy conversion of real negative balances or insolvency history.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM bankruptcy_receipts)
        OR EXISTS (SELECT 1 FROM ledger_entries WHERE balance_after < 0 OR kind='debt_conversion')
        THEN RAISE EXCEPTION 'Credit history forbids destructive downgrade'; END IF;
        END $$""")
    op.drop_table("bankruptcy_receipts")
    for table, name, condition in (
        ("clubs", "ck_club_budget", "budget >= 0"),
        (
            "ledger_entries",
            "ck_ledger_balances",
            "balance_after >= 0 AND reserved_after >= 0 AND "
            "reserved_after <= balance_after AND arrears_after >= 0",
        ),
        ("ledger_entries", "ck_ledger_kind", OLD_KINDS),
        (
            "finance_accounts",
            "ck_finance_account",
            "reserved >= 0 AND arrears >= 0 AND sequence >= 1",
        ),
        ("ownership_periods", "ck_ownership_end_reason", OLD_END),
    ):
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, condition)
    op.drop_column("finance_accounts", "valuation")
    op.drop_column("finance_accounts", "credit_limit")
    op.drop_column("managers", "bankruptcy_until")
    op.drop_column("clubs", "bankrupt_at")
