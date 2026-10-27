-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V241__review_okr.sql.
-- Original SHA-256: a8384262a44721dd2f744761e2d5db7a5334a2575aff719c6bd9801420fe4327. See shenma_whole_121_manifest.json.
BEGIN;
-- O/KR has one target reference. It cannot store or change financial amounts.
CREATE TABLE crm.review_objective (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 title text NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 200),
 scope_type text NOT NULL CHECK(scope_type IN ('person','team','department')),
 user_ref_id uuid, team_id uuid, department_code text NOT NULL CHECK(department_code IN ('sales','fde')),
 period_type text NOT NULL CHECK(period_type IN ('week','month','quarter','year')),
 period_start date NOT NULL, period_end date NOT NULL CHECK(period_end>=period_start),
 owner_user_id uuid NOT NULL, collaborator_user_ids uuid[] NOT NULL DEFAULT '{}', parent_id uuid,
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 created_by_user_id uuid NOT NULL, updated_by_user_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id),
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(team_id,workspace_id) REFERENCES platform.team(id,workspace_id),
 FOREIGN KEY(owner_user_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(created_by_user_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(updated_by_user_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(parent_id,workspace_id) REFERENCES crm.review_objective(id,workspace_id),
 CHECK(parent_id IS NULL OR parent_id<>id), CHECK(cardinality(collaborator_user_ids)<=50),
 CHECK((scope_type='person' AND user_ref_id IS NOT NULL AND team_id IS NULL AND department_code='sales')
  OR (scope_type='team' AND team_id IS NOT NULL AND user_ref_id IS NULL AND department_code='sales')
  OR (scope_type='department' AND user_ref_id IS NULL AND team_id IS NULL))
);
CREATE TABLE crm.review_key_result (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 objective_id uuid NOT NULL, title text NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 200),
 metric_code text NOT NULL, target_id uuid REFERENCES crm.sales_target(id),
 owner_user_id uuid NOT NULL, collaborator_user_ids uuid[] NOT NULL DEFAULT '{}', parent_id uuid,
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 created_by_user_id uuid NOT NULL, updated_by_user_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id),
 FOREIGN KEY(objective_id,workspace_id) REFERENCES crm.review_objective(id,workspace_id),
 FOREIGN KEY(workspace_id,metric_code) REFERENCES crm.review_metric(workspace_id,code),
 FOREIGN KEY(owner_user_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(created_by_user_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(updated_by_user_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(parent_id,workspace_id) REFERENCES crm.review_key_result(id,workspace_id),
 CHECK(parent_id IS NULL OR parent_id<>id), CHECK(cardinality(collaborator_user_ids)<=50)
);
CREATE INDEX review_objective_period ON crm.review_objective(workspace_id,period_start,period_end);
CREATE INDEX review_key_result_objective ON crm.review_key_result(workspace_id,objective_id);
CREATE TABLE crm.review_key_result_progress (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, key_result_id uuid NOT NULL,
 key_result_version integer NOT NULL CHECK(key_result_version>0), value numeric(18,2) NOT NULL CHECK(value>=0),
 evidence text NOT NULL CHECK(length(btrim(evidence)) BETWEEN 1 AND 2000), confirmed_by_user_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(key_result_id,key_result_version),
 FOREIGN KEY(key_result_id,workspace_id) REFERENCES crm.review_key_result(id,workspace_id),
 FOREIGN KEY(confirmed_by_user_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE TABLE crm.review_okr_history (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id), entity_type text NOT NULL CHECK(entity_type IN ('objective','key_result')),
 entity_id uuid NOT NULL, version_no integer NOT NULL, snapshot jsonb NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(workspace_id,entity_type,entity_id,version_no)
);

CREATE FUNCTION security.review_objective_readable(p_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.review_objective o WHERE o.id=p_id AND o.workspace_id=common.current_workspace_id()
  AND security.target_scope_read(o.scope_type,o.user_ref_id,o.team_id,o.department_code));
$$;
-- Linkability deliberately requires active O/KR; archived rows remain visible in history through table RLS.
CREATE FUNCTION security.review_key_result_readable(p_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.review_key_result k JOIN crm.review_objective o
  ON o.id=k.objective_id AND o.workspace_id=k.workspace_id
  WHERE k.id=p_id AND k.workspace_id=common.current_workspace_id() AND k.status='active' AND o.status='active'
  AND security.target_scope_read(o.scope_type,o.user_ref_id,o.team_id,o.department_code));
$$;
ALTER TABLE crm.review_objective ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.review_objective FORCE ROW LEVEL SECURITY;
ALTER TABLE crm.review_key_result ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.review_key_result FORCE ROW LEVEL SECURITY;
ALTER TABLE crm.review_key_result_progress ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.review_key_result_progress FORCE ROW LEVEL SECURITY;
ALTER TABLE crm.review_okr_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.review_okr_history FORCE ROW LEVEL SECURITY;
CREATE POLICY review_objective_read ON crm.review_objective FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND security.target_scope_read(scope_type,user_ref_id,team_id,department_code));
CREATE POLICY review_key_result_read ON crm.review_key_result FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND security.review_objective_readable(objective_id));
CREATE POLICY review_progress_read ON crm.review_key_result_progress FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND EXISTS(SELECT 1 FROM crm.review_key_result k WHERE k.workspace_id=workspace_id AND k.id=key_result_id));
CREATE POLICY review_okr_history_read ON crm.review_okr_history FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND CASE WHEN entity_type='objective' THEN security.review_objective_readable(entity_id)
 ELSE EXISTS(SELECT 1 FROM crm.review_key_result k WHERE k.id=entity_id AND k.workspace_id=common.current_workspace_id()) END);

