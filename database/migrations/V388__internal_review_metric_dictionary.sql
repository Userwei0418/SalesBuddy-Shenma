-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V240__review_metric_dictionary.sql.
-- Original SHA-256: 58f4de04d5da4fb455bdbcb8d2e231079714eee5e044e1aae3b48725d084e4c2. See shenma_whole_121_manifest.json.
BEGIN;
-- SB-REVIEW-20261014: target money remains exclusively in crm.sales_target.
CREATE TABLE crm.review_metric (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 code text NOT NULL CHECK(code ~ '^[a-z][a-z0-9_]{0,63}$'),
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 80),
 unit text NOT NULL CHECK(length(btrim(unit)) BETWEEN 1 AND 20),
 decimal_places integer NOT NULL CHECK(decimal_places IN (0,2)),
 definition text NOT NULL CHECK(length(btrim(definition)) BETWEEN 1 AND 2000),
 source_kind text NOT NULL CHECK(source_kind IN ('actual','forecast','manual','category')),
 definition_version integer NOT NULL DEFAULT 1 CHECK(definition_version>0),
 active boolean NOT NULL DEFAULT true,
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 target_kind text,
 goal_selectable boolean NOT NULL DEFAULT true,
 system_defined boolean NOT NULL DEFAULT false,
 updated_by_user_ref_id uuid,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,code),
 FOREIGN KEY(updated_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 CHECK(NOT goal_selectable OR target_kind=code)
);
CREATE TABLE crm.review_metric_history (
 workspace_id uuid NOT NULL, code text NOT NULL, version_no integer NOT NULL,
 snapshot jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,code,version_no),
 FOREIGN KEY(workspace_id,code) REFERENCES crm.review_metric(workspace_id,code)
);
ALTER TABLE crm.review_metric ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.review_metric FORCE ROW LEVEL SECURITY;
ALTER TABLE crm.review_metric_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.review_metric_history FORCE ROW LEVEL SECURITY;
CREATE POLICY review_metric_tenant ON crm.review_metric FOR SELECT
 USING(workspace_id=common.current_workspace_id());
CREATE POLICY review_metric_history_tenant ON crm.review_metric_history FOR SELECT
 USING(workspace_id=common.current_workspace_id());

CREATE FUNCTION security.seed_review_metrics(p_workspace uuid) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 INSERT INTO crm.review_metric(workspace_id,code,name,unit,decimal_places,definition,source_kind,
  target_kind,goal_selectable,system_defined)
 SELECT p_workspace,code,name,unit,precision,definition,source,kind,selectable,true FROM (VALUES
 ('collection','回款','元',2,'已确认实际回款；不包含销售预测及任务贡献。','actual','collection',true),
 ('recognized','确收','元',2,'已确认实际确收；运营实际口径不含税，不自动换算销售预测。','actual','recognized',true),
 ('acv','ACV','元',2,'沿用既有ACV目标；口径未经确认时不自动填入预测或实际值。','manual','acv',true),
 ('opportunity_count','商机数','个',0,'指定周期内权限可见的新增商机数量；以正式创建记录为准。','actual','opportunity_count',true),
 ('visit_count','跟进数','次',0,'指定周期内正式归档的跟进数量，不计草稿。','actual','visit_count',true),
 ('demo_count','演示数','次',0,'沿用既有演示数量目标；无明确证据的进展须人工确认。','manual','demo_count',true),
 ('daily_routine','日常事务','项',0,'日常任务类别，不构造财务目标或业绩。','category',NULL,false),
 ('forecast_collection','预测回款','元',2,'该季度销售预测collection_amount且collection_confidence=high；非概率加权。','forecast','collection',false),
 ('collection_ppl','回款PPL储备','元',2,'该季度销售预测collection_amount且赢单概率>=30、非丢单，直接求和不加权；目标独立。','forecast','collection_ppl',true),
 ('forecast_recognized','预测确收','元',2,'该季度销售预测recognized_amount且赢单概率>=70、非丢单；预测与目标均含税，实际确收独立且不隐式换税。','forecast','forecast_recognized',true)
 ) AS d(code,name,unit,precision,definition,source,kind,selectable)
 ON CONFLICT(workspace_id,code) DO NOTHING;
 INSERT INTO crm.review_metric_history(workspace_id,code,version_no,snapshot)
 SELECT workspace_id,code,version_no,to_jsonb(m) FROM crm.review_metric m WHERE workspace_id=p_workspace
 ON CONFLICT DO NOTHING;
