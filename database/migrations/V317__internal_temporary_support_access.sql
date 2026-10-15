-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V169__temporary_support_access.sql.
-- Original SHA-256: 1513bb93513ebe551aa0dbaddb6814da00fbb09bc2d0365d30c15fe1524648a3. See shenma_whole_121_manifest.json.
BEGIN;
-- No rows are seeded: temporary business access is closed until a company approves.
CREATE TABLE control.support_scope (
 code text PRIMARY KEY, label text NOT NULL, permission_code text NOT NULL REFERENCES config.permission_catalog(code)
);
INSERT INTO control.support_scope VALUES
 ('organization','组织目录','organization.read'),('customers','客户基本资料','customer.read'),
 ('opportunities','商机基本资料','opportunity.read'),('visits','已确认跟进记录','visit.read'),('tasks','待办记录','task.read');
CREATE TABLE control.support_grant (
 id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 operator_id uuid NOT NULL REFERENCES control.operator(id),
 approved_by uuid NOT NULL REFERENCES platform.user_ref(id),
 scopes text[] NOT NULL CHECK(cardinality(scopes) BETWEEN 1 AND 5 AND scopes <@ ARRAY['organization','customers','opportunities','visits','tasks']::text[]),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 500),
 approved_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
 revoked_at timestamptz, revoked_by uuid REFERENCES platform.user_ref(id), revoke_reason text,
 CHECK(expires_at>approved_at AND expires_at<=approved_at+interval '24 hours')
);
CREATE INDEX support_grant_company ON control.support_grant(workspace_id,approved_at DESC);
CREATE INDEX support_grant_operator ON control.support_grant(operator_id,expires_at) WHERE revoked_at IS NULL;
CREATE TABLE control.support_event (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 workspace_id uuid REFERENCES platform.workspace(id), grant_id uuid REFERENCES control.support_grant(id),
 operator_id uuid NOT NULL REFERENCES control.operator(id), actor_user_id uuid REFERENCES platform.user_ref(id),
 action text NOT NULL CHECK(action IN ('approved','revoked','read','denied')),
 scope_code text, reason text NOT NULL, row_count integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX support_event_company ON control.support_event(workspace_id,id DESC);
REVOKE ALL ON control.support_scope,control.support_grant,control.support_event FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON SEQUENCE control.support_event_id_seq FROM PUBLIC,salegent_feishu_worker;

INSERT INTO config.permission_catalog VALUES('support.manage','管理临时支持访问','公司管理',ARRAY['workspace'],true);
INSERT INTO control.permission_module VALUES('support.manage','core');
-- Only templates exactly matching the prior defaults inherit the new management
-- action. This grants approval authority, never a platform business-access grant.
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
 SELECT r.workspace_id,r.id,'support.manage','workspace' FROM config.permission_role r
 JOIN config.permission_role_default d ON d.role_code=r.builtin_role_code
 WHERE r.builtin_role_code='administrator' AND r.status='active' AND NOT EXISTS(
  (SELECT g.permission_code,g.scope_code,to_jsonb(g.team_ids) FROM config.permission_role_grant g WHERE g.role_id=r.id
   EXCEPT SELECT p->>'permission',p->>'scope',p->'team_ids' FROM jsonb_array_elements(d.permissions) p)
  UNION ALL
  (SELECT p->>'permission',p->>'scope',p->'team_ids' FROM jsonb_array_elements(d.permissions) p
   EXCEPT SELECT g.permission_code,g.scope_code,to_jsonb(g.team_ids) FROM config.permission_role_grant g WHERE g.role_id=r.id));
UPDATE config.permission_role r SET version_no=version_no+1,updated_at=clock_timestamp()
 WHERE EXISTS(SELECT 1 FROM config.permission_role_grant g WHERE g.role_id=r.id AND g.permission_code='support.manage');
UPDATE config.permission_role_default SET permissions=permissions||'[{"permission":"support.manage","scope":"workspace","team_ids":[]}]'::jsonb
 WHERE role_code='administrator';

CREATE FUNCTION control.support_has(p_workspace uuid,p_user uuid,p_permission text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH g AS MATERIALIZED(SELECT * FROM security.authorization_grants_for(p_workspace,p_user))
 SELECT EXISTS(SELECT 1 FROM g WHERE permission_code=p_permission AND effect='allow' AND scope_code='workspace')
 AND NOT EXISTS(SELECT 1 FROM g WHERE permission_code=p_permission AND effect='deny');
$$;
CREATE FUNCTION control.support_company_actor() RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); u uuid:=common.current_user_ref_id();
BEGIN
 IF NOT security.company_administrator_identity(ws,u) OR NOT control.support_has(ws,u,'access.console')
 OR NOT control.support_has(ws,u,'support.manage') THEN RAISE insufficient_privilege; END IF;
 RETURN u;
END $$;
CREATE FUNCTION control.support_valid(p_grant control.support_grant,p_scope text DEFAULT NULL) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p_grant.revoked_at IS NULL AND p_grant.expires_at>clock_timestamp()
 AND EXISTS(SELECT 1 FROM control.operator WHERE id=p_grant.operator_id AND enabled AND mfa_secret IS NOT NULL)
 AND security.company_administrator_identity(p_grant.workspace_id,p_grant.approved_by)
 AND control.support_has(p_grant.workspace_id,p_grant.approved_by,'access.console')
 AND control.support_has(p_grant.workspace_id,p_grant.approved_by,'support.manage')
 AND (p_scope IS NULL OR (p_scope=ANY(p_grant.scopes) AND EXISTS(SELECT 1 FROM control.support_scope s WHERE s.code=p_scope
   AND control.support_has(p_grant.workspace_id,p_grant.approved_by,s.permission_code))));
$$;
CREATE FUNCTION security.support_directory(p_account text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid:=control.support_company_actor(); ws uuid:=common.current_workspace_id(); person jsonb;
BEGIN
 SELECT jsonb_build_object('id',id,'account_code',account_code,'display_name',display_name) INTO person
 FROM control.operator WHERE account_code=upper(btrim(p_account)) AND enabled AND mfa_secret IS NOT NULL;
 RETURN jsonb_build_object('operator',person,'max_hours',24,'scopes',
  (SELECT jsonb_agg(jsonb_build_object('code',code,'label',label,'available',control.support_has(ws,u,permission_code)) ORDER BY code)
   FROM control.support_scope));
END $$;
CREATE FUNCTION security.support_grants() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM control.support_company_actor();
 RETURN (SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY (q.revoked_at IS NULL AND q.expires_at>clock_timestamp()) DESC,q.approved_at DESC,q.id),'[]') FROM
 (SELECT g.*,o.account_code,o.display_name,u.display_name AS approver_name,control.support_valid(g) AS active
 FROM control.support_grant g JOIN control.operator o ON o.id=g.operator_id
 JOIN platform.user_ref u ON u.id=g.approved_by AND u.workspace_id=g.workspace_id
 WHERE g.workspace_id=common.current_workspace_id() ORDER BY (g.revoked_at IS NULL AND g.expires_at>clock_timestamp()) DESC,g.approved_at DESC,g.id LIMIT 100) q);
