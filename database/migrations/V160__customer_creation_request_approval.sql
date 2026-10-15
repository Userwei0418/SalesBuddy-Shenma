BEGIN;

CREATE TABLE crm.customer_creation_request (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 applicant_user_ref_id uuid NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 normalized_name text NOT NULL CHECK(length(btrim(normalized_name))>0),
 company_reference text NOT NULL CHECK(length(btrim(company_reference))>0),
 requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 reviewed_at timestamptz,
 reviewer_user_ref_id uuid,
 decision_reason text NOT NULL DEFAULT '',
 created_customer_id uuid,
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 CHECK((status='pending')=(reviewed_at IS NULL)),
 CHECK((status='approved')=(created_customer_id IS NOT NULL)),
 FOREIGN KEY(applicant_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(reviewer_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(created_customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id)
);
CREATE UNIQUE INDEX customer_creation_one_pending
 ON crm.customer_creation_request(workspace_id,applicant_user_ref_id,normalized_name) WHERE status='pending';
CREATE INDEX customer_creation_queue
 ON crm.customer_creation_request(workspace_id,status,requested_at DESC,id DESC);

CREATE FUNCTION security.customer_name_is_available(p_name text) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT NOT EXISTS(SELECT 1 FROM crm.customer c
   WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
     AND c.normalized_name=lower(regexp_replace(btrim(p_name),'\s+','','g')))
$$;
REVOKE ALL ON FUNCTION security.customer_name_is_available(text) FROM PUBLIC,salegent_feishu_worker;

CREATE FUNCTION security.submit_customer_creation_request(p_payload jsonb) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request crm.customer_creation_request; request_name text; company_ref text;
BEGIN
 IF NOT security.authorization_has('customer.create_request') THEN RAISE insufficient_privilege; END IF;
 IF jsonb_typeof(p_payload) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION '申请内容无效' USING ERRCODE='22023'; END IF;
 request_name=btrim(p_payload->>'name'); company_ref=btrim(p_payload->>'company_reference');
 IF NULLIF(request_name,'') IS NULL OR NULLIF(company_ref,'') IS NULL THEN
  RAISE EXCEPTION '请填写客户名称和已核实的公司客户编号或审批单号' USING ERRCODE='22023'; END IF;
 IF NOT security.customer_name_is_available(request_name) THEN RAISE unique_violation USING MESSAGE='客户已存在，请使用客户认领或核对已有客户'; END IF;
 IF EXISTS(SELECT 1 FROM crm.customer c WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND lower(btrim(c.company_reference))=lower(company_ref)) THEN
  RAISE EXCEPTION '公司客户编号或审批单号已用于其他客户，请核对已有档案' USING ERRCODE='22023'; END IF;
 INSERT INTO crm.customer_creation_request(workspace_id,applicant_user_ref_id,payload,normalized_name,company_reference)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),p_payload,
   lower(regexp_replace(request_name,'\s+','','g')),company_ref) RETURNING * INTO request;
 RETURN to_jsonb(request);
END $$;
REVOKE ALL ON FUNCTION security.submit_customer_creation_request(jsonb) FROM PUBLIC,salegent_feishu_worker;

