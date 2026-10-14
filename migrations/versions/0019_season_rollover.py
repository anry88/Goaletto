"""Atomic season rollover receipts and reusable approved portrait bank."""

from alembic import op

revision = "0019_season_rollover"
down_revision = "0018_voluntary_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("persons_appearance_identity_id_key", "persons", type_="unique")
    op.create_index("ix_persons_appearance_identity_id", "persons", ["appearance_identity_id"])
    op.execute(
        "\nCREATE TABLE season_rollover_receipts (\n\tseason_id UUID NOT NULL, \n\tn"
        "ext_season_id UUID NOT NULL, \n\tpolicy_version VARCHAR(80) NOT NULL, \n\t"
        "completed_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcounts JSONB NOT NUL"
        "L, \n\tmovements JSONB NOT NULL, \n\tPRIMARY KEY (season_id), \n\tCONSTRAINT"
        " ck_rollover_distinct_seasons CHECK (season_id <> next_season_id), \n\tC"
        "ONSTRAINT ck_rollover_policy CHECK (policy_version = 'season-rollover-"
        "v1'), \n\tCONSTRAINT ck_rollover_objects CHECK (jsonb_typeof(counts) = '"
        "object' AND jsonb_typeof(movements) = 'object'), \n\tFOREIGN KEY(season_"
        "id) REFERENCES seasons (id), \n\tUNIQUE (next_season_id), \n\tFOREIGN KEY("
        "next_season_id) REFERENCES seasons (id)\n)\n\n"
    )
    op.execute("""
        CREATE FUNCTION protect_rollover_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Season rollover receipts are immutable'; END; $$;
    """)
    op.execute("""
        CREATE TRIGGER immutable_rollover_receipt BEFORE UPDATE OR DELETE
        ON season_rollover_receipts FOR EACH ROW EXECUTE FUNCTION protect_rollover_receipt();
    """)
    # Upgrade active worlds without releasing deliberately held match fixtures.
    op.execute("""
        UPDATE jobs SET status = 'pending', last_error = NULL
        WHERE kind = 'season.rollover' AND status = 'held'
          AND payload->>'season_id' IN (SELECT id::text FROM seasons WHERE status = 'active')
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM season_rollover_receipts) THEN
                RAISE EXCEPTION 'Committed season rollover prevents downgrade';
            END IF;
            IF EXISTS (SELECT 1 FROM persons GROUP BY appearance_identity_id HAVING count(*) > 1)
            THEN RAISE EXCEPTION 'Reused portraits prevent restoring unique-person constraint';
            END IF;
        END $$;
    """)
    op.drop_table("season_rollover_receipts")
    op.execute("DROP FUNCTION protect_rollover_receipt()")
    op.drop_index("ix_persons_appearance_identity_id", table_name="persons")
    op.create_unique_constraint(
        "persons_appearance_identity_id_key", "persons", ["appearance_identity_id"]
    )
