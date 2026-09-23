BEGIN;
CREATE TABLE crm.customer_ownership (
 customer_id uuid PRIMARY KEY, workspace_id uuid NOT NULL,
 owner_user_ref_id uuid, state text NOT NULL DEFAULT 'unclaimed' CHECK(state IN ('unclaimed','claimed','legacy_review')),
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((state='claimed')=(owner_user_ref_id IS NOT NULL)),
 FOREIGN KEY(customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id),
 FOREIGN KEY(owner_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
COMMENT ON TABLE crm.customer_ownership IS '唯一有效认领事实；跟进参与、历史访问权限与此独立；legacy_review 必须人工核对';
CREATE TABLE crm.customer_claim_request (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, customer_id uuid NOT NULL,
 applicant_user_ref_id uuid NOT NULL, status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
 requested_at timestamptz NOT NULL DEFAULT clock_timestamp(), reviewed_at timestamptz, reviewer_user_ref_id uuid,
 decision_reason text, source text NOT NULL DEFAULT 'sales' CHECK(source IN ('sales','legacy_review')),
 CHECK((status='pending')=(reviewed_at IS NULL)),
 FOREIGN KEY(customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id),
 FOREIGN KEY(applicant_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(reviewer_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE UNIQUE INDEX customer_one_pending_application ON crm.customer_claim_request(customer_id,applicant_user_ref_id) WHERE status='pending';
CREATE INDEX customer_claim_queue ON crm.customer_claim_request(workspace_id,status,requested_at DESC);
CREATE TABLE crm.customer_ownership_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, customer_id uuid NOT NULL,
 event_type text NOT NULL CHECK(event_type IN ('approved','released','legacy_resolved')),
 previous_owner_user_ref_id uuid, owner_user_ref_id uuid, actor_user_ref_id uuid NOT NULL,
 claim_request_id uuid REFERENCES crm.customer_claim_request(id), reason text NOT NULL,
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id),
 FOREIGN KEY(actor_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
ALTER TABLE crm.contact ADD COLUMN phone text;
ALTER TABLE crm.contact ADD COLUMN email text;
COMMENT ON COLUMN crm.contact.phone IS '业务联系人电话，受客户数据权限约束，不进入全公司客户选择列表';
ALTER TABLE crm.customer ADD COLUMN company_reference text;
ALTER TABLE crm.customer ADD COLUMN company_verified_at timestamptz;
ALTER TABLE crm.customer ADD COLUMN company_verified_by uuid REFERENCES platform.user_ref(id);
COMMENT ON COLUMN crm.customer.company_reference IS '运营核实的公司系统客户编号或审批单号；不是自动同步状态';
INSERT INTO crm.customer_ownership(customer_id,workspace_id,owner_user_ref_id,state)
 SELECT c.id,c.workspace_id,CASE WHEN count(m.user_ref_id)=1 THEN (array_agg(m.user_ref_id))[1] END,
 CASE WHEN count(m.user_ref_id)=0 THEN 'unclaimed' WHEN count(m.user_ref_id)=1 THEN 'claimed' ELSE 'legacy_review' END
 FROM crm.customer c LEFT JOIN crm.customer_sales_member m ON m.customer_id=c.id GROUP BY c.id,c.workspace_id;
COMMENT ON TABLE crm.customer_sales_member IS '历史客户参与/访问关系；不能据此判断当前认领归属，当前唯一归属见 customer_ownership';
COMMENT ON COLUMN crm.customer.owner_user_ref_id IS '兼容字段；审批/释放时与 customer_ownership 同步；历史冲突未核对前不能作为认领依据';
CREATE FUNCTION crm.initialize_customer_ownership() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO crm.customer_ownership(customer_id,workspace_id,owner_user_ref_id,state) VALUES(NEW.id,NEW.workspace_id,NEW.owner_user_ref_id,CASE WHEN NEW.owner_user_ref_id IS NULL THEN 'unclaimed' ELSE 'claimed' END);
 RETURN NEW;
END $$;
CREATE TRIGGER initialize_customer_ownership AFTER INSERT ON crm.customer FOR EACH ROW EXECUTE FUNCTION crm.initialize_customer_ownership();
CREATE FUNCTION crm.guard_customer_administration() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN RETURN NEW; END IF;
 IF TG_OP='INSERT' THEN
  IF NOT security.management_actor() OR NEW.owner_user_ref_id IS NOT NULL OR NULLIF(btrim(NEW.company_reference),'') IS NULL
   OR NEW.company_verified_by IS DISTINCT FROM common.current_user_ref_id() OR NEW.company_verified_at IS NULL THEN
   RAISE EXCEPTION '仅运营可在核实公司建档后创建未认领客户' USING ERRCODE='42501';
  END IF;
 ELSIF NEW.owner_user_ref_id IS DISTINCT FROM OLD.owner_user_ref_id THEN
  IF current_setting('app.ownership_transition',true) IS DISTINCT FROM 'on' THEN
   RAISE EXCEPTION '客户归属必须通过认领审批或释放变更' USING ERRCODE='42501';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_customer_administration BEFORE INSERT OR UPDATE ON crm.customer FOR EACH ROW EXECUTE FUNCTION crm.guard_customer_administration();
CREATE OR REPLACE FUNCTION security.add_customer_sales_member(p_customer_id uuid,p_user_id uuid) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION '请使用客户认领申请及运营审批流程' USING ERRCODE='42501'; END $$;
CREATE OR REPLACE FUNCTION security.can_claim_customer(p_customer_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_role_code()='sales' AND security.has_active_role('sales') AND EXISTS(
 SELECT 1 FROM crm.customer c JOIN crm.customer_ownership o ON o.customer_id=c.id
 WHERE c.id=p_customer_id AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL AND o.state='unclaimed');
$$;
-- Company-wide selection returns only customer reference data, never customer history.
CREATE FUNCTION security.customer_reference(p_customer_id uuid) RETURNS jsonb
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',c.id::text,'name',c.name,'customer_type_code',c.customer_type_code,'owner_team_id',c.owner_team_id::text)
 FROM crm.customer c WHERE c.id=p_customer_id AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
 AND security.has_active_role(common.current_role_code());
$$;
CREATE FUNCTION security.company_customer_directory(p_query text,p_limit integer,p_offset integer DEFAULT 0) RETURNS SETOF jsonb
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',c.id::text,'name',c.name,'industry_code',c.industry_code,'level_code',c.level_code,
 'customer_type_code',c.customer_type_code,'team_name',t.name,'owner_name',u.display_name,'ownership_state',o.state,
 'claimed',o.owner_user_ref_id=common.current_user_ref_id(),'can_claim',security.can_claim_customer(c.id),
 'claim_status',(SELECT r.status FROM crm.customer_claim_request r WHERE r.customer_id=c.id
 AND r.applicant_user_ref_id=common.current_user_ref_id() ORDER BY r.requested_at DESC LIMIT 1))
 FROM crm.customer c JOIN crm.customer_ownership o ON o.customer_id=c.id
 LEFT JOIN platform.team t ON t.id=c.owner_team_id LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id
 WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL AND security.has_active_role(common.current_role_code())
 AND (NULLIF(btrim(p_query),'') IS NULL OR c.name ILIKE '%'||p_query||'%')
 ORDER BY c.name,c.id LIMIT LEAST(GREATEST(p_limit,1),100) OFFSET GREATEST(p_offset,0);
$$;
CREATE OR REPLACE FUNCTION security.customer_claim_pool(p_query text,p_limit integer) RETURNS SETOF jsonb
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT * FROM security.company_customer_directory(p_query,p_limit,0);
$$;
CREATE FUNCTION security.claim_notification(p_recipient uuid,p_customer uuid,p_event uuid,p_title text,p_body text) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,body,object_type,object_id,status,dedupe_key,payload)
 SELECT common.current_workspace_id(),p_recipient,'customer_claim',p_title,p_body,'customer',p_customer,'pending',
 'customer-claim:'||p_event::text||':'||p_recipient::text,jsonb_build_object('customer_id',p_customer::text,'event_id',p_event::text)
 WHERE EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=p_recipient AND u.workspace_id=common.current_workspace_id() AND u.status='active')
 ON CONFLICT DO NOTHING;
$$;
CREATE OR REPLACE FUNCTION security.claim_customer(p_customer_id uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o crm.customer_ownership; req crm.customer_claim_request; recipient record; customer_name text;
BEGIN
 IF common.current_role_code()<>'sales' OR NOT security.has_active_role('sales') THEN
 RAISE EXCEPTION '仅销售可发起认领申请' USING ERRCODE='42501'; END IF;
 SELECT own.* INTO o FROM crm.customer_ownership own JOIN crm.customer c ON c.id=own.customer_id
 WHERE own.customer_id=p_customer_id AND own.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL FOR UPDATE OF own;
 IF NOT FOUND THEN RAISE EXCEPTION '客户不存在' USING ERRCODE='P0002'; END IF;
 IF o.state<>'unclaimed' THEN RAISE EXCEPTION '客户已认领或待运营核对，不能再次申请' USING ERRCODE='P0001'; END IF;
 SELECT * INTO req FROM crm.customer_claim_request WHERE customer_id=p_customer_id
 AND applicant_user_ref_id=common.current_user_ref_id() AND status='pending';
 IF NOT FOUND THEN
  INSERT INTO crm.customer_claim_request(workspace_id,customer_id,applicant_user_ref_id)
  VALUES(o.workspace_id,p_customer_id,common.current_user_ref_id()) RETURNING * INTO req;
  SELECT name INTO customer_name FROM crm.customer WHERE id=p_customer_id;
  FOR recipient IN SELECT DISTINCT rb.user_ref_id FROM platform.role_binding rb WHERE rb.workspace_id=o.workspace_id
   AND rb.role_code IN ('operations','administrator') AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
  LOOP PERFORM security.claim_notification(recipient.user_ref_id,p_customer_id,req.id,'客户认领待审批',customer_name); END LOOP;
 END IF;
 RETURN jsonb_build_object('request_id',req.id::text,'customer_id',p_customer_id::text,'status','pending','claimed',false,'added',false);
END $$;
CREATE FUNCTION security.review_customer_claim(p_request uuid,p_decision text,p_reason text) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE req crm.customer_claim_request; o crm.customer_ownership; customer_key uuid; competing record;
BEGIN
 IF NOT security.management_actor() THEN RAISE EXCEPTION '需要运营审批权限' USING ERRCODE='42501'; END IF;
 IF p_decision NOT IN ('approved','rejected') OR (p_decision='rejected' AND NULLIF(btrim(p_reason),'') IS NULL)
 THEN RAISE EXCEPTION '驳回必须填写原因' USING ERRCODE='22023'; END IF;
 SELECT customer_id INTO customer_key FROM crm.customer_claim_request WHERE id=p_request AND workspace_id=common.current_workspace_id();
 IF NOT FOUND THEN RAISE EXCEPTION '申请不存在' USING ERRCODE='P0002'; END IF;
 SELECT * INTO o FROM crm.customer_ownership WHERE customer_id=customer_key FOR UPDATE;
 SELECT * INTO req FROM crm.customer_claim_request WHERE id=p_request FOR UPDATE;
 IF req.status<>'pending' THEN RAISE EXCEPTION '申请已处理，请刷新' USING ERRCODE='P0001'; END IF;
 IF p_decision='approved' THEN
  IF o.state<>'unclaimed' THEN RAISE EXCEPTION '客户已认领或待核对' USING ERRCODE='P0001'; END IF;
  IF NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding rb ON rb.user_ref_id=u.id
   AND rb.workspace_id=u.workspace_id WHERE u.id=req.applicant_user_ref_id AND u.workspace_id=req.workspace_id
   AND u.status='active' AND u.deleted_at IS NULL AND rb.role_code='sales'
   AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to) THEN
   RAISE EXCEPTION '申请人已停用或不再为销售' USING ERRCODE='P0001'; END IF;
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
END $$;
CREATE FUNCTION security.release_customer(p_customer uuid,p_version integer,p_reason text) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o crm.customer_ownership; event_id uuid;
BEGIN
 IF NOT security.management_actor() THEN RAISE EXCEPTION '需要运营权限' USING ERRCODE='42501'; END IF;
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
END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['customer_ownership','customer_claim_request','customer_ownership_event'] LOOP
 EXECUTE format('ALTER TABLE crm.%I ENABLE ROW LEVEL SECURITY',tab);
 EXECUTE format('ALTER TABLE crm.%I FORCE ROW LEVEL SECURITY',tab);
 EXECUTE format('CREATE POLICY management_read ON crm.%I FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.management_actor())',tab);
 END LOOP;
END $$;
CREATE POLICY claim_applicant_read ON crm.customer_claim_request FOR SELECT
 USING(workspace_id=common.current_workspace_id() AND applicant_user_ref_id=common.current_user_ref_id());
CREATE POLICY owner_self_read ON crm.customer_ownership FOR SELECT
 USING(workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id());
-- No direct writes: all mutations go through the locked state transitions above.
REVOKE ALL ON crm.customer_ownership,crm.customer_claim_request,crm.customer_ownership_event FROM PUBLIC;
CREATE OR REPLACE FUNCTION security.has_customer_access(p_customer_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.customer c WHERE c.id=p_customer_id
 AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL AND (
  security.management_actor() OR (common.current_role_code()='manager' AND security.has_active_role('manager')) OR
  (security.supervises_team(c.owner_team_id) OR EXISTS(
   SELECT 1 FROM crm.customer_sales_member m JOIN platform.team_membership tm ON tm.user_ref_id=m.user_ref_id
    AND tm.workspace_id=m.workspace_id AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   WHERE m.customer_id=c.id AND security.supervises_team(tm.team_id))) OR
  (common.current_role_code()='sales' AND security.has_active_role('sales') AND EXISTS(
   SELECT 1 FROM crm.customer_sales_member m WHERE m.customer_id=c.id AND m.workspace_id=c.workspace_id
    AND m.user_ref_id=common.current_user_ref_id()))));
$$;
CREATE OR REPLACE FUNCTION security.opportunity_owner_in_scope(p_user_id uuid,p_team_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.management_actor() OR (common.current_role_code()='manager' AND security.has_active_role('manager'))
 OR (common.current_role_code()='sales' AND security.has_active_role('sales') AND p_user_id=common.current_user_ref_id())
 OR security.supervises_team(p_team_id);
$$;
CREATE POLICY operations_customer ON crm.customer USING(workspace_id=common.current_workspace_id() AND security.management_actor()) WITH CHECK(workspace_id=common.current_workspace_id() AND security.management_actor());
CREATE FUNCTION security.resolve_legacy_customer_owner(p_customer uuid,p_version integer,p_owner uuid,p_reason text) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o crm.customer_ownership; request_id uuid; result jsonb;
BEGIN
 IF NOT security.management_actor() THEN RAISE EXCEPTION '需要运营权限' USING ERRCODE='42501'; END IF;
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
END $$;
-- Following an unclaimed/other-owned company customer does not grant its history.
CREATE POLICY own_company_visit ON activity.visit
 USING(workspace_id=common.current_workspace_id() AND recorder_user_ref_id=common.current_user_ref_id())
 WITH CHECK(workspace_id=common.current_workspace_id() AND recorder_user_ref_id=common.current_user_ref_id()
 AND created_by_user_ref_id=common.current_user_ref_id() AND security.customer_reference(customer_id) IS NOT NULL);
CREATE POLICY own_visit_fields ON activity.visit_field_value FOR SELECT
 USING(workspace_id=common.current_workspace_id() AND EXISTS(SELECT 1 FROM activity.visit v
 WHERE v.id=visit_id AND v.recorder_user_ref_id=common.current_user_ref_id()));
CREATE OR REPLACE FUNCTION security.has_opportunity_access(p_opportunity_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=p_opportunity_id AND o.workspace_id=common.current_workspace_id()
 AND o.deleted_at IS NULL AND security.opportunity_owner_in_scope(o.owner_user_ref_id,o.owner_team_id));
$$;
DROP POLICY opportunity_owner_scope ON crm.opportunity;
CREATE POLICY opportunity_owner_scope ON crm.opportunity USING(workspace_id=common.current_workspace_id() AND deleted_at IS NULL
 AND security.opportunity_owner_in_scope(owner_user_ref_id,owner_team_id)) WITH CHECK(workspace_id=common.current_workspace_id()
 AND security.customer_reference(customer_id) IS NOT NULL AND security.opportunity_owner_in_scope(owner_user_ref_id,owner_team_id));
CREATE OR REPLACE FUNCTION security.opportunity_name_is_available(p_customer_id uuid,p_name text,p_exclude_id uuid) RETURNS boolean
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF security.customer_reference(p_customer_id) IS NULL THEN
  RAISE EXCEPTION '客户不存在或不可见' USING ERRCODE='42501';
 END IF;
 IF p_exclude_id IS NOT NULL AND NOT security.has_opportunity_access(p_exclude_id) THEN
  RAISE EXCEPTION '商机不存在或不可见' USING ERRCODE='42501';
 END IF;
 RETURN NOT EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.customer_id=p_customer_id AND o.deleted_at IS NULL
  AND (p_exclude_id IS NULL OR o.id<>p_exclude_id)
  AND crm.normalize_opportunity_name(o.name)=crm.normalize_opportunity_name(p_name));
END $$;
-- Opportunity history follows the same owner visibility as its opportunity.
DROP POLICY business_change_scope ON crm.business_change;
CREATE POLICY business_change_scope ON crm.business_change
USING(workspace_id=common.current_workspace_id() AND CASE WHEN opportunity_id IS NULL THEN security.has_customer_access(customer_id) ELSE security.has_opportunity_access(opportunity_id) END)
WITH CHECK(workspace_id=common.current_workspace_id() AND CASE WHEN opportunity_id IS NULL THEN security.has_customer_access(customer_id) ELSE security.has_opportunity_access(opportunity_id) END);
CREATE POLICY notification_operations_insert ON workflow.notification FOR INSERT
WITH CHECK(workspace_id=common.current_workspace_id() AND security.management_actor() AND EXISTS(
 SELECT 1 FROM platform.user_ref u WHERE u.id=recipient_user_ref_id AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL));
COMMIT;
