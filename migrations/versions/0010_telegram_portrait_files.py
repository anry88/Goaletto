"""Durable Telegram portrait file_id cache (proposal AND-48, needs Codex review).

Additive only: creates telegram_portrait_files without touching existing
tables. The key is the sha256 of the canonical rendering inputs
(identity/variant/catalog, kit colors/version, size, background), so a kit
change re-renders the same face instead of reusing a stale file.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_telegram_portrait_files"
down_revision = "0009_outbox_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_portrait_files",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("identity_id", sa.String(100), nullable=False),
        sa.Column("variant_id", sa.String(100), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("kit_primary", sa.String(7), nullable=False),
        sa.Column("kit_secondary", sa.String(7), nullable=False),
        sa.Column("kit_version", sa.Integer(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("background", sa.String(32), nullable=False, server_default="transparent"),
        sa.Column("file_id", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(cache_key) = 64", name="ck_portrait_file_key"),
        sa.CheckConstraint("catalog_version > 0", name="ck_portrait_file_catalog"),
        sa.CheckConstraint("kit_version > 0", name="ck_portrait_file_kit_version"),
        sa.CheckConstraint("size IN (96, 256)", name="ck_portrait_file_size"),
        sa.CheckConstraint("length(file_id) BETWEEN 1 AND 500", name="ck_portrait_file_id"),
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM telegram_portrait_files) THEN RAISE EXCEPTION "
        "'Reconcile stored Telegram portrait file_id before downgrade'; END IF; END $$"
    )
    op.drop_table("telegram_portrait_files")
