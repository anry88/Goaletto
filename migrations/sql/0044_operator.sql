-- Frozen report SQL from 36bae1fd, alter through a new migration when semantics change.
CREATE SCHEMA goaletto_operator;
REVOKE ALL ON SCHEMA goaletto_operator FROM PUBLIC;
CREATE FUNCTION goaletto_operator.report(p_report text, p_from timestamptz, p_at timestamptz,
 p_filters jsonb DEFAULT '{}'::jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE v_from timestamptz := p_from; v_coverage timestamptz; v_retention integer;
 v_rows jsonb; v_journey boolean;
BEGIN
 IF p_report IS NULL OR p_from IS NULL OR p_at IS NULL OR p_from > p_at
 OR NOT isfinite(p_from) OR NOT isfinite(p_at) OR p_at-p_from > interval '366 days'
 OR p_at > statement_timestamp() + interval '1 minute'
 OR p_filters IS NULL OR jsonb_typeof(p_filters) <> 'object' THEN
   RAISE EXCEPTION 'Invalid report parameters' USING ERRCODE = '22023'; END IF;
 IF EXISTS (SELECT 1 FROM jsonb_object_keys(p_filters) k
   WHERE k NOT IN ('source','locale','variant','tier','set'))
 OR COALESCE(p_filters->>'source','all') NOT IN ('all','direct','known','unknown')
 OR COALESCE(p_filters->>'locale','all') NOT IN ('all','ru','en')
 OR COALESCE(p_filters->>'variant','all') NOT IN ('all','control','a','b')
 OR COALESCE(p_filters->>'tier','all') NOT IN ('all','1','2','3','4','5')
 OR length(COALESCE(p_filters->>'set','all')) > 80 THEN
   RAISE EXCEPTION 'Invalid report filters' USING ERRCODE = '22023'; END IF;
 v_journey := p_report IN ('entry','sources','retention','last_steps','recovery','opportunities');
 IF NOT v_journey AND EXISTS (SELECT 1 FROM jsonb_each_text(p_filters) f WHERE f.value <> 'all')
 THEN RAISE EXCEPTION 'Operational reports are unfiltered' USING ERRCODE = '22023'; END IF;
 IF v_journey THEN
   SELECT started_at, retention_days INTO v_coverage,v_retention
   FROM public.analytics_coverage WHERE version = 1;
   IF v_coverage IS NULL THEN RETURN jsonb_build_object('state','coverage_not_started',
     'rows','[]'::jsonb,'requested_from',p_from,'report_at',p_at); END IF;
   v_from := GREATEST(p_from,v_coverage,p_at-make_interval(days => v_retention));
   IF v_from > p_at THEN RETURN jsonb_build_object('state','outside_coverage',
     'rows','[]'::jsonb,'requested_from',p_from,'available_from',v_from,
     'report_at',p_at,'coverage_started_at',v_coverage,'coverage_limited',true); END IF;
 END IF;
 CASE p_report
WHEN 'entry' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at,
        a.started_at + INTERVAL '24 hours' <= p_at AS mature
 FROM public.acquisition_first_touches a
 JOIN public.analytics_managers am ON am.id = a.analytics_manager_id
 JOIN public.managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND (COALESCE(p_filters->>'source','all') = 'all' OR a.kind = p_filters->>'source')
 AND (COALESCE(p_filters->>'locale','all') = 'all' OR m.locale = p_filters->>'locale')
 AND (COALESCE(p_filters->>'variant','all') = 'all' OR EXISTS (
   SELECT 1 FROM public.journey_events v WHERE v.analytics_manager_id = am.id
   AND v.name = 'experiment_exposed' AND v.version = 1 AND v.origin = 'system'
   AND v.recorded_at <= p_at AND v.occurred_at BETWEEN a.started_at AND p_at
   AND v.envelope->>'variant' = p_filters->>'variant'))
 AND ((COALESCE(p_filters->>'tier','all') = 'all' AND
       COALESCE(p_filters->>'set','all') = 'all') OR EXISTS (
   SELECT 1 FROM LATERAL (
     SELECT o.club_id, o.claimed_at FROM public.ownership_periods o
     WHERE o.manager_id = m.id AND o.claimed_at >= a.started_at AND o.claimed_at <= p_at
     ORDER BY o.claimed_at, o.id LIMIT 1
   ) first_club CROSS JOIN LATERAL (
     SELECT l.tier, ls.name FROM public.season_clubs sc JOIN public.seasons s ON s.id = sc.season_id
     JOIN public.leagues l ON l.id = sc.league_id JOIN public.league_sets ls ON ls.id = l.league_set_id
     WHERE sc.club_id = first_club.club_id
     ORDER BY (s.starts_at > first_club.claimed_at),
       CASE WHEN s.starts_at <= first_club.claimed_at THEN s.starts_at END DESC,
       s.starts_at, s.id LIMIT 1
   ) membership WHERE (COALESCE(p_filters->>'tier','all') = 'all'
     OR membership.tier::text = p_filters->>'tier')
   AND (COALESCE(p_filters->>'set','all') = 'all' OR membership.name = p_filters->>'set')
 ))

 AND NOT EXISTS (SELECT 1 FROM public.analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= v_from AND a.started_at <= p_at
), effects AS (
 SELECT c.*,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.name = 'club_claimed' AND e.origin = 'user'
   AND e.occurred_at >= c.started_at AND e.occurred_at < c.started_at + INTERVAL '24 hours'
   AND e.recorded_at <= p_at) AS claimed,
 EXISTS (SELECT 1 FROM public.journey_events claim
   JOIN public.journey_events saved
     ON saved.analytics_manager_id = claim.analytics_manager_id
    AND saved.envelope ->> 'ownership_id' = claim.envelope ->> 'ownership_id'
   WHERE claim.analytics_manager_id = c.analytics_manager_id
   AND claim.version = 1 AND claim.name = 'club_claimed' AND claim.origin = 'user'
   AND saved.version = 1 AND saved.name = 'lineup_saved' AND saved.origin = 'user'
   AND saved.occurred_at >= claim.occurred_at
   AND claim.occurred_at >= c.started_at
   AND saved.occurred_at < c.started_at + INTERVAL '24 hours'
   AND claim.recorded_at <= p_at AND saved.recorded_at <= p_at) AS ready,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.name = 'club_claimed' AND e.origin = 'user'
   AND e.occurred_at >= c.started_at AND e.occurred_at < c.started_at + INTERVAL '24 hours'
 ) AS claimed_eventual,
 EXISTS (SELECT 1 FROM public.journey_events claim
   JOIN public.journey_events saved
     ON saved.analytics_manager_id = claim.analytics_manager_id
    AND saved.envelope ->> 'ownership_id' = claim.envelope ->> 'ownership_id'
   WHERE claim.analytics_manager_id = c.analytics_manager_id
   AND claim.version = 1 AND claim.name = 'club_claimed' AND claim.origin = 'user'
   AND saved.version = 1 AND saved.name = 'lineup_saved' AND saved.origin = 'user'
   AND saved.occurred_at >= claim.occurred_at
   AND claim.occurred_at >= c.started_at
   AND saved.occurred_at < c.started_at + INTERVAL '24 hours'
 ) AS ready_eventual
 FROM cohort c
)
SELECT COUNT(*) AS starts,
 COUNT(*) FILTER (WHERE mature) AS eligible,
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE mature AND claimed) AS claimed,
 COUNT(*) FILTER (WHERE mature AND ready) AS ready,
 COUNT(*) FILTER (WHERE mature AND NOT claimed AND claimed_eventual) AS unknown_claimed_late,
 COUNT(*) FILTER (WHERE mature AND NOT ready AND ready_eventual) AS unknown_ready_late