CREATE FUNCTION security.review_okr_participants(p_owner uuid,p_collaborators jsonb) RETURNS uuid[]
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ids uuid[];
BEGIN
 IF p_owner IS NULL OR p_collaborators IS NULL OR jsonb_typeof(p_collaborators)<>'array'
  OR jsonb_array_length(p_collaborators)>50 THEN RAISE EXCEPTION '负责人或协作人格式无效' USING ERRCODE='22023'; END IF;
 SELECT COALESCE(array_agg(DISTINCT x::uuid ORDER BY x::uuid) FILTER(WHERE x::uuid<>p_owner),'{}') INTO ids
 FROM jsonb_array_elements_text(p_collaborators) x;
 IF EXISTS(SELECT 1 FROM unnest(array_append(ids,p_owner)) person WHERE person IS NULL OR NOT EXISTS(
  SELECT 1 FROM platform.user_ref u WHERE u.id=person AND u.workspace_id=common.current_workspace_id()
   AND u.status='active' AND u.deleted_at IS NULL
   AND security.authorization_subject('target.read','person',u.id,NULL)))
 THEN RAISE insufficient_privilege; END IF;
 RETURN ids;
END $$;

CREATE FUNCTION security.save_review_objective(p_id uuid,p_body jsonb,p_version integer DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row crm.review_objective; ancestor crm.review_objective; collaborators uuid[]; owner_id uuid;
 parent_key uuid; expected_end date;
BEGIN
 IF p_body IS NULL OR jsonb_typeof(p_body)<>'object' OR length(btrim(COALESCE(p_body->>'title',''))) NOT BETWEEN 1 AND 200
 THEN RAISE EXCEPTION '目标格式无效' USING ERRCODE='22023'; END IF;
 -- One lock covers all parent edges in the tenant, preventing concurrent A->B and B->A races.
 PERFORM pg_advisory_xact_lock(hashtextextended('review-okr-graph:'||common.current_workspace_id()::text,0));
 IF p_id IS NULL THEN
  IF p_version IS NOT NULL OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE k NOT IN
   ('title','scope_type','user_id','team_id','department_code','period_type','period_start','period_end',
    'owner_user_id','collaborator_user_ids','parent_id')) THEN RAISE EXCEPTION '新增目标字段无效' USING ERRCODE='22023'; END IF;
  row.id=gen_random_uuid(); row.workspace_id=common.current_workspace_id();
  row.scope_type=p_body->>'scope_type'; row.user_ref_id=(p_body->>'user_id')::uuid;
  row.team_id=(p_body->>'team_id')::uuid; row.department_code=p_body->>'department_code';
  row.period_type=p_body->>'period_type'; row.period_start=(p_body->>'period_start')::date; row.period_end=(p_body->>'period_end')::date;
  IF row.period_type IS NULL OR row.period_type NOT IN ('week','month','quarter','year') OR row.period_start IS NULL
   OR row.period_end IS NULL OR extract(year FROM row.period_start) NOT BETWEEN 2000 AND 2100
  THEN RAISE EXCEPTION '周期无效' USING ERRCODE='22023'; END IF;
  expected_end=CASE row.period_type WHEN 'week' THEN row.period_start+6
   WHEN 'month' THEN (row.period_start+interval '1 month'-interval '1 day')::date
   WHEN 'quarter' THEN (row.period_start+interval '3 months'-interval '1 day')::date
   ELSE (row.period_start+interval '1 year'-interval '1 day')::date END;
  IF date_trunc(row.period_type,row.period_start::timestamp)::date<>row.period_start OR row.period_end<>expected_end
  THEN RAISE EXCEPTION '目标须使用完整自然周期' USING ERRCODE='22023'; END IF;
 ELSE
  IF p_version IS NULL OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE k NOT IN
   ('title','owner_user_id','collaborator_user_ids','parent_id','status'))
  THEN RAISE EXCEPTION '周期和归属不可修改，请新建目标' USING ERRCODE='22023'; END IF;
  SELECT * INTO row FROM crm.review_objective WHERE id=p_id AND workspace_id=common.current_workspace_id() FOR UPDATE;
  IF row.id IS NULL OR NOT security.review_objective_readable(row.id) THEN RAISE no_data_found; END IF;
  IF row.version_no<>p_version THEN RAISE EXCEPTION '目标已更新，请刷新' USING ERRCODE='P0001'; END IF;
 END IF;
 IF NOT COALESCE(security.target_scope_write(row.scope_type,row.user_ref_id,row.team_id,row.department_code),false)
  OR NOT COALESCE(security.target_scope_read(row.scope_type,row.user_ref_id,row.team_id,row.department_code),false)
 THEN RAISE insufficient_privilege; END IF;
 owner_id=(p_body->>'owner_user_id')::uuid;
 collaborators=security.review_okr_participants(owner_id,COALESCE(p_body->'collaborator_user_ids','[]'));
 parent_key=(p_body->>'parent_id')::uuid;
 IF parent_key IS NOT NULL THEN
  SELECT * INTO ancestor FROM crm.review_objective WHERE id=parent_key AND workspace_id=row.workspace_id;
  IF ancestor.id IS NULL OR NOT security.review_objective_readable(ancestor.id) THEN RAISE no_data_found; END IF;
  IF ancestor.status<>'active' OR ancestor.period_start>row.period_start OR ancestor.period_end<row.period_end
  THEN RAISE EXCEPTION '上级目标须生效且周期覆盖当前目标' USING ERRCODE='22023'; END IF;
  IF EXISTS(WITH RECURSIVE chain AS (
    SELECT id,parent_id FROM crm.review_objective WHERE id=parent_key AND workspace_id=row.workspace_id
    UNION SELECT o.id,o.parent_id FROM crm.review_objective o JOIN chain c ON o.id=c.parent_id WHERE o.workspace_id=row.workspace_id
   ) SELECT 1 FROM chain WHERE id=row.id)
  THEN RAISE EXCEPTION '目标对齐不能形成循环' USING ERRCODE='22023'; END IF;
 END IF;
 IF p_id IS NULL THEN
  INSERT INTO crm.review_objective(id,workspace_id,title,scope_type,user_ref_id,team_id,department_code,period_type,
   period_start,period_end,owner_user_id,collaborator_user_ids,parent_id,created_by_user_id,updated_by_user_id)
  VALUES(row.id,row.workspace_id,btrim(p_body->>'title'),row.scope_type,row.user_ref_id,row.team_id,row.department_code,
   row.period_type,row.period_start,row.period_end,owner_id,collaborators,parent_key,common.current_user_ref_id(),common.current_user_ref_id())
  RETURNING * INTO row;
 ELSE
  IF p_body->>'status'='archived' AND (EXISTS(SELECT 1 FROM crm.review_objective c WHERE c.workspace_id=row.workspace_id
    AND c.parent_id=row.id AND c.status='active') OR EXISTS(SELECT 1 FROM crm.review_key_result k
    WHERE k.workspace_id=row.workspace_id AND k.objective_id=row.id AND k.status='active'))
  THEN RAISE EXCEPTION '请先归档有效子目标和KR' USING ERRCODE='P0001'; END IF;
  UPDATE crm.review_objective SET title=btrim(p_body->>'title'),owner_user_id=owner_id,
   collaborator_user_ids=collaborators,parent_id=parent_key,status=COALESCE(p_body->>'status','active'),
   version_no=version_no+1,updated_at=clock_timestamp(),updated_by_user_id=common.current_user_ref_id()
   WHERE id=row.id RETURNING * INTO row;
 END IF;
 INSERT INTO crm.review_okr_history(workspace_id,entity_type,entity_id,version_no,snapshot)
 VALUES(row.workspace_id,'objective',row.id,row.version_no,to_jsonb(row));
 RETURN to_jsonb(row);
