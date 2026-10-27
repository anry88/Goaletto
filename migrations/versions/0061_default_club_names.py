"""Preserve audited default-name updates; historical match documents stay unchanged."""

import sqlalchemy as sa
from alembic import op

revision = "0061_default_club_names"
down_revision = "0060_match_injury_severity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "default_club_name_receipts",
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), primary_key=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("before_name", sa.String(80), nullable=False),
        sa.Column("after_name", sa.String(80), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "policy_version = 'english-clubs-v1'", name="ck_default_club_name_policy"
        ),
    )
    op.execute("""CREATE FUNCTION protect_default_club_name_receipt() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Default club name history is immutable'; END $$""")
    op.execute(
        "CREATE TRIGGER default_club_name_immutable BEFORE UPDATE OR DELETE "
        "ON default_club_name_receipts FOR EACH ROW "
        "EXECUTE FUNCTION protect_default_club_name_receipt()"
    )
    op.execute("REVOKE ALL ON default_club_name_receipts FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError("Club name history must be preserved; use an additive migration")