END $$;
CREATE FUNCTION security.approve_support(p_id uuid,p_operator uuid,p_scopes text[],p_expiry timestamptz,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid:=control.support_company_actor(); ws uuid:=common.current_workspace_id(); prior control.support_grant%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('support-grant:'||p_id::text,0));
 SELECT * INTO prior FROM control.support_grant WHERE id=p_id;
 IF FOUND THEN
  IF prior.workspace_id=ws AND prior.approved_by=u AND prior.operator_id=p_operator AND prior.scopes=p_scopes
    AND prior.expires_at=p_expiry AND prior.reason=btrim(p_reason) THEN
   RETURN jsonb_build_object('id',prior.id,'replayed',true,'active',control.support_valid(prior));
  END IF;
  RAISE EXCEPTION '授权请求已变化，请刷新' USING ERRCODE='40001';
 END IF;
 -- Lock company and approver for the duration of approval; native role/permission
 -- changes are checked again on each support read, not trusted from this snapshot.
 PERFORM 1 FROM platform.workspace WHERE id=ws AND status='active' AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 PERFORM 1 FROM control.operator WHERE id=p_operator AND enabled AND mfa_secret IS NOT NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '支持账号未启用或未完成 MFA' USING ERRCODE='22023'; END IF;
 IF p_id IS NULL OR p_expiry IS NULL OR p_expiry<=clock_timestamp() OR p_expiry>clock_timestamp()+interval '24 hours'
 OR length(btrim(COALESCE(p_reason,''))) NOT BETWEEN 1 AND 500 OR COALESCE(cardinality(p_scopes),0) NOT BETWEEN 1 AND 5
 OR cardinality(p_scopes)<>(SELECT count(DISTINCT x) FROM unnest(p_scopes) x)
 OR EXISTS(SELECT 1 FROM unnest(p_scopes) x WHERE NOT EXISTS(SELECT 1 FROM control.support_scope s WHERE s.code=x
  AND control.support_has(ws,u,s.permission_code))) THEN
  RAISE EXCEPTION '请选择有权读取的功能，并设置未来二十四小时内的到期时间和用途' USING ERRCODE='22023'; END IF;
 -- Bound simultaneous approvals and always show every unexpired approval first.
 -- Company/operator locks serialize these limits across API processes.
 IF (SELECT count(*) FROM control.support_grant WHERE workspace_id=ws AND revoked_at IS NULL AND expires_at>clock_timestamp())>=20
 OR (SELECT count(*) FROM control.support_grant WHERE operator_id=p_operator AND revoked_at IS NULL AND expires_at>clock_timestamp())>=20 THEN
  RAISE EXCEPTION '同时有效的支持授权最多二十条，请先撤销不再需要的授权' USING ERRCODE='22023'; END IF;
 INSERT INTO control.support_grant(id,workspace_id,operator_id,approved_by,scopes,expires_at,reason)
 VALUES(p_id,ws,p_operator,u,p_scopes,p_expiry,btrim(p_reason));
 INSERT INTO control.support_event(workspace_id,grant_id,operator_id,actor_user_id,action,reason)
 VALUES(ws,p_id,p_operator,u,'approved',btrim(p_reason));
 RETURN jsonb_build_object('id',p_id,'replayed',false,'active',true);