END $$;

CREATE FUNCTION security.save_review_key_result(p_id uuid,p_body jsonb,p_version integer DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row crm.review_key_result; objective crm.review_objective; ancestor crm.review_key_result; ancestor_o crm.review_objective;
 metric crm.review_metric; target crm.sales_target; collaborators uuid[]; owner_id uuid; parent_key uuid;
BEGIN
 IF p_body IS NULL OR jsonb_typeof(p_body)<>'object' OR length(btrim(COALESCE(p_body->>'title',''))) NOT BETWEEN 1 AND 200
 THEN RAISE EXCEPTION 'KR格式无效' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('review-okr-graph:'||common.current_workspace_id()::text,0));
 IF p_id IS NULL THEN
  IF p_version IS NOT NULL OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE k NOT IN
   ('title','objective_id','metric_code','target_id','owner_user_id','collaborator_user_ids','parent_id'))
  THEN RAISE EXCEPTION '新增KR字段无效' USING ERRCODE='22023'; END IF;
  row.id=gen_random_uuid(); row.workspace_id=common.current_workspace_id();
  row.objective_id=(p_body->>'objective_id')::uuid; row.metric_code=p_body->>'metric_code'; row.target_id=(p_body->>'target_id')::uuid;
 ELSE
  IF p_version IS NULL OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE k NOT IN
   ('title','owner_user_id','collaborator_user_ids','parent_id','status'))
  THEN RAISE EXCEPTION 'KR指标和目标引用不可修改，请新建KR' USING ERRCODE='22023'; END IF;
  SELECT * INTO row FROM crm.review_key_result WHERE id=p_id AND workspace_id=common.current_workspace_id() FOR UPDATE;
  IF row.id IS NULL THEN RAISE no_data_found; END IF;
 END IF;
 SELECT * INTO objective FROM crm.review_objective WHERE id=row.objective_id AND workspace_id=row.workspace_id;
 IF objective.id IS NULL OR NOT security.review_objective_readable(objective.id) THEN RAISE no_data_found; END IF;
 IF NOT security.target_scope_write(objective.scope_type,objective.user_ref_id,objective.team_id,objective.department_code)
 THEN RAISE insufficient_privilege; END IF;
 IF p_id IS NOT NULL AND row.version_no<>p_version THEN RAISE EXCEPTION 'KR已更新，请刷新' USING ERRCODE='P0001'; END IF;
 IF objective.status<>'active' THEN RAISE EXCEPTION '所属目标已归档' USING ERRCODE='P0001'; END IF;
 SELECT * INTO metric FROM crm.review_metric WHERE workspace_id=row.workspace_id AND code=row.metric_code;
 IF metric.code IS NULL OR metric.source_kind='category' OR (p_id IS NULL AND NOT metric.active)
 THEN RAISE EXCEPTION 'KR指标不可用' USING ERRCODE='22023'; END IF;
 IF row.target_id IS NOT NULL THEN
  SELECT * INTO target FROM crm.sales_target WHERE id=row.target_id AND workspace_id=row.workspace_id;
  IF target.id IS NULL OR NOT security.target_scope_read(target.scope_type,target.user_ref_id,target.team_id,target.department_code)
  THEN RAISE no_data_found; END IF;
  IF target.kind IS DISTINCT FROM metric.target_kind OR target.scope_type<>objective.scope_type
   OR target.user_ref_id IS DISTINCT FROM objective.user_ref_id OR target.team_id IS DISTINCT FROM objective.team_id
   OR target.department_code<>objective.department_code OR target.period_type<>objective.period_type
   OR target.period_start<>objective.period_start OR target.period_end<>objective.period_end
  THEN RAISE EXCEPTION 'KR须引用同归属、周期和指标的唯一生效目标' USING ERRCODE='22023'; END IF;
 END IF;
 owner_id=(p_body->>'owner_user_id')::uuid;
 collaborators=security.review_okr_participants(owner_id,COALESCE(p_body->'collaborator_user_ids','[]'));
 parent_key=(p_body->>'parent_id')::uuid;
 IF parent_key IS NOT NULL THEN
  SELECT * INTO ancestor FROM crm.review_key_result WHERE id=parent_key AND workspace_id=row.workspace_id;
  IF ancestor.id IS NULL OR NOT security.review_key_result_readable(ancestor.id) THEN RAISE no_data_found; END IF;
  SELECT * INTO ancestor_o FROM crm.review_objective WHERE id=ancestor.objective_id AND workspace_id=row.workspace_id;
  IF ancestor_o.period_start>objective.period_start OR ancestor_o.period_end<objective.period_end
  THEN RAISE EXCEPTION '上级KR周期须覆盖当前KR周期' USING ERRCODE='22023'; END IF;
  IF EXISTS(WITH RECURSIVE chain AS (
    SELECT id,parent_id FROM crm.review_key_result WHERE id=parent_key AND workspace_id=row.workspace_id
    UNION SELECT k.id,k.parent_id FROM crm.review_key_result k JOIN chain c ON k.id=c.parent_id WHERE k.workspace_id=row.workspace_id
   ) SELECT 1 FROM chain WHERE id=row.id)
  THEN RAISE EXCEPTION 'KR对齐不能形成循环' USING ERRCODE='22023'; END IF;
 END IF;
 IF p_id IS NULL THEN
  INSERT INTO crm.review_key_result(id,workspace_id,objective_id,title,metric_code,target_id,owner_user_id,
   collaborator_user_ids,parent_id,created_by_user_id,updated_by_user_id)
  VALUES(row.id,row.workspace_id,row.objective_id,btrim(p_body->>'title'),row.metric_code,row.target_id,owner_id,
   collaborators,parent_key,common.current_user_ref_id(),common.current_user_ref_id()) RETURNING * INTO row;
 ELSE
  IF p_body->>'status'='archived' AND EXISTS(SELECT 1 FROM crm.review_key_result k
   WHERE k.workspace_id=row.workspace_id AND k.parent_id=row.id AND k.status='active')
  THEN RAISE EXCEPTION '请先归档有效子KR' USING ERRCODE='P0001'; END IF;
  UPDATE crm.review_key_result SET title=btrim(p_body->>'title'),owner_user_id=owner_id,
   collaborator_user_ids=collaborators,parent_id=parent_key,status=COALESCE(p_body->>'status','active'),
   version_no=version_no+1,updated_at=clock_timestamp(),updated_by_user_id=common.current_user_ref_id()
   WHERE id=row.id RETURNING * INTO row;
 END IF;
 INSERT INTO crm.review_okr_history(workspace_id,entity_type,entity_id,version_no,snapshot)
 VALUES(row.workspace_id,'key_result',row.id,row.version_no,to_jsonb(row));
 RETURN to_jsonb(row);
