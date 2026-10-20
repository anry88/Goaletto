"""Stars receipts, single-slot rename grants and administrator-reviewed refunds."""

from alembic import op

revision = "0049_stars_cosmetics"
down_revision = "0048_production_data_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE star_orders (
            id UUID NOT NULL,
            manager_id UUID NOT NULL,
            payer_id BIGINT NOT NULL,
            club_id UUID NOT NULL,
            ownership_id UUID NOT NULL,
            product VARCHAR(16) NOT NULL,
            amount INTEGER NOT NULL,
            currency VARCHAR(3) DEFAULT 'XTR' NOT NULL,
            status VARCHAR(16) DEFAULT 'created' NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            precheck_id VARCHAR(200),
            PRIMARY KEY (id),
            CONSTRAINT ck_star_order_product CHECK (
                product IN ('rename',
                'vip') AND currency='XTR' AND amount > 0),
            CONSTRAINT ck_star_order_status CHECK (
                status IN ('created',
                'approved',
                'paid',
                'cancelled')),
            FOREIGN KEY(manager_id) REFERENCES managers (id),
            FOREIGN KEY(club_id) REFERENCES clubs (id),
            FOREIGN KEY(ownership_id) REFERENCES ownership_periods (id),
            UNIQUE (precheck_id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_star_orders_club_id ON star_orders (club_id)
    """)
    op.execute("""
        REVOKE ALL ON star_orders FROM PUBLIC
    """)
    op.execute("""
        CREATE TABLE star_payments (
            id UUID NOT NULL,
            order_id UUID,
            charge_id VARCHAR(256) NOT NULL,
            payer_id BIGINT NOT NULL,
            club_id UUID,
            product VARCHAR(16) NOT NULL,
            amount INTEGER NOT NULL,
            currency VARCHAR(3) NOT NULL,
            invoice_payload VARCHAR(128) NOT NULL,
            status VARCHAR(16) NOT NULL,
            received_at TIMESTAMP WITH TIME ZONE NOT NULL,
            vip_until TIMESTAMP WITH TIME ZONE,
            refunded_at TIMESTAMP WITH TIME ZONE,
            PRIMARY KEY (id),
            CONSTRAINT ck_star_payment_amount CHECK (
                amount > 0 AND payer_id > 0),
            CONSTRAINT ck_star_payment_status CHECK (
                status IN ('delivered',
                'unfulfilled')),
            FOREIGN KEY(order_id) REFERENCES star_orders (id),
            UNIQUE (charge_id),
            FOREIGN KEY(club_id) REFERENCES clubs (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_star_payments_club_id ON star_payments (club_id)
    """)
    op.execute("""
        CREATE INDEX ix_star_payments_payer_id ON star_payments (payer_id)
    """)
    op.execute("""
        REVOKE ALL ON star_payments FROM PUBLIC
    """)
    op.execute("""
        CREATE TABLE cosmetic_grants (
            id UUID NOT NULL,
            club_id UUID NOT NULL,
            ownership_id UUID,
            payment_id UUID,
            season_id UUID,
            origin VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            consumed_at TIMESTAMP WITH TIME ZONE,
            PRIMARY KEY (id),
            CONSTRAINT ck_cosmetic_grant_origin CHECK (
                origin IN ('initial',
                'stars',
                'vip')),
            CONSTRAINT ck_cosmetic_grant_status CHECK (
                status IN ('available',
                'consumed',
                'revoked',
                'skipped')),
            CONSTRAINT uq_vip_offseason_grant UNIQUE (club_id,
        season_id,
        origin),
            FOREIGN KEY(club_id) REFERENCES clubs (id),
            FOREIGN KEY(ownership_id) REFERENCES ownership_periods (id),
            FOREIGN KEY(payment_id) REFERENCES star_payments (id),
            FOREIGN KEY(season_id) REFERENCES seasons (id)
        )
    """)
    op.execute("""
        CREATE INDEX ix_cosmetic_grants_club_id ON cosmetic_grants (club_id)
    """)
    op.execute("""
        CREATE UNIQUE INDEX uq_club_available_rename ON cosmetic_grants (club_id)
        WHERE status='available'
    """)
    op.execute("""
        REVOKE ALL ON cosmetic_grants FROM PUBLIC
    """)
    op.execute("""
        CREATE TABLE club_rename_receipts (
            id UUID NOT NULL,
            ownership_id UUID NOT NULL,
            club_id UUID NOT NULL,
            grant_id UUID NOT NULL,
            old_name VARCHAR(80) NOT NULL,
            new_name VARCHAR(30) NOT NULL,
            changed_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(ownership_id) REFERENCES ownership_periods (id),
            FOREIGN KEY(club_id) REFERENCES clubs (id),
            UNIQUE (grant_id),
            FOREIGN KEY(grant_id) REFERENCES cosmetic_grants (id)
        )
    """)
    op.execute("""
        REVOKE ALL ON club_rename_receipts FROM PUBLIC
    """)
    op.execute("""
        CREATE TABLE pay_support_requests (
            id UUID NOT NULL,
            payment_id UUID NOT NULL,
            payer_id BIGINT NOT NULL,
            admin_chat_id BIGINT NOT NULL,
            reason VARCHAR(1000) NOT NULL,
            status VARCHAR(16) DEFAULT 'pending' NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            decided_by BIGINT,
            lease_until TIMESTAMP WITH TIME ZONE,
            attempts INTEGER DEFAULT '0' NOT NULL,
            audit JSONB DEFAULT '[]' NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_pay_support_status CHECK (
                status IN ('pending',
                'info',
                'rejected',
                'approved',
                'processing',
                'unknown',
                'failed',
                'refunded')),
            UNIQUE (payment_id),
            FOREIGN KEY(payment_id) REFERENCES star_payments (id)
        )
    """)
    op.execute("""
        REVOKE ALL ON pay_support_requests FROM PUBLIC
    """)
    op.create_index("ix_pay_support_pending", "pay_support_requests", ["status", "created_at"])
    op.execute("""
        CREATE TRIGGER rename_receipt_immutable
        BEFORE UPDATE OR DELETE ON club_rename_receipts
        FOR EACH ROW EXECUTE FUNCTION protect_finance_history()
    """)
    op.execute("""
        CREATE FUNCTION freeze_star_payment() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Payment history is immutable'; END IF;
          IF (to_jsonb(NEW)-'refunded_at') IS DISTINCT FROM (to_jsonb(OLD)-'refunded_at')
             OR (OLD.refunded_at IS NOT NULL AND NEW.refunded_at IS DISTINCT FROM OLD.refunded_at)
          THEN RAISE EXCEPTION 'Payment receipt is immutable'; END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER tr_star_payment
        BEFORE UPDATE OR DELETE ON star_payments
        FOR EACH ROW EXECUTE FUNCTION freeze_star_payment()
    """)
    op.execute("""
        CREATE FUNCTION freeze_star_order() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Order history is immutable'; END IF;
          IF (to_jsonb(NEW)-ARRAY['status',
                'precheck_id']) IS DISTINCT FROM
             (to_jsonb(OLD)-ARRAY['status',
                'precheck_id'])
             OR (OLD.precheck_id IS NOT NULL AND NEW.precheck_id IS DISTINCT FROM OLD.precheck_id)
          THEN RAISE EXCEPTION 'Order terms are immutable'; END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER tr_star_order
        BEFORE UPDATE OR DELETE ON star_orders
        FOR EACH ROW EXECUTE FUNCTION freeze_star_order();
    """)
    op.execute(
        "CREATE TRIGGER tr_production_reset BEFORE TRUNCATE ON star_orders "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_production_reset()"
    )
    op.execute(
        "CREATE TRIGGER tr_production_reset BEFORE TRUNCATE ON star_payments "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_production_reset()"
    )
    op.execute(
        "CREATE TRIGGER tr_production_reset BEFORE TRUNCATE ON cosmetic_grants "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_production_reset()"
    )
    op.execute(
        "CREATE TRIGGER tr_production_reset BEFORE TRUNCATE ON club_rename_receipts "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_production_reset()"
    )
    op.execute(
        "CREATE TRIGGER tr_production_reset BEFORE TRUNCATE ON pay_support_requests "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_production_reset()"
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM star_orders) OR EXISTS (SELECT 1 FROM star_payments)
             OR EXISTS (SELECT 1 FROM cosmetic_grants)
          THEN RAISE EXCEPTION 'Used cosmetic/payment history must be preserved'; END IF;
        END $$
    """)
    op.drop_table("pay_support_requests")
    op.drop_table("club_rename_receipts")
    op.drop_table("cosmetic_grants")
    op.drop_table("star_payments")
    op.drop_table("star_orders")
    op.execute("DROP FUNCTION freeze_star_payment()")
    op.execute("DROP FUNCTION freeze_star_order()")
