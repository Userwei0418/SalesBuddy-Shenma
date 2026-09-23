BEGIN;
-- Team targets belong to an organization, not to the roles/count of its members.
-- Preserve historical person/department authorization and all write restrictions.
-- CREATE OR REPLACE preserves the established function owner and execution ACL.
CREATE OR REPLACE FUNCTION security.target_scope_read(p_scope text,p_user uuid,p_team uuid,p_department_code text)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(p_department_code IN ('sales','fde') AND (p_scope='department' OR p_department_code='sales') AND
 CASE
 WHEN p_scope='department' AND p_user IS NULL AND p_team IS NULL THEN
   security.management_actor() OR (p_department_code='sales' AND common.current_role_code()='manager'
    AND security.has_active_role('manager'))
 WHEN p_scope='person' AND p_team IS NULL THEN EXISTS(
   SELECT 1 FROM platform.user_ref u WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id()
    AND u.status='active' AND u.deleted_at IS NULL AND (
     security.management_actor() OR
     (p_user=common.current_user_ref_id() AND common.current_role_code() IN ('sales','supervisor','manager','fde','fde_lead')
      AND security.has_active_role(common.current_role_code())
      AND (common.current_role_code() NOT IN ('fde','fde_lead') OR security.is_fde_actor())) OR
     (common.current_role_code() IN ('supervisor','manager') AND security.has_active_role(common.current_role_code())
      AND EXISTS(SELECT 1 FROM platform.role_binding b WHERE b.workspace_id=u.workspace_id AND b.user_ref_id=u.id
       AND b.role_code IN ('sales','supervisor','manager') AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to)
      AND (common.current_role_code()='manager' OR EXISTS(SELECT 1 FROM platform.team_membership tm
       WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND tm.membership_role IN ('sales','supervisor','manager')
        AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to AND security.supervises_team(tm.team_id)))) OR
     (common.current_role_code()='fde_lead' AND security.is_fde_actor() AND security.fde_manages_user(p_user))))
 WHEN p_scope='team' AND p_user IS NULL THEN EXISTS(
   SELECT 1 FROM platform.team t WHERE t.id=p_team AND t.workspace_id=common.current_workspace_id()
    AND t.status='active' AND t.deleted_at IS NULL
    AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to AND (
     security.management_actor() OR
     (common.current_role_code() IN ('supervisor','manager') AND security.has_active_role(common.current_role_code())
      AND (common.current_role_code()='manager' OR security.supervises_team(t.id))) OR
     (common.current_role_code()='fde_lead' AND security.is_fde_actor()
      AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',p_team))))
 ELSE false END,false);
$$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V118','管理者可读取授权有效团队的目标，包括FDE团队与空团队；保持目标写权限');
COMMIT;
