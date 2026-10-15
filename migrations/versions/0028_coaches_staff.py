"""Staff confirmations, repeat employment receipts and one employee per role."""

import sqlalchemy as sa
from alembic import op

revision = "0028_coaches_staff"
down_revision = "0027_scout_searches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE staff_changes (\n\tid UUID NOT NULL, \n\tclub_id UUID NOT NULL, \n\towner"
        "ship_id UUID NOT NULL, \n\trole VARCHAR(32) NOT NULL, \n\toutgoing_id UUID, \n\tincomi"
        "ng_id UUID, \n\tterms JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NU"
        "LL, \n\texecuted_at TIMESTAMP WITH TIME ZONE, \n\texecution_operation_id UUID, \n\tPRI"
        "MARY KEY (id), \n\tCONSTRAINT ck_staff_change_people CHECK (incoming_id IS NOT NUL"
        "L OR outgoing_id IS NOT NULL), \n\tCONSTRAINT ck_staff_change_distinct CHECK (inco"
        "ming_id IS NULL OR outgoing_id IS NULL OR incoming_id != outgoing_id), \n\tCONSTRA"
        "INT ck_staff_change_role CHECK (role IN ('head_coach','goalkeeping_coach','defen"
        "ce_coach','attack_coach','fitness_coach','physiotherapist','scout')), \n\tCONSTRAI"
        "NT ck_staff_change_execution CHECK ((executed_at IS NULL) = (execution_operation"
        "_id IS NULL)), \n\tCONSTRAINT uq_staff_change_execution UNIQUE (execution_operatio"
        "n_id), \n\tFOREIGN KEY(club_id) REFERENCES clubs (id), \n\tFOREIGN KEY(ownership_id)"
        " REFERENCES ownership_periods (id), \n\tFOREIGN KEY(outgoing_id) REFERENCES staff "
        "(person_id), \n\tFOREIGN KEY(incoming_id) REFERENCES staff (person_id)\n)"
    )
    op.create_index(
        "uq_staff_club_role",
        "staff",
        ["club_id", "role"],
        unique=True,
        postgresql_where=sa.text("club_id IS NOT NULL"),
    )
    op.drop_constraint("uq_candidate_signing_career", "candidate_signings", type_="unique")
    op.create_index(
        "uq_candidate_player_signing",
        "candidate_signings",
        ["person_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'player'"),
    )
    op.execute("""CREATE FUNCTION protect_staff_change() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' OR (OLD.executed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'Staff confirmation history is immutable';
      END IF;
      IF ROW(NEW.id,NEW.club_id,NEW.ownership_id,NEW.role,NEW.outgoing_id,NEW.incoming_id,
        NEW.terms,NEW.created_at)
        IS DISTINCT FROM ROW(OLD.id,OLD.club_id,OLD.ownership_id,OLD.role,
        OLD.outgoing_id,OLD.incoming_id,
        OLD.terms,OLD.created_at)
      THEN RAISE EXCEPTION 'Staff quote is immutable'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER staff_change_immutable BEFORE UPDATE OR DELETE ON staff_changes "
        "FOR EACH ROW EXECUTE FUNCTION protect_staff_change()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM staff_changes) OR EXISTS
        (SELECT 1 FROM candidate_signings GROUP BY person_id,kind HAVING count(*)>1)
      THEN RAISE EXCEPTION 'Staff history prevents downgrade'; END IF;
    END $$""")
    op.drop_table("staff_changes")
    op.execute("DROP FUNCTION protect_staff_change()")
    op.drop_index("uq_candidate_player_signing", table_name="candidate_signings")
    op.create_unique_constraint(
        "uq_candidate_signing_career", "candidate_signings", ["person_id", "kind"]
    )
    op.drop_index("uq_staff_club_role", table_name="staff")
