"""Seasonal sponsor intervals and future training dates; preserve settled history."""

from alembic import op

revision = "0031_season_sponsors"
down_revision = "0030_match_staff_career"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_training_week", "training_plans", type_="check")
    op.create_check_constraint(
        "ck_training_week",
        "training_plans",
        "due_at = week_start + interval '1 day' "
        "AND selected_at >= week_start - interval '9 days' AND selected_at < due_at",
    )
    op.execute("""CREATE TABLE sponsor_contracts (
    id UUID NOT NULL,
    club_id UUID NOT NULL,
    season_id UUID NOT NULL,
    source_season_id UUID,
    basis VARCHAR(24) NOT NULL,
    policy_version VARCHAR(32) NOT NULL,
    tier SMALLINT NOT NULL,
    place SMALLINT,
    supporters INTEGER NOT NULL,
    cycle_rate INTEGER NOT NULL,
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ends_at TIMESTAMP WITH TIME ZONE,
    review_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_sponsor_club_season UNIQUE (club_id, season_id),
    CONSTRAINT ck_sponsor_policy CHECK (policy_version = 'season-sponsor-v1'),
    CONSTRAINT ck_sponsor_basis CHECK (basis IN ('opening','transition','season_result')),
    CONSTRAINT ck_sponsor_terms CHECK (tier BETWEEN 1 AND 5 AND (place IS NULL OR place BETWEEN 1
      AND 10) AND supporters >= 0 AND cycle_rate > 0),
    CONSTRAINT ck_sponsor_dates CHECK (ends_at IS NULL OR ends_at >= starts_at),
    FOREIGN KEY(club_id) REFERENCES clubs (id),
    FOREIGN KEY(season_id) REFERENCES seasons (id),
    FOREIGN KEY(source_season_id) REFERENCES seasons (id)
)""")
    op.execute(
        "CREATE UNIQUE INDEX uq_sponsor_active_club ON sponsor_contracts "
        "(club_id) WHERE ends_at IS NULL"
    )
    # Freeze the remaining unsettled portion; no balance/ledger writes or catch-up grant.
    op.execute("""WITH current_members AS (
      SELECT DISTINCT ON (sc.club_id) sc.*, s.ends_at AS review_at,
        coalesce((SELECT max(f.week_start) FROM finance_cycles f WHERE f.club_id=sc.club_id),
          (SELECT min((j.payload->>'period_start')::timestamptz) FROM jobs j
           WHERE j.kind='finance.cycle' AND j.payload->>'club_id'=sc.club_id::text),
          (SELECT min(w.created_at) FROM world_seed_receipts w
           WHERE w.league_set_id=sc.league_set_id)) AS starts_at
      FROM season_clubs sc JOIN seasons s ON s.id=sc.season_id
      WHERE s.status IN ('scheduled','active','finished')
      ORDER BY sc.club_id,s.number DESC
    ), ranked AS (
      SELECT cm.*, row_number() OVER (PARTITION BY cm.season_id,cm.league_id
        ORDER BY coalesce(st.points,0) DESC,
          coalesce(st.goals_for-st.goals_against,0) DESC,
          coalesce(st.goals_for,0) DESC,cm.tie_break_order) AS place
      FROM current_members cm LEFT JOIN standings st
        ON st.season_id=cm.season_id AND st.club_id=cm.club_id
    ) INSERT INTO sponsor_contracts
      (id,club_id,season_id,source_season_id,basis,policy_version,tier,place,
       supporters,cycle_rate,starts_at,ends_at,review_at)
    SELECT md5('season-sponsor-v1:'||r.club_id::text||':'||r.season_id::text)::uuid,
      r.club_id,r.season_id,NULL,'transition','season-sponsor-v1',l.tier,r.place,
      c.supporters,48000+(5-l.tier)*4000+(11-r.place)*500+least(c.supporters,10000),
      r.starts_at,NULL,r.review_at
    FROM ranked r JOIN clubs c ON c.id=r.club_id JOIN leagues l ON l.id=r.league_id
    """)
    op.execute("""CREATE FUNCTION protect_sponsor_contract() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Sponsor history is immutable'; END IF;
      IF TG_OP='UPDATE' THEN
        IF ROW(NEW.id,NEW.club_id,NEW.season_id,NEW.source_season_id,NEW.basis,
          NEW.policy_version,NEW.tier,NEW.place,NEW.supporters,NEW.cycle_rate,
          NEW.starts_at,NEW.review_at)
          IS DISTINCT FROM ROW(OLD.id,OLD.club_id,OLD.season_id,OLD.source_season_id,OLD.basis,
          OLD.policy_version,OLD.tier,OLD.place,OLD.supporters,OLD.cycle_rate,
          OLD.starts_at,OLD.review_at)
          OR (OLD.ends_at IS NOT NULL AND NEW.ends_at IS DISTINCT FROM OLD.ends_at)
          THEN RAISE EXCEPTION 'Sponsor terms are immutable'; END IF;
      END IF;
      IF EXISTS (SELECT 1 FROM sponsor_contracts s WHERE s.club_id=NEW.club_id
        AND s.id<>NEW.id AND s.starts_at<coalesce(NEW.ends_at,'infinity'::timestamptz)
        AND coalesce(s.ends_at,'infinity'::timestamptz)>NEW.starts_at)
        THEN RAISE EXCEPTION 'Sponsor intervals overlap'; END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER sponsor_contract_immutable BEFORE INSERT OR UPDATE OR DELETE "
        "ON sponsor_contracts FOR EACH ROW EXECUTE FUNCTION protect_sponsor_contract()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM sponsor_contracts WHERE basis='season_result') OR EXISTS
        (SELECT 1 FROM finance_cycles WHERE snapshot ? 'sponsor_periods') OR EXISTS
        (SELECT 1 FROM training_plans WHERE selected_at < week_start)
        THEN RAISE EXCEPTION 'Seasonal sponsorship history prevents downgrade'; END IF;
    END $$""")
    op.drop_table("sponsor_contracts")
    op.execute("DROP FUNCTION protect_sponsor_contract()")
    op.drop_constraint("ck_training_week", "training_plans", type_="check")
    op.create_check_constraint(
        "ck_training_week",
        "training_plans",
        "due_at = week_start + interval '1 day' "
        "AND selected_at >= week_start AND selected_at < due_at",
    )