END $$;

CREATE FUNCTION security.confirm_review_key_result_progress(p_id uuid,p_version integer,p_value numeric,p_evidence text,p_confirmed boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row crm.review_key_result; objective crm.review_objective; metric crm.review_metric; progress crm.review_key_result_progress;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('review-okr-graph:'||common.current_workspace_id()::text,0));
 SELECT * INTO row FROM crm.review_key_result WHERE id=p_id AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF row.id IS NULL OR NOT security.review_key_result_readable(row.id) THEN RAISE no_data_found; END IF;
 SELECT * INTO objective FROM crm.review_objective WHERE id=row.objective_id AND workspace_id=row.workspace_id;
 IF NOT security.target_scope_write(objective.scope_type,objective.user_ref_id,objective.team_id,objective.department_code)
  OR (common.current_user_ref_id()<>row.owner_user_id AND NOT security.authorization_subject('target.manage',
   objective.scope_type,objective.user_ref_id,objective.team_id)) THEN RAISE insufficient_privilege; END IF;
 IF p_version IS NULL OR row.version_no<>p_version THEN RAISE EXCEPTION 'KR已更新，请刷新' USING ERRCODE='P0001'; END IF;
 SELECT * INTO metric FROM crm.review_metric WHERE workspace_id=row.workspace_id AND code=row.metric_code;
 IF NOT metric.active OR metric.source_kind<>'manual' OR metric.unit IN ('元','万元','人民币','CNY','RMB')
 THEN RAISE EXCEPTION '只允许非财务手工指标确认进展，自动业绩不能手写' USING ERRCODE='22023'; END IF;
 IF p_confirmed IS DISTINCT FROM true OR p_value IS NULL OR p_value<0 OR p_value>=10000000000000000
  OR p_value<>round(p_value,metric.decimal_places) OR length(btrim(COALESCE(p_evidence,''))) NOT BETWEEN 1 AND 2000
 THEN RAISE EXCEPTION '请填写合规数值、证据并明确确认' USING ERRCODE='22023'; END IF;
 UPDATE crm.review_key_result SET version_no=version_no+1,updated_at=clock_timestamp(),updated_by_user_id=common.current_user_ref_id()
 WHERE id=row.id RETURNING * INTO row;
 INSERT INTO crm.review_key_result_progress(workspace_id,key_result_id,key_result_version,value,evidence,confirmed_by_user_id)
 VALUES(row.workspace_id,row.id,row.version_no,p_value,btrim(p_evidence),common.current_user_ref_id()) RETURNING * INTO progress;
 INSERT INTO crm.review_okr_history(workspace_id,entity_type,entity_id,version_no,snapshot)
 VALUES(row.workspace_id,'key_result',row.id,row.version_no,to_jsonb(row));
 RETURN jsonb_build_object('key_result',to_jsonb(row),'progress',to_jsonb(progress)||jsonb_build_object('value',progress.value::text));
