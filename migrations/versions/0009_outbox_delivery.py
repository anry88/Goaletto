"""Frozen delivery leases, priority backfill and serialized live boards. No network."""

import sqlalchemy as sa
from alembic import op

revision = "0009_outbox_delivery"
down_revision = "0008_club_entry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("managers", sa.Column("bot_confirmed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "outbox_messages",
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("outbox_messages", sa.Column("lease_owner", sa.String(64), nullable=True))
    op.add_column(
        "outbox_messages", sa.Column("telegram_message_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("outbox_messages", sa.Column("live_board_key", sa.String(200), nullable=True))
    op.add_column(
        "outbox_messages",
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column("outbox_messages", sa.Column("last_error", sa.String(80), nullable=True))
    op.create_check_constraint(
        "ck_outbox_priority", "outbox_messages", "priority BETWEEN 0 AND 100"
    )
    op.create_check_constraint("ck_outbox_attempts", "outbox_messages", "attempts >= 0")
    op.create_check_constraint(
        "ck_outbox_lease", "outbox_messages", "(leased_until IS NULL) = (lease_owner IS NULL)"
    )
    op.create_check_constraint(
        "ck_outbox_message_id",
        "outbox_messages",
        "telegram_message_id IS NULL OR telegram_message_id > 0",
    )
    op.create_index("ix_outbox_delivery", "outbox_messages", ["status", "priority", "due_at"])
    op.create_index("ix_outbox_lease", "outbox_messages", ["status", "leased_until"])
    op.create_index("ix_outbox_live_board", "outbox_messages", ["live_board_key"])
    op.execute(
        sa.text("""
CREATE TABLE outbox_boards (
        key VARCHAR(200) NOT NULL,
        fixture_id UUID NOT NULL,
        ownership_id UUID NOT NULL,
        chat_id BIGINT NOT NULL,
        telegram_message_id BIGINT,
        revision SMALLINT DEFAULT '-1' NOT NULL,
        leased_until TIMESTAMP WITH TIME ZONE,
        lease_owner VARCHAR(64),
        retry_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (key),
        CONSTRAINT uq_outbox_board_stream UNIQUE (fixture_id, ownership_id),
        CONSTRAINT ck_board_message_id CHECK
        (telegram_message_id IS NULL OR telegram_message_id > 0),
        CONSTRAINT ck_board_revision CHECK (revision BETWEEN -1 AND 1000),
        CONSTRAINT ck_board_lease CHECK ((leased_until IS NULL) = (lease_owner IS NULL)),
        FOREIGN KEY(fixture_id) REFERENCES fixtures (id),
        FOREIGN KEY(ownership_id) REFERENCES ownership_periods (id)
)
""")
    )
    op.execute(
        sa.text("""
CREATE TABLE outbox_delivery_attempts (
        outbox_id UUID NOT NULL,
        attempt INTEGER NOT NULL,
        lease_owner VARCHAR(64) NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE NOT NULL,
        network_started_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE,
        outcome VARCHAR(24) NOT NULL,
        PRIMARY KEY (outbox_id, attempt),
        CONSTRAINT ck_delivery_attempt_number CHECK (attempt > 0),
        CONSTRAINT ck_delivery_outcome CHECK (outcome IN
        ('claimed','sending','sent','edited','duplicate','rate_limited','transient','unknown',
        'blocked','chat_unavailable','invalid_request','skip_stale','skip_unavailable')),
        CONSTRAINT ck_delivery_finished CHECK
        ((outcome IN ('claimed','sending')) = (finished_at IS NULL)),
        CONSTRAINT ck_delivery_times CHECK
        ((network_started_at IS NULL OR network_started_at >= started_at) AND
        (finished_at IS NULL OR finished_at >= started_at) AND
        (finished_at IS NULL OR network_started_at IS NULL OR
        finished_at >= network_started_at)),
        FOREIGN KEY(outbox_id) REFERENCES outbox_messages (id)
)
""")
    )
    # Producers created before this migration wrote the default priority/key.
    # Do not parse UUIDs here: malformed payloads are quarantined by the sender.
    op.execute(
        sa.text("""
UPDATE outbox_messages SET priority = CASE payload->>'kind'
    WHEN 'match_result' THEN 100 WHEN 'match_board' THEN 50
    WHEN 'fixture_reminder' THEN 30 WHEN 'ownership_reminder' THEN 10 ELSE 0 END
""")
    )
    op.execute(
        sa.text("""
UPDATE outbox_messages SET live_board_key =
    'live:v1:' || lower(payload->>'fixture_id') || ':' || lower(payload->>'ownership_id')
WHERE payload->>'kind' IN ('match_board','match_result')
  AND payload->>'fixture_id' ~ '^[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$'
  AND payload->>'ownership_id' ~ '^[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$';
""")
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM outbox_messages WHERE leased_until IS NOT NULL "
        "OR telegram_message_id IS NOT NULL) OR EXISTS (SELECT 1 FROM outbox_boards "
        "WHERE leased_until IS NOT NULL OR telegram_message_id IS NOT NULL) "
        "OR EXISTS (SELECT 1 FROM outbox_delivery_attempts) THEN RAISE EXCEPTION "
        "'Reconcile leased/delivered outbox before downgrade'; END IF; END $$"
    )
    op.drop_table("outbox_delivery_attempts")
    op.drop_table("outbox_boards")
    op.drop_column("managers", "bot_confirmed_at")
    op.drop_index("ix_outbox_live_board", table_name="outbox_messages")
    op.drop_index("ix_outbox_lease", table_name="outbox_messages")
    op.drop_index("ix_outbox_delivery", table_name="outbox_messages")
    op.drop_constraint("ck_outbox_message_id", "outbox_messages", type_="check")
    op.drop_constraint("ck_outbox_priority", "outbox_messages", type_="check")
    op.drop_constraint("ck_outbox_lease", "outbox_messages", type_="check")
    op.drop_constraint("ck_outbox_attempts", "outbox_messages", type_="check")
    op.drop_column("outbox_messages", "last_error")
    op.drop_column("outbox_messages", "priority")
    op.drop_column("outbox_messages", "live_board_key")
    op.drop_column("outbox_messages", "telegram_message_id")
    op.drop_column("outbox_messages", "lease_owner")
    op.drop_column("outbox_messages", "leased_until")
