BEGIN;

CREATE OR REPLACE FUNCTION security.business_ranking(p_metric text, p_start date, p_end date, p_months integer[] DEFAULT NULL::integer[], p_self_only boolean DEFAULT false)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE answer jsonb;
BEGIN
 IF p_metric NOT IN ('followup','customers','opportunities','opportunity_acv','active_opportunities')
   OR p_start IS NULL OR p_end IS NULL OR p_start>p_end
   OR (p_months IS NOT NULL AND (cardinality(p_months)=0 OR EXISTS(SELECT 1 FROM unnest(p_months) m WHERE m<1 OR m>12)))
 THEN RAISE EXCEPTION 'INVALID_RANKING_QUERY' USING ERRCODE='22023'; END IF;
 IF NOT security.authorization_has('dashboard.ranking') THEN RAISE EXCEPTION 'RANKING_FORBIDDEN' USING ERRCODE='42501'; END IF;

 WITH members AS (
  SELECT u.id,u.account_code,u.display_name AS name,rb.role_code AS role,t.id AS team_id,t.name AS team
  FROM platform.user_ref u
  JOIN LATERAL (SELECT b.role_code FROM platform.role_binding b
    WHERE b.workspace_id=u.workspace_id AND b.user_ref_id=u.id
      AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
      AND b.role_code IN ('sales','supervisor','manager')
      -- Statistical peer cohort is distinct from permission to read an aggregate.
      AND (common.current_role_code()<>'sales' OR b.role_code='sales')
    ORDER BY CASE b.role_code WHEN 'manager' THEN 0 WHEN 'supervisor' THEN 1 ELSE 2 END,b.id LIMIT 1) rb ON true
  LEFT JOIN LATERAL (SELECT tm.team_id FROM platform.team_membership tm
    WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND tm.is_primary
      AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    ORDER BY tm.valid_from DESC,tm.id LIMIT 1) primary_team ON true
  LEFT JOIN platform.team t ON t.id=primary_team.team_id AND t.workspace_id=u.workspace_id AND t.deleted_at IS NULL
  WHERE u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL
    AND security.authorization_subject('dashboard.ranking','person',u.id,NULL)
    AND (common.current_role_code()='sales' OR security.authorization_subject('dashboard.read','person',u.id,NULL))
 ), events AS (
  SELECT v.recorder_user_ref_id AS person,COALESCE(v.recorder_team_id,m.team_id) AS team_id,
    1::numeric AS value,v.customer_id
  FROM activity.visit v JOIN members m ON m.id=v.recorder_user_ref_id
  WHERE p_metric='followup' AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
    AND v.status IN ('confirmed','archived')
    AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY(p_months))
    AND (common.current_role_code()='sales' OR security.authorization_allows('dashboard.read',v.workspace_id,v.recorder_user_ref_id,ARRAY[COALESCE(v.recorder_team_id,m.team_id)],v.recorder_user_ref_id=common.current_user_ref_id()))
    AND security.authorization_allows('dashboard.ranking',v.workspace_id,v.recorder_user_ref_id,ARRAY[COALESCE(v.recorder_team_id,m.team_id)],v.recorder_user_ref_id=common.current_user_ref_id())
  UNION ALL
  SELECT m.id,c.owner_team_id,1::numeric,c.id
  FROM crm.customer c JOIN crm.customer_sales_member cm ON cm.customer_id=c.id AND cm.workspace_id=c.workspace_id
    JOIN members m ON m.id=cm.user_ref_id
  WHERE p_metric='customers' AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
    AND security.authorization_customer('dashboard.ranking',c.id)
    AND (common.current_role_code()='sales' OR security.authorization_allows('dashboard.read',c.workspace_id,cm.user_ref_id,ARRAY[c.owner_team_id],cm.user_ref_id=common.current_user_ref_id()))
    AND timezone('Asia/Shanghai',c.created_at)::date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',c.created_at))::integer=ANY(p_months))
  UNION ALL
  SELECT o.owner_user_ref_id,o.owner_team_id,CASE WHEN p_metric='opportunity_acv' THEN o.amount ELSE 1 END,o.customer_id
  FROM crm.opportunity o JOIN members m ON m.id=o.owner_user_ref_id
    JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
  WHERE p_metric IN ('opportunities','opportunity_acv','active_opportunities')
    AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL AND o.status IN ('open','won')
    AND security.authorization_opportunity_direct('dashboard.ranking',o.id)
    AND (common.current_role_code()='sales' OR security.authorization_opportunity_direct('dashboard.read',o.id))
    AND CASE WHEN p_metric='active_opportunities' THEN EXISTS(
      SELECT 1 FROM activity.visit v WHERE v.workspace_id=o.workspace_id AND v.opportunity_id=o.id
        AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
        AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
        AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY(p_months)))
      WHEN p_metric='opportunity_acv' THEN o.status='open' AND o.expected_close_date BETWEEN p_start AND p_end
        AND (p_months IS NULL OR extract(month FROM o.expected_close_date)::integer=ANY(p_months))
      ELSE timezone('Asia/Shanghai',o.created_at)::date BETWEEN p_start AND p_end
        AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',o.created_at))::integer=ANY(p_months)) END
 ), totals AS (
  SELECT m.id AS user_id,m.account_code,m.name,m.role,m.team,
    COALESCE(sum(e.value),0) AS value,count(e.person) AS record_count,count(DISTINCT e.customer_id) AS customer_count
  FROM members m LEFT JOIN events e ON e.person=m.id GROUP BY m.id,m.account_code,m.name,m.role,m.team
 ), ranked AS (
  SELECT *,rank() OVER(ORDER BY value DESC) AS rank,count(*) OVER() AS population FROM totals
 ), group_roster AS (
  SELECT DISTINCT security.ranking_team_group(t.name) AS code FROM platform.team t
    WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL AND t.status='active'
      AND security.authorization_subject('dashboard.ranking','team',NULL,t.id)
      AND (common.current_role_code()='sales' OR security.authorization_subject('dashboard.read','team',NULL,t.id))
  UNION SELECT security.ranking_team_group(t.name) FROM events e LEFT JOIN platform.team t ON t.id=e.team_id
 ), group_values AS (
  SELECT g.code,CASE g.code WHEN 'north_east' THEN '北区＋东区' WHEN 'south_hkmo' THEN '南区＋港澳' ELSE '未归入两组' END AS name,
    COALESCE(sum(e.value),0) AS value,count(e.person) AS record_count,count(DISTINCT e.customer_id) AS customer_count
  FROM group_roster g LEFT JOIN (events e LEFT JOIN platform.team t ON t.id=e.team_id)
    ON security.ranking_team_group(t.name)=g.code GROUP BY g.code
 ), groups AS (
  SELECT *,rank() OVER(ORDER BY value DESC) AS rank FROM group_values WHERE code<>'unassigned'
  UNION ALL SELECT *,NULL::bigint AS rank FROM group_values WHERE code='unassigned'
 ) SELECT jsonb_build_object('data_source','database','metric',p_metric,'start',p_start,'end',p_end,'months',p_months,
   'scope',CASE WHEN p_self_only AND common.current_role_code()<>'sales' THEN 'self'
     WHEN common.current_role_code()='sales' THEN 'peer'
     WHEN common.current_role_code()='supervisor' THEN 'team' ELSE 'workspace' END,
   'cohort',CASE WHEN common.current_role_code()='sales' THEN 'workspace_sales'
     WHEN common.current_role_code()='supervisor' THEN 'authorized_team_members' ELSE 'workspace_business_members' END,
   'rows',COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.rank,r.account_code) FROM ranked r
      WHERE NOT p_self_only OR common.current_role_code()='sales' OR r.user_id=common.current_user_ref_id()),'[]'::jsonb),
   'region_groups',COALESCE((SELECT jsonb_agg(to_jsonb(g) ORDER BY g.rank,g.code) FROM groups g),'[]'::jsonb),
   'groups',CASE WHEN p_self_only OR common.current_role_code()='sales' THEN '[]'::jsonb
     ELSE COALESCE((SELECT jsonb_agg(to_jsonb(g) ORDER BY g.rank,g.code) FROM groups g),'[]'::jsonb) END,
   'calculation','confirmed_followup_current_claim_nonlost_opportunity_peer_v4') INTO answer;
 RETURN answer;
