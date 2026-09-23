BEGIN;

-- Dedicated aggregate boundary; legacy business_ranking remains unchanged.
CREATE FUNCTION security.dashboard_subject_ranking(p_metric text,p_start date,p_end date,
 p_months integer[] DEFAULT NULL,p_member uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE answer jsonb; subject_role text;
BEGIN
 IF p_metric NOT IN ('followup','opportunity_acv')
   OR p_start IS NULL OR p_end IS NULL OR p_start>p_end
   OR (p_months IS NOT NULL AND (cardinality(p_months)=0 OR EXISTS(SELECT 1 FROM unnest(p_months) m WHERE m<1 OR m>12)))
 THEN RAISE EXCEPTION 'INVALID_RANKING_QUERY' USING ERRCODE='22023'; END IF;
 IF common.current_role_code() NOT IN ('sales','supervisor','manager')
   OR NOT security.has_active_role(common.current_role_code())
   OR NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=common.current_user_ref_id()
      AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL)
 THEN RAISE EXCEPTION 'RANKING_FORBIDDEN' USING ERRCODE='42501'; END IF;

 IF p_member IS NULL AND common.current_role_code()='sales' THEN RAISE insufficient_privilege; END IF;
 IF p_member IS NOT NULL THEN
   IF p_member<>common.current_user_ref_id() AND common.current_role_code()<>'manager' AND NOT
     (common.current_role_code()='supervisor' AND EXISTS(
       SELECT 1 FROM platform.team_membership tm WHERE tm.workspace_id=common.current_workspace_id()
         AND tm.user_ref_id=p_member AND security.supervises_team(tm.team_id)
         AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to))
   THEN RAISE insufficient_privilege; END IF;
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

 ), events AS (
  SELECT v.recorder_user_ref_id AS person,COALESCE(v.recorder_team_id,m.team_id) AS team_id,
    1::numeric AS value,v.customer_id
  FROM activity.visit v JOIN members m ON m.id=v.recorder_user_ref_id
  WHERE p_metric='followup' AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
    AND v.status IN ('confirmed','archived')
    AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY(p_months))
  UNION ALL
  SELECT o.owner_user_ref_id,o.owner_team_id,o.amount,o.customer_id
  FROM crm.opportunity o JOIN members m ON m.id=o.owner_user_ref_id
    JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
  WHERE p_metric='opportunity_acv'
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
    WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL AND t.status='active'
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
$$;
COMMENT ON FUNCTION security.dashboard_subject_ranking(text,date,date,integer[],uuid) IS
 '看板所选成员的稳定同级榜、公司销售团队聚合榜；不返回业务明细，不变更CRM权限';