END $$;
CREATE FUNCTION security.revoke_support(p_id uuid,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE u uuid:=control.support_company_actor(); g control.support_grant%ROWTYPE;
BEGIN
 IF length(btrim(COALESCE(p_reason,''))) NOT BETWEEN 1 AND 500 THEN RAISE invalid_parameter_value; END IF;
 SELECT * INTO g FROM control.support_grant WHERE id=p_id AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND THEN RAISE no_data_found; END IF;
 IF g.revoked_at IS NULL THEN
  UPDATE control.support_grant SET revoked_at=clock_timestamp(),revoked_by=u,revoke_reason=btrim(p_reason) WHERE id=p_id;
  INSERT INTO control.support_event(workspace_id,grant_id,operator_id,actor_user_id,action,reason)
  VALUES(g.workspace_id,g.id,g.operator_id,u,'revoked',btrim(p_reason));
 END IF;
 RETURN jsonb_build_object('id',p_id,'revoked',true);
END $$;
CREATE FUNCTION security.support_audit_page(p_before bigint DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM control.support_company_actor();
 RETURN (SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY q.id DESC),'[]') FROM (
 SELECT e.*,o.account_code,o.display_name FROM control.support_event e JOIN control.operator o ON o.id=e.operator_id
 WHERE e.workspace_id=common.current_workspace_id() AND (p_before IS NULL OR e.id<p_before) ORDER BY e.id DESC LIMIT 100) q);
END $$;
CREATE FUNCTION control.support_assignments(p_session text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_actor(p_session);
BEGIN
 RETURN (SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY (q.revoked_at IS NULL AND q.expires_at>clock_timestamp()) DESC,q.approved_at DESC,q.id),'[]') FROM (
 SELECT g.id,g.workspace_id,w.name AS company_name,g.reason,g.approved_at,g.expires_at,g.revoked_at,
 u.display_name AS approver_name,control.support_valid(g) AS active,
 (SELECT jsonb_agg(jsonb_build_object('code',s.code,'label',s.label,'available',control.support_valid(g,s.code)) ORDER BY s.code)
  FROM control.support_scope s WHERE s.code=ANY(g.scopes)) AS scopes
 FROM control.support_grant g JOIN platform.workspace w ON w.id=g.workspace_id
 JOIN platform.user_ref u ON u.id=g.approved_by AND u.workspace_id=g.workspace_id
 WHERE g.operator_id=actor ORDER BY (g.revoked_at IS NULL AND g.expires_at>clock_timestamp()) DESC,g.approved_at DESC,g.id LIMIT 100) q);
