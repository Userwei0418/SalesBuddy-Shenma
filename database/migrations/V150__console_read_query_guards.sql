BEGIN;

-- These guards depend on the authenticated statement, never on a row. Keep
-- tenant predicates outside the initplan and preserve every permission/worker
-- boundary; do not cache authorization across statements or transactions.
ALTER POLICY permission_select ON ops.audit_log USING (
 workspace_id=(SELECT common.current_workspace_id()) AND
 (SELECT security.authorization_has('audit.read') OR security.authorization_has('audit.export')));
ALTER POLICY permission_select ON ops.system_event USING (
 workspace_id=(SELECT common.current_workspace_id()) AND
 (SELECT security.authorization_has('audit.events_read') OR security.authorization_has('audit.events_export')));
ALTER POLICY permission_select ON config.feishu_connection USING (
 workspace_id=(SELECT common.current_workspace_id()) AND (SELECT CASE
 WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false
 ELSE security.authorization_has('feishu.read') OR security.authorization_has('feishu.configure')
   OR security.authorization_has('feishu.control') OR security.authorization_has('feishu.recover') END));

DO $migration$
DECLARE relation text; predicate text;
BEGIN
 predicate := $guard$workspace_id=(SELECT common.current_workspace_id()) AND (SELECT CASE
 WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false
 ELSE security.authorization_has('feishu.read') OR security.authorization_has('feishu.recover')
   OR security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control') END)$guard$;
 FOREACH relation IN ARRAY ARRAY['feishu_event','feishu_delivery','feishu_record_map','feishu_config_audit'] LOOP
  EXECUTE format('ALTER POLICY company_management ON ops.%I USING (%s)',relation,predicate);
  IF relation<>'feishu_config_audit' THEN
   EXECUTE format('ALTER POLICY company_management ON ops.%I WITH CHECK (%s)',relation,predicate);
  END IF;
 END LOOP;
END $migration$;

-- Business activity has a SECURITY DEFINER projection. Its invariant final
-- guard must also be an initplan; all tenant/time predicates remain in place.
DO $migration$
DECLARE definition text; old_guard text;
BEGIN
 SELECT pg_get_functiondef('security.business_activity_rows(timestamptz,timestamptz)'::regprocedure)
 INTO definition;
 old_guard := '(security.authorization_has(''audit.business_read'') OR security.authorization_has(''audit.business_export''))';
 IF strpos(definition,old_guard)=0 THEN
  RAISE EXCEPTION 'Unexpected business activity permission guard';
 END IF;
 EXECUTE replace(definition,old_guard,
  '(SELECT security.authorization_has(''audit.business_read'') OR security.authorization_has(''audit.business_export''))');
END $migration$;

-- Snapshot lookup casts object_id to text for heterogeneous event IDs. Match
-- that lookup rather than repeatedly scanning a tenant's entire audit history.
CREATE INDEX audit_activity_snapshot ON ops.audit_log
 (workspace_id,object_type,(object_id::text),actor_user_ref_id,id);

INSERT INTO ops.schema_migration(version,description)
 VALUES('V150','Bound console permission checks and business activity snapshot lookup');
COMMIT;
