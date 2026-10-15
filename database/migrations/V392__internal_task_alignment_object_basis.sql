-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V244__task_alignment_object_basis.sql.
-- Original SHA-256: 5f60cca782015a27a78274aff0872a3e92a82cbae2bfcf2f8f01543ffbb7ff42. See shenma_whole_121_manifest.json.
BEGIN;

-- DELTA04 D03. Report work follows in V245/V246; no historical SQL is rewritten.
ALTER TABLE crm.sales_target ADD CONSTRAINT sales_target_id_workspace_key UNIQUE(id,workspace_id);
ALTER TABLE workflow.task_alignment ADD COLUMN contract_version smallint
 CHECK(contract_version IS NULL OR contract_version IN (1,2));
ALTER TABLE workflow.task_alignment ADD COLUMN other_reason text
 CHECK(other_reason IS NULL OR (contract_version IS NOT DISTINCT FROM 2 AND char_length(other_reason) BETWEEN 1 AND 2000
  AND other_reason=btrim(other_reason,U&'\0009\000A\000B\000C\000D\0020\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000\FEFF')));
CREATE TABLE workflow.task_target_alignment (
 task_id uuid NOT NULL,workspace_id uuid NOT NULL,target_id uuid NOT NULL,
 PRIMARY KEY(task_id,target_id),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task_alignment(task_id,workspace_id),
 FOREIGN KEY(target_id,workspace_id) REFERENCES crm.sales_target(id,workspace_id)
);
CREATE INDEX task_target_alignment_target ON workflow.task_target_alignment(workspace_id,target_id,task_id);
ALTER TABLE workflow.task_target_alignment ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.task_target_alignment FORCE ROW LEVEL SECURITY;
CREATE POLICY task_target_alignment_read ON workflow.task_target_alignment FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.authorization_task('task.read',task_id)
 AND EXISTS(SELECT 1 FROM crm.sales_target t WHERE t.id=target_id AND t.workspace_id=task_target_alignment.workspace_id
  AND security.target_scope_read(t.scope_type,t.user_ref_id,t.team_id,t.department_code)));
-- Only the existing three bounded functions access this table. No new runtime ACL.
REVOKE ALL ON workflow.task_target_alignment FROM PUBLIC;

