"""Anchor future inactivity decay without changing existing forms or history."""

import sqlalchemy as sa
from alembic import op

revision = "0057_active_form"
down_revision = "0056_position_development"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("players", sa.Column("form_activity_at", sa.DateTime(timezone=True)))
    op.add_column(
        "players", sa.Column("form_idle_steps", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute("UPDATE players SET form_activity_at = CURRENT_TIMESTAMP")
    op.create_check_constraint("ck_player_form_idle_steps", "players", "form_idle_steps >= 0")


def downgrade() -> None:
    raise RuntimeError("Form activity cursors and played history require an additive migration")
