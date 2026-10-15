-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V196__phone_first_login.sql.
-- Original SHA-256: d54c66214872d8495246646a71f162a5de4b7094ee879fd0a64bb5f93febd7e7. See shenma_whole_121_manifest.json.
BEGIN;

-- Check under a write-excluding table lock. Conflicts require an administrator
-- to resolve ownership; an upgrade must never merge accounts or clear aliases.
LOCK TABLE platform.user_ref IN SHARE ROW EXCLUSIVE MODE;
DO $$ DECLARE phone_conflicts bigint; reserved_conflicts bigint; BEGIN
 SELECT count(*) INTO phone_conflicts FROM (
  SELECT phone_number FROM platform.user_ref WHERE deleted_at IS NULL AND phone_number IS NOT NULL
  GROUP BY phone_number HAVING count(*)>1
 ) conflicts;
 SELECT count(*) INTO reserved_conflicts FROM platform.user_ref u
 JOIN platform.workspace w ON w.id=u.workspace_id
 WHERE u.deleted_at IS NULL AND w.external_workspace_id<>'demo-sales-workspace'
  AND upper(u.account_code)=ANY(ARRAY['XS001','XS002','XS003','ZJ001','ZJ002','CPXS001','CPXS002',
   'CPXS003','CPXS004','FDEL001','FDE001','FDE002','FDE003','ZJL001']);
 IF phone_conflicts>0 OR reserved_conflicts>0 THEN
  RAISE EXCEPTION 'PHONE_LOGIN_PREFLIGHT_FAILED' USING ERRCODE='23514',
   DETAIL=format('duplicate_phone_groups=%s; reserved_account_rows=%s',phone_conflicts,reserved_conflicts),
   HINT='Resolve existing bindings explicitly before retrying; no accounts were changed.';
 END IF;
END $$;

-- Inactive accounts and suspended/closed companies retain their phone binding.
-- Explicit unbinding (NULL) or account soft deletion releases the identifier.
CREATE UNIQUE INDEX user_ref_global_phone_unique ON platform.user_ref(phone_number)
 WHERE deleted_at IS NULL AND phone_number IS NOT NULL;
COMMENT ON INDEX platform.user_ref_global_phone_unique
 IS 'One undeleted account globally owns a phone binding, including inactive accounts';
COMMENT ON COLUMN platform.user_ref.phone_number
 IS '管理员绑定的全局唯一可选11位登录手机号；停用仍占用，解除后可重新绑定；不代表已验证手机所有权';

CREATE FUNCTION security.guard_demo_account_reservation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.deleted_at IS NULL
  AND upper(NEW.account_code)=ANY(ARRAY['XS001','XS002','XS003','ZJ001','ZJ002','CPXS001','CPXS002',
   'CPXS003','CPXS004','FDEL001','FDE001','FDE002','FDE003','ZJL001'])
  AND NOT EXISTS(SELECT 1 FROM platform.workspace w WHERE w.id=NEW.workspace_id
   AND w.external_workspace_id='demo-sales-workspace') THEN
  RAISE EXCEPTION 'DEMO_ACCOUNT_RESERVED' USING ERRCODE='23514',CONSTRAINT='user_ref_demo_account_reserved';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER user_ref_demo_account_reserved
 BEFORE INSERT OR UPDATE OF account_code,workspace_id,deleted_at ON platform.user_ref
 FOR EACH ROW EXECUTE FUNCTION security.guard_demo_account_reservation();

