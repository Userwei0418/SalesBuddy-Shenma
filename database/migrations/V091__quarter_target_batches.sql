BEGIN;

-- Keep the existing target IDs and independent person/team scopes. Only the
-- department singleton gains a business-family discriminator.
ALTER TABLE crm.sales_target ADD COLUMN department_code text NOT NULL DEFAULT 'sales';
ALTER TABLE crm.sales_target ADD COLUMN change_reason text NOT NULL DEFAULT '';
ALTER TABLE crm.sales_target ADD COLUMN source text NOT NULL DEFAULT 'legacy'
 CHECK(source IN ('legacy','self','operations','approved'));
ALTER TABLE crm.sales_target ADD CONSTRAINT target_department_code_check
 CHECK(department_code IN ('sales','fde') AND (scope_type='department' OR department_code='sales'));
ALTER TABLE crm.sales_target DROP CONSTRAINT target_scope_period_unique;
ALTER TABLE crm.sales_target ADD CONSTRAINT target_scope_period_unique UNIQUE NULLS NOT DISTINCT
 (workspace_id,period_type,period_start,scope_type,user_ref_id,team_id,department_code,kind);
COMMENT ON COLUMN crm.sales_target.department_code IS
 '仅区分部门级销售/FDE目标；person/team固定sales占位，不改变原人员/团队目标唯一身份';
COMMENT ON COLUMN crm.sales_target.change_reason IS '最近一次生效设置或调整原因；历史旧目标可为空，新提交必填';