CREATE FUNCTION security.fde_dashboard_rankings(p_start date,p_end date,p_quarters integer[] DEFAULT NULL,p_member uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE answer jsonb; subject_role text;
BEGIN
 IF NOT security.is_fde_actor() THEN RAISE insufficient_privilege; END IF;
 IF p_start IS NULL OR p_end IS NULL OR p_start>p_end OR
   (p_quarters IS NOT NULL AND EXISTS(SELECT 1 FROM unnest(p_quarters) q WHERE q NOT BETWEEN 1 AND 4))
 THEN RAISE EXCEPTION 'INVALID_FDE_RANKING_RANGE' USING ERRCODE='22023'; END IF;

 IF p_member IS NULL AND common.current_role_code()<>'fde_lead' THEN RAISE insufficient_privilege; END IF;
 IF p_member IS NOT NULL AND p_member<>common.current_user_ref_id() AND NOT security.fde_manages_user(p_member)
   AND NOT EXISTS(SELECT 1 FROM security.fde_recorded_visit_history() h WHERE h.user_ref_id=p_member)
 THEN RAISE insufficient_privilege; END IF;
 SELECT b.role_code INTO subject_role FROM platform.role_binding b
 WHERE b.workspace_id=common.current_workspace_id() AND b.user_ref_id=p_member
   AND b.role_code IN ('fde','fde_lead') AND security.fde_user_is_active(p_member,b.role_code)
   AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
 ORDER BY CASE b.role_code WHEN 'fde_lead' THEN 0 ELSE 1 END,b.id LIMIT 1;
 WITH members AS MATERIALIZED (
   SELECT DISTINCT ON(u.id) u.id,u.display_name AS name,t.id AS team_id,t.name AS team_name,b.role_code AS role
   FROM platform.user_ref u JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
   JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
   WHERE u.workspace_id=common.current_workspace_id() AND b.role_code IN ('fde','fde_lead') AND tm.membership_role=b.role_code
     AND security.fde_user_is_active(u.id,b.role_code,tm.team_id)
     AND (b.team_id IS NULL OR b.team_id=tm.team_id)
     AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   ORDER BY u.id,CASE b.role_code WHEN 'fde_lead' THEN 0 ELSE 1 END,tm.is_primary DESC,tm.valid_from DESC,tm.id
 ), recorded_visits AS MATERIALIZED (
   SELECT v.recorder_user_ref_id,v.recording_team_id_snapshot,v.opportunity_id
   FROM activity.visit v
   WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND v.status='archived'
     AND v.recording_role_code_snapshot IN ('fde','fde_lead') AND v.created_by_user_ref_id=v.recorder_user_ref_id
     AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
     AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',v.interaction_at))::int=ANY(p_quarters))
 ), visits AS (
   SELECT v.recorder_user_ref_id AS user_id,count(*) AS followup_count,
     count(DISTINCT v.opportunity_id) AS opportunity_count
   FROM recorded_visits v JOIN members m ON m.id=v.recorder_user_ref_id
   GROUP BY v.recorder_user_ref_id
 ), team_visits AS (
   -- Historical team ownership is immutable, independent of the author's current membership/status.
   SELECT recording_team_id_snapshot AS team_id,count(*) AS followup_count FROM recorded_visits
   WHERE recording_team_id_snapshot IS NOT NULL GROUP BY recording_team_id_snapshot
 ), relations AS MATERIALIZED (
   SELECT DISTINCT p.user_ref_id AS user_id,m.team_id,o.id AS opportunity_id
   FROM crm.opportunity_participant p JOIN members m ON m.id=p.user_ref_id
   JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id AND o.deleted_at IS NULL
   WHERE p.workspace_id=common.current_workspace_id() AND p.participant_role='fde'
     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 ), project_counts AS (SELECT user_id,count(*) AS opportunity_count FROM relations GROUP BY user_id),
 ledger AS (
   SELECT r.user_id,sum(a.amount) AS recognized_amount FROM relations r
   JOIN crm.customer_actual a ON a.opportunity_id=r.opportunity_id AND a.workspace_id=common.current_workspace_id()
   WHERE a.voided_at IS NULL AND a.kind='recognized' AND a.occurred_on BETWEEN p_start AND p_end
     AND a.occurred_on<=timezone('Asia/Shanghai',clock_timestamp())::date
     AND (p_quarters IS NULL OR extract(quarter FROM a.occurred_on)::int=ANY(p_quarters)) GROUP BY r.user_id
 ), demos AS (
   SELECT d.created_by AS user_id,count(*) AS demo_scene_count
   FROM crm.opportunity_demo_scenes d JOIN members m ON m.id=d.created_by
   WHERE d.workspace_id=common.current_workspace_id() AND d.deleted_at IS NULL
     AND timezone('Asia/Shanghai',d.created_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',d.created_at))::int=ANY(p_quarters))
   GROUP BY d.created_by
 ), facts AS (
   SELECT m.id::text AS user_id,m.team_id::text AS team_id,m.name,m.team_name,m.role,COALESCE(v.followup_count,0) AS followup_count,
     COALESCE(l.recognized_amount,0) AS recognized_amount,COALESCE(d.demo_scene_count,0) AS demo_scene_count,
     COALESCE(v.opportunity_count,0) AS opportunity_count,COALESCE(p.opportunity_count,0) AS current_opportunity_count
   FROM members m LEFT JOIN visits v ON v.user_id=m.id LEFT JOIN ledger l ON l.user_id=m.id
   LEFT JOIN demos d ON d.user_id=m.id LEFT JOIN project_counts p ON p.user_id=m.id
 ), team_members AS (
   SELECT DISTINCT tm.team_id,m.id AS user_id FROM platform.team_membership tm JOIN members m ON m.id=tm.user_ref_id
   WHERE tm.workspace_id=common.current_workspace_id() AND tm.membership_role IN ('fde','fde_lead')
     AND security.fde_user_is_active(m.id,NULL,tm.team_id)
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 ), team_relations AS (
   SELECT DISTINCT tm.team_id,r.opportunity_id FROM team_members tm JOIN relations r ON r.user_id=tm.user_id
 ), team_ledger AS (
   SELECT r.team_id,sum(a.amount) AS recognized_amount FROM team_relations r
   JOIN crm.customer_actual a ON a.opportunity_id=r.opportunity_id AND a.workspace_id=common.current_workspace_id()
   WHERE a.voided_at IS NULL AND a.kind='recognized' AND a.occurred_on BETWEEN p_start AND p_end
     AND a.occurred_on<=timezone('Asia/Shanghai',clock_timestamp())::date
     AND (p_quarters IS NULL OR extract(quarter FROM a.occurred_on)::int=ANY(p_quarters)) GROUP BY r.team_id
 ), ranked_teams AS (
   SELECT team_id FROM team_members UNION SELECT team_id FROM team_visits
 ), teams AS (
   SELECT t.id::text AS user_id,t.name,t.name AS team_name,'fde_team'::text AS role,
     COALESCE(max(v.followup_count),0) AS followup_count,COALESCE(sum(f.demo_scene_count),0) AS demo_scene_count,
     COALESCE(max(l.recognized_amount),0) AS recognized_amount
   FROM platform.team t JOIN ranked_teams rt ON rt.team_id=t.id
   LEFT JOIN team_members tm ON tm.team_id=t.id
   LEFT JOIN facts f ON f.user_id=tm.user_id::text LEFT JOIN team_ledger l ON l.team_id=t.id
   LEFT JOIN team_visits v ON v.team_id=t.id
   WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL AND t.status='active'
   GROUP BY t.id,t.name
 ), selected_teams AS (
   SELECT DISTINCT tm.team_id::text AS id FROM platform.team_membership tm
   WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=common.current_user_ref_id()
     AND tm.membership_role='fde_lead' AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',tm.team_id)
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 ), result AS (
   SELECT to_jsonb(f) AS item FROM facts f WHERE p_member IS NOT NULL AND f.role=subject_role
   UNION ALL SELECT to_jsonb(t) FROM teams t WHERE p_member IS NULL
 ) SELECT jsonb_build_object('contract_version',2,'data_source','database',
   'scope',CASE WHEN p_member IS NULL THEN 'company_fde_teams' WHEN subject_role='fde_lead' THEN 'all_fde_leads' ELSE 'all_fde' END,
   'complete',true,'start',p_start,'end',p_end,'total',count(*),
   'selection',jsonb_build_object('personal',p_member IS NOT NULL,'member_id',p_member,'cohort_role',subject_role,
     'team_ids',COALESCE((SELECT jsonb_agg(id) FROM selected_teams),'[]'::jsonb)),
   'items',COALESCE(jsonb_agg(item ORDER BY item->>'name',item->>'user_id'),'[]'::jsonb),
   'amount_basis','个人按参与项目计确收；团队按参与项目并集计确收，同一项目在同一团队仅计一次') INTO answer FROM result;
 RETURN answer;
END $$;
COMMENT ON FUNCTION security.fde_dashboard_rankings(date,date,integer[],uuid) IS
 'FDE看板稳定同级个人榜和公司同类团队榜；团队确收按项目去重，仅返回汇总';

INSERT INTO ops.schema_migration(version,description) VALUES('V090','看板个人与团队排名主体及完整比较范围');
COMMIT;