END $$;
CREATE FUNCTION control.support_read(p_session text,p_grant uuid,p_scope text,p_after uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_actor(p_session); g control.support_grant%ROWTYPE; result jsonb; permitted boolean;
BEGIN
 -- Explicit bounded projections; this does not expose a general SQL runner or
 -- issue a tenant session. The runtime role still has no business table grants.
 SELECT * INTO g FROM control.support_grant WHERE id=p_grant AND operator_id=actor FOR SHARE;
 IF NOT FOUND THEN
  INSERT INTO control.support_event(operator_id,action,reason) VALUES(actor,'denied','SUPPORT_ACCESS_DENIED');
  RETURN jsonb_build_object('error','SUPPORT_ACCESS_DENIED');
 END IF;
 -- Lifecycle lock order matches tenant state updates, which revoke grants.
 -- Grant locks alone are enough for explicit revocation; active status is checked
 -- without another row lock here to avoid a workspace/grant lock inversion.
 permitted:=control.support_valid(g,p_scope);
 IF permitted THEN
  CASE p_scope
  WHEN 'organization' THEN
   SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY q.id),'[]') INTO result FROM
   (SELECT id,account_code,display_name,status FROM platform.user_ref WHERE workspace_id=g.workspace_id
    AND deleted_at IS NULL AND (p_after IS NULL OR id>p_after) ORDER BY id LIMIT 50) q;
  WHEN 'customers' THEN
   SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY q.id),'[]') INTO result FROM
   (SELECT id,name,customer_code,industry_code,lifecycle_status,level_code,owner_user_ref_id,owner_team_id,updated_at
    FROM crm.customer WHERE workspace_id=g.workspace_id AND deleted_at IS NULL AND (p_after IS NULL OR id>p_after) ORDER BY id LIMIT 50) q;
  WHEN 'opportunities' THEN
   SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY q.id),'[]') INTO result FROM
   (SELECT id,customer_id,name,stage_code,status,amount,currency,owner_user_ref_id,owner_team_id,expected_close_date,updated_at
    FROM crm.opportunity WHERE workspace_id=g.workspace_id AND deleted_at IS NULL AND (p_after IS NULL OR id>p_after) ORDER BY id LIMIT 50) q;
  WHEN 'visits' THEN
   SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY q.id),'[]') INTO result FROM
   (SELECT id,customer_id,opportunity_id,status,recorder_user_ref_id,interaction_at,left(follow_up_record,10000) AS follow_up_record,
     left(next_action,10000) AS next_action,updated_at FROM activity.visit
    WHERE workspace_id=g.workspace_id AND deleted_at IS NULL AND status IN ('confirmed','archived')
    AND (p_after IS NULL OR id>p_after) ORDER BY id LIMIT 50) q;
  WHEN 'tasks' THEN
   SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY q.id),'[]') INTO result FROM
   (SELECT id,customer_id,opportunity_id,title,left(description,10000) AS description,status,due_at,updated_at
    FROM workflow.task WHERE workspace_id=g.workspace_id AND deleted_at IS NULL AND (p_after IS NULL OR id>p_after) ORDER BY id LIMIT 50) q;
  ELSE permitted:=false;
  END CASE;
 END IF;
 -- Recheck the clock and approval authority after reading; never return a page
 -- whose lease expired while the bounded query was executing.
 permitted:=permitted AND control.support_valid(g,p_scope);
 INSERT INTO control.support_event(workspace_id,grant_id,operator_id,action,scope_code,reason,row_count)
 VALUES(g.workspace_id,g.id,actor,CASE WHEN permitted THEN 'read' ELSE 'denied' END,
  CASE WHEN p_scope IN ('organization','customers','opportunities','visits','tasks') THEN p_scope END,
  CASE WHEN permitted THEN 'APPROVED_READ' ELSE 'SUPPORT_ACCESS_DENIED' END,CASE WHEN permitted THEN jsonb_array_length(result) ELSE 0 END);
 IF NOT permitted THEN RETURN jsonb_build_object('error','SUPPORT_ACCESS_DENIED'); END IF;
 RETURN jsonb_build_object('items',result,'grant_id',g.id,'scope',p_scope,'expires_at',g.expires_at,
  'next_cursor',CASE WHEN jsonb_array_length(result)=50 THEN result->49->>'id' ELSE NULL END);
END $$;
CREATE FUNCTION control.revoke_support_on_pause() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE g control.support_grant%ROWTYPE;
BEGIN
 IF NEW.status IS DISTINCT FROM OLD.status AND NEW.status<>'active' THEN
  FOR g IN UPDATE control.support_grant SET revoked_at=clock_timestamp(),revoke_reason='TENANT_INACTIVE'
    WHERE workspace_id=NEW.id AND revoked_at IS NULL RETURNING * LOOP
   INSERT INTO control.support_event(workspace_id,grant_id,operator_id,action,reason)
    VALUES(g.workspace_id,g.id,g.operator_id,'revoked','TENANT_INACTIVE');
  END LOOP;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER workspace_revoke_support AFTER UPDATE OF status ON platform.workspace FOR EACH ROW EXECUTE FUNCTION control.revoke_support_on_pause();
REVOKE ALL ON FUNCTION control.support_has(uuid,uuid,text),control.support_company_actor(),control.support_valid(control.support_grant,text),
 security.support_directory(text),security.support_grants(),security.approve_support(uuid,uuid,text[],timestamptz,text),
 security.revoke_support(uuid,text),security.support_audit_page(bigint),control.support_assignments(text),control.support_read(text,uuid,text,uuid),
 control.revoke_support_on_pause() FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_support;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_before_support();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.save_account_authorization(uuid,jsonb)','EXECUTE') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_before_support() FROM %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.support_directory(text),security.support_grants(),security.approve_support(uuid,uuid,text[],timestamptz,text),security.revoke_support(uuid,text),security.support_audit_page(bigint) TO %I',r.rolname);
 END LOOP;
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
 AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.support_assignments(text),control.support_read(text,uuid,text,uuid) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_support() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V317','Company-approved bounded temporary platform support access');
COMMIT;