FROM effects

) r;
WHEN 'sources' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

WITH cohort AS (
 SELECT a.analytics_manager_id, a.kind, a.campaign_id, a.started_at,
        a.started_at + INTERVAL '24 hours' <= p_at AS mature
 FROM public.acquisition_first_touches a
 JOIN public.analytics_managers am ON am.id = a.analytics_manager_id
 JOIN public.managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND (COALESCE(p_filters->>'source','all') = 'all' OR a.kind = p_filters->>'source')
 AND (COALESCE(p_filters->>'locale','all') = 'all' OR m.locale = p_filters->>'locale')
 AND (COALESCE(p_filters->>'variant','all') = 'all' OR EXISTS (
   SELECT 1 FROM public.journey_events v WHERE v.analytics_manager_id = am.id
   AND v.name = 'experiment_exposed' AND v.version = 1 AND v.origin = 'system'
   AND v.recorded_at <= p_at AND v.occurred_at BETWEEN a.started_at AND p_at
   AND v.envelope->>'variant' = p_filters->>'variant'))
 AND ((COALESCE(p_filters->>'tier','all') = 'all' AND
       COALESCE(p_filters->>'set','all') = 'all') OR EXISTS (
   SELECT 1 FROM LATERAL (
     SELECT o.club_id, o.claimed_at FROM public.ownership_periods o
     WHERE o.manager_id = m.id AND o.claimed_at >= a.started_at AND o.claimed_at <= p_at
     ORDER BY o.claimed_at, o.id LIMIT 1
   ) first_club CROSS JOIN LATERAL (
     SELECT l.tier, ls.name FROM public.season_clubs sc JOIN public.seasons s ON s.id = sc.season_id
     JOIN public.leagues l ON l.id = sc.league_id JOIN public.league_sets ls ON ls.id = l.league_set_id
     WHERE sc.club_id = first_club.club_id
     ORDER BY (s.starts_at > first_club.claimed_at),
       CASE WHEN s.starts_at <= first_club.claimed_at THEN s.starts_at END DESC,
       s.starts_at, s.id LIMIT 1
   ) membership WHERE (COALESCE(p_filters->>'tier','all') = 'all'
     OR membership.tier::text = p_filters->>'tier')
   AND (COALESCE(p_filters->>'set','all') = 'all' OR membership.name = p_filters->>'set')
 ))

 AND NOT EXISTS (SELECT 1 FROM public.analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= v_from AND a.started_at <= p_at
), effects AS (
 SELECT c.*,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.name = 'club_claimed' AND e.origin = 'user'
   AND e.occurred_at >= c.started_at AND e.occurred_at < c.started_at + INTERVAL '24 hours'
   AND e.recorded_at <= p_at) AS claimed,
 EXISTS (SELECT 1 FROM public.journey_events claim
   JOIN public.journey_events saved
     ON saved.analytics_manager_id = claim.analytics_manager_id
    AND saved.envelope ->> 'ownership_id' = claim.envelope ->> 'ownership_id'
   WHERE claim.analytics_manager_id = c.analytics_manager_id
   AND claim.version = 1 AND claim.name = 'club_claimed' AND claim.origin = 'user'
   AND saved.version = 1 AND saved.name = 'lineup_saved' AND saved.origin = 'user'
   AND saved.occurred_at >= claim.occurred_at
   AND claim.occurred_at >= c.started_at
   AND saved.occurred_at < c.started_at + INTERVAL '24 hours'
   AND claim.recorded_at <= p_at AND saved.recorded_at <= p_at) AS ready
 FROM cohort c
)
SELECT e.kind, e.campaign_id, p.source, p.placement, p.published_on,
 COUNT(*) AS starts, COUNT(*) FILTER (WHERE mature) AS eligible,
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE mature AND claimed) AS claimed,
 COUNT(*) FILTER (WHERE mature AND ready) AS ready