-- Omitted company is allowed only when exactly one eligible identity matches.
-- Typed phone never falls back to an 11-digit account name, and legacy auto
-- refuses account/phone alias ambiguity. Keep legacy overloads and ACLs intact.
CREATE FUNCTION security.password_login_identifier(p_workspace text,p_identifier text,p_login_type text)
 RETURNS TABLE(workspace text,account_code text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH candidates AS (
  SELECT w.external_workspace_id AS workspace,u.account_code
  FROM platform.workspace w JOIN platform.user_ref u ON u.workspace_id=w.id
  JOIN platform.password_credential p ON p.user_ref_id=u.id AND p.workspace_id=u.workspace_id
  WHERE w.status='active' AND w.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL
   AND NULLIF(btrim(p_identifier),'') IS NOT NULL
   AND (NULLIF(btrim(p_workspace),'') IS NULL OR lower(w.external_workspace_id)=lower(btrim(p_workspace)))
   AND CASE p_login_type
    WHEN 'phone' THEN btrim(p_identifier) ~ '^1[3-9][0-9]{9}$' AND u.phone_number=btrim(p_identifier)
    WHEN 'account' THEN u.account_code=upper(btrim(p_identifier))
    WHEN 'auto' THEN u.account_code=upper(btrim(p_identifier)) OR u.phone_number=btrim(p_identifier)
    ELSE false END
 ) SELECT c.workspace,c.account_code FROM candidates c WHERE (SELECT count(*) FROM candidates)=1;
$$;

CREATE FUNCTION security.password_candidate(p_workspace text,p_identifier text,p_role text,p_login_type text)
 RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT to_jsonb(a)||jsonb_build_object('password_hash',p.password_hash,
  'must_change_password',p.must_change_password AND COALESCE(policy.require_initial_change,true))
 FROM security.password_login_identifier(p_workspace,p_identifier,p_login_type) i
 CROSS JOIN LATERAL security.resolve_account_actor(i.workspace,i.account_code,p_role) a
 JOIN platform.password_credential p ON p.user_ref_id=a.user_id::uuid AND p.workspace_id=a.workspace_id::uuid
 LEFT JOIN security.password_policy policy ON policy.workspace_id=p.workspace_id;
$$;

CREATE FUNCTION security.lock_password_login_identifier(p_identifier text,p_login_type text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE found_id uuid;
BEGIN
 SELECT u.id INTO found_id FROM platform.user_ref u
 WHERE u.id=common.current_user_ref_id() AND u.workspace_id=common.current_workspace_id()
  AND u.status='active' AND u.deleted_at IS NULL
  AND CASE p_login_type
   WHEN 'phone' THEN btrim(p_identifier) ~ '^1[3-9][0-9]{9}$' AND u.phone_number=btrim(p_identifier)
   WHEN 'account' THEN u.account_code=upper(btrim(p_identifier))
   WHEN 'auto' THEN u.account_code=upper(btrim(p_identifier)) OR u.phone_number=btrim(p_identifier)
   ELSE false END
 FOR SHARE;
 RETURN found_id IS NOT NULL;
END $$;

-- Late runtime creation may explicitly reconcile grants. On first application
-- only these three new signatures are granted; do not replay historic grants.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_phone_first_login;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; runtime record;
BEGIN
 total:=security.reconcile_runtime_grants_before_phone_first_login();
 FOR runtime IN SELECT r.rolname FROM pg_roles r
  WHERE NOT r.rolsuper AND NOT r.rolbypassrls AND left(r.rolname,3)<>'pg_'
   AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
   AND has_schema_privilege(r.oid,'security','USAGE')
   AND has_function_privilege(r.oid,'security.password_candidate(text,text,text)','EXECUTE')
   AND has_function_privilege(r.oid,'security.save_account_authorization(uuid,jsonb)','EXECUTE')
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.password_login_identifier(text,text,text),security.password_candidate(text,text,text,text),security.lock_password_login_identifier(text,text) TO %I',runtime.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;

-- Defeat both PUBLIC and explicit ALTER DEFAULT PRIVILEGES grants. No runtime
-- role may invoke the trigger or grant-reconciliation helpers directly.
DO $$ DECLARE signature text; grantee record; BEGIN
 FOREACH signature IN ARRAY ARRAY['security.guard_demo_account_reservation()',
  'security.password_login_identifier(text,text,text)','security.password_candidate(text,text,text,text)',
  'security.lock_password_login_identifier(text,text)','security.reconcile_runtime_grants()',
  'security.reconcile_runtime_grants_before_phone_first_login()'] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',signature);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',signature,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;
DO $$ DECLARE runtime record; BEGIN
 FOR runtime IN SELECT r.rolname FROM pg_roles r
  WHERE NOT r.rolsuper AND NOT r.rolbypassrls AND left(r.rolname,3)<>'pg_'
   AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
   AND has_schema_privilege(r.oid,'security','USAGE')
   AND has_function_privilege(r.oid,'security.password_candidate(text,text,text)','EXECUTE')
   AND has_function_privilege(r.oid,'security.save_account_authorization(uuid,jsonb)','EXECUTE')
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.password_login_identifier(text,text,text),security.password_candidate(text,text,text,text),security.lock_password_login_identifier(text,text) TO %I',runtime.rolname);
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V344','Global administrator phone bindings, exact demo account reservation and typed password login');
COMMIT;
