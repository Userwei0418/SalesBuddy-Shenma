-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V211__customer_flow_history_and_review.sql.
-- Original SHA-256: 7e59755791ce316d454a10a2c4f1559d59e871ee5df062d2c5d296da159208a2. See shenma_whole_121_manifest.json.
BEGIN;
-- Standalone forward change on frozen V204 + V209 + V210. V205-V208 are not dependencies.
ALTER TABLE crm.customer_creation_request ADD COLUMN reviewed_payload jsonb
 CHECK(reviewed_payload IS NULL OR jsonb_typeof(reviewed_payload)='object');
COMMENT ON COLUMN crm.customer_creation_request.reviewed_payload IS
 'Approved, validated customer snapshot; original submitted payload remains unchanged.';

-- Names, not external numbers, identify customer candidates. Preserve old values.
ALTER TABLE crm.customer_creation_request
 DROP CONSTRAINT customer_creation_request_company_reference_check,
 ALTER COLUMN company_reference DROP NOT NULL;
COMMENT ON COLUMN crm.customer_creation_request.company_reference IS 'Optional source reference; never populated with the login company code.';
COMMENT ON COLUMN crm.customer.company_reference IS 'Optional external customer or approval reference; customer matching uses normalized names.';

-- Preserve the existing import and ownership rules, removing only the external
-- reference requirement from ordinary authorized creation.
CREATE OR REPLACE FUNCTION crm.guard_customer_administration()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN RETURN NEW; END IF;
 IF TG_OP='INSERT' THEN
  IF COALESCE(NEW.import_meta->>'import_type','')='crm_history'
   AND current_setting('app.feishu_historical_import',true)='on'
   AND security.management_actor() AND NEW.workspace_id=common.current_workspace_id()
   AND NEW.created_by_user_ref_id=common.current_user_ref_id() AND NEW.owner_user_ref_id IS NULL
   AND NEW.company_reference IS NULL AND NEW.company_verified_at IS NULL AND NEW.company_verified_by IS NULL
   AND EXISTS(SELECT 1 FROM ops.crm_import_record r JOIN ops.crm_import_batch b ON b.id=r.batch_id AND b.workspace_id=r.workspace_id
    WHERE r.id=(NEW.import_meta->>'import_record_id')::uuid AND r.workspace_id=NEW.workspace_id
    AND r.object_kind='customer' AND r.target_id=NEW.id AND r.status='approved'
    AND NEW.import_meta->>'import_batch_id'=r.batch_id::text
    AND NEW.import_meta->>'source_key'=concat_ws(':',b.source_system,b.source_base_id,r.source_table_id,r.source_record_id)
    AND NEW.import_meta->'source_fields'=r.raw_fields AND NEW.name=r.normalized->>'name'
    AND b.status IN ('approved','applying','applied')) THEN RETURN NEW; END IF;
  IF NOT security.authorization_allows('customer.create',NEW.workspace_id,NEW.created_by_user_ref_id,ARRAY[NEW.owner_team_id],false) OR NEW.owner_user_ref_id IS NOT NULL
   OR NEW.company_verified_by IS DISTINCT FROM common.current_user_ref_id() OR NEW.company_verified_at IS NULL THEN
   RAISE EXCEPTION '仅获授权账号可核实资料并创建未认领客户' USING ERRCODE='42501';
  END IF;
 ELSIF NEW.owner_user_ref_id IS DISTINCT FROM OLD.owner_user_ref_id
  AND current_setting('app.ownership_transition',true) IS DISTINCT FROM 'on' THEN
  RAISE EXCEPTION '客户归属必须通过认领审批或释放变更' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $function$;


-- Existing names may contain historical duplicates. Do not rewrite them or
-- introduce an index that requires deleting history. Serialize new name writes
-- and reject new live duplicates, including concurrent approvals by two people.
CREATE FUNCTION crm.guard_customer_new_name() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE normalized text;
BEGIN
 normalized:=lower(regexp_replace(btrim(NEW.name),'\s+','','g'));
 NEW.normalized_name:=normalized;
 IF NEW.deleted_at IS NOT NULL THEN RETURN NEW; END IF;
 IF TG_OP='UPDATE' AND NEW.workspace_id=OLD.workspace_id
   AND NEW.normalized_name=OLD.normalized_name AND OLD.deleted_at IS NULL THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('customer-name:'||NEW.workspace_id::text||':'||normalized,0));
 IF EXISTS(SELECT 1 FROM crm.customer c WHERE c.workspace_id=NEW.workspace_id
   AND c.deleted_at IS NULL AND c.id<>NEW.id
   AND lower(regexp_replace(btrim(c.name),'\s+','','g'))=normalized) THEN
  RAISE unique_violation USING MESSAGE='客户已存在，请核对后再建档',CONSTRAINT='customer_live_name_conflict';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER customer_new_name_guard BEFORE INSERT OR UPDATE OF name,normalized_name,workspace_id,deleted_at
 ON crm.customer FOR EACH ROW EXECUTE FUNCTION crm.guard_customer_new_name();

