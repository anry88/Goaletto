"""Stable nationalities for every existing person, without touching snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0047_nationalities"
down_revision = "0046_club_credit"
branch_labels = None
depends_on = None

COUNTRIES = (
    "AR",
    "AU",
    "BE",
    "BR",
    "CA",
    "CH",
    "CL",
    "CM",
    "CO",
    "DE",
    "DK",
    "DZ",
    "EG",
    "ES",
    "FR",
    "GB",
    "GH",
    "HR",
    "IT",
    "JP",
    "KR",
    "MA",
    "MX",
    "NG",
    "NL",
    "NO",
    "PL",
    "PT",
    "RS",
    "SE",
    "SN",
    "TR",
)


def upgrade() -> None:
    op.add_column("persons", sa.Column("country_code", sa.String(2)))
    countries = ",".join("'" + c + "'" for c in COUNTRIES)
    op.execute(
        f"UPDATE persons SET country_code = (ARRAY[{countries}])"
        "[((('x' || substr(md5(id::text),1,7))::bit(28)::int) % 32) + 1]"
    )
    op.alter_column("persons", "country_code", nullable=False)
    op.create_check_constraint("ck_person_country", "persons", "country_code ~ '^[A-Z]{2}$'")
    op.execute("""CREATE FUNCTION freeze_person_country() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.country_code IS DISTINCT FROM OLD.country_code THEN
        RAISE EXCEPTION 'Person nationality is immutable'; END IF; RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER tr_person_country BEFORE UPDATE OF country_code ON persons "
        "FOR EACH ROW EXECUTE FUNCTION freeze_person_country()"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM persons) THEN "
        "RAISE EXCEPTION 'Person nationalities must be preserved'; END IF; END $$"
    )
    op.execute("DROP TRIGGER tr_person_country ON persons")
    op.execute("DROP FUNCTION freeze_person_country()")
    op.drop_constraint("ck_person_country", "persons", type_="check")
    op.drop_column("persons", "country_code")
