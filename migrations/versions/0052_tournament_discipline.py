"""Tournament cautions, bans and immutable match receipts; no historical backfill."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052_tournament_discipline"
down_revision = "0051_club_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_discipline",
        sa.Column("scope", sa.String(120), primary_key=True),
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("persons.id"), primary_key=True),
        sa.Column("yellows", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("bans", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("yellows IN (0,1) AND bans >= 0", name="ck_discipline_balance"),
    )
    op.create_table(
        "match_discipline",
        sa.Column("match_id", sa.Uuid(), sa.ForeignKey("matches.id"), primary_key=True),
        sa.Column("scope", sa.String(120), nullable=False),
        sa.Column("excluded", postgresql.JSONB(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("state_hash", sa.String(64)),
        sa.CheckConstraint("jsonb_typeof(excluded) = 'array'", name="ck_discipline_excluded"),
        sa.CheckConstraint(
            "(applied_at IS NULL) = (state_hash IS NULL) AND "
            "(applied_at IS NULL OR applied_at >= locked_at)",
            name="ck_discipline_applied",
        ),
    )
    op.create_table(
        "discipline_receipts",
        sa.Column(
            "match_id", sa.Uuid(), sa.ForeignKey("match_discipline.match_id"), primary_key=True
        ),
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("persons.id"), primary_key=True),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("yellows", sa.SmallInteger(), nullable=False),
        sa.Column("dismissed", sa.Boolean(), nullable=False),
        sa.Column("served", sa.Boolean(), nullable=False),
        sa.Column("awarded", sa.SmallInteger(), nullable=False),
        sa.Column("carried_after", sa.SmallInteger(), nullable=False),
        sa.Column("bans_after", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "yellows BETWEEN 0 AND 2 AND carried_after IN (0,1) AND awarded IN (0,1) "
            "AND bans_after >= 0 AND (yellows <> 2 OR dismissed) "
            "AND (NOT served OR (yellows=0 AND NOT dismissed AND awarded=0))",
            name="ck_discipline_receipt",
        ),
    )
    op.execute("""CREATE FUNCTION protect_match_discipline() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' OR (OLD.applied_at IS NOT NULL AND NEW IS DISTINCT FROM OLD)
          OR ROW(NEW.match_id,NEW.scope,NEW.excluded,NEW.locked_at)
          IS DISTINCT FROM ROW(OLD.match_id,OLD.scope,OLD.excluded,OLD.locked_at)
          THEN RAISE EXCEPTION 'Match discipline terms/history are immutable'; END IF;
        RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER match_discipline_immutable BEFORE UPDATE OR DELETE "
        "ON match_discipline FOR EACH ROW EXECUTE FUNCTION protect_match_discipline()"
    )
    op.execute("""CREATE FUNCTION protect_discipline_receipt() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'Discipline receipt history is immutable'; END IF;
        IF EXISTS (SELECT 1 FROM match_discipline WHERE match_id=NEW.match_id
                   AND applied_at IS NOT NULL) THEN
          RAISE EXCEPTION 'Match discipline already finalized'; END IF;
        RETURN NEW; END $$""")
    op.execute(
        "CREATE TRIGGER discipline_receipts_immutable BEFORE INSERT OR UPDATE OR DELETE "
        "ON discipline_receipts FOR EACH ROW EXECUTE FUNCTION protect_discipline_receipt()"
    )


def downgrade() -> None:
    raise RuntimeError("Disciplinary history must be preserved; use an additive migration")