FROM effects e LEFT JOIN public.acquisition_campaigns p ON p.id = e.campaign_id
GROUP BY e.kind, e.campaign_id, p.source, p.placement, p.published_on
ORDER BY e.kind, e.campaign_id

) r;
WHEN 'retention' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (
SELECT 1 AS day, r.* FROM (
WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at,
        a.started_at + INTERVAL '48 hours' <= p_at AS mature
 FROM public.acquisition_first_touches a
 JOIN public.analytics_managers am ON am.id = a.analytics_manager_id
 JOIN public.managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND (COALESCE(p_filters->>'source','all') = 'all' OR a.kind = p_filters->>'source')
 AND (COALESCE(p_filters->>'locale','all') = 'all' OR m.locale = p_filters->>'locale')
 AND (COALESCE(p_filters->>'variant','all') = 'all' OR EXISTS (
   SELECT 1 FROM public.journey_events v WHERE v.analytics_manager_id = am.id
   AND v.name = 'experiment_exposed' AND v.version = 1 AND v.origin = 'system'
   AND v.recorded_at <= p_at AND v.occurred_at BETWEEN a.started_at AND p_at
   AND v.envelope->>'variant' = p_filters->>'variant'))
 AND ((COALESCE(p_filters->>'tier','all') = 'all' AND
       COALESCE(p_filters->>'set','all') = 'all') OR EXISTS (
   SELECT 1 FROM LATERAL (
     SELECT o.club_id, o.claimed_at FROM public.ownership_periods o
     WHERE o.manager_id = m.id AND o.claimed_at >= a.started_at AND o.claimed_at <= p_at
     ORDER BY o.claimed_at, o.id LIMIT 1
   ) first_club CROSS JOIN LATERAL (
     SELECT l.tier, ls.name FROM public.season_clubs sc JOIN public.seasons s ON s.id = sc.season_id
     JOIN public.leagues l ON l.id = sc.league_id JOIN public.league_sets ls ON ls.id = l.league_set_id
     WHERE sc.club_id = first_club.club_id
     ORDER BY (s.starts_at > first_club.claimed_at),
       CASE WHEN s.starts_at <= first_club.claimed_at THEN s.starts_at END DESC,
       s.starts_at, s.id LIMIT 1
   ) membership WHERE (COALESCE(p_filters->>'tier','all') = 'all'
     OR membership.tier::text = p_filters->>'tier')
   AND (COALESCE(p_filters->>'set','all') = 'all' OR membership.name = p_filters->>'set')
 ))

 AND NOT EXISTS (SELECT 1 FROM public.analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= v_from AND a.started_at <= p_at
), effects AS (
 SELECT c.*,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '24 hours'
   AND e.occurred_at < c.started_at + INTERVAL '48 hours'
   AND e.recorded_at <= p_at) AS retained,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '24 hours'
   AND e.occurred_at < c.started_at + INTERVAL '48 hours'
 ) AS retained_eventual
 FROM cohort c
)
SELECT COUNT(*) AS starts,
 COUNT(*) FILTER (WHERE mature) AS eligible,
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE mature AND retained) AS retained,
 COUNT(*) FILTER (WHERE mature AND NOT retained AND retained_eventual) AS unknown_late
FROM effects
) r UNION ALL SELECT 3 AS day, r.* FROM (
WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at,
        a.started_at + INTERVAL '96 hours' <= p_at AS mature
 FROM public.acquisition_first_touches a
 JOIN public.analytics_managers am ON am.id = a.analytics_manager_id
 JOIN public.managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND (COALESCE(p_filters->>'source','all') = 'all' OR a.kind = p_filters->>'source')
 AND (COALESCE(p_filters->>'locale','all') = 'all' OR m.locale = p_filters->>'locale')
 AND (COALESCE(p_filters->>'variant','all') = 'all' OR EXISTS (
   SELECT 1 FROM public.journey_events v WHERE v.analytics_manager_id = am.id
   AND v.name = 'experiment_exposed' AND v.version = 1 AND v.origin = 'system'
   AND v.recorded_at <= p_at AND v.occurred_at BETWEEN a.started_at AND p_at
   AND v.envelope->>'variant' = p_filters->>'variant'))
 AND ((COALESCE(p_filters->>'tier','all') = 'all' AND
       COALESCE(p_filters->>'set','all') = 'all') OR EXISTS (
   SELECT 1 FROM LATERAL (
     SELECT o.club_id, o.claimed_at FROM public.ownership_periods o
     WHERE o.manager_id = m.id AND o.claimed_at >= a.started_at AND o.claimed_at <= p_at
     ORDER BY o.claimed_at, o.id LIMIT 1
   ) first_club CROSS JOIN LATERAL (
     SELECT l.tier, ls.name FROM public.season_clubs sc JOIN public.seasons s ON s.id = sc.season_id
     JOIN public.leagues l ON l.id = sc.league_id JOIN public.league_sets ls ON ls.id = l.league_set_id
     WHERE sc.club_id = first_club.club_id
     ORDER BY (s.starts_at > first_club.claimed_at),
       CASE WHEN s.starts_at <= first_club.claimed_at THEN s.starts_at END DESC,
       s.starts_at, s.id LIMIT 1
   ) membership WHERE (COALESCE(p_filters->>'tier','all') = 'all'
     OR membership.tier::text = p_filters->>'tier')
   AND (COALESCE(p_filters->>'set','all') = 'all' OR membership.name = p_filters->>'set')
 ))

 AND NOT EXISTS (SELECT 1 FROM public.analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= v_from AND a.started_at <= p_at
), effects AS (
 SELECT c.*,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '72 hours'
   AND e.occurred_at < c.started_at + INTERVAL '96 hours'
   AND e.recorded_at <= p_at) AS retained,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '72 hours'
   AND e.occurred_at < c.started_at + INTERVAL '96 hours'
 ) AS retained_eventual
 FROM cohort c
)
SELECT COUNT(*) AS starts,
 COUNT(*) FILTER (WHERE mature) AS eligible,
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE mature AND retained) AS retained,
 COUNT(*) FILTER (WHERE mature AND NOT retained AND retained_eventual) AS unknown_late
