-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V183__scoped_target_history.sql.
-- Original SHA-256: b0285d5950b4d0712a041dda1499cf7d771cfca22f47b0ff61f74dedd514f692. See shenma_whole_121_manifest.json.
BEGIN;

-- Target history is an object-scoped business projection. It must not require
-- technical audit access or inherit the scope of an unrelated audit grant.
CREATE FUNCTION security.target_history_rows(p_target uuid,p_limit integer,p_offset integer)
RETURNS TABLE(id bigint,occurred_at timestamptz,actor_name text,action_code text,
 before_snapshot jsonb,after_snapshot jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target crm.sales_target;
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 OR p_offset IS NULL OR p_offset<0 THEN
  RAISE EXCEPTION 'Invalid target history pagination' USING ERRCODE='22023';
 END IF;
 SELECT t.* INTO target FROM crm.sales_target t
  WHERE t.id=p_target AND t.workspace_id=common.current_workspace_id()
   AND security.target_scope_read(t.scope_type,t.user_ref_id,t.team_id,t.department_code);
 IF target.id IS NULL OR NOT security.authorization_subject(
   'target.history',target.scope_type,target.user_ref_id,target.team_id) THEN
  RAISE insufficient_privilege;
 END IF;
 RETURN QUERY SELECT a.id,a.occurred_at,a.actor_name_snapshot,a.action_code,
   a.before_snapshot,a.after_snapshot
  FROM ops.audit_log a
  WHERE a.workspace_id=target.workspace_id AND a.object_type='sales_target' AND a.object_id=target.id
  ORDER BY a.occurred_at DESC,a.id DESC LIMIT p_limit OFFSET p_offset;
END $$;
COMMENT ON FUNCTION security.target_history_rows(uuid,integer,integer) IS
 'Only the current tenant target readable under both target.read and target.history; no general audit access.';
REVOKE ALL ON FUNCTION security.target_history_rows(uuid,integer,integer) FROM PUBLIC,salegent_feishu_worker;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_target_history;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record; is_api boolean;
BEGIN
 total:=security.reconcile_runtime_grants_before_target_history();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE') LOOP
  is_api:=has_function_privilege(r.rolname,'security.save_account_authorization(uuid,jsonb)','EXECUTE');
  EXECUTE format('REVOKE ALL ON FUNCTION security.target_history_rows(uuid,integer,integer),security.reconcile_runtime_grants_before_target_history(),security.reconcile_runtime_grants() FROM %I',r.rolname);
  IF is_api THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.target_history_rows(uuid,integer,integer) TO %I',r.rolname);
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_target_history()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V331','Object-scoped target history without technical audit access');
COMMIT;
