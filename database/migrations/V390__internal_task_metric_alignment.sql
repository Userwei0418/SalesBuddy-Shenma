-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V242__task_metric_alignment.sql.
-- Original SHA-256: 431e9e5d89ff3d854c58f36e11bf14111142c35dbcf1b010db5719f981d01221. See shenma_whole_121_manifest.json.
BEGIN;

-- Relations only: the existing task lifecycle, targets and financial records stay authoritative.
CREATE TABLE workflow.task_alignment (
 task_id uuid PRIMARY KEY, workspace_id uuid NOT NULL,
 expected_contribution jsonb, actual_contribution jsonb,
 updated_by_user_ref_id uuid NOT NULL, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(task_id,workspace_id),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task(id,workspace_id),
 FOREIGN KEY(updated_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE TABLE workflow.task_metric_alignment (
 task_id uuid NOT NULL, workspace_id uuid NOT NULL, metric_code text NOT NULL, direct boolean NOT NULL,
 PRIMARY KEY(task_id,metric_code),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task_alignment(task_id,workspace_id),
 FOREIGN KEY(workspace_id,metric_code) REFERENCES crm.review_metric(workspace_id,code)
);
CREATE TABLE workflow.task_key_result_alignment (
 task_id uuid NOT NULL, workspace_id uuid NOT NULL, key_result_id uuid NOT NULL,
 PRIMARY KEY(task_id,key_result_id),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task_alignment(task_id,workspace_id),
 FOREIGN KEY(key_result_id,workspace_id) REFERENCES crm.review_key_result(id,workspace_id)
);
CREATE INDEX task_key_result_alignment_kr ON workflow.task_key_result_alignment(workspace_id,key_result_id,task_id);
CREATE INDEX task_metric_alignment_metric ON workflow.task_metric_alignment(workspace_id,metric_code,task_id);
CREATE TABLE workflow.task_alignment_history (
 task_id uuid NOT NULL, workspace_id uuid NOT NULL, task_version integer NOT NULL,
 event_kind text NOT NULL CHECK(event_kind IN ('alignment','reported_contribution')),
 snapshot jsonb NOT NULL, actor_user_ref_id uuid NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(task_id,task_version,event_kind),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task_alignment(task_id,workspace_id),
 FOREIGN KEY(actor_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['task_alignment','task_metric_alignment','task_key_result_alignment','task_alignment_history'] LOOP
  EXECUTE format('ALTER TABLE workflow.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE workflow.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY task_alignment_read ON workflow.%I FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.authorization_task(''task.read'',task_id))',t);
 END LOOP;
END $$;
-- Task visibility never confers access to the referenced KR (including raw link/history tables).
ALTER POLICY task_alignment_read ON workflow.task_key_result_alignment USING(
 workspace_id=common.current_workspace_id() AND security.authorization_task('task.read',task_id)
 AND security.review_key_result_readable(key_result_id));
DROP POLICY task_alignment_read ON workflow.task_alignment_history;

CREATE FUNCTION security.task_alignment_read(p_task uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object(
  'metric_codes',COALESCE((SELECT jsonb_agg(metric_code ORDER BY metric_code) FROM workflow.task_metric_alignment m WHERE m.task_id=a.task_id),'[]'::jsonb),
  'direct_metric_codes',COALESCE((SELECT jsonb_agg(metric_code ORDER BY metric_code) FROM workflow.task_metric_alignment m WHERE m.task_id=a.task_id AND m.direct),'[]'::jsonb),
  'key_result_ids',COALESCE((SELECT jsonb_agg(key_result_id ORDER BY key_result_id) FROM workflow.task_key_result_alignment k WHERE k.task_id=a.task_id AND security.review_key_result_readable(key_result_id)),'[]'::jsonb),
  'restricted_key_result_count',(SELECT count(*) FROM workflow.task_key_result_alignment k WHERE k.task_id=a.task_id AND NOT security.review_key_result_readable(key_result_id)),
  'expected_contribution',a.expected_contribution,'actual_contribution',a.actual_contribution,
  'version_no',t.version_no,'updated_at',a.updated_at,'counts_toward_financial_actuals',false)
 FROM workflow.task_alignment a JOIN workflow.task t ON t.id=a.task_id AND t.workspace_id=a.workspace_id
 WHERE a.task_id=p_task AND a.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
  AND security.authorization_task('task.read',t.id);
$$;

CREATE FUNCTION security.validate_task_contribution(p_task uuid,p_body jsonb,p_actual boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE m crm.review_metric; number numeric; note text;
BEGIN
 IF p_body IS NULL OR p_body='null'::jsonb THEN RETURN NULL; END IF;
 IF jsonb_typeof(p_body)<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE k NOT IN ('metric_code','value','note'))
  OR jsonb_typeof(p_body->'metric_code') IS DISTINCT FROM 'string'
  OR jsonb_typeof(p_body->'value') IS DISTINCT FROM 'string'
  OR (p_body->>'value') !~ '^(0|[1-9][0-9]{0,15})(\.[0-9]{1,2})?$'
  OR (p_body->'note' IS NOT NULL AND p_body->'note'<>'null'::jsonb AND jsonb_typeof(p_body->'note')<>'string')
 THEN RAISE EXCEPTION '贡献声明的指标、数值或说明格式无效' USING ERRCODE='22023'; END IF;
 -- The selected metric must be an actual relation, never a caller-supplied financial field.
 SELECT metric.* INTO m FROM crm.review_metric metric JOIN workflow.task_metric_alignment link
  ON link.workspace_id=metric.workspace_id AND link.metric_code=metric.code
  WHERE link.task_id=p_task AND link.workspace_id=common.current_workspace_id() AND metric.code=p_body->>'metric_code'
  FOR SHARE OF metric;
 IF m.code IS NULL OR NOT m.active THEN RAISE EXCEPTION '贡献指标未关联任务或已停用' USING ERRCODE='22023'; END IF;
 number:=(p_body->>'value')::numeric; note:=NULLIF(btrim(p_body->>'note'),'');
 IF (m.decimal_places=0 AND number<>trunc(number)) OR (p_actual AND note IS NULL) OR length(note)>2000
 THEN RAISE EXCEPTION '贡献数量精度或实际贡献说明无效' USING ERRCODE='22023'; END IF;
 RETURN jsonb_build_object('metric_code',m.code,'value',number::text,'unit',m.unit,
  'definition_version',m.definition_version,'note',note,'status',CASE WHEN p_actual THEN 'reported' ELSE 'planned' END,
  'counts_toward_financial_actuals',false);
END $$;

CREATE FUNCTION security.save_task_alignment(p_task uuid,p_body jsonb,p_expected_version integer DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; is_new boolean; codes text[]; ids uuid[]; v_metric_code text; kr uuid; contribution jsonb; saved jsonb;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF t.id IS NULL THEN RAISE no_data_found; END IF;
 is_new:=security.authorization_task_created(t.id) AND NOT EXISTS(SELECT 1 FROM workflow.task_alignment WHERE task_id=t.id);
 IF NOT is_new AND NOT (security.authorization_task('task.read',t.id) AND security.fde_can_coordinate_task(t.id))
 THEN RAISE insufficient_privilege; END IF;
 IF t.status IN ('completed','cancelled','pending_review') THEN RAISE EXCEPTION '任务当前不可修改对齐关系' USING ERRCODE='P0001'; END IF;
 IF NOT is_new AND (p_expected_version IS NULL OR p_expected_version<>t.version_no)
 THEN RAISE EXCEPTION '任务已更新，请刷新后提交' USING ERRCODE='P0001'; END IF;
 IF is_new AND p_expected_version IS NOT NULL AND p_expected_version<>t.version_no
 THEN RAISE EXCEPTION '任务版本不一致' USING ERRCODE='P0001'; END IF;
 IF p_body IS NULL OR jsonb_typeof(p_body)<>'object'
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE k NOT IN ('metric_codes','key_result_ids','expected_contribution'))
  OR jsonb_typeof(p_body->'metric_codes') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'metric_codes')>20
  OR jsonb_typeof(p_body->'key_result_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'key_result_ids')>20
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'metric_codes') x WHERE jsonb_typeof(x)<>'string')
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'key_result_ids') x WHERE jsonb_typeof(x)<>'string')
 THEN RAISE EXCEPTION '任务指标和 KR 格式无效' USING ERRCODE='22023'; END IF;
 SELECT COALESCE(array_agg(DISTINCT value ORDER BY value),'{}'::text[]) INTO codes FROM jsonb_array_elements_text(p_body->'metric_codes');
 SELECT COALESCE(array_agg(DISTINCT value::uuid ORDER BY value::uuid),'{}'::uuid[]) INTO ids FROM jsonb_array_elements_text(p_body->'key_result_ids');
 IF cardinality(codes)+cardinality(ids)=0 THEN RAISE EXCEPTION '任务至少选择一个指标或类别' USING ERRCODE='22023'; END IF;
 -- An editor cannot unknowingly drop a hidden link while replacing the visible set.
 IF EXISTS(SELECT 1 FROM workflow.task_key_result_alignment k WHERE k.task_id=t.id AND NOT security.review_key_result_readable(k.key_result_id))
 THEN RAISE insufficient_privilege; END IF;
 FOREACH kr IN ARRAY ids LOOP
  PERFORM 1 FROM crm.review_key_result k JOIN crm.review_objective o ON o.id=k.objective_id AND o.workspace_id=k.workspace_id
   WHERE k.id=kr AND k.workspace_id=t.workspace_id FOR SHARE OF k,o;
  IF NOT FOUND OR NOT security.review_key_result_readable(kr) THEN RAISE insufficient_privilege; END IF;
 END LOOP;
 FOR v_metric_code IN SELECT unnest(codes) UNION SELECT k.metric_code FROM crm.review_key_result k WHERE k.id=ANY(ids) AND k.workspace_id=t.workspace_id LOOP
  PERFORM 1 FROM crm.review_metric m WHERE m.workspace_id=t.workspace_id AND m.code=v_metric_code AND m.active FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION '任务指标不存在或已停用' USING ERRCODE='22023'; END IF;
 END LOOP;
 INSERT INTO workflow.task_alignment(task_id,workspace_id,updated_by_user_ref_id)
 VALUES(t.id,t.workspace_id,common.current_user_ref_id()) ON CONFLICT(task_id) DO NOTHING;
 DELETE FROM workflow.task_metric_alignment WHERE task_id=t.id;
 DELETE FROM workflow.task_key_result_alignment WHERE task_id=t.id;
 INSERT INTO workflow.task_key_result_alignment(task_id,workspace_id,key_result_id) SELECT t.id,t.workspace_id,unnest(ids);
 INSERT INTO workflow.task_metric_alignment(task_id,workspace_id,metric_code,direct)
 SELECT t.id,t.workspace_id,c,c=ANY(codes) FROM (
  SELECT unnest(codes) c UNION SELECT k.metric_code FROM crm.review_key_result k WHERE k.id=ANY(ids) AND k.workspace_id=t.workspace_id
 ) all_codes;
 contribution:=security.validate_task_contribution(t.id,p_body->'expected_contribution',false);
 -- A previously reported claim cannot be reassigned to another metric by editing links.
 IF EXISTS(SELECT 1 FROM workflow.task_alignment a WHERE a.task_id=t.id AND a.actual_contribution IS NOT NULL
   AND NOT EXISTS(SELECT 1 FROM workflow.task_metric_alignment m WHERE m.task_id=t.id AND m.metric_code=a.actual_contribution->>'metric_code'))
 THEN RAISE EXCEPTION '已报告贡献所属指标须保留' USING ERRCODE='P0001'; END IF;
 UPDATE workflow.task_alignment SET expected_contribution=contribution,updated_by_user_ref_id=common.current_user_ref_id(),updated_at=clock_timestamp() WHERE task_id=t.id;
 IF NOT is_new THEN UPDATE workflow.task SET version_no=version_no+1 WHERE id=t.id RETURNING * INTO t; END IF;
 saved:=security.task_alignment_read(t.id);
 INSERT INTO workflow.task_alignment_history(task_id,workspace_id,task_version,event_kind,snapshot,actor_user_ref_id)
 VALUES(t.id,t.workspace_id,t.version_no,'alignment',saved,common.current_user_ref_id());
 RETURN saved;
END $$;

CREATE FUNCTION security.record_task_contribution(p_task uuid,p_body jsonb,p_expected_version integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; claim jsonb;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF t.id IS NULL THEN RAISE no_data_found; END IF;
 IF NOT security.authorization_task('task.complete',t.id) OR NOT EXISTS(
  SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.assignee_user_ref_id=common.current_user_ref_id()
   AND a.responsibility='owner' AND security.task_recipient_eligible(a.task_id,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
 THEN RAISE insufficient_privilege; END IF;
 IF p_expected_version IS NULL OR p_expected_version<>t.version_no OR t.status NOT IN ('pending_execution','in_progress')
 THEN RAISE EXCEPTION '任务版本或完成状态已变化' USING ERRCODE='P0001'; END IF;
 IF p_body IS NULL OR p_body='null'::jsonb THEN RAISE EXCEPTION '实际贡献声明不能为空' USING ERRCODE='22023'; END IF;
 claim:=security.validate_task_contribution(t.id,p_body,true)||jsonb_build_object(
  'reported_by_user_id',common.current_user_ref_id(),'reported_at',clock_timestamp(),'task_version',t.version_no+1);
 UPDATE workflow.task_alignment SET actual_contribution=claim,updated_by_user_ref_id=common.current_user_ref_id(),updated_at=clock_timestamp() WHERE task_id=t.id;
 IF NOT FOUND THEN RAISE EXCEPTION '请先设置任务指标再报告贡献' USING ERRCODE='22023'; END IF;
 INSERT INTO workflow.task_alignment_history(task_id,workspace_id,task_version,event_kind,snapshot,actor_user_ref_id)
 VALUES(t.id,t.workspace_id,t.version_no+1,'reported_contribution',claim,common.current_user_ref_id());
 RETURN claim;
END $$;

REVOKE ALL ON FUNCTION security.task_alignment_read(uuid),security.validate_task_contribution(uuid,jsonb,boolean),
 security.save_task_alignment(uuid,jsonb,integer),security.record_task_contribution(uuid,jsonb,integer) FROM PUBLIC;
REVOKE ALL ON workflow.task_alignment,workflow.task_metric_alignment,workflow.task_key_result_alignment,workflow.task_alignment_history FROM PUBLIC;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_task_alignment;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record; is_api boolean;
BEGIN
 total:=security.reconcile_runtime_grants_before_task_alignment();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE') LOOP
  is_api:=has_function_privilege(r.rolname,'security.save_account_authorization(uuid,jsonb)','EXECUTE');
  EXECUTE format('REVOKE ALL ON workflow.task_alignment,workflow.task_metric_alignment,workflow.task_key_result_alignment,workflow.task_alignment_history FROM %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.task_alignment_read(uuid),security.validate_task_contribution(uuid,jsonb,boolean),security.save_task_alignment(uuid,jsonb,integer),security.record_task_contribution(uuid,jsonb,integer),security.reconcile_runtime_grants_before_task_alignment(),security.reconcile_runtime_grants() FROM %I',r.rolname);
  IF is_api THEN
   EXECUTE format('GRANT SELECT ON workflow.task_alignment,workflow.task_metric_alignment,workflow.task_key_result_alignment TO %I',r.rolname);
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.task_alignment_read(uuid),security.save_task_alignment(uuid,jsonb,integer),security.record_task_contribution(uuid,jsonb,integer) TO %I',r.rolname);
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_task_alignment() FROM PUBLIC;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V390','Task metric and KR alignment with declared contributions');
COMMIT;
