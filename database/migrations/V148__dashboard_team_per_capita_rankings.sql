-- Ranking-only projection: organization selectors and business facts stay intact.
CREATE OR REPLACE FUNCTION security.dashboard_team_ranking(
 p_metric text, p_start date, p_end date, p_months integer[], p_team_ids uuid[], p_legacy boolean DEFAULT false)
 RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $function$
DECLARE answer jsonb;
BEGIN
 IF p_metric NOT IN ('followup','opportunity_acv') OR p_metric IS NULL
   OR p_start IS NULL OR p_end IS NULL OR p_start>p_end OR p_legacy IS NULL
   OR (p_months IS NOT NULL AND (cardinality(p_months)=0 OR EXISTS(SELECT 1 FROM unnest(p_months) m WHERE m<1 OR m>12)))
 THEN RAISE EXCEPTION 'INVALID_RANKING_QUERY' USING ERRCODE='22023'; END IF;
 IF NOT security.authorization_has('dashboard.ranking') THEN
   RAISE EXCEPTION 'RANKING_FORBIDDEN' USING ERRCODE='42501'; END IF;
 WITH teams AS MATERIALIZED (
  SELECT t.id,t.name,CASE WHEN p_legacy THEN security.ranking_team_group(t.name)
    ELSE 'team:'||t.id::text END AS code
  FROM platform.team t
  WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL AND t.status='active'
    AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
    AND COALESCE(t.attributes->>'kind','general')<>'fde'
    AND (p_team_ids IS NULL OR t.id=ANY(p_team_ids))
    AND NOT EXISTS(SELECT 1 FROM platform.team child WHERE child.workspace_id=t.workspace_id
      AND child.parent_team_id=t.id AND child.deleted_at IS NULL)
    AND security.authorization_subject('dashboard.ranking','team',NULL,t.id)
 ), authors AS MATERIALIZED (
  SELECT u.id,u.account_code,u.display_name AS name,primary_team.team_id
  FROM platform.user_ref u
  LEFT JOIN LATERAL (
    SELECT tm.team_id FROM platform.team_membership tm
    WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id
      AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    ORDER BY tm.is_primary DESC,tm.valid_from DESC,tm.id LIMIT 1
  ) primary_team ON true
  WHERE u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL
    AND security.authorization_subject('dashboard.ranking','person',u.id,NULL)
    AND EXISTS(SELECT 1 FROM platform.role_binding rb WHERE rb.workspace_id=u.workspace_id AND rb.user_ref_id=u.id
      AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
      AND (rb.role_code IN ('sales','supervisor','manager')
        OR (p_metric='opportunity_acv' AND rb.role_code IN ('fde','fde_lead'))))
 ), roster AS MATERIALIZED (
  -- One person per team/group, independent of their highest display role or number of appointments.
  SELECT DISTINCT t.code,a.id FROM teams t
  JOIN platform.team_membership tm ON tm.team_id=t.id AND tm.workspace_id=common.current_workspace_id()
  JOIN authors a ON a.id=tm.user_ref_id
  WHERE clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    AND tm.membership_role IN ('sales','supervisor','manager')
    AND EXISTS(SELECT 1 FROM platform.role_binding rb
      WHERE rb.workspace_id=tm.workspace_id AND rb.user_ref_id=tm.user_ref_id
        AND rb.role_code=tm.membership_role AND (rb.team_id=tm.team_id OR rb.team_id IS NULL)
        AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to)
 ), events AS MATERIALIZED (
  SELECT t.code,a.id AS person,1::numeric AS value,v.customer_id
  FROM activity.visit v JOIN authors a ON a.id=v.recorder_user_ref_id
  JOIN teams t ON t.id=COALESCE(v.recorder_team_id,a.team_id)
  WHERE p_metric='followup' AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
    AND v.status IN ('confirmed','archived')
    AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY(p_months))
    AND security.authorization_allows('dashboard.ranking',v.workspace_id,a.id,ARRAY[t.id],a.id=common.current_user_ref_id())
  UNION ALL
  SELECT t.code,a.id,o.amount,o.customer_id
  FROM crm.opportunity o JOIN authors a ON a.id=o.owner_user_ref_id JOIN teams t ON t.id=o.owner_team_id
  JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
  WHERE p_metric='opportunity_acv' AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
    AND o.status='open' AND o.expected_close_date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM o.expected_close_date)::integer=ANY(p_months))
    AND security.authorization_opportunity_direct('dashboard.ranking',o.id)
 ), names AS (
  SELECT code,CASE WHEN p_legacy THEN CASE code WHEN 'north_east' THEN '北区＋东区'
    WHEN 'south_hkmo' THEN '南区＋港澳' ELSE '未归入两组' END ELSE max(name) END AS name
  FROM teams GROUP BY code
 ), participants AS (
  SELECT code,id,bool_or(current_member) AS current_member FROM (
    SELECT code,id,true AS current_member FROM roster
    UNION ALL SELECT code,person,false FROM events WHERE p_metric='followup'
  ) all_people GROUP BY code,id
 ), member_totals AS (
  SELECT p.code,p.id AS user_id,a.name,a.account_code,p.current_member,
    count(e.person) AS followup_count,count(DISTINCT e.customer_id) AS customer_count
  FROM participants p JOIN authors a ON a.id=p.id
  LEFT JOIN events e ON e.code=p.code AND e.person=p.id
  WHERE p_metric='followup' GROUP BY p.code,p.id,a.name,a.account_code,p.current_member
 ), totals AS (
  SELECT n.code,n.name,COALESCE(sum(e.value),0) AS total_value,
    count(e.person) AS record_count,count(DISTINCT e.customer_id) AS customer_count,
    (SELECT count(*) FROM roster r WHERE r.code=n.code) AS member_count
  FROM names n LEFT JOIN events e ON e.code=n.code GROUP BY n.code,n.name
 ), metric_values AS (
  SELECT *,CASE WHEN p_metric='followup' THEN total_value/NULLIF(member_count,0) ELSE total_value END AS value
  FROM totals
 ), ranked AS (
  SELECT *,CASE WHEN value IS NOT NULL AND code<>'unassigned' THEN
    rank() OVER(ORDER BY CASE WHEN code<>'unassigned' THEN value END DESC NULLS LAST) END AS rank
  FROM metric_values
 ) SELECT COALESCE(jsonb_agg(to_jsonb(r) || CASE WHEN p_metric='followup' THEN jsonb_build_object(
    'average',round(r.value,2),'members',COALESCE((SELECT jsonb_agg(to_jsonb(m)-'code'
      ORDER BY m.followup_count DESC,m.account_code,m.user_id) FROM member_totals m WHERE m.code=r.code),'[]'::jsonb))
    ELSE '{}'::jsonb END ORDER BY r.rank NULLS LAST,r.code),'[]'::jsonb) INTO answer FROM ranked r;
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
  WHERE security.authorization_allows('dashboard.ranking',v.workspace_id,v.recorder_user_ref_id,ARRAY[COALESCE(v.recorder_team_id,m.team_id)],v.recorder_user_ref_id=common.current_user_ref_id()) AND NOT EXISTS(SELECT 1 FROM platform.team excluded WHERE excluded.id=COALESCE(v.recorder_team_id,m.team_id) AND excluded.workspace_id=v.workspace_id AND excluded.attributes->>'kind'='fde') AND p_metric='followup' AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
    AND v.status IN ('confirmed','archived')
    AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY(p_months))
  UNION ALL
  SELECT o.owner_user_ref_id,o.owner_team_id,o.amount,o.customer_id
  FROM crm.opportunity o JOIN members m ON m.id=o.owner_user_ref_id
    JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
  WHERE security.authorization_opportunity_direct('dashboard.ranking',o.id) AND NOT EXISTS(SELECT 1 FROM platform.team excluded WHERE excluded.id=o.owner_team_id AND excluded.workspace_id=o.workspace_id AND excluded.attributes->>'kind'='fde') AND p_metric='opportunity_acv'
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
    WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL AND t.status='active' AND COALESCE(t.attributes->>'kind','general')<>'fde' AND security.authorization_subject('dashboard.ranking','team',NULL,t.id)
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

-- Inherit owner, exact EXECUTE grants and grant options from the established
-- ranking aggregate, removing deployment defaults before assigning ownership.
DO $$ DECLARE permission record; function_owner name; target text; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
 WHERE p.oid='security.dashboard_subject_ranking(text,date,date,integer[],uuid)'::regprocedure;
 FOREACH target IN ARRAY ARRAY[
  'security.dashboard_team_ranking(text,date,date,integer[],uuid[],boolean)'
 ] LOOP
  FOR permission IN
   SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=target::regprocedure
  LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',target,
    CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
  END LOOP;
  EXECUTE format('ALTER FUNCTION %s OWNER TO %I',target,function_owner);
  FOR permission IN
   SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   LEFT JOIN pg_roles r ON r.oid=a.grantee
   WHERE p.oid='security.dashboard_subject_ranking(text,date,date,integer[],uuid)'::regprocedure
    AND a.privilege_type='EXECUTE'
  LOOP
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %s%s',target,
    CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
    CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
  END LOOP;
 END LOOP;
END $$;
