-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V161__tenant_account_quota.sql.
-- Original SHA-256: e47c135290905293998389d55a69a40658b9be2ddb2851dd7ebb2a7560015ecb. See shenma_whole_121_manifest.json.
BEGIN;
-- Count identities, not memberships or permission bindings. Block writes while
-- backfilling so accounts created during migration cannot escape the counter.
LOCK TABLE platform.user_ref IN SHARE ROW EXCLUSIVE MODE;
CREATE TABLE control.tenant_account_quota (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 account_limit bigint CHECK(account_limit BETWEEN 0 AND 9007199254740991),
 active_accounts bigint NOT NULL DEFAULT 0 CHECK(active_accounts >= 0),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO control.tenant_account_quota(workspace_id,active_accounts)
 SELECT w.id,count(u.id) FROM platform.workspace w LEFT JOIN platform.user_ref u
 ON u.workspace_id=w.id AND u.status='active' AND u.deleted_at IS NULL GROUP BY w.id;

CREATE FUNCTION control.initialize_tenant_account_quota() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO control.tenant_account_quota(workspace_id) VALUES(NEW.id);
 RETURN NEW;
END $$;
CREATE TRIGGER initialize_tenant_account_quota AFTER INSERT ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION control.initialize_tenant_account_quota();

CREATE FUNCTION control.track_tenant_accounts() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE old_active integer:=0; new_active integer:=0; change integer; tenant_id uuid;
BEGIN
 IF TG_OP='TRUNCATE' THEN
  -- A privileged bulk reset must use DELETE, otherwise row accounting is lost.
  RAISE EXCEPTION 'ACCOUNT_TRUNCATE_REQUIRES_EXPLICIT_RECONCILIATION' USING ERRCODE='P7422';
 END IF;
 IF TG_OP<>'INSERT' THEN
  old_active:=(OLD.status='active' AND OLD.deleted_at IS NULL)::integer;
  tenant_id:=OLD.workspace_id;
 END IF;
 IF TG_OP<>'DELETE' THEN
  new_active:=(NEW.status='active' AND NEW.deleted_at IS NULL)::integer;
  tenant_id:=NEW.workspace_id;
 END IF;
 IF TG_OP='UPDATE' AND NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
  RAISE EXCEPTION 'ACCOUNT_TENANT_IMMUTABLE' USING ERRCODE='P7422';
 END IF;
 change:=new_active-old_active;
 IF change<>0 THEN
  -- One conditional UPDATE observes the latest row after a concurrent writer
  -- commits. Counting user_ref after taking a lock could use a stale snapshot.
  UPDATE control.tenant_account_quota SET active_accounts=active_accounts+change,
   updated_at=clock_timestamp() WHERE workspace_id=tenant_id
   AND active_accounts+change>=0
   AND (change<0 OR account_limit IS NULL OR active_accounts+change<=account_limit);
  IF NOT FOUND THEN
   IF change>0 AND EXISTS(SELECT 1 FROM control.tenant_account_quota WHERE workspace_id=tenant_id) THEN
    RAISE EXCEPTION 'TENANT_ACCOUNT_QUOTA_EXCEEDED' USING ERRCODE='P7429';
   END IF;
   RAISE EXCEPTION 'TENANT_ACCOUNT_QUOTA_INCONSISTENT' USING ERRCODE='P7500';
  END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER track_tenant_accounts AFTER INSERT OR DELETE OR UPDATE OF workspace_id,status,deleted_at
 ON platform.user_ref FOR EACH ROW EXECUTE FUNCTION control.track_tenant_accounts();
CREATE TRIGGER reject_unaccounted_account_truncate BEFORE TRUNCATE ON platform.user_ref
 FOR EACH STATEMENT EXECUTE FUNCTION control.track_tenant_accounts();

CREATE FUNCTION control.account_quota(p_session text,p_workspace uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 PERFORM control.require_actor(p_session);
 SELECT jsonb_build_object('workspace_id',w.id,'version_no',w.version_no,
  'account_limit',q.account_limit,'active_accounts',q.active_accounts,
  'remaining',CASE WHEN q.account_limit IS NULL THEN NULL ELSE greatest(0,q.account_limit-q.active_accounts) END,
  'over_limit',q.account_limit IS NOT NULL AND q.active_accounts>q.account_limit)
 INTO result FROM platform.workspace w JOIN control.tenant_account_quota q ON q.workspace_id=w.id
 WHERE w.id=p_workspace AND w.deleted_at IS NULL;
 IF result IS NULL THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 RETURN result;
END $$;

CREATE FUNCTION control.save_account_quota(p_session text,p_workspace uuid,p_version integer,p_limit bigint,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_sensitive(p_session); version integer; before_value jsonb; after_value jsonb;
BEGIN
 SELECT version_no INTO version FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 IF version IS DISTINCT FROM p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='P7409'; END IF;
 IF (p_limit IS NOT NULL AND p_limit NOT BETWEEN 0 AND 9007199254740991)
  OR COALESCE(length(btrim(p_reason)),0) NOT BETWEEN 1 AND 500 THEN
  RAISE EXCEPTION 'INVALID_ACCOUNT_QUOTA' USING ERRCODE='P7422'; END IF;
 -- Serialize the audit snapshot with account mutations as well as other saves.
 PERFORM 1 FROM control.tenant_account_quota WHERE workspace_id=p_workspace FOR UPDATE;
 before_value:=control.account_quota(p_session,p_workspace);
 UPDATE control.tenant_account_quota SET account_limit=p_limit,updated_at=clock_timestamp() WHERE workspace_id=p_workspace;
 UPDATE platform.workspace SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_workspace;
 after_value:=control.account_quota(p_session,p_workspace);
 INSERT INTO control.audit(operator_id,workspace_id,action,reason,before_state,after_state)
 VALUES(actor,p_workspace,'tenant.account_quota',btrim(p_reason),before_value,after_value);
 RETURN after_value;
END $$;

REVOKE ALL ON control.tenant_account_quota FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON FUNCTION control.initialize_tenant_account_quota(),control.track_tenant_accounts(),
 control.account_quota(text,uuid),control.save_account_quota(text,uuid,integer,bigint,text)
 FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_internal_v160;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_internal_v160();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_internal_v160() FROM %I',r.rolname);
 END LOOP;
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
 AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.account_quota(text,uuid),control.save_account_quota(text,uuid,integer,bigint,text) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_internal_v160() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V309','Platform account quotas and atomic active identity accounting');
COMMIT;