FROM effects
) r UNION ALL SELECT 7 AS day, r.* FROM (
WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at,
        a.started_at + INTERVAL '192 hours' <= p_at AS mature
 FROM public.acquisition_first_touches a
 JOIN public.analytics_managers am ON am.id = a.analytics_manager_id
 JOIN public.managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND (COALESCE(p_filters->>'source','all') = 'all' OR a.kind = p_filters->>'source')
 AND (COALESCE(p_filters->>'locale','all') = 'all' OR m.locale = p_filters->>'locale')
 AND (COALESCE(p_filters->>'variant','all') = 'all' OR EXISTS (
   SELECT 1 FROM public.journey_events v WHERE v.analytics_manager_id = am.id
   AND v.name = 'experiment_exposed' AND v.version = 1 AND v.origin = 'system'
   AND v.recorded_at <= p_at AND v.occurred_at BETWEEN a.started_at AND p_at
   AND v.envelope->>'variant' = p_filters->>'variant'))
 AND ((COALESCE(p_filters->>'tier','all') = 'all' AND
       COALESCE(p_filters->>'set','all') = 'all') OR EXISTS (
   SELECT 1 FROM LATERAL (
     SELECT o.club_id, o.claimed_at FROM public.ownership_periods o
     WHERE o.manager_id = m.id AND o.claimed_at >= a.started_at AND o.claimed_at <= p_at
     ORDER BY o.claimed_at, o.id LIMIT 1
   ) first_club CROSS JOIN LATERAL (
     SELECT l.tier, ls.name FROM public.season_clubs sc JOIN public.seasons s ON s.id = sc.season_id
     JOIN public.leagues l ON l.id = sc.league_id JOIN public.league_sets ls ON ls.id = l.league_set_id
     WHERE sc.club_id = first_club.club_id
     ORDER BY (s.starts_at > first_club.claimed_at),
       CASE WHEN s.starts_at <= first_club.claimed_at THEN s.starts_at END DESC,
       s.starts_at, s.id LIMIT 1
   ) membership WHERE (COALESCE(p_filters->>'tier','all') = 'all'
     OR membership.tier::text = p_filters->>'tier')
   AND (COALESCE(p_filters->>'set','all') = 'all' OR membership.name = p_filters->>'set')
 ))

 AND NOT EXISTS (SELECT 1 FROM public.analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= v_from AND a.started_at <= p_at
), effects AS (
 SELECT c.*,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '168 hours'
   AND e.occurred_at < c.started_at + INTERVAL '192 hours'
   AND e.recorded_at <= p_at) AS retained,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '168 hours'
   AND e.occurred_at < c.started_at + INTERVAL '192 hours'
 ) AS retained_eventual
 FROM cohort c
)
SELECT COUNT(*) AS starts,
 COUNT(*) FILTER (WHERE mature) AS eligible,
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE mature AND retained) AS retained,
 COUNT(*) FILTER (WHERE mature AND NOT retained AND retained_eventual) AS unknown_late
FROM effects
) r UNION ALL SELECT 30 AS day, r.* FROM (
WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at,
        a.started_at + INTERVAL '744 hours' <= p_at AS mature
 FROM public.acquisition_first_touches a
 JOIN public.analytics_managers am ON am.id = a.analytics_manager_id
 JOIN public.managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND (COALESCE(p_filters->>'source','all') = 'all' OR a.kind = p_filters->>'source')
 AND (COALESCE(p_filters->>'locale','all') = 'all' OR m.locale = p_filters->>'locale')
 AND (COALESCE(p_filters->>'variant','all') = 'all' OR EXISTS (
   SELECT 1 FROM public.journey_events v WHERE v.analytics_manager_id = am.id
   AND v.name = 'experiment_exposed' AND v.version = 1 AND v.origin = 'system'
   AND v.recorded_at <= p_at AND v.occurred_at BETWEEN a.started_at AND p_at
   AND v.envelope->>'variant' = p_filters->>'variant'))
 AND ((COALESCE(p_filters->>'tier','all') = 'all' AND
       COALESCE(p_filters->>'set','all') = 'all') OR EXISTS (
   SELECT 1 FROM LATERAL (
     SELECT o.club_id, o.claimed_at FROM public.ownership_periods o
     WHERE o.manager_id = m.id AND o.claimed_at >= a.started_at AND o.claimed_at <= p_at
     ORDER BY o.claimed_at, o.id LIMIT 1
   ) first_club CROSS JOIN LATERAL (
     SELECT l.tier, ls.name FROM public.season_clubs sc JOIN public.seasons s ON s.id = sc.season_id
     JOIN public.leagues l ON l.id = sc.league_id JOIN public.league_sets ls ON ls.id = l.league_set_id
     WHERE sc.club_id = first_club.club_id
     ORDER BY (s.starts_at > first_club.claimed_at),
       CASE WHEN s.starts_at <= first_club.claimed_at THEN s.starts_at END DESC,
       s.starts_at, s.id LIMIT 1
   ) membership WHERE (COALESCE(p_filters->>'tier','all') = 'all'
     OR membership.tier::text = p_filters->>'tier')
   AND (COALESCE(p_filters->>'set','all') = 'all' OR membership.name = p_filters->>'set')
 ))

 AND NOT EXISTS (SELECT 1 FROM public.analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= v_from AND a.started_at <= p_at
), effects AS (
 SELECT c.*,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '720 hours'
   AND e.occurred_at < c.started_at + INTERVAL '744 hours'
   AND e.recorded_at <= p_at) AS retained,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '720 hours'
   AND e.occurred_at < c.started_at + INTERVAL '744 hours'
 ) AS retained_eventual
 FROM cohort c
)
SELECT COUNT(*) AS starts,
 COUNT(*) FILTER (WHERE mature) AS eligible,
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE mature AND retained) AS retained,
 COUNT(*) FILTER (WHERE mature AND NOT retained AND retained_eventual) AS unknown_late
FROM effects
) r
) r;
WHEN 'last_steps' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at, am.manager_id, a.kind,
        a.campaign_id, m.locale
 FROM public.acquisition_first_touches a
 JOIN public.analytics_managers am ON am.id = a.analytics_manager_id
 JOIN public.managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND (COALESCE(p_filters->>'source','all') = 'all' OR a.kind = p_filters->>'source')
 AND (COALESCE(p_filters->>'locale','all') = 'all' OR m.locale = p_filters->>'locale')
 AND (COALESCE(p_filters->>'variant','all') = 'all' OR EXISTS (
   SELECT 1 FROM public.journey_events v WHERE v.analytics_manager_id = am.id
   AND v.name = 'experiment_exposed' AND v.version = 1 AND v.origin = 'system'
   AND v.recorded_at <= p_at AND v.occurred_at BETWEEN a.started_at AND p_at
   AND v.envelope->>'variant' = p_filters->>'variant'))
 AND ((COALESCE(p_filters->>'tier','all') = 'all' AND
       COALESCE(p_filters->>'set','all') = 'all') OR EXISTS (
   SELECT 1 FROM LATERAL (
     SELECT o.club_id, o.claimed_at FROM public.ownership_periods o
     WHERE o.manager_id = m.id AND o.claimed_at >= a.started_at AND o.claimed_at <= p_at
     ORDER BY o.claimed_at, o.id LIMIT 1
   ) first_club CROSS JOIN LATERAL (
     SELECT l.tier, ls.name FROM public.season_clubs sc JOIN public.seasons s ON s.id = sc.season_id
     JOIN public.leagues l ON l.id = sc.league_id JOIN public.league_sets ls ON ls.id = l.league_set_id
     WHERE sc.club_id = first_club.club_id
     ORDER BY (s.starts_at > first_club.claimed_at),
       CASE WHEN s.starts_at <= first_club.claimed_at THEN s.starts_at END DESC,
       s.starts_at, s.id LIMIT 1
   ) membership WHERE (COALESCE(p_filters->>'tier','all') = 'all'
     OR membership.tier::text = p_filters->>'tier')
   AND (COALESCE(p_filters->>'set','all') = 'all' OR membership.name = p_filters->>'set')
 ))

 AND NOT EXISTS (SELECT 1 FROM public.analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= v_from AND a.started_at <= p_at
)

