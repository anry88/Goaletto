"""Record fresh ownership assets while preserving all earlier histories."""

import sqlalchemy as sa
from alembic import op

revision = "0064_claim_starter_assets"
down_revision = "0063_percent_position_experience"
branch_labels = None
depends_on = None

LEDGER_KINDS = (
    "kind IN ('opening','grant','tickets','sponsor','merchandise',"
    "'player_salary','staff_salary','stadium_upkeep','prize','reserve','release',"
    "'transfer_in','transfer_out','arrears_paid','stadium_upgrade','signing_fee',"
    "'scout_search','academy_upkeep','academy_upgrade','debt_conversion','player_release',"
    "'claim_balance')"
)


def upgrade() -> None:
    op.create_table(
        "club_claim_resets",
        sa.Column(
            "ownership_id", sa.Uuid(), sa.ForeignKey("ownership_periods.id"), primary_key=True
        ),
        sa.Column("club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supporters_before", sa.Integer(), nullable=False),
        sa.Column("supporters_after", sa.Integer(), nullable=False),
        sa.Column("capacity_before", sa.Integer(), nullable=False),
        sa.Column("capacity_after", sa.Integer(), nullable=False),
        sa.Column("academy_level_before", sa.SmallInteger(), nullable=False),
        sa.Column("academy_level_after", sa.SmallInteger(), nullable=False),
    )
    op.create_index("ix_club_claim_resets_club_id", "club_claim_resets", ["club_id"])
    op.execute("REVOKE ALL ON club_claim_resets FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER club_claim_reset_immutable BEFORE UPDATE OR DELETE ON club_claim_resets "
        "FOR EACH ROW EXECUTE FUNCTION protect_finance_history()"
    )
    op.execute(
        "CREATE TRIGGER tr_production_reset BEFORE TRUNCATE ON club_claim_resets "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_production_reset()"
    )
    op.drop_constraint("ck_ledger_kind", "ledger_entries", type_="check")
    op.create_check_constraint("ck_ledger_kind", "ledger_entries", LEDGER_KINDS)
    op.add_column("stadium_expansions", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.drop_constraint("ck_expansion_state", "stadium_expansions", type_="check")
    op.create_check_constraint(
        "ck_expansion_state",
        "stadium_expansions",
        "status IN ('queued','finished','cancelled') AND "
        "((status = 'finished') = (completed_at IS NOT NULL)) AND "
        "((status = 'cancelled') = (cancelled_at IS NOT NULL)) AND "
        "(cancelled_at IS NULL OR cancelled_at >= started_at)",
    )
    op.execute("""CREATE OR REPLACE FUNCTION protect_stadium_expansion() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Paid expansion history is immutable'; END IF;
        IF ROW(NEW.id,NEW.club_id,NEW.ownership_id,NEW.operation_id,NEW.from_capacity,
          NEW.to_capacity,NEW.cost,NEW.started_at,NEW.due_at) IS DISTINCT FROM
          ROW(OLD.id,OLD.club_id,OLD.ownership_id,OLD.operation_id,OLD.from_capacity,
          OLD.to_capacity,OLD.cost,OLD.started_at,OLD.due_at)
          OR (OLD.status <> 'queued' AND NEW IS DISTINCT FROM OLD)
        THEN RAISE EXCEPTION 'Paid expansion quote is immutable'; END IF;
        RETURN NEW;
      END $$""")
    op.execute("""CREATE FUNCTION protect_academy_generation() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.generation IS DISTINCT FROM OLD.generation THEN
          RAISE EXCEPTION 'Academy generation is immutable';
        END IF;
        RETURN NEW;
      END $$""")
    for table, constraint in (
        ("academy_intakes", "academy_intakes_pkey"),
        ("academy_candidates", "uq_academy_club_season_slot"),
        ("academy_prospects", "uq_academy_prospect_slot"),
    ):
        op.add_column(
            table, sa.Column("generation", sa.Integer(), server_default="0", nullable=False)
        )
        if table == "academy_intakes":
            op.drop_constraint(constraint, table, type_="primary")
            op.create_primary_key(constraint, table, ["club_id", "season_id", "generation"])
        else:
            op.drop_constraint(constraint, table, type_="unique")
            op.create_unique_constraint(
                constraint, table, ["club_id", "season_id", "generation", "slot"]
            )
        op.execute(
            f"CREATE TRIGGER academy_generation_immutable BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION protect_academy_generation()"
        )


def downgrade() -> None:
    raise RuntimeError("Ownership starter histories require an additive migration")