END;
$function$;

CREATE OR REPLACE FUNCTION security.dashboard_subject_ranking(p_metric text, p_start date, p_end date, p_months integer[] DEFAULT NULL::integer[], p_member uuid DEFAULT NULL::uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE answer jsonb; subject_role text;
BEGIN
 IF p_metric NOT IN ('followup','opportunity_acv')
   OR p_start IS NULL OR p_end IS NULL OR p_start>p_end
   OR (p_months IS NOT NULL AND (cardinality(p_months)=0 OR EXISTS(SELECT 1 FROM unnest(p_months) m WHERE m<1 OR m>12)))
 THEN RAISE EXCEPTION 'INVALID_RANKING_QUERY' USING ERRCODE='22023'; END IF;
 IF NOT security.authorization_has('dashboard.ranking') THEN RAISE EXCEPTION 'RANKING_FORBIDDEN' USING ERRCODE='42501'; END IF;

 IF p_member IS NOT NULL THEN
   IF NOT security.authorization_subject('dashboard.ranking','person',p_member,NULL) THEN RAISE insufficient_privilege; END IF;
   SELECT b.role_code INTO subject_role FROM platform.role_binding b JOIN platform.user_ref u ON u.id=b.user_ref_id
     AND u.workspace_id=b.workspace_id
   WHERE u.id=p_member AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL
     AND b.role_code IN ('sales','supervisor','manager') AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
   ORDER BY CASE b.role_code WHEN 'manager' THEN 0 WHEN 'supervisor' THEN 1 ELSE 2 END,b.id LIMIT 1;
   IF subject_role IS NULL THEN RAISE insufficient_privilege; END IF;
 END IF;

 WITH members AS (
  SELECT u.id,u.account_code,u.display_name AS name,rb.role_code AS role,t.id AS team_id,t.name AS team
  FROM platform.user_ref u
  JOIN LATERAL (SELECT b.role_code FROM platform.role_binding b
    WHERE b.workspace_id=u.workspace_id AND b.user_ref_id=u.id
      AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
      AND b.role_code IN ('sales','supervisor','manager')
    ORDER BY CASE b.role_code WHEN 'manager' THEN 0 WHEN 'supervisor' THEN 1 ELSE 2 END,b.id LIMIT 1) rb ON true
  LEFT JOIN LATERAL (SELECT tm.team_id FROM platform.team_membership tm
    WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND tm.is_primary
      AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    ORDER BY tm.valid_from DESC,tm.id LIMIT 1) primary_team ON true
  LEFT JOIN platform.team t ON t.id=primary_team.team_id AND t.workspace_id=u.workspace_id AND t.deleted_at IS NULL
  WHERE u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL
    AND security.authorization_subject('dashboard.ranking','person',u.id,NULL)

 ), events AS (
  SELECT v.recorder_user_ref_id AS person,COALESCE(v.recorder_team_id,m.team_id) AS team_id,
    1::numeric AS value,v.customer_id
  FROM activity.visit v JOIN members m ON m.id=v.recorder_user_ref_id
  WHERE security.authorization_allows('dashboard.ranking',v.workspace_id,v.recorder_user_ref_id,ARRAY[COALESCE(v.recorder_team_id,m.team_id)],v.recorder_user_ref_id=common.current_user_ref_id()) AND p_metric='followup' AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
    AND v.status IN ('confirmed','archived')
    AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY(p_months))
  UNION ALL
  SELECT o.owner_user_ref_id,o.owner_team_id,o.amount,o.customer_id
  FROM crm.opportunity o JOIN members m ON m.id=o.owner_user_ref_id
    JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
  WHERE security.authorization_opportunity_direct('dashboard.ranking',o.id) AND p_metric='opportunity_acv'
    AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL AND o.status='open'
    AND o.expected_close_date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM o.expected_close_date)::integer=ANY(p_months))
 ), totals AS (
  SELECT m.id AS user_id,m.account_code,m.name,m.role,m.team,
    COALESCE(sum(e.value),0) AS value,count(e.person) AS record_count,count(DISTINCT e.customer_id) AS customer_count
  FROM members m LEFT JOIN events e ON e.person=m.id GROUP BY m.id,m.account_code,m.name,m.role,m.team
 ), ranked AS (
  SELECT *,rank() OVER(ORDER BY value DESC) AS rank,count(*) OVER() AS population FROM totals WHERE role=subject_role
 ), group_roster AS (
  SELECT DISTINCT security.ranking_team_group(t.name) AS code FROM platform.team t
    WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL AND t.status='active' AND security.authorization_subject('dashboard.ranking','team',NULL,t.id)
  UNION SELECT security.ranking_team_group(t.name) FROM events e LEFT JOIN platform.team t ON t.id=e.team_id
 ), group_values AS (
  SELECT g.code,CASE g.code WHEN 'north_east' THEN '北区＋东区' WHEN 'south_hkmo' THEN '南区＋港澳' ELSE '未归入两组' END AS name,
    COALESCE(sum(e.value),0) AS value,count(e.person) AS record_count,count(DISTINCT e.customer_id) AS customer_count
  FROM group_roster g LEFT JOIN (events e LEFT JOIN platform.team t ON t.id=e.team_id)
    ON security.ranking_team_group(t.name)=g.code GROUP BY g.code
 ), groups AS (
  SELECT *,rank() OVER(ORDER BY value DESC) AS rank FROM group_values WHERE code<>'unassigned'
  UNION ALL SELECT *,NULL::bigint AS rank FROM group_values WHERE code='unassigned'
 ) SELECT jsonb_build_object('data_source','database','metric',p_metric,'start',p_start,'end',p_end,'months',p_months,
   'scope',CASE WHEN p_member IS NULL THEN 'company_teams' ELSE 'peer' END,
   'cohort_role',subject_role,
   'subject_region_codes',COALESCE((SELECT jsonb_agg(security.ranking_team_group(m.team)) FROM members m WHERE m.id=p_member),'[]'::jsonb),
   'rows',CASE WHEN p_member IS NULL THEN COALESCE((SELECT jsonb_agg(to_jsonb(g) ORDER BY g.rank,g.code) FROM groups g),'[]'::jsonb)
     ELSE COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.rank,r.account_code) FROM ranked r),'[]'::jsonb) END,
   'groups',COALESCE((SELECT jsonb_agg(to_jsonb(g) ORDER BY g.rank,g.code) FROM groups g),'[]'::jsonb),
   'calculation','dashboard_subject_company_v1') INTO answer;
 RETURN answer;
END;
$function$;

INSERT INTO ops.schema_migration(version,description) VALUES('V141','Rank aggregate authorization uses the configured per-feature cohort');
COMMIT;