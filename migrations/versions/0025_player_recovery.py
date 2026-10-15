"""Durable physical condition and immutable physio qualification at employment."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0025_player_recovery"
down_revision = "0024_cycle_calendar"
branch_labels = None
depends_on = None


def salary_guard(include_skill: bool) -> None:
    new = ",NEW.recovery_skill" if include_skill else ""
    old = ",OLD.recovery_skill" if include_skill else ""
    op.execute(f"""CREATE OR REPLACE FUNCTION protect_salary_contract() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Salary history is immutable'; END IF;
      IF ROW(NEW.id,NEW.person_id,NEW.club_id,NEW.role,NEW.weekly_rate,
        NEW.policy_version,NEW.started_at{new}) IS DISTINCT FROM
        ROW(OLD.id,OLD.person_id,OLD.club_id,OLD.role,OLD.weekly_rate,
        OLD.policy_version,OLD.started_at{old}) OR
        (OLD.ended_at IS NOT NULL AND NEW.ended_at IS DISTINCT FROM OLD.ended_at)
      THEN RAISE EXCEPTION 'Salary contract is immutable except its first close'; END IF;
      RETURN NEW; END $$""")


def upgrade() -> None:
    for column in (
        sa.Column("possible_club_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column("stage", sa.String(80)),
        sa.Column("leg", sa.SmallInteger()),
        sa.Column("neutral", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        op.add_column("fixture_plans", column)
    op.add_column(
        "salary_contracts",
        sa.Column("recovery_skill", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_salary_recovery_skill", "salary_contracts", "recovery_skill BETWEEN 0 AND 20"
    )
    salary_guard(True)
    op.create_table(
        "player_recovery",
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("players.person_id"), primary_key=True),
        sa.Column("energy_bps", sa.Integer(), nullable=False),
        sa.Column("remainder", sa.Integer(), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stamina", sa.SmallInteger(), nullable=False),
        sa.Column("age", sa.SmallInteger(), nullable=False),
        sa.Column("active_match_id", sa.Uuid(), sa.ForeignKey("matches.id")),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.CheckConstraint("energy_bps BETWEEN 0 AND 10000", name="ck_recovery_energy"),
        sa.CheckConstraint("remainder BETWEEN 0 AND 86399", name="ck_recovery_remainder"),
        sa.CheckConstraint(
            "stamina BETWEEN 1 AND 20 AND age BETWEEN 17 AND 38", name="ck_recovery_traits"
        ),
        sa.CheckConstraint("policy_version = 'recovery-v1'", name="ck_recovery_policy"),
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM player_recovery) OR
      EXISTS (SELECT 1 FROM salary_contracts WHERE recovery_skill > 0) OR
      EXISTS (SELECT 1 FROM fixture_plans WHERE stage IS NOT NULL OR leg IS NOT NULL
              OR neutral OR possible_club_ids <> '[]'::jsonb) THEN
      RAISE EXCEPTION 'Recovery history prevents downgrade'; END IF; END $$""")
    op.drop_table("player_recovery")
    for name in ("possible_club_ids", "stage", "leg", "neutral"):
        op.drop_column("fixture_plans", name)
    salary_guard(False)
    op.drop_constraint("ck_salary_recovery_skill", "salary_contracts", type_="check")
    op.drop_column("salary_contracts", "recovery_skill")