$$;
REVOKE ALL ON FUNCTION security.seed_review_metrics(uuid) FROM PUBLIC;
SELECT security.seed_review_metrics(id) FROM platform.workspace;
CREATE FUNCTION security.workspace_review_metric_seed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN PERFORM security.seed_review_metrics(NEW.id); RETURN NEW; END $$;
REVOKE ALL ON FUNCTION security.workspace_review_metric_seed() FROM PUBLIC;
CREATE TRIGGER workspace_review_metric_seed AFTER INSERT ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION security.workspace_review_metric_seed();

CREATE FUNCTION security.save_review_metric(p_code text,p_body jsonb,p_version integer DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row crm.review_metric;
BEGIN
 IF NOT security.authorization_subject('target.manage','department',NULL,NULL) THEN RAISE insufficient_privilege; END IF;
 IF p_code IS NULL OR p_code !~ '^[a-z][a-z0-9_]{0,63}$' OR p_body IS NULL OR jsonb_typeof(p_body)<>'object'
 THEN RAISE EXCEPTION '指标格式无效' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('review-metric:'||common.current_workspace_id()::text||':'||p_code,0));
 SELECT * INTO row FROM crm.review_metric WHERE workspace_id=common.current_workspace_id() AND code=p_code FOR UPDATE;
 IF p_version IS NULL THEN
  IF row.code IS NOT NULL THEN RAISE EXCEPTION '指标已存在，请刷新' USING ERRCODE='P0001'; END IF;
  IF p_code !~ '^custom_[a-z0-9_]+$' OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE k NOT IN ('name','unit','decimal_places','definition','active'))
   OR jsonb_typeof(p_body->'name') IS DISTINCT FROM 'string' OR jsonb_typeof(p_body->'unit') IS DISTINCT FROM 'string'
   OR jsonb_typeof(p_body->'definition') IS DISTINCT FROM 'string' OR (p_body->>'decimal_places') NOT IN ('0','2')
   OR (p_body ? 'active' AND jsonb_typeof(p_body->'active') IS DISTINCT FROM 'boolean')
  THEN RAISE EXCEPTION '自定义指标须有名称、单位、精度和口径' USING ERRCODE='22023'; END IF;
  INSERT INTO crm.review_metric(workspace_id,code,name,unit,decimal_places,definition,source_kind,target_kind,updated_by_user_ref_id,active)
  VALUES(common.current_workspace_id(),p_code,btrim(p_body->>'name'),btrim(p_body->>'unit'),(p_body->>'decimal_places')::integer,
   btrim(p_body->>'definition'),'manual',p_code,common.current_user_ref_id(),COALESCE((p_body->>'active')::boolean,true)) RETURNING * INTO row;
 ELSE
  IF row.code IS NULL THEN RAISE no_data_found; END IF;
  IF p_version<1 OR row.version_no<>p_version THEN RAISE EXCEPTION '指标已更新，请刷新' USING ERRCODE='P0001'; END IF;
  IF p_body IS NULL OR jsonb_typeof(p_body->'active') IS DISTINCT FROM 'boolean'
   OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE k<>'active')
  THEN RAISE EXCEPTION '本接口仅修改指标启停状态' USING ERRCODE='22023'; END IF;
  UPDATE crm.review_metric SET active=(p_body->>'active')::boolean,version_no=version_no+1,
   updated_at=clock_timestamp(),updated_by_user_ref_id=common.current_user_ref_id()
   WHERE workspace_id=row.workspace_id AND code=row.code RETURNING * INTO row;
 END IF;
 INSERT INTO crm.review_metric_history(workspace_id,code,version_no,snapshot)
 VALUES(row.workspace_id,row.code,row.version_no,to_jsonb(row));
 RETURN to_jsonb(row);
END $$;
REVOKE ALL ON FUNCTION security.save_review_metric(text,jsonb,integer) FROM PUBLIC;

