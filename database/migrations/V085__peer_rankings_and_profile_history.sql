BEGIN;

-- The primary membership is a display label, not an authorization boundary.
-- Any effective team membership can place a person in the authorized cohort.
-- EXISTS keeps overlapping memberships from multiplying facts; events remain
-- attributed and filtered by their recorded team, including customer ownership.
CREATE OR REPLACE FUNCTION security.business_ranking(p_metric text,p_start date,p_end date,
 p_months integer[] DEFAULT NULL,p_self_only boolean DEFAULT false) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE answer jsonb;
BEGIN
 IF p_metric NOT IN ('followup','customers','opportunities','opportunity_acv','active_opportunities')
   OR p_start IS NULL OR p_end IS NULL OR p_start>p_end
   OR (p_months IS NOT NULL AND (cardinality(p_months)=0 OR EXISTS(SELECT 1 FROM unnest(p_months) m WHERE m<1 OR m>12)))
 THEN RAISE EXCEPTION 'INVALID_RANKING_QUERY' USING ERRCODE='22023'; END IF;
 IF common.current_role_code() NOT IN ('sales','supervisor','manager')
   OR NOT security.has_active_role(common.current_role_code())
   OR NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=common.current_user_ref_id()
      AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL)
 THEN RAISE EXCEPTION 'RANKING_FORBIDDEN' USING ERRCODE='42501'; END IF;

 WITH members AS (
  SELECT u.id,u.account_code,u.display_name AS name,rb.role_code AS role,t.id AS team_id,t.name AS team
  FROM platform.user_ref u
  JOIN LATERAL (SELECT b.role_code FROM platform.role_binding b
    WHERE b.workspace_id=u.workspace_id AND b.user_ref_id=u.id
      AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
      AND ((common.current_role_code()='sales' AND b.role_code='sales')
        OR (common.current_role_code()='supervisor' AND b.role_code IN ('sales','supervisor'))
        OR (common.current_role_code()='manager' AND b.role_code IN ('sales','supervisor','manager')))
    ORDER BY CASE b.role_code WHEN 'manager' THEN 0 WHEN 'supervisor' THEN 1 ELSE 2 END,b.id LIMIT 1) rb ON true
  LEFT JOIN LATERAL (SELECT tm.team_id FROM platform.team_membership tm
    WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND tm.is_primary
      AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    ORDER BY tm.valid_from DESC,tm.id LIMIT 1) primary_team ON true
  LEFT JOIN platform.team t ON t.id=primary_team.team_id AND t.workspace_id=u.workspace_id AND t.deleted_at IS NULL
  WHERE u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL
    AND (common.current_role_code()='manager' OR u.id=common.current_user_ref_id()
      OR (common.current_role_code()='supervisor' AND EXISTS(
        SELECT 1 FROM platform.team_membership member_team
        WHERE member_team.workspace_id=u.workspace_id AND member_team.user_ref_id=u.id
          AND clock_timestamp()>=member_team.valid_from AND clock_timestamp()<member_team.valid_to
          AND security.supervises_team(member_team.team_id)))
      OR common.current_role_code()='sales')
 ), events AS (
  SELECT v.recorder_user_ref_id AS person,COALESCE(v.recorder_team_id,m.team_id) AS team_id,
    1::numeric AS value,v.customer_id
  FROM activity.visit v JOIN members m ON m.id=v.recorder_user_ref_id
  WHERE p_metric='followup' AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
    AND v.status IN ('confirmed','archived')
    AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',v.interaction_at))::integer=ANY(p_months))
    AND (common.current_role_code()<>'supervisor' OR security.supervises_team(COALESCE(v.recorder_team_id,m.team_id)))
  UNION ALL
  SELECT m.id,c.owner_team_id,1::numeric,c.id
  FROM crm.customer c JOIN crm.customer_sales_member cm ON cm.customer_id=c.id AND cm.workspace_id=c.workspace_id
    JOIN members m ON m.id=cm.user_ref_id
  WHERE p_metric='customers' AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
    AND (common.current_role_code()<>'supervisor' OR security.supervises_team(c.owner_team_id))
    AND timezone('Asia/Shanghai',c.created_at)::date BETWEEN p_start AND p_end
    AND (p_months IS NULL OR extract(month FROM timezone('Asia/Shanghai',c.created_at))::integer=ANY(p_months))
  UNION ALL
  SELECT o.owner_user_ref_id,o.owner_team_id,CASE WHEN p_metric='opportunity_acv' THEN o.amount ELSE 1 END,o.customer_id
  FROM crm.opportunity o JOIN members m ON m.id=o.owner_user_ref_id
    JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
  WHERE p_metric IN ('opportunities','opportunity_acv','active_opportunities')
    AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL AND o.status IN ('open','won')
    AND (common.current_role_code()<>'supervisor' OR security.supervises_team(o.owner_team_id))
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
      AND (common.current_role_code()='manager' OR security.supervises_team(t.id))
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
   'groups',CASE WHEN p_self_only OR common.current_role_code()='sales' THEN '[]'::jsonb
     ELSE COALESCE((SELECT jsonb_agg(to_jsonb(g) ORDER BY g.rank,g.code) FROM groups g),'[]'::jsonb) END,
   'calculation','confirmed_followup_current_claim_nonlost_opportunity_peer_v3') INTO answer;
 RETURN answer;
