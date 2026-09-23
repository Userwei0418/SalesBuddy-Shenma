BEGIN;
SET LOCAL check_function_bodies=on;

-- Permission epochs contain effective authorization facts, not account profile
-- versions. Display-name edits, password resets and login recovery do not alter
-- the customer's panorama or another member's active analysis.
CREATE OR REPLACE FUNCTION security.fde_permission_version() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
WITH actor_teams AS (
 SELECT DISTINCT tm.team_id
 FROM platform.team_membership tm
 WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=common.current_user_ref_id()
 AND tm.membership_role IN ('fde','fde_lead')
 AND (common.current_role_code()<>'fde_lead' OR tm.membership_role='fde_lead')
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 AND security.fde_user_is_active(common.current_user_ref_id(),common.current_role_code(),tm.team_id)
), scope_users AS (
 SELECT common.current_user_ref_id() AS id
 UNION
 SELECT u.id FROM platform.user_ref u
 WHERE u.workspace_id=common.current_workspace_id() AND common.current_role_code()='fde_lead'
 AND security.fde_manages_user(u.id)
), projects AS (
 SELECT p.opportunity_id,p.user_ref_id,p.valid_from,p.valid_to,o.customer_id
 FROM crm.opportunity_participant p
 JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id
 JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 WHERE p.workspace_id=common.current_workspace_id() AND p.participant_role='fde'
 AND p.user_ref_id IN (SELECT id FROM scope_users)
 AND security.fde_user_is_active(p.user_ref_id)
 AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 AND o.deleted_at IS NULL AND c.deleted_at IS NULL
), facts AS (
 SELECT concat_ws(':','team',team_id) AS value FROM actor_teams
 UNION ALL
 SELECT concat_ws(':','member',id) FROM scope_users
 UNION ALL
 SELECT concat_ws(':','project',opportunity_id,user_ref_id,valid_from,valid_to,customer_id) FROM projects
 UNION ALL
 -- A participating FDE reads all projects under each authorized customer. A
 -- sibling moved/deleted during inference must invalidate its old snapshot too.
 SELECT concat_ws(':','panorama',o.id,o.customer_id)
 FROM crm.opportunity o
 WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
 AND o.customer_id IN (SELECT customer_id FROM projects)
)
SELECT CASE WHEN common.current_role_code() IN ('fde','fde_lead') THEN
 md5(concat_ws('|',common.current_workspace_id(),common.current_user_ref_id(),common.current_role_code(),
 security.is_fde_actor(),security.fde_visit_entry_enabled(),
 (SELECT string_agg(value,'|' ORDER BY value) FROM facts))) ELSE NULL END;
$$;
COMMENT ON FUNCTION security.fde_permission_version() IS
 'FDE当前主体/有效辖区/项目及客户全景授权指纹；无关账号资料和登录限制变化不触发业务AI失效';

-- The parameterized function owns the complete historical authorization rule.
-- Filters apply at activity.visit, before building/returning history projections.
CREATE FUNCTION security.fde_recorded_visit_history(
 p_start timestamptz,p_end timestamptz,p_user uuid,p_opportunity uuid,p_quarters integer[])
RETURNS TABLE(visit_id uuid,user_ref_id uuid,customer_id uuid,customer_name text,
 opportunity_id uuid,opportunity_name text,interaction_at timestamptz,recorder_name text,team_id_at_event uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT v.id,v.recorder_user_ref_id,v.customer_id,COALESCE(v.archived_fields->>'customer_name',c.name),
 v.opportunity_id,COALESCE(v.archived_fields->>'opportunity_name',o.name),v.interaction_at,u.display_name,
 v.recording_team_id_snapshot
 FROM activity.visit v
 LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
 LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
 LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id AND u.workspace_id=v.workspace_id
 WHERE security.is_fde_actor() AND v.workspace_id=common.current_workspace_id()
 AND v.deleted_at IS NULL AND v.status='archived'
 AND v.recording_role_code_snapshot IN ('fde','fde_lead')
 AND v.created_by_user_ref_id=v.recorder_user_ref_id AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
 AND (p_start IS NULL OR v.interaction_at>=p_start)
 AND (p_end IS NULL OR v.interaction_at<p_end)
 AND (p_user IS NULL OR v.recorder_user_ref_id=p_user)
 AND (p_opportunity IS NULL OR v.opportunity_id=p_opportunity)
 AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',v.interaction_at))::int=ANY(p_quarters))
 AND (v.recorder_user_ref_id=common.current_user_ref_id() OR (common.current_role_code()='fde_lead'
 AND EXISTS(SELECT 1 FROM platform.team_membership tm JOIN platform.role_binding rb
 ON rb.user_ref_id=tm.user_ref_id AND rb.workspace_id=tm.workspace_id AND rb.role_code='fde_lead'
 WHERE tm.user_ref_id=common.current_user_ref_id() AND tm.workspace_id=v.workspace_id
 AND tm.team_id=v.recording_team_id_snapshot AND tm.membership_role='fde_lead'
 AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',tm.team_id)
 AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
 AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to)));
$$;
CREATE OR REPLACE FUNCTION security.fde_recorded_visit_history()
RETURNS TABLE(visit_id uuid,user_ref_id uuid,customer_id uuid,customer_name text,
 opportunity_id uuid,opportunity_name text,interaction_at timestamptz,recorder_name text,team_id_at_event uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT * FROM security.fde_recorded_visit_history(NULL,NULL,NULL,NULL,NULL);
$$;
COMMENT ON FUNCTION security.fde_recorded_visit_history(timestamptz,timestamptz,uuid,uuid,integer[]) IS
 'FDE本人归档摘要，按日期/作者/商机/季度在源表过滤；参数只缩小已授权范围，移出后仅历史摘要';
COMMENT ON FUNCTION security.fde_recorded_visit_history() IS
 '历史调用兼容入口，授权及投影统一由有参历史函数维护；新列表使用有参函数及SQL分页/聚合';
INSERT INTO ops.schema_migration(version,description)
VALUES('V075','FDE主体授权指纹与数据库历史过滤，保留本人归档及事件时部门历史权限');
COMMIT;