SELECT c.kind AS source_kind, c.campaign_id, c.locale,
       date_trunc('week', c.started_at AT TIME ZONE 'UTC')::date AS cohort_week,
       CASE WHEN first_claim.claimed_at IS NULL THEN 'unclaimed'
            WHEN EXISTS (SELECT 1 FROM public.journey_events preview
              WHERE preview.analytics_manager_id = c.analytics_manager_id
              AND preview.version = 1 AND preview.name = 'club_preview_opened'
              AND preview.origin = 'user' AND preview.recorded_at <= p_at
              AND preview.occurred_at >= c.started_at
              AND preview.occurred_at <= first_claim.claimed_at)
              THEN 'preview_claim' ELSE 'direct_claim' END AS entry_path,
       COALESCE(last_step.name, 'start_accepted') AS last_step,
       COALESCE(last_step.surface, 'onboarding') AS surface,
       COUNT(*) AS managers,
       COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM public.journey_events screen
         WHERE screen.analytics_manager_id = c.analytics_manager_id AND screen.version = 1
         AND screen.name = 'club_offer_delivered' AND screen.origin = 'system'
         AND screen.occurred_at >= c.started_at AND screen.occurred_at <= p_at
         AND screen.recorded_at <= p_at)) AS offers_accepted
FROM cohort c
LEFT JOIN LATERAL (
 SELECT MIN(e.occurred_at) AS claimed_at FROM public.journey_events e
 WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
 AND e.name = 'club_claimed' AND e.origin = 'user' AND e.recorded_at <= p_at
 AND e.occurred_at >= c.started_at AND e.occurred_at <= p_at
) first_claim ON true
LEFT JOIN LATERAL (
 SELECT e.name, e.envelope ->> 'surface' AS surface
 FROM public.journey_events e
 WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
 AND e.origin = 'user' AND e.user_activity
 AND e.occurred_at >= c.started_at AND e.occurred_at <= p_at
 AND e.recorded_at <= p_at
 ORDER BY e.occurred_at DESC, e.recorded_at DESC, e.operation_id, e.effect_number DESC
 LIMIT 1
) last_step ON true
GROUP BY c.kind, c.campaign_id, c.locale, cohort_week, entry_path, last_step.name, last_step.surface
ORDER BY c.kind, c.campaign_id, c.locale, last_step.name, last_step.surface

) r;
WHEN 'recovery' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at, am.manager_id, a.kind,
        a.campaign_id, m.locale
 FROM public.acquisition_first_touches a
 JOIN public.analytics_managers am ON am.id = a.analytics_manager_id
 JOIN public.managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND (COALESCE(p_filters->>'source','all') = 'all' OR a.kind = p_filters->>'source')
 AND (COALESCE(p_filters->>'locale','all') = 'all' OR m.locale = p_filters->>'locale')
 AND (COALESCE(p_filters->>'variant','all') = 'all' OR EXISTS (
   SELECT 1 FROM public.journey_events v WHERE v.analytics_manager_id = am.id
   AND v.name = 'experiment_exposed' AND v.version = 1 AND v.origin = 'system'
   AND v.recorded_at <= p_at AND v.occurred_at BETWEEN a.started_at AND p_at
   AND v.envelope->>'variant' = p_filters->>'variant'))
 AND ((COALESCE(p_filters->>'tier','all') = 'all' AND
       COALESCE(p_filters->>'set','all') = 'all') OR EXISTS (
   SELECT 1 FROM LATERAL (
     SELECT o.club_id, o.claimed_at FROM public.ownership_periods o
     WHERE o.manager_id = m.id AND o.claimed_at >= a.started_at AND o.claimed_at <= p_at
     ORDER BY o.claimed_at, o.id LIMIT 1
   ) first_club CROSS JOIN LATERAL (
     SELECT l.tier, ls.name FROM public.season_clubs sc JOIN public.seasons s ON s.id = sc.season_id
     JOIN public.leagues l ON l.id = sc.league_id JOIN public.league_sets ls ON ls.id = l.league_set_id
     WHERE sc.club_id = first_club.club_id
     ORDER BY (s.starts_at > first_club.claimed_at),
       CASE WHEN s.starts_at <= first_club.claimed_at THEN s.starts_at END DESC,
       s.starts_at, s.id LIMIT 1
   ) membership WHERE (COALESCE(p_filters->>'tier','all') = 'all'
     OR membership.tier::text = p_filters->>'tier')
   AND (COALESCE(p_filters->>'set','all') = 'all' OR membership.name = p_filters->>'set')
 ))

 AND NOT EXISTS (SELECT 1 FROM public.analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= v_from AND a.started_at <= p_at
)

