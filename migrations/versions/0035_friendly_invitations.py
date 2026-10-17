"""Explicit human-vs-human friendly challenges with accept/decline/cancel."""

import sqlalchemy as sa
from alembic import op

revision = "0035_friendly_invitations"
down_revision = "0034_real_knockout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "friendly_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("season_id", sa.Uuid(), sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("home_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("away_club_id", sa.Uuid(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column(
            "challenger_ownership_id",
            sa.Uuid(),
            sa.ForeignKey("ownership_periods.id"),
            nullable=False,
        ),
        sa.Column("assigned_day", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("fixture_plans.id"), nullable=True),
        sa.CheckConstraint("home_club_id <> away_club_id", name="ck_friendly_invite_clubs"),
        sa.CheckConstraint(
            "status IN ('pending','accepted','declined','cancelled','expired')",
            name="ck_friendly_invite_status",
        ),
        sa.CheckConstraint(
            "(status = 'accepted') = (plan_id IS NOT NULL)",
            name="ck_friendly_invite_plan",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (responded_at IS NULL)",
            name="ck_friendly_invite_response",
        ),
    )
    op.create_index("ix_friendly_invite_day", "friendly_invitations", ["season_id", "assigned_day"])
    op.create_index(
        "ix_friendly_invite_clubs",
        "friendly_invitations",
        ["home_club_id", "away_club_id", "assigned_day"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_friendly_pending_pair_day ON friendly_invitations "
        "(season_id, home_club_id, away_club_id, assigned_day) WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM friendly_invitations WHERE status = 'accepted') THEN
        RAISE EXCEPTION 'Accepted friendly challenges prevent downgrade'; END IF;
    END $$""")
    op.execute("DROP INDEX uq_friendly_pending_pair_day")
    op.drop_index("ix_friendly_invite_clubs", table_name="friendly_invitations")
    op.drop_index("ix_friendly_invite_day", table_name="friendly_invitations")
    op.drop_table("friendly_invitations")
