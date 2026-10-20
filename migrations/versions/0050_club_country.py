"""One-time club country and immutable nationality on academy offers."""

import sqlalchemy as sa
from alembic import op

revision = "0050_club_country"
down_revision = "0049_stars_cosmetics"
branch_labels = None
depends_on = None

# Frozen 0047 mapping: old unmaterialized academy offers retain their old flag.
LEGACY_COUNTRIES = (
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
    op.add_column("clubs", sa.Column("country_code", sa.String(2)))
    op.add_column("clubs", sa.Column("country_selected_at", sa.DateTime(timezone=True)))
    op.add_column("clubs", sa.Column("country_ownership_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_club_country_owner", "clubs", "ownership_periods", ["country_ownership_id"], ["id"]
    )
    op.create_check_constraint(
        "ck_club_country",
        "clubs",
        "(country_code IS NULL AND country_selected_at IS NULL "
        "AND country_ownership_id IS NULL) OR (country_code IS NOT NULL "
        "AND country_code ~ '^[A-Z]{2}$' AND country_selected_at IS NOT NULL "
        "AND country_ownership_id IS NOT NULL)",
    )
    op.add_column("academy_candidates", sa.Column("country_code", sa.String(2)))
    countries = ",".join("'" + c + "'" for c in LEGACY_COUNTRIES)
    # ADD COLUMN holds ACCESS EXCLUSIVE until commit. Only the new field is
    # backfilled; every previously frozen column stays unchanged.
    op.execute("ALTER TABLE academy_candidates DISABLE TRIGGER academy_candidates_immutable")
    op.execute(
        "UPDATE academy_candidates a SET country_code = COALESCE("
        "(SELECT p.country_code FROM persons p WHERE p.id=a.id), "
        f"(ARRAY[{countries}])[((('x' || substr(md5(a.id::text),1,7))::bit(28)::int) % 32) + 1])"
    )
    op.execute("ALTER TABLE academy_candidates ENABLE TRIGGER academy_candidates_immutable")
    op.alter_column("academy_candidates", "country_code", nullable=False)
    op.create_check_constraint(
        "ck_academy_country", "academy_candidates", "country_code ~ '^[A-Z]{2}$'"
    )
    op.execute("""CREATE TRIGGER tr_academy_country BEFORE UPDATE OF country_code
        ON academy_candidates FOR EACH ROW EXECUTE FUNCTION freeze_person_country()""")
    op.execute("""CREATE FUNCTION freeze_club_country() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.country_code IS NOT NULL AND
             (NEW.country_code, NEW.country_selected_at, NEW.country_ownership_id)
             IS DISTINCT FROM (OLD.country_code, OLD.country_selected_at, OLD.country_ownership_id)
          THEN RAISE EXCEPTION 'Club country is already selected'; END IF;
          IF OLD.country_code IS NULL AND NEW.country_code IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM ownership_periods p WHERE p.id=NEW.country_ownership_id
            AND p.club_id=NEW.id AND p.ended_at IS NULL
            AND p.claimed_at <= NEW.country_selected_at AND p.expires_at > NEW.country_selected_at
          ) THEN RAISE EXCEPTION 'Current club ownership required for country choice'; END IF;
          RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER tr_club_country BEFORE UPDATE OF country_code,
        country_selected_at, country_ownership_id ON clubs
        FOR EACH ROW EXECUTE FUNCTION freeze_club_country()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM clubs WHERE country_code IS NOT NULL)
        OR EXISTS (SELECT 1 FROM academy_candidates) THEN
        RAISE EXCEPTION 'Club and academy countries must be preserved'; END IF;
        END $$""")
    op.execute("DROP TRIGGER tr_club_country ON clubs")
    op.execute("DROP FUNCTION freeze_club_country()")
    op.execute("DROP TRIGGER tr_academy_country ON academy_candidates")
    op.drop_constraint("ck_academy_country", "academy_candidates", type_="check")
    op.drop_column("academy_candidates", "country_code")
    op.drop_constraint("ck_club_country", "clubs", type_="check")
    op.drop_constraint("fk_club_country_owner", "clubs", type_="foreignkey")
    op.drop_column("clubs", "country_ownership_id")
    op.drop_column("clubs", "country_selected_at")
    op.drop_column("clubs", "country_code")