, errors AS (
 SELECT e.*, e.envelope ->> 'reason' AS reason,
        e.envelope ->> 'surface' AS surface,
        COALESCE(e.envelope ->> 'target', e.envelope ->> 'surface') AS target,
        e.occurred_at + INTERVAL '30 minutes' <= p_at AS mature
 FROM cohort c JOIN public.journey_events e ON e.analytics_manager_id = c.analytics_manager_id
 WHERE e.version = 1 AND e.name = 'action_rejected' AND e.origin = 'user'
 AND e.occurred_at >= c.started_at AND e.occurred_at <= p_at
 AND e.recorded_at <= p_at
), outcomes AS (
 SELECT r.*,
 EXISTS (SELECT 1 FROM public.journey_events h
   WHERE h.operation_id = r.operation_id AND h.analytics_manager_id = r.analytics_manager_id
   AND h.version = 1 AND h.name = 'help_delivered' AND h.origin = 'system'
   AND h.occurred_at >= r.occurred_at AND h.occurred_at <= p_at
   AND h.recorded_at <= p_at) AS helped,
 EXISTS (SELECT 1 FROM public.journey_events s
   WHERE s.analytics_manager_id = r.analytics_manager_id AND s.version = 1
   AND s.origin = 'user' AND s.user_activity
   AND ((r.target = 'onboarding' AND s.name = 'club_claimed')
     OR (r.target = 'squad' AND s.name = 'lineup_saved'))
   AND (r.envelope ->> 'ownership_id' IS NULL
     OR s.envelope ->> 'ownership_id' = r.envelope ->> 'ownership_id')
   AND s.occurred_at >= r.occurred_at
   AND s.occurred_at < r.occurred_at + INTERVAL '30 minutes'
   AND s.recorded_at <= p_at) AS recovered
 FROM errors r
)
SELECT reason, surface, target, COUNT(*) AS errors,
 COUNT(DISTINCT analytics_manager_id) AS managers,
 COUNT(*) FILTER (WHERE mature) AS "eligible_N",
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE helped) AS help_accepted,
 COUNT(*) FILTER (WHERE NOT helped) AS help_unconfirmed,
 COUNT(*) FILTER (WHERE mature AND recovered) AS recovered_n
FROM outcomes GROUP BY reason, surface, target ORDER BY reason, surface, target

) r;
WHEN 'opportunities' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at, am.manager_id, a.kind,
        a.campaign_id, m.locale
 FROM public.acquisition_first_touches a
 JOIN public.analytics_managers am ON am.id = a.analytics_manager_id
 JOIN public.managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND (COALESCE(p_filters->>'source','all') = 'all' OR a.kind = p_filters->>'source')
 AND (COALESCE(p_filters->>'locale','all') = 'all' OR m.locale = p_filters->>'locale')
 AND (COALESCE(p_filters->>'variant','all') = 'all' OR EXISTS (
   SELECT 1 FROM public.journey_events v WHERE v.analytics_manager_id = am.id
   AND v.name = 'experiment_exposed' AND v.version = 1 AND v.origin = 'system'
   AND v.recorded_at <= p_at AND v.occurred_at BETWEEN a.started_at AND p_at
   AND v.envelope->>'variant' = p_filters->>'variant'))
 AND ((COALESCE(p_filters->>'tier','all') = 'all' AND
       COALESCE(p_filters->>'set','all') = 'all') OR EXISTS (
   SELECT 1 FROM LATERAL (
     SELECT o.club_id, o.claimed_at FROM public.ownership_periods o
     WHERE o.manager_id = m.id AND o.claimed_at >= a.started_at AND o.claimed_at <= p_at
     ORDER BY o.claimed_at, o.id LIMIT 1
   ) first_club CROSS JOIN LATERAL (
     SELECT l.tier, ls.name FROM public.season_clubs sc JOIN public.seasons s ON s.id = sc.season_id
     JOIN public.leagues l ON l.id = sc.league_id JOIN public.league_sets ls ON ls.id = l.league_set_id
     WHERE sc.club_id = first_club.club_id
     ORDER BY (s.starts_at > first_club.claimed_at),
       CASE WHEN s.starts_at <= first_club.claimed_at THEN s.starts_at END DESC,
       s.starts_at, s.id LIMIT 1
   ) membership WHERE (COALESCE(p_filters->>'tier','all') = 'all'
     OR membership.tier::text = p_filters->>'tier')
   AND (COALESCE(p_filters->>'set','all') = 'all' OR membership.name = p_filters->>'set')
 ))

 AND NOT EXISTS (SELECT 1 FROM public.analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= v_from AND a.started_at <= p_at
)

, calendar AS (
 SELECT p.id, p.season_id, p.league_id, p.home_club_id, p.away_club_id,
        p.assigned_day, f.status, f.scheduled_start_at, f.lineup_lock_at,
        f.actual_start_at, f.actual_finish_at
 FROM public.fixture_plans p LEFT JOIN public.fixtures f ON f.id = p.id
 WHERE p.competition = 'league'
 UNION ALL
 SELECT f.id, f.season_id, f.league_id, f.home_club_id, f.away_club_id,
        (f.scheduled_start_at AT TIME ZONE 'UTC')::date, f.status,
        f.scheduled_start_at, f.lineup_lock_at, f.actual_start_at, f.actual_finish_at
 FROM public.fixtures f WHERE f.competition = 'league'
 AND NOT EXISTS (SELECT 1 FROM public.fixture_plans p WHERE p.id = f.id)
), candidates AS (
 SELECT c.analytics_manager_id, c.started_at, o.id AS ownership_id, o.claimed_at,
        LEAST(o.ended_at, o.expires_at) AS ownership_end, p.*,
        ROW_NUMBER() OVER (PARTITION BY c.analytics_manager_id ORDER BY
          COALESCE(p.actual_start_at, p.scheduled_start_at,
            p.assigned_day::timestamp AT TIME ZONE 'UTC'), p.id) AS ordinal
 FROM cohort c JOIN public.ownership_periods o ON o.manager_id = c.manager_id
 JOIN calendar p ON p.home_club_id = o.club_id OR p.away_club_id = o.club_id
 WHERE o.claimed_at <= p_at
 AND o.claimed_at <= COALESCE(p.lineup_lock_at,
     (p.assigned_day + 1)::timestamp AT TIME ZONE 'UTC')
 AND LEAST(o.ended_at, o.expires_at) > COALESCE(p.lineup_lock_at,
     p.assigned_day::timestamp AT TIME ZONE 'UTC')
 AND p.status IS DISTINCT FROM 'cancelled'
), classified AS (
 SELECT p.*,
 CASE
 WHEN p.claimed_at < p.started_at THEN 'coverage'
 WHEN (SELECT COUNT(*) FROM calendar full_cal
       WHERE full_cal.season_id = p.season_id AND full_cal.league_id = p.league_id) <> 90
   THEN 'coverage'
 WHEN p.status IS NULL OR p.status IN ('scheduled','live','paused') THEN 'waiting'
 WHEN p.status = 'abandoned' THEN 'unsupported'
 WHEN p.actual_start_at IS NULL OR p.actual_finish_at IS NULL
   OR NOT EXISTS (SELECT 1 FROM public.match_results r
     WHERE r.fixture_id = p.id AND r.recorded_at <= p_at)
   THEN 'unavailable'
 WHEN p.actual_finish_at + INTERVAL '24 hours' > p_at THEN 'waiting'
 WHEN p.ownership_end < p.actual_finish_at + INTERVAL '24 hours' THEN 'interrupted'
 ELSE 'mature' END AS state,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = p.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.name IN ('match_opened','match_report_opened','match_command_accepted')
   AND e.envelope ->> 'fixture_id' = p.id::text
   AND e.envelope ->> 'ownership_id' = p.ownership_id::text
   AND e.occurred_at >= GREATEST(p.claimed_at, p.actual_start_at - INTERVAL '24 hours')
   AND e.occurred_at < p.actual_finish_at + INTERVAL '24 hours'
   AND e.recorded_at <= p_at) AS participated,
 EXISTS (SELECT 1 FROM public.journey_events e
   WHERE e.analytics_manager_id = p.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.name IN ('match_opened','match_report_opened','match_command_accepted')
   AND e.envelope ->> 'fixture_id' = p.id::text
   AND e.envelope ->> 'ownership_id' = p.ownership_id::text
   AND e.occurred_at >= GREATEST(p.claimed_at, p.actual_start_at - INTERVAL '24 hours')
   AND e.occurred_at < p.actual_finish_at + INTERVAL '24 hours'
   AND e.recorded_at > p_at) AS late
 FROM candidates p
 WHERE ordinal <= 2
)