CREATE FUNCTION security.withdraw_customer_creation_request(p_request uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request crm.customer_creation_request;
BEGIN
 IF NOT security.authorization_has('customer.create_request') THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO request FROM crm.customer_creation_request
  WHERE id=p_request AND workspace_id=common.current_workspace_id()
    AND applicant_user_ref_id=common.current_user_ref_id() FOR UPDATE;
 IF NOT FOUND THEN RAISE no_data_found; END IF;
 IF request.status<>'pending' THEN RAISE EXCEPTION '申请已处理，请刷新后查看' USING ERRCODE='P0001'; END IF;
 UPDATE crm.customer_creation_request SET status='cancelled',reviewed_at=clock_timestamp(),
  decision_reason='申请人撤回',version_no=version_no+1 WHERE id=p_request RETURNING * INTO request;
 RETURN to_jsonb(request);
END $$;
REVOKE ALL ON FUNCTION security.withdraw_customer_creation_request(uuid) FROM PUBLIC,salegent_feishu_worker;

CREATE FUNCTION security.customer_creation_request_lock(p_request uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request crm.customer_creation_request;
BEGIN
 IF NOT security.authorization_has('customer.create_review') THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO request FROM crm.customer_creation_request
  WHERE id=p_request AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND THEN RAISE no_data_found; END IF;
 RETURN to_jsonb(request);
END $$;
REVOKE ALL ON FUNCTION security.customer_creation_request_lock(uuid) FROM PUBLIC,salegent_feishu_worker;

CREATE FUNCTION security.complete_customer_creation_request(p_request uuid,p_decision text,p_reason text,p_customer uuid DEFAULT NULL) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request crm.customer_creation_request;
BEGIN
 IF NOT security.authorization_has('customer.create_review') THEN RAISE insufficient_privilege; END IF;
 IF p_decision NOT IN ('approved','rejected') OR (p_decision='rejected' AND NULLIF(btrim(p_reason),'') IS NULL)
 THEN RAISE EXCEPTION '审批决定或原因无效' USING ERRCODE='22023'; END IF;
 SELECT * INTO request FROM crm.customer_creation_request
  WHERE id=p_request AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND THEN RAISE no_data_found; END IF;
 IF request.status<>'pending' THEN RAISE EXCEPTION '客户新建申请已处理，请刷新后再操作' USING ERRCODE='P0001'; END IF;
 IF (p_decision='approved' AND NOT EXISTS(SELECT 1 FROM crm.customer c
   WHERE c.id=p_customer AND c.workspace_id=request.workspace_id AND c.deleted_at IS NULL
    AND c.normalized_name=request.normalized_name
    AND lower(btrim(c.company_reference))=lower(btrim(request.company_reference))
    AND c.created_by_user_ref_id=common.current_user_ref_id() AND c.created_at>=transaction_timestamp()))
   OR (p_decision='rejected' AND p_customer IS NOT NULL) THEN
  RAISE EXCEPTION '审批结果必须关联本次建档的正确客户' USING ERRCODE='22023'; END IF;
 UPDATE crm.customer_creation_request SET status=p_decision,reviewed_at=clock_timestamp(),
   reviewer_user_ref_id=common.current_user_ref_id(),decision_reason=btrim(COALESCE(p_reason,'')),
   created_customer_id=p_customer,version_no=version_no+1
 WHERE id=p_request AND workspace_id=common.current_workspace_id() AND status='pending'
 RETURNING * INTO request;
 IF NOT FOUND THEN RAISE EXCEPTION '客户新建申请已处理，请刷新后再操作' USING ERRCODE='P0001'; END IF;
 RETURN to_jsonb(request);
END $$;
REVOKE ALL ON FUNCTION security.complete_customer_creation_request(uuid,text,text,uuid) FROM PUBLIC,salegent_feishu_worker;

CREATE FUNCTION security.protect_customer_creation_request() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE insufficient_privilege; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','reviewed_at','reviewer_user_ref_id','decision_reason','created_customer_id','version_no'])
    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','reviewed_at','reviewer_user_ref_id','decision_reason','created_customer_id','version_no'])
    OR OLD.status<>'pending' OR NEW.status='pending' OR NEW.version_no<>OLD.version_no+1 THEN
  RAISE EXCEPTION '申请原始内容和已处理结果不可修改' USING ERRCODE='22023'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION security.protect_customer_creation_request() FROM PUBLIC,salegent_feishu_worker;
CREATE TRIGGER protect_creation_request BEFORE UPDATE OR DELETE ON crm.customer_creation_request
 FOR EACH ROW EXECUTE FUNCTION security.protect_customer_creation_request();
CREATE TRIGGER business_audit AFTER INSERT OR UPDATE ON crm.customer_creation_request
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();

ALTER TABLE crm.customer_creation_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.customer_creation_request FORCE ROW LEVEL SECURITY;
CREATE POLICY creation_request_applicant_read ON crm.customer_creation_request FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND applicant_user_ref_id=common.current_user_ref_id()
 AND security.authorization_has('customer.create_request'));
CREATE POLICY creation_request_reviewer_read ON crm.customer_creation_request FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.authorization_has('customer.create_review'));
CREATE POLICY creation_request_applicant_insert ON crm.customer_creation_request FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND applicant_user_ref_id=common.current_user_ref_id()
 AND status='pending' AND security.authorization_has('customer.create_request'));
CREATE POLICY creation_request_reviewer_update ON crm.customer_creation_request FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.authorization_has('customer.create_review'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.authorization_has('customer.create_review'));
CREATE POLICY creation_request_applicant_withdraw ON crm.customer_creation_request FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND applicant_user_ref_id=common.current_user_ref_id()
 AND status='pending' AND security.authorization_has('customer.create_request'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND applicant_user_ref_id=common.current_user_ref_id()
 AND status='cancelled' AND security.authorization_has('customer.create_request'));
REVOKE ALL ON crm.customer_creation_request FROM PUBLIC,salegent_feishu_worker;

INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('customer.create_request','提交客户新建申请','客户管理',ARRAY['self'],false),
 ('customer.create_review','审批客户新建申请','客户管理',ARRAY['workspace'],false)
 ON CONFLICT(code) DO NOTHING;