ALTER TABLE crm.sales_target DROP CONSTRAINT sales_target_kind_check;
ALTER TABLE crm.sales_target ADD CONSTRAINT sales_target_metric_fk
 FOREIGN KEY(workspace_id,kind) REFERENCES crm.review_metric(workspace_id,code);
-- Keep original amount>0 and unique business identity. No second monetary table.
CREATE FUNCTION security.target_metric_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE m crm.review_metric;
BEGIN
 SELECT * INTO m FROM crm.review_metric WHERE workspace_id=NEW.workspace_id AND code=NEW.kind FOR SHARE;
 IF m.code IS NULL OR NOT m.goal_selectable OR NOT m.active OR (m.decimal_places=0 AND NEW.amount<>trunc(NEW.amount))
 THEN RAISE EXCEPTION '指标不可设目标、已停用或数量精度无效' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION security.target_metric_guard() FROM PUBLIC;
CREATE TRIGGER target_metric_guard BEFORE INSERT OR UPDATE OF amount,kind ON crm.sales_target
 FOR EACH ROW EXECUTE FUNCTION security.target_metric_guard();

-- Existing V133 approval/version/period body; only metric lookup and precision generalized.
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
  PERFORM 1 FROM crm.review_metric WHERE workspace_id=common.current_workspace_id() AND code=item->>'kind' FOR SHARE;
  IF jsonb_typeof(item)<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(item) k WHERE k NOT IN ('kind','amount','version_no'))
   OR item->>'kind' IS NULL OR NOT EXISTS(SELECT 1 FROM crm.review_metric m WHERE m.workspace_id=common.current_workspace_id() AND m.code=item->>'kind' AND m.goal_selectable AND m.active)
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
   OR (EXISTS(SELECT 1 FROM crm.review_metric m WHERE m.workspace_id=common.current_workspace_id() AND m.code=item->>'kind' AND m.decimal_places=0) AND item_amount<>trunc(item_amount))
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

CREATE FUNCTION security.review_target_money_json(p_value jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result jsonb; k text; v jsonb;
BEGIN
 IF jsonb_typeof(p_value)='object' THEN
  result:='{}'::jsonb;
  FOR k,v IN SELECT key,value FROM jsonb_each(p_value) LOOP
   result:=result||jsonb_build_object(k,CASE WHEN k IN ('amount','previous_amount','proposed_amount','effective_amount')
    AND v<>'null'::jsonb THEN to_jsonb(v#>>'{}') ELSE security.review_target_money_json(v) END);
  END LOOP;
  RETURN result;
 ELSIF jsonb_typeof(p_value)='array' THEN
  SELECT COALESCE(jsonb_agg(security.review_target_money_json(value) ORDER BY ord),'[]'::jsonb) INTO result
   FROM jsonb_array_elements(p_value) WITH ORDINALITY a(value,ord);
  RETURN result;
 END IF;
 RETURN p_value;
END $$;
REVOKE ALL ON FUNCTION security.review_target_money_json(jsonb) FROM PUBLIC;

-- Exact API-only grants, including late-created limited runtime roles.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_review_metrics;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record; is_api boolean;
BEGIN
 total:=security.reconcile_runtime_grants_before_review_metrics();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE') LOOP
  is_api:=has_function_privilege(r.rolname,'security.save_account_authorization(uuid,jsonb)','EXECUTE');
  EXECUTE format('REVOKE ALL ON crm.review_metric,crm.review_metric_history FROM %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.save_review_metric(text,jsonb,integer),security.review_target_money_json(jsonb),security.seed_review_metrics(uuid),security.workspace_review_metric_seed(),security.target_metric_guard(),security.reconcile_runtime_grants_before_review_metrics(),security.reconcile_runtime_grants() FROM %I',r.rolname);
  IF is_api THEN
   EXECUTE format('GRANT SELECT ON crm.review_metric TO %I',r.rolname);
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.save_review_metric(text,jsonb,integer),security.review_target_money_json(jsonb) TO %I',r.rolname);
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_review_metrics() FROM PUBLIC;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V388','Review metric dictionary and shared approved target identity');
COMMIT;