CREATE OR REPLACE FUNCTION security.task_alignment_read(p_task uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH visible_targets AS (
  SELECT t.* FROM workflow.task_target_alignment l JOIN crm.sales_target t ON t.id=l.target_id AND t.workspace_id=l.workspace_id
  WHERE l.task_id=p_task AND l.workspace_id=common.current_workspace_id()
   AND security.target_scope_read(t.scope_type,t.user_ref_id,t.team_id,t.department_code)
 ), visible_krs AS (
  SELECT k.*,o.period_type,o.period_start,o.period_end,o.scope_type,o.user_ref_id,o.team_id,o.department_code
  FROM workflow.task_key_result_alignment l JOIN crm.review_key_result k ON k.id=l.key_result_id AND k.workspace_id=l.workspace_id
  JOIN crm.review_objective o ON o.id=k.objective_id AND o.workspace_id=k.workspace_id
  WHERE l.task_id=p_task AND l.workspace_id=common.current_workspace_id() AND security.review_key_result_readable(k.id)
 )
 SELECT jsonb_build_object(
  'contract_version',COALESCE(a.contract_version,1),
  'basis',CASE WHEN a.contract_version=2 THEN CASE WHEN a.other_reason IS NULL THEN 'objects' ELSE 'other' END ELSE 'legacy' END,
  'other_reason',a.other_reason,
  'target_ids',COALESCE((SELECT jsonb_agg(id ORDER BY id) FROM visible_targets),'[]'::jsonb),
  'targets',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',v.id,'metric_code',v.kind,'version_no',v.version_no,
    'period',jsonb_build_object('type',v.period_type,'start',v.period_start,'end',v.period_end,'timezone','Asia/Shanghai'),
    'scope',jsonb_build_object('scope_type',v.scope_type,'user_id',v.user_ref_id,'team_id',v.team_id,'department_code',v.department_code))
    ORDER BY v.id) FROM visible_targets v),'[]'::jsonb),
  'restricted_target_count',(SELECT count(*) FROM workflow.task_target_alignment l WHERE l.task_id=a.task_id)
    -(SELECT count(*) FROM visible_targets),
  'metric_codes',COALESCE((SELECT jsonb_agg(metric_code ORDER BY metric_code) FROM workflow.task_metric_alignment m WHERE m.task_id=a.task_id),'[]'::jsonb),
  'direct_metric_codes',COALESCE((SELECT jsonb_agg(metric_code ORDER BY metric_code) FROM workflow.task_metric_alignment m WHERE m.task_id=a.task_id AND m.direct),'[]'::jsonb),
  'key_result_ids',COALESCE((SELECT jsonb_agg(id ORDER BY id) FROM visible_krs),'[]'::jsonb),
  'key_results',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',k.id,'objective_id',k.objective_id,
    'metric_code',k.metric_code,'version_no',k.version_no,
    'period',jsonb_build_object('type',k.period_type,'start',k.period_start,'end',k.period_end,'timezone','Asia/Shanghai'),
    'scope',jsonb_build_object('scope_type',k.scope_type,'user_id',k.user_ref_id,'team_id',k.team_id,'department_code',k.department_code))
    ORDER BY k.id) FROM visible_krs k),'[]'::jsonb),
  'restricted_key_result_count',(SELECT count(*) FROM workflow.task_key_result_alignment l WHERE l.task_id=a.task_id)
    -(SELECT count(*) FROM visible_krs),
  'expected_contribution',a.expected_contribution,'actual_contribution',a.actual_contribution,
  'version_no',t.version_no,'updated_at',a.updated_at,'counts_toward_financial_actuals',false)
 FROM workflow.task_alignment a JOIN workflow.task t ON t.id=a.task_id AND t.workspace_id=a.workspace_id
 WHERE a.task_id=p_task AND a.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
  AND security.authorization_task('task.read',t.id);
$$;

