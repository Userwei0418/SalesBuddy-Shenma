BEGIN;
-- Evaluate a different account only inside guarded company workflows. This
-- helper is private: it must never become a general account-enumeration API.
CREATE FUNCTION security.authorization_user_allows(p_permission text,p_workspace uuid,p_user uuid,
 p_owner uuid,p_teams uuid[],p_assigned boolean DEFAULT false) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS (SELECT * FROM security.authorization_grants_for(p_workspace,p_user) WHERE permission_code=p_permission)
 SELECT p_workspace=common.current_workspace_id() AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny')
 AND EXISTS(SELECT 1 FROM grants WHERE effect='allow' AND (scope_code='workspace'
 OR (scope_code='self' AND p_owner=p_user) OR (scope_code='assigned' AND p_assigned)
 OR (scope_code='teams' AND team_ids&&p_teams)));
$$;
REVOKE ALL ON FUNCTION security.authorization_user_allows(text,uuid,uuid,uuid,uuid[],boolean) FROM PUBLIC,salegent_feishu_worker;
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
  IF NOT security.authorization_allows('customer.create',NEW.workspace_id,NEW.created_by_user_ref_id,ARRAY[NEW.owner_team_id],false) OR NEW.owner_user_ref_id IS NOT NULL OR NULLIF(btrim(NEW.company_reference),'') IS NULL
   OR NEW.company_verified_by IS DISTINCT FROM common.current_user_ref_id() OR NEW.company_verified_at IS NULL THEN
   RAISE EXCEPTION '仅获授权账号可在核实公司建档后创建未认领客户' USING ERRCODE='42501';
  END IF;
 ELSIF NEW.owner_user_ref_id IS DISTINCT FROM OLD.owner_user_ref_id
  AND current_setting('app.ownership_transition',true) IS DISTINCT FROM 'on' THEN
  RAISE EXCEPTION '客户归属必须通过认领审批或释放变更' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $function$;

CREATE OR REPLACE FUNCTION security.can_claim_customer(p_customer_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.customer c JOIN crm.customer_ownership o ON o.customer_id=c.id
  AND o.workspace_id=c.workspace_id WHERE c.id=p_customer_id AND c.workspace_id=common.current_workspace_id()
  AND c.deleted_at IS NULL AND o.state='unclaimed'
  AND security.authorization_allows('customer.claim',c.workspace_id,common.current_user_ref_id(),ARRAY[c.owner_team_id],false));