END $$;

CREATE FUNCTION security.mention_review_okr(p_type text,p_id uuid,p_version integer,p_recipients uuid[]) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE objective crm.review_objective; kr crm.review_key_result; current_version integer; person uuid; inserted integer:=0; delta integer;
BEGIN
 IF p_type='objective' THEN
  SELECT * INTO objective FROM crm.review_objective WHERE id=p_id AND workspace_id=common.current_workspace_id();
  current_version=objective.version_no;
 ELSIF p_type='key_result' THEN
  SELECT * INTO kr FROM crm.review_key_result WHERE id=p_id AND workspace_id=common.current_workspace_id();
  SELECT * INTO objective FROM crm.review_objective WHERE id=kr.objective_id AND workspace_id=common.current_workspace_id();
  current_version=kr.version_no;
 ELSE RAISE EXCEPTION '提醒对象无效' USING ERRCODE='22023'; END IF;
 IF objective.id IS NULL OR NOT security.review_objective_readable(objective.id) THEN RAISE no_data_found; END IF;
 IF NOT security.target_scope_write(objective.scope_type,objective.user_ref_id,objective.team_id,objective.department_code)
 THEN RAISE insufficient_privilege; END IF;
 IF p_version IS NULL OR current_version<>p_version THEN RAISE EXCEPTION '目标已更新，请刷新' USING ERRCODE='P0001'; END IF;
 IF p_recipients IS NULL OR cardinality(p_recipients) NOT BETWEEN 1 AND 20 THEN RAISE EXCEPTION '提醒接收人无效' USING ERRCODE='22023'; END IF;
 FOR person IN SELECT DISTINCT id FROM unnest(p_recipients) id LOOP
  IF person IS NULL OR NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=person AND u.workspace_id=objective.workspace_id
    AND u.status='active' AND u.deleted_at IS NULL)
   OR NOT security.authorization_user_allows('target.read',objective.workspace_id,person,objective.user_ref_id,
    CASE WHEN objective.scope_type='team' THEN ARRAY[objective.team_id] ELSE '{}'::uuid[] END,objective.user_ref_id=person)
   OR (objective.department_code='fde' AND NOT security.authorization_user_allows('target.fde_department',
    objective.workspace_id,person,NULL,'{}',false))
  THEN RAISE insufficient_privilege; END IF;
  INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,body,object_type,object_id,status,dedupe_key,payload)
  VALUES(objective.workspace_id,person,'review_okr_mention','目标协作提醒','请查看您有权访问的目标进展',
   'review_'||p_type,p_id,'pending','review-mention:'||p_type||':'||p_id::text||':'||p_version::text||':'||common.current_user_ref_id()::text,
   jsonb_build_object('entity_type',p_type,'entity_id',p_id,'version_no',p_version,'actor_user_id',common.current_user_ref_id()))
  ON CONFLICT (workspace_id,recipient_user_ref_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING;
  GET DIAGNOSTICS delta=ROW_COUNT; inserted=inserted+delta;
 END LOOP;
 RETURN jsonb_build_object('status','queued','created',inserted,'permission_changed',false);
END $$;

-- Exact API grants only; worker roles cannot write or read the new tables.
REVOKE ALL ON crm.review_objective,crm.review_key_result,crm.review_key_result_progress,crm.review_okr_history FROM PUBLIC;
REVOKE ALL ON FUNCTION security.review_objective_readable(uuid),security.review_key_result_readable(uuid),
 security.review_okr_participants(uuid,jsonb),security.save_review_objective(uuid,jsonb,integer),
 security.save_review_key_result(uuid,jsonb,integer),security.confirm_review_key_result_progress(uuid,integer,numeric,text,boolean),
 security.mention_review_okr(text,uuid,integer,uuid[]) FROM PUBLIC;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_review_okr;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record; is_api boolean;
BEGIN
 total:=security.reconcile_runtime_grants_before_review_okr();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE') LOOP
  is_api:=has_function_privilege(r.rolname,'security.save_account_authorization(uuid,jsonb)','EXECUTE');
  EXECUTE format('REVOKE ALL ON crm.review_objective,crm.review_key_result,crm.review_key_result_progress,crm.review_okr_history FROM %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.review_objective_readable(uuid),security.review_key_result_readable(uuid),security.review_okr_participants(uuid,jsonb),security.save_review_objective(uuid,jsonb,integer),security.save_review_key_result(uuid,jsonb,integer),security.confirm_review_key_result_progress(uuid,integer,numeric,text,boolean),security.mention_review_okr(text,uuid,integer,uuid[]),security.reconcile_runtime_grants_before_review_okr(),security.reconcile_runtime_grants() FROM %I',r.rolname);
  IF is_api THEN
   EXECUTE format('GRANT SELECT ON crm.review_objective,crm.review_key_result,crm.review_key_result_progress TO %I',r.rolname);
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.review_objective_readable(uuid),security.review_key_result_readable(uuid),security.save_review_objective(uuid,jsonb,integer),security.save_review_key_result(uuid,jsonb,integer),security.confirm_review_key_result_progress(uuid,integer,numeric,text,boolean),security.mention_review_okr(text,uuid,integer,uuid[]) TO %I',r.rolname);
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_review_okr() FROM PUBLIC;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V389','Lightweight scoped objectives, key results and confirmed nonfinancial progress');
COMMIT;