CREATE OR REPLACE FUNCTION security.save_task_alignment(p_task uuid,p_body jsonb,p_expected_version integer DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; is_new boolean; codes text[]; ids uuid[]; v_metric_code text; kr uuid; contribution jsonb; saved jsonb; v2 boolean; target_ids uuid[]; reason text;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF t.id IS NULL THEN RAISE no_data_found; END IF;
 is_new:=security.authorization_task_created(t.id) AND NOT EXISTS(SELECT 1 FROM workflow.task_alignment WHERE task_id=t.id);
 IF NOT is_new AND NOT (security.authorization_task('task.read',t.id) AND security.fde_can_coordinate_task(t.id))
 THEN RAISE insufficient_privilege; END IF;
 v2:=COALESCE(p_body->>'contract_version'='2' AND jsonb_typeof(p_body->'contract_version')='number',false);
 IF EXISTS(SELECT 1 FROM workflow.task_alignment WHERE task_id=t.id AND contract_version=2) AND NOT v2
 THEN RAISE EXCEPTION 'alignment_contract_upgrade_required' USING ERRCODE='P0001'; END IF;
 IF t.status IN ('completed','cancelled','pending_review') THEN RAISE EXCEPTION '任务当前不可修改对齐关系' USING ERRCODE='P0001'; END IF;
 IF NOT is_new AND (p_expected_version IS NULL OR p_expected_version<>t.version_no)
 THEN RAISE EXCEPTION '任务已更新，请刷新后提交' USING ERRCODE='P0001'; END IF;
 IF is_new AND p_expected_version IS NOT NULL AND p_expected_version<>t.version_no
 THEN RAISE EXCEPTION '任务版本不一致' USING ERRCODE='P0001'; END IF;
 IF p_body IS NULL OR jsonb_typeof(p_body)<>'object'
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE
   (v2 AND k NOT IN ('contract_version','target_ids','key_result_ids','other_reason','expected_contribution'))
   OR (NOT v2 AND k NOT IN ('metric_codes','key_result_ids','expected_contribution')))
  OR jsonb_typeof(p_body->'key_result_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'key_result_ids')>20
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'key_result_ids') x WHERE jsonb_typeof(x)<>'string')
 THEN RAISE EXCEPTION '任务关联格式无效' USING ERRCODE='22023'; END IF;
 SELECT COALESCE(array_agg(DISTINCT value::uuid ORDER BY value::uuid),'{}'::uuid[]) INTO ids FROM jsonb_array_elements_text(p_body->'key_result_ids');
 codes:='{}'; target_ids:='{}';
 IF v2 THEN
  IF jsonb_typeof(p_body->'target_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'target_ids')>20
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'target_ids') x WHERE jsonb_typeof(x)<>'string')
   OR (p_body->'other_reason' IS NOT NULL AND p_body->'other_reason'<>'null'::jsonb AND jsonb_typeof(p_body->'other_reason')<>'string')
  THEN RAISE EXCEPTION '目标或其他原因格式无效' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(array_agg(DISTINCT value::uuid ORDER BY value::uuid),'{}'::uuid[]) INTO target_ids FROM jsonb_array_elements_text(p_body->'target_ids');
  reason:=NULLIF(btrim(p_body->>'other_reason',U&'\0009\000A\000B\000C\000D\0020\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000\FEFF'),'');
  IF ((cardinality(target_ids)+cardinality(ids)>0)=(reason IS NOT NULL)) OR char_length(reason)>2000
   OR (reason IS NOT NULL AND p_body->'expected_contribution' IS NOT NULL AND p_body->'expected_contribution'<>'null'::jsonb)
  THEN RAISE EXCEPTION '请选择具体目标或KR，或填写其他必要性原因' USING ERRCODE='22023'; END IF;
 ELSE
  IF jsonb_typeof(p_body->'metric_codes') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'metric_codes')>20
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'metric_codes') x WHERE jsonb_typeof(x)<>'string')
  THEN RAISE EXCEPTION '任务指标格式无效' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(array_agg(DISTINCT value ORDER BY value),'{}'::text[]) INTO codes FROM jsonb_array_elements_text(p_body->'metric_codes');
  IF cardinality(codes)+cardinality(ids)=0 THEN RAISE EXCEPTION '任务至少选择一个指标或类别' USING ERRCODE='22023'; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM workflow.task_target_alignment l JOIN crm.sales_target s ON s.id=l.target_id AND s.workspace_id=l.workspace_id
  WHERE l.task_id=t.id AND NOT security.target_scope_read(s.scope_type,s.user_ref_id,s.team_id,s.department_code))
 THEN RAISE insufficient_privilege; END IF;
 -- Deterministic shared locks preserve the selected object identity until commit.
 PERFORM s.id FROM crm.sales_target s WHERE s.id=ANY(target_ids) AND s.workspace_id=t.workspace_id ORDER BY s.id FOR SHARE;
 IF (SELECT count(*) FROM crm.sales_target s JOIN crm.review_metric m ON m.workspace_id=s.workspace_id AND m.code=s.kind
   WHERE s.id=ANY(target_ids) AND s.workspace_id=t.workspace_id
    AND security.target_scope_read(s.scope_type,s.user_ref_id,s.team_id,s.department_code)
    AND m.active AND m.goal_selectable)<>cardinality(target_ids)
 THEN RAISE insufficient_privilege; END IF;
 -- An editor cannot unknowingly drop a hidden link while replacing the visible set.
 IF EXISTS(SELECT 1 FROM workflow.task_key_result_alignment k WHERE k.task_id=t.id AND NOT security.review_key_result_readable(k.key_result_id))
 THEN RAISE insufficient_privilege; END IF;
 FOREACH kr IN ARRAY ids LOOP
  PERFORM 1 FROM crm.review_key_result k JOIN crm.review_objective o ON o.id=k.objective_id AND o.workspace_id=k.workspace_id
   WHERE k.id=kr AND k.workspace_id=t.workspace_id FOR SHARE OF k,o;
  IF NOT FOUND OR NOT security.review_key_result_readable(kr) THEN RAISE insufficient_privilege; END IF;
 END LOOP;
 FOR v_metric_code IN SELECT code FROM (SELECT unnest(codes) code UNION SELECT k.metric_code FROM crm.review_key_result k WHERE k.id=ANY(ids) AND k.workspace_id=t.workspace_id UNION SELECT s.kind FROM crm.sales_target s WHERE s.id=ANY(target_ids) AND s.workspace_id=t.workspace_id) selected ORDER BY code LOOP
  PERFORM 1 FROM crm.review_metric m WHERE m.workspace_id=t.workspace_id AND m.code=v_metric_code AND m.active FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION '任务指标不存在或已停用' USING ERRCODE='22023'; END IF;
 END LOOP;
 INSERT INTO workflow.task_alignment(task_id,workspace_id,updated_by_user_ref_id)
 VALUES(t.id,t.workspace_id,common.current_user_ref_id()) ON CONFLICT(task_id) DO NOTHING;
 DELETE FROM workflow.task_target_alignment WHERE task_id=t.id;
 INSERT INTO workflow.task_target_alignment(task_id,workspace_id,target_id) SELECT t.id,t.workspace_id,unnest(target_ids);
 DELETE FROM workflow.task_metric_alignment WHERE task_id=t.id;
 DELETE FROM workflow.task_key_result_alignment WHERE task_id=t.id;
 INSERT INTO workflow.task_key_result_alignment(task_id,workspace_id,key_result_id) SELECT t.id,t.workspace_id,unnest(ids);
 INSERT INTO workflow.task_metric_alignment(task_id,workspace_id,metric_code,direct)
 SELECT t.id,t.workspace_id,c,c=ANY(codes) FROM (
  SELECT unnest(codes) c UNION SELECT k.metric_code FROM crm.review_key_result k WHERE k.id=ANY(ids) AND k.workspace_id=t.workspace_id
  UNION SELECT s.kind FROM crm.sales_target s WHERE s.id=ANY(target_ids) AND s.workspace_id=t.workspace_id
 ) all_codes;
 contribution:=security.validate_task_contribution(t.id,p_body->'expected_contribution',false);
 -- A previously reported claim cannot be reassigned to another metric by editing links.
 IF EXISTS(SELECT 1 FROM workflow.task_alignment a WHERE a.task_id=t.id AND a.actual_contribution IS NOT NULL
   AND NOT EXISTS(SELECT 1 FROM workflow.task_metric_alignment m WHERE m.task_id=t.id AND m.metric_code=a.actual_contribution->>'metric_code'))
 THEN RAISE EXCEPTION '已报告贡献所属指标须保留' USING ERRCODE='P0001'; END IF;
 UPDATE workflow.task_alignment SET contract_version=CASE WHEN v2 THEN 2 ELSE 1 END,other_reason=reason,expected_contribution=contribution,updated_by_user_ref_id=common.current_user_ref_id(),updated_at=clock_timestamp() WHERE task_id=t.id;
 IF NOT is_new THEN UPDATE workflow.task SET version_no=version_no+1 WHERE id=t.id RETURNING * INTO t; END IF;
 saved:=security.task_alignment_read(t.id);
 INSERT INTO workflow.task_alignment_history(task_id,workspace_id,task_version,event_kind,snapshot,actor_user_ref_id)
 VALUES(t.id,t.workspace_id,t.version_no,'alignment',saved,common.current_user_ref_id());
 RETURN saved;
END $$;

-- CREATE OR REPLACE retains the prior API-only ACL, while the new table has no direct grants.
REVOKE ALL ON FUNCTION security.task_alignment_read(uuid),security.save_task_alignment(uuid,jsonb,integer) FROM PUBLIC;
INSERT INTO ops.schema_migration(version,description) VALUES('V392','Task V2 explicit period target or KR links and required other reason');
COMMIT;
