-- Explicit zero is an effective target; missing rows remain unconfigured.
-- Preserve subject authorization, optimistic versions, approval and audit flow.
ALTER TABLE crm.sales_target DROP CONSTRAINT sales_target_amount_check;
ALTER TABLE crm.sales_target ADD CONSTRAINT sales_target_amount_check CHECK(amount>=0);

CREATE OR REPLACE FUNCTION security.save_target_batch(p_scope text, p_user uuid, p_team uuid, p_period text, p_start date, p_end date, p_items jsonb, p_reason text, p_department_code text DEFAULT 'sales'::text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
 IF NOT security.authorization_subject('target.manage',p_scope,p_user,p_team) AND (p_period<>'quarter' OR
  p_start<>date_trunc('quarter',timezone('Asia/Shanghai',clock_timestamp()))::date) THEN
  RAISE EXCEPTION '本人只能填写或申请变更当前季度目标' USING ERRCODE='22023'; END IF;
 PERFORM security.lock_target_period(p_scope,p_user,p_team,p_department_code,p_period,p_start);
 FOR item IN SELECT value FROM jsonb_array_elements(p_items) ORDER BY value->>'kind' LOOP
  IF jsonb_typeof(item)<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(item) k WHERE k NOT IN ('kind','amount','version_no'))
   OR item->>'kind' IS NULL OR item->>'kind' NOT IN ('collection','collection_ppl','recognized','acv','opportunity_count','visit_count','demo_count')
   OR item->>'kind'=ANY(kinds) OR jsonb_typeof(item->'amount') NOT IN ('number','string')
   OR item->>'amount' IS NULL OR length(item->>'amount')>32 OR (item->>'amount')!~'^[0-9]+([.][0-9]{1,2})?$'
   OR ((item->'version_no') IS NOT NULL AND item->'version_no'<>'null'::jsonb AND
    (jsonb_typeof(item->'version_no')<>'number' OR (item->>'version_no')!~'^[1-9][0-9]*$'))
  THEN RAISE EXCEPTION '目标指标、金额或版本格式无效，请刷新表单' USING ERRCODE='22023'; END IF;
  IF item->>'version_no' IS NOT NULL AND (length(item->>'version_no')>10 OR
   (length(item->>'version_no')=10 AND (item->>'version_no')>'2147483647')) THEN
   RAISE EXCEPTION '目标版本超出范围，请刷新表单' USING ERRCODE='22023'; END IF;
  item_amount=(item->>'amount')::numeric; item_version=(item->>'version_no')::integer;
  IF item_amount<0 OR item_amount>=10000000000000000 OR item_amount<>round(item_amount,2)
   OR (item->>'kind' IN ('opportunity_count','visit_count','demo_count') AND item_amount<>trunc(item_amount))
  THEN RAISE EXCEPTION '目标金额须为非负数且最多两位小数，数量须为非负整数' USING ERRCODE='22023'; END IF;
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
  IF security.authorization_subject('target.manage',p_scope,p_user,p_team) THEN
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
  IF NOT security.authorization_subject('target.manage',p_scope,p_user,p_team) THEN RAISE EXCEPTION '已有待审批的历史目标申请，请等待运营处理' USING ERRCODE='P0001'; END IF;
  UPDATE crm.sales_target_change_request r SET status='cancelled',reviewed_at=clock_timestamp(),
   reviewer_user_ref_id=common.current_user_ref_id(),decision_reason='目标已由运营调整，请核对后重新申请'
   FROM crm.sales_target t WHERE t.id=r.target_id AND t.workspace_id=common.current_workspace_id()
    AND t.scope_type=p_scope AND t.user_ref_id IS NOT DISTINCT FROM p_user AND t.team_id IS NOT DISTINCT FROM p_team
    AND t.department_code=p_department_code AND t.period_type=p_period AND t.period_start=p_start AND t.kind=ANY(kinds) AND r.status='pending';
 END IF;
 IF requires_review AND NOT security.authorization_subject('target.manage',p_scope,p_user,p_team) THEN
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
    CASE WHEN security.authorization_subject('target.manage',p_scope,p_user,p_team) THEN 'operations' ELSE 'self' END) RETURNING * INTO target;
  ELSIF item->'previous_amount'<>item->'proposed_amount' THEN
   UPDATE crm.sales_target SET amount=(item->>'proposed_amount')::numeric,updated_by_user_ref_id=common.current_user_ref_id(),
    version_no=version_no+1,updated_at=clock_timestamp(),change_reason=normalized_reason,
    source=CASE WHEN security.authorization_subject('target.manage',p_scope,p_user,p_team) THEN 'operations' ELSE 'self' END
    WHERE id=(item->>'target_id')::uuid RETURNING * INTO target;
  ELSE SELECT * INTO target FROM crm.sales_target WHERE id=(item->>'target_id')::uuid;
  END IF;
  effective_items=effective_items||jsonb_build_array(to_jsonb(target));
 END LOOP;
 RETURN jsonb_build_object('status','effective','items',effective_items,'request',NULL);
END $function$
;


INSERT INTO ops.schema_migration(version,description) VALUES('V403','Explicit zero targets preserve approval and scope rules');