CREATE TABLE crm.sales_target_batch_request (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 scope_type text NOT NULL CHECK(scope_type IN ('person','team','department')),
 user_ref_id uuid,
 team_id uuid REFERENCES platform.team(id),
 department_code text NOT NULL DEFAULT 'sales',
 period_type text NOT NULL CHECK(period_type IN ('week','month','quarter','year')),
 period_start date NOT NULL,
 period_end date NOT NULL CHECK(period_end>=period_start),
 applicant_user_ref_id uuid NOT NULL,
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 2000),
 items jsonb NOT NULL CHECK(jsonb_typeof(items)='array' AND jsonb_array_length(items) BETWEEN 1 AND 6),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 reviewed_at timestamptz,
 reviewer_user_ref_id uuid,
 decision_reason text NOT NULL DEFAULT '',
 CHECK((scope_type='person' AND user_ref_id IS NOT NULL AND team_id IS NULL)
  OR (scope_type='team' AND team_id IS NOT NULL AND user_ref_id IS NULL)
  OR (scope_type='department' AND user_ref_id IS NULL AND team_id IS NULL)),
 CHECK(department_code IN ('sales','fde') AND (scope_type='department' OR department_code='sales')),
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(applicant_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(reviewer_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE INDEX target_batch_inbox ON crm.sales_target_batch_request(workspace_id,status,created_at DESC);
CREATE INDEX target_batch_pending_scope ON crm.sales_target_batch_request
 (workspace_id,scope_type,user_ref_id,team_id,department_code,period_type,period_start) WHERE status='pending';
COMMENT ON TABLE crm.sales_target_batch_request IS
 '季度等周期多指标整单审批；items保存生效目标ID、基准版本、原值、申请值，整单原子生效，历史永不删除';

-- A business subject must be active in its business family. A sales manager's
-- workspace role does not expose operations or FDE profiles.
CREATE FUNCTION security.target_scope_read(p_scope text,p_user uuid,p_team uuid,p_department_code text)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(p_department_code IN ('sales','fde') AND (p_scope='department' OR p_department_code='sales') AND
 CASE
 WHEN p_scope='department' AND p_user IS NULL AND p_team IS NULL THEN
   security.management_actor() OR (p_department_code='sales' AND common.current_role_code()='manager'
    AND security.has_active_role('manager'))
 WHEN p_scope='person' AND p_team IS NULL THEN EXISTS(
   SELECT 1 FROM platform.user_ref u WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id()
    AND u.status='active' AND u.deleted_at IS NULL AND (
     security.management_actor() OR
     (p_user=common.current_user_ref_id() AND common.current_role_code() IN ('sales','supervisor','manager','fde','fde_lead')
      AND security.has_active_role(common.current_role_code())
      AND (common.current_role_code() NOT IN ('fde','fde_lead') OR security.is_fde_actor())) OR
     (common.current_role_code() IN ('supervisor','manager') AND security.has_active_role(common.current_role_code())
      AND EXISTS(SELECT 1 FROM platform.role_binding b WHERE b.workspace_id=u.workspace_id AND b.user_ref_id=u.id
       AND b.role_code IN ('sales','supervisor','manager') AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to)
      AND (common.current_role_code()='manager' OR EXISTS(SELECT 1 FROM platform.team_membership tm
       WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND tm.membership_role IN ('sales','supervisor','manager')
        AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to AND security.supervises_team(tm.team_id)))) OR
     (common.current_role_code()='fde_lead' AND security.is_fde_actor() AND security.fde_manages_user(p_user))))
 WHEN p_scope='team' AND p_user IS NULL THEN EXISTS(
   SELECT 1 FROM platform.team t WHERE t.id=p_team AND t.workspace_id=common.current_workspace_id()
    AND t.status='active' AND t.deleted_at IS NULL AND (
     security.management_actor() OR
     (common.current_role_code() IN ('supervisor','manager') AND security.has_active_role(common.current_role_code())
      AND (common.current_role_code()='manager' OR security.supervises_team(t.id))
      AND EXISTS(SELECT 1 FROM platform.team_membership tm JOIN platform.user_ref u ON u.id=tm.user_ref_id AND u.workspace_id=tm.workspace_id
       JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
        AND b.role_code=tm.membership_role AND (b.team_id IS NULL OR b.team_id=tm.team_id)
       WHERE tm.team_id=t.id AND tm.workspace_id=t.workspace_id AND u.status='active' AND u.deleted_at IS NULL
        AND tm.membership_role IN ('sales','supervisor','manager') AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
        AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to)) OR
     (common.current_role_code()='fde_lead' AND security.is_fde_actor()
      AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',p_team))))
 ELSE false END,false);
$$;
CREATE OR REPLACE FUNCTION security.target_scope_read(p_scope text,p_user uuid,p_team uuid)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.target_scope_read(p_scope,p_user,p_team,'sales');
$$;
CREATE FUNCTION security.target_scope_write(p_scope text,p_user uuid,p_team uuid,p_department_code text)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.target_scope_read(p_scope,p_user,p_team,p_department_code) AND
 (security.management_actor() OR (p_scope='person' AND p_user=common.current_user_ref_id()
  AND common.current_role_code() IN ('sales','supervisor','manager','fde','fde_lead')
  AND security.has_active_role(common.current_role_code())));
$$;
CREATE OR REPLACE FUNCTION security.target_scope_write(p_scope text,p_user uuid,p_team uuid)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.target_scope_write(p_scope,p_user,p_team,'sales');
$$;
DROP POLICY sales_target_read ON crm.sales_target;
CREATE POLICY sales_target_read ON crm.sales_target FOR SELECT USING
 (workspace_id=common.current_workspace_id() AND security.target_scope_read(scope_type,user_ref_id,team_id,department_code));
ALTER TABLE crm.sales_target_batch_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.sales_target_batch_request FORCE ROW LEVEL SECURITY;
CREATE POLICY target_batch_read ON crm.sales_target_batch_request FOR SELECT USING
 (workspace_id=common.current_workspace_id() AND (security.management_actor() OR
  (applicant_user_ref_id=common.current_user_ref_id() AND security.target_scope_read(scope_type,user_ref_id,team_id,department_code))));
CREATE TRIGGER target_batch_audit AFTER INSERT OR UPDATE ON crm.sales_target_batch_request
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
-- crm.sales_target already has V050 business_audit; do not duplicate audit rows.
CREATE FUNCTION crm.protect_target_batch() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.status<>'pending' OR NEW.status='pending' OR
  (to_jsonb(NEW)-ARRAY['status','reviewed_at','reviewer_user_ref_id','decision_reason']) IS DISTINCT FROM
  (to_jsonb(OLD)-ARRAY['status','reviewed_at','reviewer_user_ref_id','decision_reason'])
 THEN RAISE EXCEPTION '已提交目标申请不可覆盖，请新建申请' USING ERRCODE='22023'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER target_batch_immutable BEFORE UPDATE ON crm.sales_target_batch_request
 FOR EACH ROW EXECUTE FUNCTION crm.protect_target_batch();
CREATE TRIGGER target_batch_no_delete BEFORE DELETE OR TRUNCATE ON crm.sales_target_batch_request
 FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();

-- One transaction lock per complete subject/period, shared by submissions,
-- operations adjustments, new approvals and legacy approvals. Missing target
-- rows are covered too; per-row locking alone cannot serialize first entries.
CREATE FUNCTION security.lock_target_period(p_scope text,p_user uuid,p_team uuid,p_department_code text,p_period text,p_start date)
 RETURNS void LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT pg_advisory_xact_lock(hashtextextended(concat_ws(':','target-period-v2',common.current_workspace_id(),
  p_scope,p_user,p_team,p_department_code,p_period,p_start),0));
$$;
CREATE FUNCTION security.save_target_batch(p_scope text,p_user uuid,p_team uuid,p_period text,p_start date,p_end date,
 p_items jsonb,p_reason text,p_department_code text DEFAULT 'sales') RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target crm.sales_target; req crm.sales_target_batch_request; item jsonb; snapshot jsonb:='[]'::jsonb;
 effective_items jsonb:='[]'::jsonb; current_items jsonb:='[]'::jsonb; expected_end date;
 item_amount numeric; item_version integer; changed boolean:=false; requires_review boolean:=false;
 kinds text[]:='{}'; normalized_reason text:=btrim(p_reason);
BEGIN
 IF NOT security.target_scope_write(p_scope,p_user,p_team,p_department_code) THEN RAISE insufficient_privilege; END IF;
 IF p_period IS NULL OR p_period NOT IN ('week','month','quarter','year') OR p_start IS NULL OR p_end IS NULL
  OR extract(year FROM p_start) NOT BETWEEN 2000 AND 2100 OR normalized_reason IS NULL OR length(normalized_reason) NOT BETWEEN 1 AND 2000
  OR p_items IS NULL OR jsonb_typeof(p_items)<>'array' OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 6
 THEN RAISE EXCEPTION '请填写完整周期、目标和设置或调整原因' USING ERRCODE='22023'; END IF;
 expected_end=CASE p_period WHEN 'week' THEN p_start+6 WHEN 'month' THEN (p_start+interval '1 month'-interval '1 day')::date
  WHEN 'quarter' THEN (p_start+interval '3 months'-interval '1 day')::date ELSE (p_start+interval '1 year'-interval '1 day')::date END;
 IF date_trunc(p_period,p_start::timestamp)::date<>p_start OR expected_end<>p_end THEN
  RAISE EXCEPTION '目标须使用完整自然周、月、季度或年' USING ERRCODE='22023'; END IF;
 IF NOT security.management_actor() AND (p_period<>'quarter' OR
  p_start<>date_trunc('quarter',timezone('Asia/Shanghai',clock_timestamp()))::date) THEN
  RAISE EXCEPTION '本人只能填写或申请变更当前季度目标' USING ERRCODE='22023'; END IF;
 PERFORM security.lock_target_period(p_scope,p_user,p_team,p_department_code,p_period,p_start);
 FOR item IN SELECT value FROM jsonb_array_elements(p_items) ORDER BY value->>'kind' LOOP
  IF jsonb_typeof(item)<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(item) k WHERE k NOT IN ('kind','amount','version_no'))
   OR item->>'kind' IS NULL OR item->>'kind' NOT IN ('collection','recognized','acv','opportunity_count','visit_count','demo_count')
   OR item->>'kind'=ANY(kinds) OR jsonb_typeof(item->'amount') NOT IN ('number','string')
   OR item->>'amount' IS NULL OR length(item->>'amount')>32 OR (item->>'amount')!~'^[0-9]+([.][0-9]{1,2})?$'
   OR ((item->'version_no') IS NOT NULL AND item->'version_no'<>'null'::jsonb AND
    (jsonb_typeof(item->'version_no')<>'number' OR (item->>'version_no')!~'^[1-9][0-9]*$'))
  THEN RAISE EXCEPTION '目标指标、金额或版本格式无效，请刷新表单' USING ERRCODE='22023'; END IF;
  IF item->>'version_no' IS NOT NULL AND (length(item->>'version_no')>10 OR
   (length(item->>'version_no')=10 AND (item->>'version_no')>'2147483647')) THEN
   RAISE EXCEPTION '目标版本超出范围，请刷新表单' USING ERRCODE='22023'; END IF;
  item_amount=(item->>'amount')::numeric; item_version=(item->>'version_no')::integer;
  IF item_amount<=0 OR item_amount>=10000000000000000 OR item_amount<>round(item_amount,2)
   OR (item->>'kind' IN ('opportunity_count','visit_count','demo_count') AND item_amount<>trunc(item_amount))
  THEN RAISE EXCEPTION '目标金额须为正数且最多两位小数，数量须为正整数' USING ERRCODE='22023'; END IF;
  kinds=array_append(kinds,item->>'kind');
  SELECT * INTO target FROM crm.sales_target WHERE workspace_id=common.current_workspace_id()
   AND scope_type=p_scope AND user_ref_id IS NOT DISTINCT FROM p_user AND team_id IS NOT DISTINCT FROM p_team
   AND department_code=p_department_code AND period_type=p_period AND period_start=p_start AND kind=item->>'kind' FOR UPDATE;
  IF target.id IS NULL AND item_version IS NOT NULL THEN
   RAISE EXCEPTION '目标状态已变化，请刷新表单' USING ERRCODE='P0001';
  ELSIF target.id IS NOT NULL AND (item_version IS NULL OR target.version_no<>item_version) THEN
   RAISE EXCEPTION '目标已更新，请刷新后再提交' USING ERRCODE='P0001';
  END IF;
  snapshot=snapshot||jsonb_build_array(jsonb_build_object('kind',item->>'kind','target_id',target.id,
   'previous_amount',target.amount,'proposed_amount',item_amount,'base_version',target.version_no));
  IF target.id IS NOT NULL THEN current_items=current_items||jsonb_build_array(to_jsonb(target)); END IF;
  changed=changed OR target.id IS NULL OR target.amount<>item_amount;
  requires_review=requires_review OR (target.id IS NOT NULL AND target.amount<>item_amount);
 END LOOP;
 IF NOT changed THEN RETURN jsonb_build_object('status','unchanged','items',current_items,'request',NULL); END IF;
 -- All overlapping pending requests are immutable. Identical retries replay the
 -- same batch; a disjoint request can coexist under the same period lock.
 FOR req IN SELECT r.* FROM crm.sales_target_batch_request r WHERE r.workspace_id=common.current_workspace_id()
  AND r.scope_type=p_scope AND r.user_ref_id IS NOT DISTINCT FROM p_user AND r.team_id IS NOT DISTINCT FROM p_team
  AND r.department_code=p_department_code AND r.period_type=p_period AND r.period_start=p_start AND r.status='pending'
  AND EXISTS(SELECT 1 FROM jsonb_array_elements(r.items) i WHERE i->>'kind'=ANY(kinds)) FOR UPDATE LOOP
  IF security.management_actor() THEN
   UPDATE crm.sales_target_batch_request SET status='cancelled',reviewed_at=clock_timestamp(),
    reviewer_user_ref_id=common.current_user_ref_id(),decision_reason='运营已调整相关目标，原整单申请关闭，请核对后重新申请' WHERE id=req.id;
  ELSIF req.items=snapshot AND req.reason=normalized_reason AND req.applicant_user_ref_id=common.current_user_ref_id() THEN
   RETURN jsonb_build_object('status','pending','items',current_items,'request',to_jsonb(req));
  ELSE RAISE EXCEPTION '已有待审批的相关目标申请，请等待运营处理' USING ERRCODE='P0001'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM crm.sales_target_change_request r JOIN crm.sales_target t ON t.id=r.target_id
  WHERE t.workspace_id=common.current_workspace_id() AND t.scope_type=p_scope AND t.user_ref_id IS NOT DISTINCT FROM p_user
   AND t.team_id IS NOT DISTINCT FROM p_team AND t.department_code=p_department_code AND t.period_type=p_period
   AND t.period_start=p_start AND t.kind=ANY(kinds) AND r.status='pending') THEN
  IF NOT security.management_actor() THEN RAISE EXCEPTION '已有待审批的历史目标申请，请等待运营处理' USING ERRCODE='P0001'; END IF;
  UPDATE crm.sales_target_change_request r SET status='cancelled',reviewed_at=clock_timestamp(),
   reviewer_user_ref_id=common.current_user_ref_id(),decision_reason='目标已由运营调整，请核对后重新申请'
   FROM crm.sales_target t WHERE t.id=r.target_id AND t.workspace_id=common.current_workspace_id()
    AND t.scope_type=p_scope AND t.user_ref_id IS NOT DISTINCT FROM p_user AND t.team_id IS NOT DISTINCT FROM p_team
    AND t.department_code=p_department_code AND t.period_type=p_period AND t.period_start=p_start AND t.kind=ANY(kinds) AND r.status='pending';
 END IF;
 IF requires_review AND NOT security.management_actor() THEN
  INSERT INTO crm.sales_target_batch_request(workspace_id,scope_type,user_ref_id,team_id,department_code,period_type,
   period_start,period_end,applicant_user_ref_id,reason,items)
  VALUES(common.current_workspace_id(),p_scope,p_user,p_team,p_department_code,p_period,p_start,p_end,
   common.current_user_ref_id(),normalized_reason,snapshot) RETURNING * INTO req;
  RETURN jsonb_build_object('status','pending','items',current_items,'request',to_jsonb(req));
 END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(snapshot) LOOP
  IF item->>'target_id' IS NULL THEN
   INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,team_id,department_code,kind,amount,
    updated_by_user_ref_id,period_type,period_start,period_end,change_reason,source)
   VALUES(common.current_workspace_id(),extract(year FROM p_start)::integer,p_scope,p_user,p_team,p_department_code,
    item->>'kind',(item->>'proposed_amount')::numeric,common.current_user_ref_id(),p_period,p_start,p_end,normalized_reason,
    CASE WHEN security.management_actor() THEN 'operations' ELSE 'self' END) RETURNING * INTO target;
  ELSIF item->'previous_amount'<>item->'proposed_amount' THEN
   UPDATE crm.sales_target SET amount=(item->>'proposed_amount')::numeric,updated_by_user_ref_id=common.current_user_ref_id(),
    version_no=version_no+1,updated_at=clock_timestamp(),change_reason=normalized_reason,
    source=CASE WHEN security.management_actor() THEN 'operations' ELSE 'self' END
    WHERE id=(item->>'target_id')::uuid RETURNING * INTO target;
  ELSE SELECT * INTO target FROM crm.sales_target WHERE id=(item->>'target_id')::uuid;
  END IF;
  effective_items=effective_items||jsonb_build_array(to_jsonb(target));
 END LOOP;
 RETURN jsonb_build_object('status','effective','items',effective_items,'request',NULL);
END $$;

CREATE FUNCTION security.review_target_batch(p_request uuid,p_decision text,p_reason text) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE req crm.sales_target_batch_request; target crm.sales_target; item jsonb; effective_items jsonb:='[]'::jsonb;
BEGIN
 IF NOT security.management_actor() THEN RAISE insufficient_privilege; END IF;
 IF p_decision IS NULL OR p_decision NOT IN ('approved','rejected') OR length(COALESCE(p_reason,''))>2000
  OR (p_decision='rejected' AND NULLIF(btrim(p_reason),'') IS NULL)
 THEN RAISE EXCEPTION '审批结论无效，驳回必须填写原因' USING ERRCODE='22023'; END IF;
 SELECT * INTO req FROM crm.sales_target_batch_request WHERE id=p_request AND workspace_id=common.current_workspace_id();
 IF req.id IS NULL THEN RAISE no_data_found; END IF;
 PERFORM security.lock_target_period(req.scope_type,req.user_ref_id,req.team_id,req.department_code,req.period_type,req.period_start);
 SELECT * INTO req FROM crm.sales_target_batch_request WHERE id=p_request AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF req.status<>'pending' THEN RAISE EXCEPTION '申请已处理，请刷新' USING ERRCODE='P0001'; END IF;
 IF p_decision='approved' THEN
  IF NOT security.target_scope_read(req.scope_type,req.user_ref_id,req.team_id,req.department_code)
   OR NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
    WHERE u.id=req.applicant_user_ref_id AND u.workspace_id=req.workspace_id AND u.status='active' AND u.deleted_at IS NULL
     AND b.role_code IN ('sales','supervisor','manager','fde','fde_lead') AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to)
  THEN RAISE EXCEPTION '申请对象或申请人已停用，不能批准' USING ERRCODE='P0001'; END IF;
  -- Validate every baseline before applying any item; null baseline means still absent.
  FOR item IN SELECT value FROM jsonb_array_elements(req.items) ORDER BY value->>'kind' LOOP
   SELECT * INTO target FROM crm.sales_target WHERE workspace_id=req.workspace_id AND scope_type=req.scope_type
    AND user_ref_id IS NOT DISTINCT FROM req.user_ref_id AND team_id IS NOT DISTINCT FROM req.team_id
    AND department_code=req.department_code AND period_type=req.period_type AND period_start=req.period_start AND kind=item->>'kind' FOR UPDATE;
   IF target.id IS DISTINCT FROM (item->>'target_id')::uuid OR target.version_no IS DISTINCT FROM (item->>'base_version')::integer
   THEN RAISE EXCEPTION '生效目标已变化，请驳回后重新申请' USING ERRCODE='P0001'; END IF;
  END LOOP;
  FOR item IN SELECT value FROM jsonb_array_elements(req.items) ORDER BY value->>'kind' LOOP
   IF item->>'target_id' IS NULL THEN
    INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,team_id,department_code,kind,amount,
     updated_by_user_ref_id,period_type,period_start,period_end,change_reason,source)
    VALUES(req.workspace_id,extract(year FROM req.period_start)::integer,req.scope_type,req.user_ref_id,req.team_id,req.department_code,
     item->>'kind',(item->>'proposed_amount')::numeric,common.current_user_ref_id(),req.period_type,req.period_start,req.period_end,req.reason,'approved');
   ELSIF item->'previous_amount'<>item->'proposed_amount' THEN
    UPDATE crm.sales_target SET amount=(item->>'proposed_amount')::numeric,version_no=version_no+1,updated_at=clock_timestamp(),
     updated_by_user_ref_id=common.current_user_ref_id(),change_reason=req.reason,source='approved' WHERE id=(item->>'target_id')::uuid;
   END IF;
  END LOOP;
 END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.kind),'[]'::jsonb) INTO effective_items FROM crm.sales_target t
  WHERE t.workspace_id=req.workspace_id AND t.scope_type=req.scope_type AND t.user_ref_id IS NOT DISTINCT FROM req.user_ref_id
   AND t.team_id IS NOT DISTINCT FROM req.team_id AND t.department_code=req.department_code AND t.period_type=req.period_type
   AND t.period_start=req.period_start AND EXISTS(SELECT 1 FROM jsonb_array_elements(req.items) i WHERE i->>'kind'=t.kind);
 UPDATE crm.sales_target_batch_request SET status=p_decision,reviewed_at=clock_timestamp(),
  reviewer_user_ref_id=common.current_user_ref_id(),decision_reason=COALESCE(btrim(p_reason),'') WHERE id=req.id RETURNING * INTO req;
 RETURN jsonb_build_object('status',p_decision,'items',effective_items,'request',to_jsonb(req));
