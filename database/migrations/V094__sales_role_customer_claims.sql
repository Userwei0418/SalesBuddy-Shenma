BEGIN;

-- Business sales roles may apply for their own customer ownership. Applications
-- remain pending until operations approval; no direct ownership write is granted.
-- CREATE OR REPLACE preserves every existing function owner and EXECUTE ACL.
-- Customer owner_team_id remains the existing business team; a manager without
-- team membership does not invent or transfer the customer to a different team.
CREATE OR REPLACE FUNCTION security.can_claim_customer(p_customer_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_role_code() IN ('sales','supervisor','manager')
 AND security.has_active_role(common.current_role_code()) AND EXISTS(
 SELECT 1 FROM platform.user_ref u WHERE u.id=common.current_user_ref_id()
 AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL)
 AND EXISTS(
 SELECT 1 FROM crm.customer c JOIN crm.customer_ownership o ON o.customer_id=c.id
 WHERE c.id=p_customer_id AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL AND o.state='unclaimed');
$$;

CREATE OR REPLACE FUNCTION security.claim_customer(p_customer_id uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o crm.customer_ownership; req crm.customer_claim_request; recipient record; customer_name text;
BEGIN
 IF common.current_role_code() NOT IN ('sales','supervisor','manager')
 OR NOT security.has_active_role(common.current_role_code()) OR NOT EXISTS(
 SELECT 1 FROM platform.user_ref u WHERE u.id=common.current_user_ref_id()
 AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL) THEN
 RAISE EXCEPTION '仅有效的一线销售、销售总监或销售总经理可发起认领申请' USING ERRCODE='42501'; END IF;
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

CREATE OR REPLACE FUNCTION security.review_customer_claim(p_request uuid,p_decision text,p_reason text) RETURNS jsonb
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
   AND u.status='active' AND u.deleted_at IS NULL AND rb.role_code IN ('sales','supervisor','manager')
   AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to) THEN
   RAISE EXCEPTION '申请人已停用或已无有效销售业务身份' USING ERRCODE='P0001'; END IF;
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

CREATE OR REPLACE FUNCTION security.has_customer_write_access(p_customer_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.customer c WHERE c.id=p_customer_id
 AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL AND (
 (common.current_role_code() IN ('sales','supervisor','manager')
 AND security.has_active_role(common.current_role_code()) AND EXISTS(
 SELECT 1 FROM crm.customer_ownership own WHERE own.customer_id=c.id
 AND own.workspace_id=c.workspace_id AND own.state='claimed'
 AND own.owner_user_ref_id=common.current_user_ref_id())) OR
 security.management_actor() OR (common.current_role_code()='manager' AND security.has_active_role('manager')) OR
 (security.supervises_team(c.owner_team_id) OR EXISTS(SELECT 1 FROM crm.customer_sales_member m
 JOIN platform.team_membership tm ON tm.user_ref_id=m.user_ref_id AND tm.workspace_id=m.workspace_id
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 WHERE m.customer_id=c.id AND security.supervises_team(tm.team_id))) OR
 (common.current_role_code()='sales' AND security.has_active_role('sales') AND EXISTS(
 SELECT 1 FROM crm.customer_sales_member m WHERE m.customer_id=c.id AND m.workspace_id=c.workspace_id
 AND m.user_ref_id=common.current_user_ref_id()))));
 $$;

COMMENT ON FUNCTION security.can_claim_customer(uuid) IS
 '三类销售本人可申请未认领客户；FDE不可认领，审批、唯一归属及客户访问范围独立校验';
COMMENT ON FUNCTION security.has_customer_write_access(uuid) IS
 '保留既有商业访问范围；三类销售经运营审批后的本人客户归属可读写，不扩展他人商机权限';

INSERT INTO ops.schema_migration(version,description)
 VALUES ('V094','三类销售本人认领申请及审批归属访问');
COMMIT;
