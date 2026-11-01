"""Pin teamwide specialist sessions without rewriting any existing plans or results."""

from alembic import op

revision = "0062_teamwide_training"
down_revision = "0061_default_club_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_training_policy", "training_plans", type_="check")
    op.create_check_constraint(
        "ck_training_policy",
        "training_plans",
        "policy_version IN ('training-daily-v1','training-specialists-v2',"
        "'training-specialists-v3')",
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM training_plans WHERE policy_version='training-specialists-v3')
        THEN RAISE EXCEPTION 'Teamwide training pins/history prevent downgrade'; END IF;
    END $$""")
    op.drop_constraint("ck_training_policy", "training_plans", type_="check")
    op.create_check_constraint(
        "ck_training_policy",
        "training_plans",
        "policy_version IN ('training-daily-v1','training-specialists-v2')",
    )