$$;
CREATE OR REPLACE FUNCTION security.claim_customer(p_customer_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE o crm.customer_ownership; req crm.customer_claim_request; recipient record; customer_name text;
BEGIN
 IF NOT security.authorization_has('customer.claim') THEN RAISE insufficient_privilege; END IF;
 SELECT own.* INTO o FROM crm.customer_ownership own JOIN crm.customer c ON c.id=own.customer_id
 WHERE own.customer_id=p_customer_id AND own.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL FOR UPDATE OF own;
 IF NOT FOUND THEN RAISE EXCEPTION '客户不存在' USING ERRCODE='P0002'; END IF;
 IF o.state<>'unclaimed' THEN RAISE EXCEPTION '客户已认领或待运营核对，不能再次申请' USING ERRCODE='P0001'; END IF;
 IF NOT security.can_claim_customer(p_customer_id) THEN RAISE EXCEPTION '客户不可认领或不在授权范围' USING ERRCODE='42501'; END IF;

 SELECT * INTO req FROM crm.customer_claim_request WHERE customer_id=p_customer_id
 AND applicant_user_ref_id=common.current_user_ref_id() AND status='pending';
 IF NOT FOUND THEN
  INSERT INTO crm.customer_claim_request(workspace_id,customer_id,applicant_user_ref_id)
  VALUES(o.workspace_id,p_customer_id,common.current_user_ref_id()) RETURNING * INTO req;
  SELECT name INTO customer_name FROM crm.customer WHERE id=p_customer_id;
  FOR recipient IN SELECT DISTINCT u.id AS user_ref_id FROM platform.user_ref u JOIN crm.customer c ON c.id=p_customer_id
   WHERE u.workspace_id=o.workspace_id AND u.status='active' AND u.deleted_at IS NULL
   AND security.authorization_user_allows('customer.claim_review',o.workspace_id,u.id,c.owner_user_ref_id,ARRAY[c.owner_team_id],false)
  LOOP PERFORM security.claim_notification(recipient.user_ref_id,p_customer_id,req.id,'客户认领待审批',customer_name); END LOOP;
 END IF;
 RETURN jsonb_build_object('request_id',req.id::text,'customer_id',p_customer_id::text,'status','pending','claimed',false,'added',false);
END $function$;
CREATE OR REPLACE FUNCTION security.review_customer_claim(p_request uuid, p_decision text, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE req crm.customer_claim_request; o crm.customer_ownership; customer_key uuid; competing record;
BEGIN
 IF NOT security.authorization_has('customer.claim_review') AND NOT security.authorization_has('customer.resolve_owner') THEN RAISE EXCEPTION '需要运营审批权限' USING ERRCODE='42501'; END IF;
 IF p_decision NOT IN ('approved','rejected') OR (p_decision='rejected' AND NULLIF(btrim(p_reason),'') IS NULL)
 THEN RAISE EXCEPTION '驳回必须填写原因' USING ERRCODE='22023'; END IF;
 SELECT customer_id INTO customer_key FROM crm.customer_claim_request WHERE id=p_request AND workspace_id=common.current_workspace_id();
 IF NOT FOUND THEN RAISE EXCEPTION '申请不存在' USING ERRCODE='P0002'; END IF;
 SELECT * INTO o FROM crm.customer_ownership WHERE customer_id=customer_key FOR UPDATE;
 SELECT * INTO req FROM crm.customer_claim_request WHERE id=p_request FOR UPDATE;
 IF NOT security.authorization_customer('customer.claim_review',customer_key) AND NOT
  (req.source='legacy_review' AND security.authorization_customer('customer.resolve_owner',customer_key)) THEN RAISE insufficient_privilege; END IF;
 IF req.status<>'pending' THEN RAISE EXCEPTION '申请已处理，请刷新' USING ERRCODE='P0001'; END IF;
 IF p_decision='approved' THEN
  IF o.state<>'unclaimed' THEN RAISE EXCEPTION '客户已认领或待核对' USING ERRCODE='P0001'; END IF;
  IF NOT EXISTS(SELECT 1 FROM crm.customer c WHERE c.id=customer_key AND
    security.authorization_user_allows('customer.claim',req.workspace_id,req.applicant_user_ref_id,
       req.applicant_user_ref_id,ARRAY[c.owner_team_id],false)) THEN
   RAISE EXCEPTION '申请人已停用或认领权限已失效' USING ERRCODE='P0001'; END IF;
  UPDATE crm.customer_ownership SET state='claimed',owner_user_ref_id=req.applicant_user_ref_id,
   version_no=version_no+1,updated_at=clock_timestamp() WHERE customer_id=customer_key;
  PERFORM set_config('app.ownership_transition','on',true);
  UPDATE crm.customer SET owner_user_ref_id=req.applicant_user_ref_id,version_no=version_no+1 WHERE id=customer_key;
  PERFORM set_config('app.ownership_transition','off',true);
  INSERT INTO crm.customer_ownership_event(workspace_id,customer_id,event_type,owner_user_ref_id,actor_user_ref_id,claim_request_id,reason)
  VALUES(req.workspace_id,customer_key,'approved',req.applicant_user_ref_id,common.current_user_ref_id(),req.id,COALESCE(NULLIF(btrim(p_reason),''),'认领审批通过'));
  FOR competing IN UPDATE crm.customer_claim_request SET status='rejected',reviewed_at=clock_timestamp(),
   reviewer_user_ref_id=common.current_user_ref_id(),decision_reason='客户已有认领人，本次申请关闭'
   WHERE customer_id=customer_key AND status='pending' AND id<>req.id RETURNING *
  LOOP PERFORM security.claim_notification(competing.applicant_user_ref_id,customer_key,competing.id,'客户认领未通过','客户已有认领人，本次申请关闭'); END LOOP;
 END IF;
 UPDATE crm.customer_claim_request SET status=p_decision,reviewed_at=clock_timestamp(),
 reviewer_user_ref_id=common.current_user_ref_id(),decision_reason=NULLIF(btrim(p_reason),'') WHERE id=req.id;
 PERFORM security.claim_notification(req.applicant_user_ref_id,customer_key,req.id,
 CASE WHEN p_decision='approved' THEN '客户认领已通过' ELSE '客户认领未通过' END,COALESCE(NULLIF(btrim(p_reason),''),'运营已确认认领申请'));
 RETURN jsonb_build_object('request_id',req.id::text,'customer_id',customer_key::text,'status',p_decision);
END $function$;
CREATE OR REPLACE FUNCTION security.release_customer(p_customer uuid, p_version integer, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE o crm.customer_ownership; event_id uuid;
BEGIN
 IF NOT security.authorization_customer('customer.release',p_customer) THEN RAISE EXCEPTION '需要运营权限' USING ERRCODE='42501'; END IF;
 IF NULLIF(btrim(p_reason),'') IS NULL THEN RAISE EXCEPTION '请填写释放原因' USING ERRCODE='22023'; END IF;
 SELECT * INTO o FROM crm.customer_ownership WHERE customer_id=p_customer AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '客户不存在' USING ERRCODE='P0002'; END IF;
 IF o.version_no<>p_version OR o.state='unclaimed' THEN RAISE EXCEPTION '归属已变化，请刷新' USING ERRCODE='P0001'; END IF;
 UPDATE crm.customer_ownership SET state='unclaimed',owner_user_ref_id=NULL,version_no=version_no+1,updated_at=clock_timestamp() WHERE customer_id=p_customer;
 PERFORM set_config('app.ownership_transition','on',true);
 UPDATE crm.customer SET owner_user_ref_id=NULL,version_no=version_no+1 WHERE id=p_customer;
 PERFORM set_config('app.ownership_transition','off',true);
 INSERT INTO crm.customer_ownership_event(workspace_id,customer_id,event_type,previous_owner_user_ref_id,actor_user_ref_id,reason)
 VALUES(o.workspace_id,p_customer,'released',o.owner_user_ref_id,common.current_user_ref_id(),p_reason) RETURNING id INTO event_id;
 IF o.owner_user_ref_id IS NOT NULL THEN PERFORM security.claim_notification(o.owner_user_ref_id,p_customer,event_id,'客户已释放',p_reason); END IF;
 RETURN jsonb_build_object('customer_id',p_customer::text,'state','unclaimed','version_no',o.version_no+1);
END $function$;
CREATE OR REPLACE FUNCTION security.resolve_legacy_customer_owner(p_customer uuid, p_version integer, p_owner uuid, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE o crm.customer_ownership; request_id uuid; result jsonb;
BEGIN
 IF NOT security.authorization_customer('customer.resolve_owner',p_customer) THEN RAISE EXCEPTION '需要运营权限' USING ERRCODE='42501'; END IF;
 IF NULLIF(btrim(p_reason),'') IS NULL THEN RAISE EXCEPTION '请填写核对依据' USING ERRCODE='22023'; END IF;
 SELECT * INTO o FROM crm.customer_ownership WHERE customer_id=p_customer AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND OR o.state<>'legacy_review' OR o.version_no<>p_version THEN RAISE EXCEPTION '归属状态已变化，请刷新' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS(SELECT 1 FROM crm.customer_sales_member WHERE customer_id=p_customer AND user_ref_id=p_owner) THEN
 RAISE EXCEPTION '只能从历史关联销售中确认，需更换其他销售时请先释放' USING ERRCODE='22023'; END IF;
 UPDATE crm.customer_ownership SET state='unclaimed' WHERE customer_id=p_customer;
 INSERT INTO crm.customer_claim_request(workspace_id,customer_id,applicant_user_ref_id,source)
 VALUES(o.workspace_id,p_customer,p_owner,'legacy_review') RETURNING id INTO request_id;
 result=security.review_customer_claim(request_id,'approved',p_reason);
 INSERT INTO crm.customer_ownership_event(workspace_id,customer_id,event_type,owner_user_ref_id,actor_user_ref_id,claim_request_id,reason)
 VALUES(o.workspace_id,p_customer,'legacy_resolved',p_owner,common.current_user_ref_id(),request_id,p_reason);
 RETURN result;
END $function$;
CREATE OR REPLACE FUNCTION security.company_customer_directory_page(p_query text, p_limit integer, p_offset integer)
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH bounds AS (
  SELECT LEAST(GREATEST(p_limit,1),100) AS page_size,GREATEST(p_offset,0)::bigint AS page_start
 ), matched AS MATERIALIZED (
  SELECT c.id,c.name FROM crm.customer c
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND security.authorization_allows('customer.claim_directory',c.workspace_id,c.owner_user_ref_id,ARRAY[c.owner_team_id],false)
   AND (NULLIF(btrim(p_query),'') IS NULL OR c.name ILIKE '%'||p_query||'%')
 ), page AS (
  SELECT id,name FROM matched ORDER BY name,id
  LIMIT (SELECT page_size FROM bounds) OFFSET (SELECT page_start FROM bounds)
 ), items AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
   'id',c.id::text,'name',c.name,'industry_code',c.industry_code,'level_code',c.level_code,
   'customer_type_code',c.customer_type_code,'team_name',t.name,'owner_name',u.display_name,
   'ownership_state',o.state,'claimed',o.owner_user_ref_id=common.current_user_ref_id(),
   'can_claim',security.can_claim_customer(c.id),
   'claim_status',(SELECT r.status FROM crm.customer_claim_request r WHERE r.customer_id=c.id
    AND r.applicant_user_ref_id=common.current_user_ref_id() ORDER BY r.requested_at DESC LIMIT 1)
  ) ORDER BY page.name,page.id),'[]'::jsonb) AS value
  FROM page JOIN crm.customer c ON c.id=page.id
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  LEFT JOIN platform.team t ON t.id=c.owner_team_id
  LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id
 ), totals AS (SELECT count(*) AS value FROM matched)
 SELECT jsonb_build_object('items',items.value,'total',totals.value,
  'has_more',bounds.page_start+jsonb_array_length(items.value)<totals.value,
  'next_offset',CASE WHEN bounds.page_start+jsonb_array_length(items.value)<totals.value
    THEN bounds.page_start+jsonb_array_length(items.value) ELSE NULL END)
 FROM items CROSS JOIN totals CROSS JOIN bounds;
