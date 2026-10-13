"""Voluntary feedback durable sessions and restricted comments (AND-78, part A)."""

from alembic import op

revision = "0018_voluntary_feedback"
down_revision = "0017_transfer_auctions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE feedback_campaigns (\n"
        "\tid UUID NOT NULL, \n"
        "\tslug VARCHAR(64) NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT ck_feedback_campaign_slug CHECK (slug ~ '^[a-z0-9_-]{1,64}$'), \n"
        "\tCONSTRAINT uq_feedback_campaign_slug UNIQUE (slug)\n"
        ")"
    )
    op.execute(
        "CREATE TABLE feedback_responses (\n"
        "\tid UUID NOT NULL, \n"
        "\tcampaign_id UUID NOT NULL, \n"
        "\tmanager_id UUID NOT NULL, \n"
        "\toperation_id UUID NOT NULL, \n"
        "\tstatus VARCHAR(16) NOT NULL, \n"
        "\treason VARCHAR(32), \n"
        "\tprompted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
        "\tresponded_at TIMESTAMP WITH TIME ZONE, \n"
        "\tcomment_due_at TIMESTAMP WITH TIME ZONE, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT uq_feedback_manager_campaign UNIQUE (manager_id, campaign_id), \n"
        "\tCONSTRAINT uq_feedback_operation UNIQUE (operation_id), \n"
        "\tCONSTRAINT ck_feedback_status CHECK "
        "(status IN ('prompted','answered','skipped','opted_out')), \n"
        "\tCONSTRAINT ck_feedback_reason CHECK (reason IS NULL OR reason IN "
        "('unclear_next_step','difficult_lineup','long_wait','inconvenient_time',"
        "'uninteresting_game','technical_problem','other')), \n"
        "\tCONSTRAINT ck_feedback_responded CHECK "
        "((status = 'prompted') = (responded_at IS NULL)), \n"
        "\tCONSTRAINT ck_feedback_answer CHECK "
        "((status = 'answered') = (reason IS NOT NULL AND responded_at IS NOT NULL)), \n"
        "\tCONSTRAINT ck_feedback_response_time CHECK "
        "(responded_at IS NULL OR responded_at >= prompted_at), \n"
        "\tCONSTRAINT ck_feedback_comment_due CHECK "
        "(comment_due_at IS NULL OR comment_due_at >= responded_at), \n"
        "\tFOREIGN KEY(campaign_id) REFERENCES feedback_campaigns (id), \n"
        "\tFOREIGN KEY(manager_id) REFERENCES managers (id)\n"
        ")"
    )
    op.execute(
        "CREATE INDEX ix_feedback_manager_prompted ON feedback_responses (manager_id, prompted_at)"
    )
    op.execute(
        "CREATE TABLE feedback_comments (\n"
        "\tresponse_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
        "\ttext VARCHAR(500) NOT NULL, \n"
        "\tPRIMARY KEY (response_id), \n"
        "\tCONSTRAINT ck_feedback_comment_length CHECK "
        "(char_length(text) BETWEEN 1 AND 500), \n"
        "\tFOREIGN KEY(response_id) REFERENCES feedback_responses (id) ON DELETE CASCADE\n"
        ")"
    )
    op.execute("CREATE INDEX ix_feedback_comment_created ON feedback_comments (created_at)")


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM feedback_responses) OR "
        "EXISTS (SELECT 1 FROM feedback_comments) THEN RAISE EXCEPTION 'Reconcile "
        "voluntary feedback before downgrade'; END IF; END $$"
    )
    op.execute("DROP INDEX ix_feedback_comment_created")
    op.execute("DROP TABLE feedback_comments")
    op.execute("DROP INDEX ix_feedback_manager_prompted")
    op.execute("DROP TABLE feedback_responses")
    op.execute("DROP TABLE feedback_campaigns")