-- Supply the guarded helpers absent from the frozen V204 variant. The old
-- runtime used direct table DML; the prior V204 checksum is not changed.
CREATE OR REPLACE FUNCTION security.customer_name_is_available(p_name text) RETURNS boolean
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.authorization_has('customer.create_request') AND NOT security.authorization_has('customer.create') THEN
  RAISE insufficient_privilege;
 END IF;
 RETURN NOT EXISTS(SELECT 1 FROM crm.customer c
   WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
     AND lower(regexp_replace(btrim(c.name),'\s+','','g'))=lower(regexp_replace(btrim(p_name),'\s+','','g')));
END $$;
REVOKE ALL ON FUNCTION security.customer_name_is_available(text) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION security.submit_customer_creation_request(p_payload jsonb) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request crm.customer_creation_request; request_name text; company_ref text;
BEGIN
 IF NOT security.authorization_has('customer.create_request') THEN RAISE insufficient_privilege; END IF;
 IF jsonb_typeof(p_payload) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION '申请内容无效' USING ERRCODE='22023'; END IF;
 request_name=btrim(p_payload->>'name'); company_ref=NULLIF(btrim(p_payload->>'company_reference'),'');
 IF NULLIF(request_name,'') IS NULL THEN
  RAISE EXCEPTION '请填写客户名称' USING ERRCODE='22023';
 END IF;
 IF NOT security.customer_name_is_available(request_name) THEN RAISE unique_violation USING MESSAGE='客户已存在，请使用客户认领或核对已有客户'; END IF;
 INSERT INTO crm.customer_creation_request(workspace_id,applicant_user_ref_id,payload,normalized_name,company_reference)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),p_payload,
   lower(regexp_replace(request_name,'\s+','','g')),company_ref) RETURNING * INTO request;
 RETURN to_jsonb(request);
END $$;
REVOKE ALL ON FUNCTION security.submit_customer_creation_request(jsonb) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION security.customer_creation_request_lock(p_request uuid) RETURNS jsonb
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