$function$;
CREATE OR REPLACE FUNCTION security.profile_customer_owner(p_customer uuid)
 RETURNS uuid
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT o.owner_user_ref_id
 FROM crm.customer_ownership o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 WHERE c.id=p_customer AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
  AND o.state='claimed'
  AND security.authorization_customer(security.authorization_read_feature('customer'),c.id);
$function$;

CREATE FUNCTION security.authorization_customer_mutation(p_customer uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_customer('customer.update',p_customer) OR EXISTS(
  SELECT 1 FROM crm.customer c WHERE c.id=p_customer AND c.workspace_id=common.current_workspace_id()
  AND c.created_by_user_ref_id=common.current_user_ref_id() AND c.created_at>=transaction_timestamp()
  AND pg_xact_status(c.xmin::text::xid8)='in progress'
  AND security.authorization_allows('customer.create',c.workspace_id,c.created_by_user_ref_id,ARRAY[c.owner_team_id],false));
$$;
CREATE OR REPLACE FUNCTION security.has_customer_write_access(p_customer_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_customer_mutation(p_customer_id);
$$;
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT * FROM pg_policies WHERE schemaname='crm' AND tablename IN
  ('contact','customer_claim_request','customer_ownership','customer_ownership_event','customer_sales_member','partner')
  AND policyname LIKE 'fde_commercial_%' LOOP
  EXECUTE format('DROP POLICY %I ON %I.%I',p.policyname,p.schemaname,p.tablename);
 END LOOP;
END $$;
ALTER POLICY management_read ON crm.customer_claim_request USING(workspace_id=common.current_workspace_id()
 AND security.authorization_customer('customer.claim_review',customer_id));
ALTER POLICY management_read ON crm.customer_ownership USING(workspace_id=common.current_workspace_id()
 AND (security.authorization_customer(security.authorization_read_feature('customer'),customer_id)
 OR security.authorization_customer('customer.claim_review',customer_id)));
ALTER POLICY management_read ON crm.customer_ownership_event USING(workspace_id=common.current_workspace_id()
 AND security.authorization_customer(security.authorization_read_feature('customer'),customer_id));
ALTER POLICY partner_create ON crm.partner WITH CHECK(workspace_id=common.current_workspace_id()
 AND security.authorization_has('partner.manage') AND created_by_user_ref_id=common.current_user_ref_id());
ALTER POLICY partner_update ON crm.partner USING(workspace_id=common.current_workspace_id()
 AND security.authorization_has('partner.manage')) WITH CHECK(workspace_id=common.current_workspace_id()
 AND security.authorization_has('partner.manage') AND updated_by_user_ref_id=common.current_user_ref_id());
ALTER POLICY partner_read ON crm.partner USING(workspace_id=common.current_workspace_id()
 AND (security.authorization_has('partner.read') OR security.authorization_has('partner.manage')));
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v134;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v134();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_customer_mutation(uuid) TO %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.authorization_user_allows(text,uuid,uuid,uuid,uuid[],boolean) FROM %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v134(),
 security.authorization_customer_mutation(uuid) FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V136','Permission-based customer claim workflows and partner maintenance');
COMMIT;