END;
$$;
COMMENT ON FUNCTION security.business_ranking(text,date,date,integer[],boolean) IS
 '一线销售同级全员汇总排名，负责人保持授权部门统计；排名不扩大明细数据权限';

-- Public peer comparison exposes aggregates only, never project/customer IDs or visit text.
CREATE FUNCTION security.fde_peer_rankings(p_start date,p_end date,p_quarters integer[] DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE answer jsonb;
BEGIN
 IF NOT security.is_fde_actor() THEN RAISE insufficient_privilege; END IF;
 IF p_start IS NULL OR p_end IS NULL OR p_start>p_end OR
   (p_quarters IS NOT NULL AND EXISTS(SELECT 1 FROM unnest(p_quarters) q WHERE q NOT BETWEEN 1 AND 4))
 THEN RAISE EXCEPTION 'INVALID_FDE_RANKING_RANGE' USING ERRCODE='22023'; END IF;
 WITH members AS MATERIALIZED (
   SELECT DISTINCT ON(u.id) u.id,u.display_name AS name,t.name AS team_name,'fde'::text AS role
   FROM platform.user_ref u JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
   JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
   WHERE u.workspace_id=common.current_workspace_id() AND b.role_code='fde' AND tm.membership_role='fde'
     AND security.fde_user_is_active(u.id,'fde',tm.team_id)
     AND (b.team_id IS NULL OR b.team_id=tm.team_id)
     AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   ORDER BY u.id,tm.is_primary DESC,tm.valid_from DESC
 ), visits AS (
   SELECT v.recorder_user_ref_id AS user_id,count(*) AS followup_count,
     count(DISTINCT v.opportunity_id) AS opportunity_count
   FROM activity.visit v JOIN members m ON m.id=v.recorder_user_ref_id
   WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND v.status='archived'
     AND v.recording_role_code_snapshot='fde' AND v.created_by_user_ref_id=v.recorder_user_ref_id
     AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
     AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',v.interaction_at))::int=ANY(p_quarters))
   GROUP BY v.recorder_user_ref_id
 ), relations AS MATERIALIZED (
   SELECT DISTINCT p.user_ref_id AS user_id,o.id AS opportunity_id
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
   SELECT m.id::text AS user_id,m.name,m.team_name,m.role,COALESCE(v.followup_count,0) AS followup_count,
     COALESCE(l.recognized_amount,0) AS recognized_amount,COALESCE(d.demo_scene_count,0) AS demo_scene_count,
     COALESCE(v.opportunity_count,0) AS opportunity_count,COALESCE(p.opportunity_count,0) AS current_opportunity_count
   FROM members m LEFT JOIN visits v ON v.user_id=m.id LEFT JOIN ledger l ON l.user_id=m.id
   LEFT JOIN demos d ON d.user_id=m.id LEFT JOIN project_counts p ON p.user_id=m.id
 ) SELECT jsonb_build_object('data_source','database','scope','all_fde','complete',true,
   'start',p_start,'end',p_end,'total',count(*),'items',COALESCE(jsonb_agg(to_jsonb(f) ORDER BY f.name,f.user_id),'[]'::jsonb),
   'amount_basis','参与项目已确认确收；同项目可展示在多名协助人的个人行，不可累加为公司业绩') INTO answer FROM facts f;
 RETURN answer;
END $$;
COMMENT ON FUNCTION security.fde_peer_rankings(date,date,integer[]) IS
 '普通FDE同级全员归档跟进/协助确收/Demo登记汇总；严格禁止返回业务详情';

-- Own registration totals survive leaving a project. Only aggregates are
-- released; project details retain the existing opportunity read permission.
CREATE FUNCTION security.fde_demo_statistics(p_users uuid[],p_opportunities uuid[],p_start timestamptz,
 p_end timestamptz,p_quarters integer[] DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE answer jsonb;
BEGIN
 IF NOT security.is_fde_actor() OR EXISTS(SELECT 1 FROM unnest(p_users) uid
   WHERE uid<>common.current_user_ref_id() AND NOT security.fde_manages_user(uid))
   OR EXISTS(SELECT 1 FROM unnest(p_opportunities) oid WHERE NOT security.has_opportunity_read_access(oid))
 THEN RAISE insufficient_privilege; END IF;
 WITH scenes AS MATERIALIZED (
   SELECT opportunity_id,created_by FROM crm.opportunity_demo_scenes
   WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND created_at>=p_start AND created_at<p_end
     AND (created_by=ANY(p_users) OR opportunity_id=ANY(p_opportunities))
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',created_at))::int=ANY(p_quarters))
 ), people AS (SELECT created_by::text AS id,count(*) AS total FROM scenes
   WHERE created_by=ANY(p_users) GROUP BY created_by)
 SELECT jsonb_build_object('project_count',count(*) FILTER(WHERE opportunity_id=ANY(p_opportunities)),
   'own_count',count(*) FILTER(WHERE created_by=ANY(p_users)),
   'by_person',COALESCE((SELECT jsonb_object_agg(id,total) FROM people),'{}'::jsonb)) INTO answer FROM scenes;
 RETURN answer;
END $$;

-- Bounded historical evidence for an authorized member; only the six factual
-- dimensions and currently readable coaching are projected out of Agent runs.
CREATE FUNCTION security.fde_profile_history(p_user uuid,p_days integer DEFAULT 30,p_limit integer DEFAULT 30)
RETURNS TABLE(run_id uuid,reviewed_at timestamptz,dimensions jsonb,advice jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.is_fde_actor() OR NOT (p_user=common.current_user_ref_id() OR security.fde_manages_user(p_user))
 THEN RAISE insufficient_privilege; END IF;
 RETURN QUERY
 SELECT r.id,r.completed_at,r.business_context->'profile_snapshot'->'dimensions',
 CASE WHEN NOT EXISTS (
   SELECT 1 FROM jsonb_array_elements(COALESCE(r.business_context->'profile_snapshot'->'coaching_inputs'->'sources','[]'::jsonb)) src
   WHERE NOT CASE src->>'source_type'
     WHEN 'visit' THEN EXISTS(SELECT 1 FROM activity.visit v
       WHERE 'visit:'||v.id::text=src->>'source_ref' AND v.workspace_id=common.current_workspace_id()
       AND v.deleted_at IS NULL AND security.has_customer_access(v.customer_id))
     WHEN 'task' THEN EXISTS(SELECT 1 FROM workflow.task t
       WHERE 'task:'||t.id::text=src->>'source_ref' AND t.workspace_id=common.current_workspace_id()
       AND t.deleted_at IS NULL AND (security.fde_can_act_on_task(t.id) OR security.fde_can_coordinate_task(t.id)))
     ELSE false END
 ) THEN COALESCE(m.structured_content->'action_plan','[]'::jsonb) ELSE '[]'::jsonb END
 FROM agent.run r JOIN agent.conversation c ON c.id=r.conversation_id AND c.workspace_id=r.workspace_id
 LEFT JOIN LATERAL(SELECT structured_content FROM agent.message am WHERE am.source_run_id=r.id
   AND am.sender_type='assistant' ORDER BY am.created_at DESC,am.id DESC LIMIT 1) m ON true
 WHERE r.workspace_id=common.current_workspace_id() AND c.user_ref_id=p_user
   AND r.identity_context->>'user_id'=p_user::text AND r.identity_context->>'role' IN ('fde','fde_lead')
   AND r.business_context->>'surface'='fde_profile' AND r.business_context->>'profile_days'=p_days::text
   AND r.status='succeeded' AND jsonb_array_length(r.business_context->'profile_snapshot'->'dimensions')=6
 ORDER BY r.completed_at DESC,r.id DESC LIMIT LEAST(GREATEST(p_limit,1),30);
END $$;
COMMENT ON FUNCTION security.fde_profile_history(uuid,integer,integer) IS
 '本人或当前所管理成员的已成功六维快照；AI建议再次校验当前原始材料权限，不允许重用为绩效总分';
INSERT INTO ops.schema_migration(version,description) VALUES('V085','同级销售及FDE真实汇总排名与授权画像历史');
COMMIT;
