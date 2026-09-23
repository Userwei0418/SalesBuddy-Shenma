BEGIN;

-- Keep V090 authorization, personal cohorts and all existing metrics intact.
-- Period project counts belong to the immutable recording-team snapshot, with
-- each opportunity counted once per team rather than summed across its members.
-- CREATE OR REPLACE keeps the existing function owner and execution privileges.
CREATE OR REPLACE FUNCTION security.fde_dashboard_rankings(p_start date,p_end date,p_quarters integer[] DEFAULT NULL,p_member uuid DEFAULT NULL)
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
   SELECT recording_team_id_snapshot AS team_id,count(*) AS followup_count,
     count(DISTINCT opportunity_id) AS opportunity_count FROM recorded_visits
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
     COALESCE(max(l.recognized_amount),0) AS recognized_amount,
     COALESCE(max(v.opportunity_count),0) AS opportunity_count
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
 'FDE稳定同级个人榜和公司同类团队榜；团队期间商机按归档时团队去重，团队确收按项目去重，仅返回汇总';

INSERT INTO ops.schema_migration(version,description)
 VALUES('V093','FDE团队效率榜补齐期间跟进商机去重数');
COMMIT;
