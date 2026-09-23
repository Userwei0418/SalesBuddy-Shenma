-- Actor-only guards are invariant within a statement. Evaluate them once,
-- keeping row workspace checks and the existing restrictive SELECT policy.
-- The initplan is rerun for each statement; nothing is cached across sessions.
ALTER POLICY audit_management_read ON ops.audit_log
 USING(workspace_id=(SELECT common.current_workspace_id()) AND (SELECT security.management_actor()));

ALTER POLICY system_event_read ON ops.system_event
 USING((SELECT security.management_actor()) AND
   (workspace_id=(SELECT common.current_workspace_id())
    OR (workspace_id IS NULL AND (SELECT common.current_role_code())='administrator')));
