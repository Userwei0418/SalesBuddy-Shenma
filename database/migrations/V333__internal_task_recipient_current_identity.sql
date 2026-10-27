-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V185__task_recipient_current_identity.sql.
-- Original SHA-256: 3c2728963702b165b24beecd697a3e095ce8ea20494cf35b89867852cf8ad9f3. See shenma_whole_121_manifest.json.
BEGIN;

-- A direct assignment (or a claimed position task) belongs to the current
-- owner account. Its stored role/team describe the assignment, not a permanent
-- requirement to retain that exact appointment. Unclaimed position candidates
-- must still hold the particular role/team for which they were selected.
CREATE OR REPLACE FUNCTION security.task_recipient_eligible(
 p_task uuid,p_user uuid,p_role text,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH recipient AS (
  SELECT u.id,u.workspace_id,EXISTS(
   SELECT 1 FROM workflow.task_assignee a
   WHERE a.task_id=t.id AND a.workspace_id=t.workspace_id
    AND a.assignee_user_ref_id=u.id AND a.responsibility='owner'
  ) AS is_owner
  FROM workflow.task t JOIN platform.user_ref u
   ON u.id=p_user AND u.workspace_id=t.workspace_id
  WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id()
   AND security.workspace_is_active(t.workspace_id) AND t.deleted_at IS NULL
   AND u.status='active' AND u.deleted_at IS NULL
 )
 SELECT EXISTS(
  SELECT 1 FROM recipient person JOIN platform.role_binding r
   ON r.user_ref_id=person.id AND r.workspace_id=person.workspace_id
  WHERE clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
   AND r.role_code IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead')
   AND (person.is_owner OR r.role_code=p_role)
   AND (r.role_code NOT IN ('fde','fde_lead') OR security.fde_user_is_active(
    person.id,r.role_code,CASE WHEN person.is_owner THEN NULL::uuid ELSE p_team END))
 );
$$;

-- FDE-only projections must use the owner's current FDE appointment too. An
-- old FDE snapshot does not turn a now sales-only owner into an active FDE;
-- conversely, an owner who moved into FDE is not hidden by an old sales label.
CREATE OR REPLACE FUNCTION security.fde_task_eligible(
 p_task uuid,p_user uuid,p_role text,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.task_recipient_eligible(p_task,p_user,p_role,p_team) AND CASE
  WHEN EXISTS(SELECT 1 FROM workflow.task_assignee a
   WHERE a.task_id=p_task AND a.workspace_id=common.current_workspace_id()
    AND a.assignee_user_ref_id=p_user AND a.responsibility='owner')
   THEN security.fde_user_is_active(p_user)
  ELSE p_role IN ('fde','fde_lead')
 END;
$$;

COMMENT ON FUNCTION security.task_recipient_eligible(uuid,uuid,text,uuid) IS
 '当前负责人按账号的当前有效身份检查接收资格；未认领岗位候选人保留原岗位要求。功能权限与数据范围仍由 authorization_task 独立检查。';
COMMENT ON FUNCTION security.fde_task_eligible(uuid,uuid,text,uuid) IS
 '当前负责人须具有当前有效 FDE 任职；未认领岗位候选人按原 FDE 岗位及团队检查。角色快照只保留分配历史。';

-- Replacing the existing signatures retains their runtime ACLs. No task,
-- owner, appointment, status, version or notification is changed by migration.
INSERT INTO ops.schema_migration(version,description)
VALUES('V333','Current owner task qualification survives valid identity changes without changing assignments');
COMMIT;