, per_manager AS (
 SELECT c.analytics_manager_id, COALESCE(f.state, 'no_calendar') AS first_state,
        COALESCE(f.participated, false) AS first_participated,
        COALESCE(f.late, false) AS first_late,
        CASE WHEN f.state = 'mature' AND f.participated THEN COALESCE(s.state, 'no_calendar')
             ELSE 'no_first' END AS second_state,
        COALESCE(s.participated, false) AS second_participated,
        COALESCE(s.late, false) AS second_late
 FROM cohort c LEFT JOIN classified f
 ON f.analytics_manager_id = c.analytics_manager_id AND f.ordinal = 1
 LEFT JOIN classified s ON s.analytics_manager_id = c.analytics_manager_id AND s.ordinal = 2
), stages AS (
 SELECT 'first' AS stage, first_state AS state, first_participated AS participated,
        first_late AS late FROM per_manager
 UNION ALL
 SELECT 'second', second_state, second_participated, second_late FROM per_manager
)
SELECT stage, state, COUNT(*) AS managers,
 COUNT(*) FILTER (WHERE state = 'mature' AND participated) AS participated_n,
 COUNT(*) FILTER (WHERE state = 'mature' AND NOT participated AND late) AS unknown_late
FROM stages GROUP BY stage, state ORDER BY stage, state

) r;
WHEN 'feedback' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

      SELECT c.slug AS campaign, m.is_internal AS internal,
        COUNT(*) AS "offered_N",
        COUNT(*) FILTER (WHERE r.delivered_at <= p_at) AS delivery_confirmed,
        COUNT(*) FILTER (WHERE r.delivered_at IS NULL OR r.delivered_at > p_at)
          AS delivery_unconfirmed,
        COUNT(*) FILTER (WHERE r.status='answered' AND r.responded_at <= p_at) AS answered_n,
        COUNT(*) FILTER (WHERE r.status='skipped' AND r.responded_at <= p_at) AS skipped_n,
        COUNT(*) FILTER (WHERE r.status='opted_out' AND x.kind='opt_out'
          AND x.excluded_at <= p_at) AS opted_out_n,
        COUNT(*) FILTER (WHERE r.status='opted_out' AND
          (x.excluded_at IS NULL OR x.excluded_at > p_at)) AS redacted_unknown,
        COUNT(*) FILTER (WHERE r.status <> 'opted_out'
          AND (r.responded_at IS NULL OR r.responded_at > p_at)
          AND r.expires_at <= p_at) AS expired_unanswered,
        COUNT(*) FILTER (WHERE r.status <> 'opted_out'
          AND (r.responded_at IS NULL OR r.responded_at > p_at)
          AND (r.expires_at IS NULL OR r.expires_at > p_at)) AS waiting
      FROM public.feedback_responses r JOIN public.feedback_campaigns c ON c.id=r.campaign_id
      JOIN public.managers m ON m.id=r.manager_id
      LEFT JOIN public.analytics_exclusions x ON x.manager_id=m.id
      WHERE r.prompted_at >= p_from AND r.prompted_at <= p_at
      GROUP BY c.slug,m.is_internal ORDER BY c.slug,m.is_internal
    
) r;
WHEN 'feedback_reasons' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

      SELECT c.slug AS campaign, m.is_internal AS internal, r.reason, COUNT(*) AS answers
      FROM public.feedback_responses r JOIN public.feedback_campaigns c ON c.id=r.campaign_id
      JOIN public.managers m ON m.id=r.manager_id
      WHERE r.prompted_at >= p_from AND r.prompted_at <= p_at
        AND r.status='answered' AND r.responded_at <= p_at
      GROUP BY c.slug,m.is_internal,r.reason ORDER BY c.slug,m.is_internal,r.reason
    
) r;
WHEN 'delivery' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

SELECT payload ->> 'kind' AS kind,
 COUNT(*) AS total,
 COUNT(*) FILTER (WHERE status = 'sent') AS delivered,
 COUNT(*) FILTER (WHERE status = 'failed' AND last_error IS DISTINCT FROM 'unknown_outcome')
   AS failed_known,
 COUNT(*) FILTER (WHERE status = 'failed' AND last_error = 'unknown_outcome')
   AS failed_unknown,
 COUNT(*) FILTER (WHERE status = 'pending') AS pending,
 COUNT(*) FILTER (WHERE status != 'sent' AND (
   last_error = 'unknown_outcome' OR EXISTS (
     SELECT 1 FROM public.outbox_delivery_attempts a
     WHERE a.outbox_id = outbox_messages.id AND a.outcome = 'unknown'
   ))) AS unknown_messages