END $$;

-- The old writer has no reason field or batch semantics. Existing HTTP callers
-- are migrated to save_target_batch; direct old calls must fail clearly.
CREATE OR REPLACE FUNCTION security.save_period_target(p_scope text,p_user uuid,p_team uuid,p_period text,p_start date,p_end date,
 p_kind text,p_amount numeric,p_version integer DEFAULT NULL) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 RAISE EXCEPTION '请使用新版目标表单，填写设置或调整原因后整单提交' USING ERRCODE='22023';
END $$;

-- Legacy applications can still be reviewed, with the same complete-period
-- serialization and overlap guard. New requests never enter the legacy table.
CREATE OR REPLACE FUNCTION security.review_target_change(p_request uuid,p_decision text,p_reason text) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE req crm.sales_target_change_request; target crm.sales_target;
BEGIN
 IF NOT security.management_actor() THEN RAISE insufficient_privilege; END IF;
 IF p_decision IS NULL OR p_decision NOT IN ('approved','rejected') OR length(COALESCE(p_reason,''))>2000
  OR (p_decision='rejected' AND NULLIF(btrim(p_reason),'') IS NULL)
 THEN RAISE EXCEPTION '驳回必须填写原因' USING ERRCODE='22023'; END IF;
 SELECT t.* INTO target FROM crm.sales_target t JOIN crm.sales_target_change_request r ON r.target_id=t.id
  WHERE r.id=p_request AND t.workspace_id=common.current_workspace_id() AND r.workspace_id=t.workspace_id;
 IF target.id IS NULL THEN RAISE no_data_found; END IF;
 PERFORM security.lock_target_period(target.scope_type,target.user_ref_id,target.team_id,target.department_code,target.period_type,target.period_start);
 SELECT * INTO target FROM crm.sales_target WHERE id=target.id FOR UPDATE;
 SELECT * INTO req FROM crm.sales_target_change_request WHERE id=p_request AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF req.status<>'pending' THEN RAISE EXCEPTION '申请已处理，请刷新' USING ERRCODE='P0001'; END IF;
 IF p_decision='approved' THEN
  IF target.version_no<>req.base_version THEN RAISE EXCEPTION '生效目标已变化，请驳回后重新申请' USING ERRCODE='P0001'; END IF;
  IF NOT security.target_scope_read(target.scope_type,target.user_ref_id,target.team_id,target.department_code)
   OR NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=req.applicant_user_ref_id AND u.workspace_id=req.workspace_id
    AND u.status='active' AND u.deleted_at IS NULL)
  THEN RAISE EXCEPTION '申请对象或申请人已停用，不能批准' USING ERRCODE='P0001'; END IF;
  IF EXISTS(SELECT 1 FROM crm.sales_target_batch_request r WHERE r.workspace_id=target.workspace_id
   AND r.scope_type=target.scope_type AND r.user_ref_id IS NOT DISTINCT FROM target.user_ref_id AND r.team_id IS NOT DISTINCT FROM target.team_id
   AND r.department_code=target.department_code AND r.period_type=target.period_type AND r.period_start=target.period_start AND r.status='pending'
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(r.items) i WHERE i->>'kind'=target.kind))
  THEN RAISE EXCEPTION '存在新版整单申请，请先处理冲突申请' USING ERRCODE='P0001'; END IF;
  UPDATE crm.sales_target SET amount=req.proposed_amount,updated_by_user_ref_id=common.current_user_ref_id(),
   updated_at=clock_timestamp(),version_no=version_no+1,change_reason='历史单指标申请获批'||CASE WHEN NULLIF(btrim(p_reason),'') IS NULL THEN '' ELSE '：'||btrim(p_reason) END,
   source='approved' WHERE id=target.id RETURNING * INTO target;
 END IF;
 UPDATE crm.sales_target_change_request SET status=p_decision,reviewer_user_ref_id=common.current_user_ref_id(),
  reviewed_at=clock_timestamp(),decision_reason=COALESCE(btrim(p_reason),'') WHERE id=req.id RETURNING * INTO req;
 RETURN jsonb_build_object('status',p_decision,'target',to_jsonb(target),'request',to_jsonb(req));
END $$;

-- Preserve the prior grant reconciler, including grants unrelated to targets.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v084;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE permission record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v084();
 FOR permission IN SELECT DISTINCT r.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
  WHERE n.nspname='crm' AND c.relname='sales_target' AND a.grantee<>c.relowner AND a.privilege_type='SELECT'
 LOOP
  EXECUTE format('GRANT SELECT ON crm.sales_target_batch_request TO %I',permission.rolname);
  reconciled:=reconciled+1;
 END LOOP;
 RETURN reconciled;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM PUBLIC;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V091','季度多指标目标整单审批及业务范围隔离');
COMMIT;