-- Approved customers must
-- really exist in this company, have the reviewed name and have been created
-- by this authorized operator. The API's customer insert and this completion
-- share one transaction; a failed duplicate or correction rolls back both.
CREATE OR REPLACE FUNCTION security.complete_customer_creation_request(
 p_request uuid,p_decision text,p_reason text,p_customer uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE request crm.customer_creation_request; customer crm.customer;
BEGIN
 IF NOT security.authorization_has('customer.create_review') THEN RAISE insufficient_privilege; END IF;
 IF p_decision IS NULL OR p_decision NOT IN ('approved','rejected')
   OR (p_decision='rejected' AND NULLIF(btrim(p_reason),'') IS NULL)
   OR (p_decision='rejected' AND p_customer IS NOT NULL) THEN
  RAISE EXCEPTION '审批决定或原因无效' USING ERRCODE='22023';
 END IF;
 SELECT * INTO request FROM crm.customer_creation_request
 WHERE id=p_request AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND THEN RAISE no_data_found; END IF;
 IF request.status<>'pending' THEN
  RAISE EXCEPTION '客户新建申请已处理，请刷新后再操作' USING ERRCODE='P0001';
 END IF;
 IF p_decision='approved' THEN
  SELECT * INTO customer FROM crm.customer WHERE id=p_customer
    AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
  IF NOT FOUND OR customer.created_by_user_ref_id IS DISTINCT FROM common.current_user_ref_id()
    OR customer.company_verified_by IS DISTINCT FROM common.current_user_ref_id()
    OR customer.company_verified_at IS NULL
    OR customer.normalized_name IS DISTINCT FROM lower(regexp_replace(
      btrim(COALESCE(request.reviewed_payload,request.payload)->>'name'),'\s+','','g')) THEN
   RAISE EXCEPTION '批准必须关联本次核实创建的正式客户' USING ERRCODE='22023';
  END IF;
  IF NOT security.authorization_allows('customer.create',customer.workspace_id,
    common.current_user_ref_id(),ARRAY[customer.owner_team_id],false) THEN RAISE insufficient_privilege; END IF;
 END IF;
 UPDATE crm.customer_creation_request SET status=p_decision,reviewed_at=clock_timestamp(),
   reviewer_user_ref_id=common.current_user_ref_id(),decision_reason=btrim(COALESCE(p_reason,'')),
   created_customer_id=p_customer,version_no=version_no+1,
   reviewed_payload=CASE WHEN p_decision='approved' THEN COALESCE(reviewed_payload,payload) ELSE NULL END
 WHERE id=p_request RETURNING * INTO request;
 RETURN to_jsonb(request);
END $$;

CREATE FUNCTION security.customer_request_history(
 p_scope text DEFAULT 'self',p_team uuid DEFAULT NULL,p_kind text DEFAULT NULL,
 p_status text DEFAULT NULL,p_query text DEFAULT NULL,p_limit integer DEFAULT 50,p_offset integer DEFAULT 0,
 p_member uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF NOT security.authorization_has('customer.read') THEN RAISE insufficient_privilege; END IF;
 IF p_scope IS NULL OR p_scope NOT IN ('self','team')
   OR (p_scope='self' AND (p_team IS NOT NULL OR p_member IS NOT NULL))
   OR (p_kind IS NOT NULL AND p_kind NOT IN ('creation','claim'))
   OR (p_status IS NOT NULL AND p_status NOT IN ('pending','approved','rejected','cancelled'))
   OR p_limit IS NULL OR p_limit<1 OR p_limit>100 OR p_offset IS NULL OR p_offset<0
   OR length(COALESCE(p_query,''))>100 THEN
  RAISE EXCEPTION '客户历史筛选无效' USING ERRCODE='22023';
 END IF;
 IF p_team IS NOT NULL AND NOT security.authorization_subject('customer.read','team',NULL,p_team) THEN
  RAISE insufficient_privilege;
 END IF;
 IF p_member IS NOT NULL AND (NOT security.authorization_subject('customer.read','person',p_member,NULL)
   OR (p_team IS NOT NULL AND NOT EXISTS(SELECT 1 FROM platform.team_membership tm
       WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=p_member AND tm.team_id=p_team
         AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to))) THEN
  RAISE insufficient_privilege;
 END IF;
 WITH subjects AS MATERIALIZED (
   SELECT u.id FROM platform.user_ref u WHERE u.workspace_id=common.current_workspace_id()
    AND security.authorization_subject('customer.read','person',u.id,NULL)
    AND (p_scope<>'self' OR u.id=common.current_user_ref_id())
    AND (p_member IS NULL OR u.id=p_member)
    AND (p_team IS NULL OR EXISTS(SELECT 1 FROM platform.team_membership tm
      WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND tm.team_id=p_team
       AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to))
 ), requests AS (
   SELECT r.id,'creation'::text AS kind,r.created_customer_id AS customer_id,
     COALESCE(r.reviewed_payload->>'name',r.payload->>'name') AS customer_name,r.applicant_user_ref_id,
     r.status,r.requested_at,r.reviewed_at,r.reviewer_user_ref_id,
     r.decision_reason
   FROM crm.customer_creation_request r JOIN subjects s ON s.id=r.applicant_user_ref_id
   WHERE r.workspace_id=common.current_workspace_id() AND (p_kind IS NULL OR p_kind='creation')
   UNION ALL
   SELECT r.id,'claim',r.customer_id,COALESCE(c.name,'已删除客户'),r.applicant_user_ref_id,
     r.status,r.requested_at,r.reviewed_at,r.reviewer_user_ref_id,
     COALESCE(r.decision_reason,'')
   FROM crm.customer_claim_request r JOIN subjects s ON s.id=r.applicant_user_ref_id
   LEFT JOIN crm.customer c ON c.id=r.customer_id AND c.workspace_id=r.workspace_id AND c.deleted_at IS NULL
   WHERE r.workspace_id=common.current_workspace_id() AND (p_kind IS NULL OR p_kind='claim')
 ), records AS MATERIALIZED (
   SELECT r.*,applicant.display_name AS applicant_name,reviewer.display_name AS reviewer_name
   FROM requests r
   JOIN platform.user_ref applicant ON applicant.id=r.applicant_user_ref_id
     AND applicant.workspace_id=common.current_workspace_id()
   LEFT JOIN platform.user_ref reviewer ON reviewer.id=r.reviewer_user_ref_id
     AND reviewer.workspace_id=common.current_workspace_id()
   WHERE NULLIF(btrim(p_query),'') IS NULL
       OR r.customer_name ILIKE '%'||btrim(p_query)||'%'
       OR applicant.display_name ILIKE '%'||btrim(p_query)||'%'
 ), filtered AS MATERIALIZED (
   SELECT * FROM records WHERE p_status IS NULL OR status=p_status
 ), page AS (
   SELECT * FROM filtered ORDER BY requested_at DESC,id DESC,kind LIMIT p_limit OFFSET p_offset
 ), facts AS (
   SELECT count(*)::integer AS total,
     count(*) FILTER(WHERE status='pending')::integer AS pending,
     count(*) FILTER(WHERE status='approved')::integer AS approved,
     count(*) FILTER(WHERE status='rejected')::integer AS rejected,
     count(*) FILTER(WHERE status='cancelled')::integer AS cancelled FROM records
 )
 SELECT jsonb_build_object(
   'items',COALESCE((SELECT jsonb_agg(to_jsonb(p)||jsonb_build_object(
       'can_read_customer',COALESCE(security.authorization_customer('customer.read',p.customer_id),false))
       ORDER BY p.requested_at DESC,p.id DESC,p.kind) FROM page p),'[]'::jsonb),
   'total',(SELECT count(*)::integer FROM filtered),
   'summary',(SELECT to_jsonb(facts) FROM facts),
   'scope',p_scope,'team_id',p_team,'member_id',p_member,'offset',p_offset,'limit',p_limit,
   'has_more',(SELECT count(*) FROM filtered)>p_offset::bigint+p_limit,
   'next_offset',CASE WHEN (SELECT count(*) FROM filtered)>p_offset::bigint+p_limit
     THEN p_offset::bigint+p_limit ELSE NULL END
 ) INTO result;
 RETURN result;
END $$;
COMMENT ON FUNCTION security.customer_request_history(text,uuid,text,text,text,integer,integer,uuid) IS
 'Current authorized applicant summaries only; no raw payload/contact disclosure or customer detail grant.';

-- Preserve the existing runtime ACL exactly. Calling the historical broad
-- reconciler would regrant functions intentionally removed by the installation
-- policy. New installations receive these five API functions from that policy.
DO $$ DECLARE signature text; grantee record; runtime record; BEGIN
 FOREACH signature IN ARRAY ARRAY[
  'crm.guard_customer_new_name()',
  'security.customer_name_is_available(text)','security.submit_customer_creation_request(jsonb)',
  'security.customer_creation_request_lock(uuid)','security.complete_customer_creation_request(uuid,text,text,uuid)',
  'security.customer_request_history(text,uuid,text,text,text,integer,integer,uuid)'] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',signature);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   JOIN pg_roles r ON r.oid=a.grantee
   WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',signature,grantee.rolname);
  END LOOP;
 END LOOP;
 FOR runtime IN SELECT r.rolname FROM pg_roles r
   WHERE NOT r.rolsuper AND NOT r.rolbypassrls AND left(r.rolname,3)<>'pg_'
     AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
     AND has_schema_privilege(r.oid,'security','USAGE')
     AND has_function_privilege(r.oid,'security.authorization_subject(text,text,uuid,uuid)','EXECUTE')
     AND has_table_privilege(r.oid,'crm.customer_creation_request','INSERT')
     AND has_function_privilege(r.oid,'security.create_auth_session(uuid,uuid,uuid,text,timestamp with time zone,jsonb)','EXECUTE')
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.customer_request_history(text,uuid,text,text,text,integer,integer,uuid),security.customer_name_is_available(text),security.submit_customer_creation_request(jsonb),security.customer_creation_request_lock(uuid),security.complete_customer_creation_request(uuid,text,text,uuid) TO %I',runtime.rolname);
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V359','Customer request scope, reviewed snapshots and concurrent name protection');
COMMIT;