FROM public.outbox_messages
WHERE due_at >= p_from AND due_at <= p_at
GROUP BY payload ->> 'kind'
ORDER BY payload ->> 'kind'

) r;
WHEN 'delivery_attempts' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

SELECT outcome, COUNT(*) AS count
FROM public.outbox_delivery_attempts
WHERE outbox_id IN (
 SELECT id FROM public.outbox_messages WHERE due_at >= p_from AND due_at <= p_at
)
AND started_at >= p_from AND started_at <= p_at
GROUP BY outcome
ORDER BY outcome

) r;
WHEN 'latency' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (

WITH requests AS (
 SELECT * FROM public.runtime_requests WHERE received_at >= p_from AND received_at <= p_at
), samples(metric, seconds) AS (
 SELECT 'request_commit', CASE WHEN committed_at <= p_at THEN
   extract(epoch FROM committed_at-received_at) END FROM requests
 UNION ALL SELECT 'request_render', CASE WHEN rendered_at <= p_at THEN
   extract(epoch FROM rendered_at-render_started_at) END FROM requests
 UNION ALL SELECT 'request_telegram', CASE WHEN finished_at <= p_at AND
   delivery_outcome IN ('accepted','rejected','unknown') THEN
   extract(epoch FROM finished_at-network_started_at) END FROM requests
 UNION ALL SELECT 'request_total', CASE WHEN finished_at <= p_at THEN
   extract(epoch FROM finished_at-received_at) END FROM requests
 UNION ALL SELECT 'worker_processing', extract(epoch FROM finished_at-started_at)
   FROM public.operation_attempts WHERE started_at BETWEEN p_from AND p_at AND finished_at <= p_at
   AND stage LIKE 'match.%'
 UNION ALL SELECT 'worker_lateness', greatest(0, extract(epoch FROM started_at-due_at))
   FROM public.operation_attempts WHERE started_at BETWEEN p_from AND p_at AND finished_at <= p_at
   AND stage LIKE 'match.%'
 UNION ALL SELECT 'delivery_enqueue_to_claim',
   extract(epoch FROM a.started_at-greatest(o.enqueued_at,a.available_at))
   FROM public.outbox_delivery_attempts a JOIN public.outbox_messages o ON o.id=a.outbox_id
   WHERE a.started_at BETWEEN p_from AND p_at AND a.available_at IS NOT NULL
   AND o.enqueued_at IS NOT NULL
 UNION ALL SELECT 'delivery_render', CASE WHEN rendered_at <= p_at THEN
   extract(epoch FROM rendered_at-render_started_at) END
   FROM public.outbox_delivery_attempts WHERE started_at BETWEEN p_from AND p_at
 UNION ALL SELECT 'delivery_telegram', CASE WHEN finished_at <= p_at THEN
   extract(epoch FROM finished_at-network_started_at) END
   FROM public.outbox_delivery_attempts WHERE started_at BETWEEN p_from AND p_at
 UNION ALL SELECT 'command_wait_for_tick', CASE WHEN applied_at <= p_at THEN
   extract(epoch FROM applied_at-accepted_at) END
   FROM public.accepted_commands WHERE accepted_at BETWEEN p_from AND p_at
), metrics AS (
 SELECT metric, count(*) AS observations,
   count(*) FILTER (WHERE seconds >= 0) AS measured,
   count(*) FILTER (WHERE seconds IS NULL OR seconds < 0) AS unknown,
   percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds) FILTER (WHERE seconds >= 0) AS p50_seconds,
   percentile_cont(0.95) WITHIN GROUP (ORDER BY seconds) FILTER (WHERE seconds >= 0) AS p95_seconds
 FROM samples GROUP BY metric
)
SELECT * FROM metrics ORDER BY metric

) r;
WHEN 'queues' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (
SELECT 'jobs' AS queue, status, count(*) AS items,
 count(*) FILTER (WHERE due_at <= p_at) AS due_items,
 extract(epoch FROM p_at-min(due_at) FILTER (WHERE due_at <= p_at)) AS oldest_due_seconds
 FROM public.jobs GROUP BY status UNION ALL
 SELECT 'outbox', status, count(*), count(*) FILTER (WHERE due_at <= p_at),
 extract(epoch FROM p_at-min(due_at) FILTER (WHERE due_at <= p_at))
 FROM public.outbox_messages GROUP BY status
) r;
WHEN 'worker' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (
WITH latest AS (SELECT instance_id FROM public.worker_heartbeats ORDER BY started_at DESC LIMIT 1)
 SELECT component, outcome, started_at, observed_at, succeeded_at,
 extract(epoch FROM p_at-observed_at) AS observation_age_seconds,
 extract(epoch FROM p_at-succeeded_at) AS success_age_seconds,
 CASE WHEN observed_at > p_at OR succeeded_at > p_at THEN 'invalid_clock'
 WHEN p_at-observed_at > interval '120 seconds' THEN 'stale'
 WHEN outcome = 'ok' AND (succeeded_at IS NULL OR p_at-succeeded_at > interval '120 seconds')
 THEN 'stale' ELSE outcome END AS state
 FROM public.worker_heartbeats WHERE instance_id IN (SELECT instance_id FROM latest) ORDER BY component
) r;
WHEN 'sets' THEN
 SELECT COALESCE(jsonb_agg(to_jsonb(r)), '[]'::jsonb) INTO v_rows FROM (
SELECT name FROM public.league_sets ORDER BY name
) r;
 ELSE RAISE EXCEPTION 'Unknown report' USING ERRCODE = '22023';
 END CASE;
 RETURN jsonb_build_object('state','available','rows',v_rows,
   'requested_from',p_from,'available_from',v_from,'report_at',p_at,
   'coverage_started_at',v_coverage,'coverage_limited',v_from > p_from,
   'filters',p_filters,'scope',CASE WHEN v_journey THEN 'external_journey' ELSE 'operational' END);
END
$function$;
REVOKE ALL ON FUNCTION goaletto_operator.report(text,timestamptz,timestamptz,jsonb) FROM PUBLIC;
