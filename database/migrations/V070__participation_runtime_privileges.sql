BEGIN;
SET LOCAL check_function_bodies=on;

-- Replacing a current assignee or normalized participant list needs DELETE on
-- these relationship tables. Existing source-table privileges remain the role
-- contract; do not grant ownership, schema-wide writes, credentials or BYPASSRLS.
-- INVOKER plus private EXECUTE means only a trusted deployment/grant authority
-- can reconcile grants. The deployer repeats this after migrations so roles
-- created after initial installation are covered as well.
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE permission record; reconciled integer:=0;
BEGIN
 FOR permission IN
  SELECT DISTINCT r.rolname,a.privilege_type
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
  WHERE n.nspname='activity' AND c.relname='visit' AND a.grantee<>c.relowner
    AND a.privilege_type IN ('SELECT','INSERT','UPDATE')
 LOOP
  EXECUTE format('GRANT %s ON activity.visit_participant TO %I',permission.privilege_type,permission.rolname);
  IF permission.privilege_type='SELECT' THEN
   EXECUTE format('GRANT SELECT ON activity.v_visit_collaborators TO %I',permission.rolname);
  END IF;
  reconciled:=reconciled+1;
 END LOOP;
 FOR permission IN
  SELECT r.rolname,CASE WHEN n.nspname='activity' THEN 'activity.visit_participant'
    ELSE 'workflow.task_assignee' END AS target_table
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
  WHERE ((n.nspname='activity' AND c.relname='visit')
    OR (n.nspname='workflow' AND c.relname='task_assignee'))
    AND a.grantee<>c.relowner AND a.privilege_type IN ('INSERT','UPDATE')
  GROUP BY r.rolname,n.nspname
  HAVING count(DISTINCT a.privilege_type)=2
 LOOP
  EXECUTE format('GRANT DELETE ON %s TO %I',permission.target_table,permission.rolname);
  reconciled:=reconciled+1;
 END LOOP;
 RETURN reconciled;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM PUBLIC;

-- Adding the table-level permission must not let an ordinary workspace member
-- remove somebody else's task owner. Only the same native coordination scope
-- accepted by the business service is authorized, for every application role.
CREATE POLICY task_assignee_delete_coordinator ON workflow.task_assignee
 AS RESTRICTIVE FOR DELETE USING(
 workspace_id=common.current_workspace_id() AND security.fde_can_coordinate_task(task_id));

SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description)
 VALUES('V070','规范化参与名单与任务转交的运行权限及后建角色部署核对');
COMMIT;