-- Extend only stock templates; customized roles and account overrides remain untouched.
CREATE TEMP TABLE s2_stock_roles ON COMMIT DROP AS
 SELECT r.id,r.workspace_id,r.builtin_role_code FROM config.permission_role r
 JOIN config.permission_role_default d ON d.role_code=r.builtin_role_code
 WHERE r.status='active' AND r.builtin_role_code IN ('sales','supervisor','manager','operations','administrator')
 AND NOT EXISTS(SELECT 1 FROM config.authorization_audit a WHERE a.subject_type='role' AND a.subject_id=r.id)
 AND NOT EXISTS(
  (SELECT g.permission_code,g.scope_code,to_jsonb(g.team_ids) FROM config.permission_role_grant g WHERE g.role_id=r.id
   EXCEPT SELECT p->>'permission',p->>'scope',p->'team_ids' FROM jsonb_array_elements(d.permissions) p)
  UNION ALL
  (SELECT p->>'permission',p->>'scope',p->'team_ids' FROM jsonb_array_elements(d.permissions) p
   EXCEPT SELECT g.permission_code,g.scope_code,to_jsonb(g.team_ids) FROM config.permission_role_grant g WHERE g.role_id=r.id));
UPDATE config.permission_role_default
 SET permissions=permissions||jsonb_build_array(jsonb_build_object('permission','customer.create_request','scope','self','team_ids','[]'::jsonb))
 WHERE role_code IN ('sales','supervisor','manager')
   AND NOT (permissions @> '[{"permission":"customer.create_request"}]'::jsonb);
UPDATE config.permission_role_default
 SET permissions=permissions||jsonb_build_array(jsonb_build_object('permission','customer.create_review','scope','workspace','team_ids','[]'::jsonb))
 WHERE role_code IN ('operations','administrator')
   AND NOT (permissions @> '[{"permission":"customer.create_review"}]'::jsonb);
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
 SELECT r.workspace_id,r.id,'customer.create_request','self' FROM s2_stock_roles r
 WHERE r.builtin_role_code IN ('sales','supervisor','manager')
 AND NOT EXISTS(SELECT 1 FROM config.permission_role_grant g WHERE g.role_id=r.id AND g.permission_code='customer.create_request');
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
 SELECT r.workspace_id,r.id,'customer.create_review','workspace' FROM s2_stock_roles r
 WHERE r.builtin_role_code IN ('operations','administrator')
 AND NOT EXISTS(SELECT 1 FROM config.permission_role_grant g WHERE g.role_id=r.id AND g.permission_code='customer.create_review');
UPDATE config.permission_role SET version_no=version_no+1,updated_at=clock_timestamp()
 WHERE id IN (SELECT id FROM s2_stock_roles)
 AND EXISTS(SELECT 1 FROM config.permission_role_grant g WHERE g.role_id=config.permission_role.id
   AND g.permission_code IN ('customer.create_request','customer.create_review'));

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v159;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v159();
 FOR r IN SELECT DISTINCT role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles role ON role.oid=a.grantee
  WHERE n.nspname='platform' AND c.relname='user_ref'
   AND a.privilege_type='SELECT' AND a.grantee<>c.relowner
 LOOP
  -- Application writes go only through checked functions; snapshots are immutable.
  EXECUTE format('REVOKE INSERT,UPDATE,DELETE,TRUNCATE ON crm.customer_creation_request FROM %I',r.rolname);
  EXECUTE format('GRANT SELECT ON crm.customer_creation_request TO %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.customer_name_is_available(text),security.submit_customer_creation_request(jsonb),security.withdraw_customer_creation_request(uuid),security.customer_creation_request_lock(uuid),security.complete_customer_creation_request(uuid,text,text,uuid) TO %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.protect_customer_creation_request() FROM %I',r.rolname);
 END LOOP;
 REVOKE ALL ON crm.customer_creation_request FROM salegent_feishu_worker;
 REVOKE ALL ON FUNCTION security.customer_name_is_available(text),security.submit_customer_creation_request(jsonb),security.withdraw_customer_creation_request(uuid),security.customer_creation_request_lock(uuid),security.complete_customer_creation_request(uuid,text,text,uuid),security.protect_customer_creation_request() FROM salegent_feishu_worker;
 RETURN reconciled;
END $$;
-- Preserve the maintenance function's owner and exact ACL across upgrades.
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='security.reconcile_runtime_grants_v159()'::regprocedure;
 FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END); END LOOP;
 EXECUTE format('ALTER FUNCTION security.reconcile_runtime_grants() OWNER TO %I',function_owner);
 FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants_v159()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO %s%s',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
  CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END); END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();

COMMENT ON TABLE crm.customer_creation_request IS '销售新建客户申请；申请阶段不写 crm.customer，批准时在同一事务落库';
INSERT INTO ops.schema_migration(version,description) VALUES('V160','Customer creation request and operations approval MVP');
COMMIT;
