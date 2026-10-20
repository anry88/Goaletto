"""Persist the end of destructive development resets on real production data."""

import sqlalchemy as sa
from alembic import op

revision = "0048_production_data_guard"
down_revision = "0047_nationalities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_protection",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("protected_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("id = 1", name="ck_data_protection_singleton"),
    )
    op.execute("INSERT INTO data_protection (id) VALUES (1)")
    op.execute("""CREATE FUNCTION guard_production_reset() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF EXISTS (SELECT 1 FROM data_protection WHERE protected_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Real Goaletto data: development reset is forbidden';
        END IF; RETURN NULL; END $$""")
    for table in ("clubs", "persons", "ownership_periods", "ledger_entries", "data_protection"):
        op.execute(
            f"CREATE TRIGGER tr_production_reset BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION guard_production_reset()"
        )
    op.execute("""CREATE FUNCTION freeze_production_latch() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF OLD.protected_at IS NOT NULL AND
        (TG_OP = 'DELETE' OR NEW.protected_at IS DISTINCT FROM OLD.protected_at) THEN
        RAISE EXCEPTION 'Production data protection cannot be removed'; END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER tr_production_latch BEFORE UPDATE OR DELETE ON data_protection "
        "FOR EACH ROW EXECUTE FUNCTION freeze_production_latch()"
    )
    op.execute("REVOKE ALL ON data_protection FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM data_protection WHERE protected_at IS NOT NULL) "
        "THEN RAISE EXCEPTION 'Production guard must be preserved'; END IF; END $$"
    )
    for table in ("clubs", "persons", "ownership_periods", "ledger_entries", "data_protection"):
        op.execute(f"DROP TRIGGER tr_production_reset ON {table}")
    op.drop_table("data_protection")
    op.execute("DROP FUNCTION guard_production_reset()")
    op.execute("DROP FUNCTION freeze_production_latch()")
