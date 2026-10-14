"""Keep player purchase and staff hire receipts for the same person independently."""

import sqlalchemy as sa
from alembic import op

revision = "0021_candidate_career_receipts"
down_revision = "0020_global_candidate_pools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidate_signings", sa.Column("kind", sa.String(16), nullable=True))
    op.add_column("candidate_signings", sa.Column("profile", sa.String(32), nullable=True))
    op.execute("""UPDATE candidate_signings s SET kind = o.kind, profile = o.profile
        FROM candidate_offers o WHERE o.person_id = s.person_id""")
    op.alter_column("candidate_signings", "kind", nullable=False)
    op.alter_column("candidate_signings", "profile", nullable=False)
    op.drop_constraint("candidate_signings_person_id_key", "candidate_signings", type_="unique")
    op.create_unique_constraint(
        "uq_candidate_signing_career", "candidate_signings", ["person_id", "kind"]
    )
    op.create_check_constraint(
        "ck_signing_kind", "candidate_signings", "kind IN ('player','staff')"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM candidate_signings GROUP BY person_id HAVING count(*) > 1)
        THEN RAISE EXCEPTION 'Two-career signing history prevents downgrade'; END IF;
        END $$""")
    op.drop_constraint("ck_signing_kind", "candidate_signings", type_="check")
    op.drop_constraint("uq_candidate_signing_career", "candidate_signings", type_="unique")
    op.create_unique_constraint(
        "candidate_signings_person_id_key", "candidate_signings", ["person_id"]
    )
    op.drop_column("candidate_signings", "profile")
    op.drop_column("candidate_signings", "kind")
