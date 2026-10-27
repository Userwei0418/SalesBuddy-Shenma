-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V190__meeting_task_import.sql.
-- Original SHA-256: ae76935eef6bf72a453a9aa2af7d9b1d0a100260d3976d05e8ec0b051c4b5656. See shenma_whole_121_manifest.json.
-- Meeting imports retain source drafts, but dispatch ordinary workflow tasks.
-- No historical tasks, Feishu queues, or company settings are rewritten.
ALTER TABLE workflow.task ADD CONSTRAINT task_id_workspace_unique UNIQUE(id,workspace_id);

CREATE TABLE workflow.task_import_batch (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 creator_user_ref_id uuid NOT NULL,
 batch_name text NOT NULL CHECK(length(btrim(batch_name)) BETWEEN 1 AND 120),
 meeting_date date NOT NULL CHECK(isfinite(meeting_date)),
 source_text text NOT NULL CHECK(length(source_text) BETWEEN 5 AND 30000),
 source_hash text NOT NULL CHECK(source_hash ~ '^[0-9a-f]{64}$'),
 parse_method text NOT NULL CHECK(parse_method IN ('ai','rules')),
 parse_warnings jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(parse_warnings)='array'),
 items jsonb NOT NULL CHECK(jsonb_typeof(items)='array' AND jsonb_array_length(items) BETWEEN 1 AND 100),
 editable_items jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(editable_items)='array' AND jsonb_array_length(editable_items)<=100),
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','confirmed')),
 confirmed_payload_hash text,
 confirmed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id),
 UNIQUE(workspace_id,creator_user_ref_id,meeting_date,source_hash),
 FOREIGN KEY(creator_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 CHECK((status='draft' AND confirmed_at IS NULL AND confirmed_payload_hash IS NULL) OR
       (status='confirmed' AND confirmed_at IS NOT NULL AND isfinite(confirmed_at)
        AND confirmed_payload_hash ~ '^[0-9a-f]{64}$') IS TRUE)
);
CREATE INDEX task_import_batch_creator ON workflow.task_import_batch(workspace_id,creator_user_ref_id,created_at DESC,id);
CREATE INDEX task_import_batch_recent ON workflow.task_import_batch(workspace_id,created_at DESC,id DESC);

CREATE TABLE workflow.task_import_item (
 batch_id uuid NOT NULL,
 item_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 task_id uuid NOT NULL UNIQUE,
 source_line integer NOT NULL CHECK(source_line>0),
 original_assignee_user_ref_id uuid NOT NULL,
 confirmed_item jsonb NOT NULL CHECK(jsonb_typeof(confirmed_item)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(batch_id,item_id),
 FOREIGN KEY(batch_id,workspace_id) REFERENCES workflow.task_import_batch(id,workspace_id),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task(id,workspace_id),
 FOREIGN KEY(original_assignee_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE INDEX task_import_item_workspace_batch ON workflow.task_import_item(workspace_id,batch_id,task_id);

-- Exposing a batch never grants access to another task. Raw source is available
-- only to its author; other readers use the safe summary below.
CREATE FUNCTION security.task_import_is_creator(p_batch uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM workflow.task_import_batch b
  WHERE b.id=p_batch AND b.workspace_id=common.current_workspace_id()
   AND b.creator_user_ref_id=common.current_user_ref_id()
   AND security.workspace_is_active(b.workspace_id)
   AND (security.authorization_has('task.read') OR security.authorization_has('task.create_daily')
        OR security.authorization_has('task.create_customer')));
$$;
ALTER TABLE workflow.task_import_batch ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.task_import_item ENABLE ROW LEVEL SECURITY;
CREATE POLICY task_import_batch_read ON workflow.task_import_batch FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND creator_user_ref_id=common.current_user_ref_id()
 AND security.workspace_is_active(workspace_id)
 AND (security.authorization_has('task.read') OR security.authorization_has('task.create_daily')
      OR security.authorization_has('task.create_customer')));
CREATE POLICY task_import_batch_insert ON workflow.task_import_batch FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND creator_user_ref_id=common.current_user_ref_id()
 AND security.workspace_is_active(workspace_id)
 AND (security.authorization_has('task.create_daily') OR security.authorization_has('task.create_customer')));
CREATE POLICY task_import_batch_update ON workflow.task_import_batch FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND creator_user_ref_id=common.current_user_ref_id()
 AND security.workspace_is_active(workspace_id)
 AND (security.authorization_has('task.create_daily') OR security.authorization_has('task.create_customer')))
 WITH CHECK(workspace_id=common.current_workspace_id() AND creator_user_ref_id=common.current_user_ref_id()
 AND security.workspace_is_active(workspace_id)
 AND (security.authorization_has('task.create_daily') OR security.authorization_has('task.create_customer')));
CREATE POLICY task_import_item_read ON workflow.task_import_item FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.task_import_is_creator(batch_id)
 AND security.authorization_task('task.read',task_id));
CREATE POLICY task_import_item_insert ON workflow.task_import_item FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.task_import_is_creator(batch_id)
 AND security.authorization_task_created(task_id));

CREATE FUNCTION workflow.guard_task_import_batch() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE item jsonb; source_ids uuid[]:='{}'; edit_ids uuid[]:='{}'; item_id uuid;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Task import source is immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'draft' OR NEW.version_no<>1 THEN
   RAISE EXCEPTION 'Task import must start as a draft' USING ERRCODE='23514'; END IF;
  IF NEW.source_hash IS DISTINCT FROM encode(sha256(convert_to(NEW.source_text,'UTF8')),'hex') THEN
   RAISE EXCEPTION 'Task import source hash mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.editable_items='[]'::jsonb THEN NEW.editable_items:=NEW.items; END IF;
 ELSE
  IF OLD.status<>'draft' OR
   (to_jsonb(NEW)-ARRAY['editable_items','version_no','status','confirmed_payload_hash','confirmed_at','updated_at'])
    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['editable_items','version_no','status','confirmed_payload_hash','confirmed_at','updated_at'])
   OR NEW.version_no<>OLD.version_no+1 THEN
   RAISE EXCEPTION 'Task import source or version is immutable' USING ERRCODE='23514'; END IF;
  IF NEW.status='confirmed' AND NOT EXISTS(SELECT 1 FROM workflow.task_import_item i
    WHERE i.batch_id=NEW.id AND i.workspace_id=NEW.workspace_id) THEN
   RAISE EXCEPTION 'Cannot confirm an empty task import' USING ERRCODE='23514'; END IF;
  NEW.updated_at:=clock_timestamp();
 END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(NEW.items) LOOP
  IF jsonb_typeof(item)<>'object' OR item->>'item_id' IS NULL
   OR COALESCE(item->>'source_line','')!~'^[1-9][0-9]*$' THEN
   RAISE EXCEPTION 'Invalid task import source item' USING ERRCODE='23514'; END IF;
  item_id:=(item->>'item_id')::uuid;
  IF item_id=ANY(source_ids) THEN RAISE EXCEPTION 'Duplicate source item ID' USING ERRCODE='23514'; END IF;
  source_ids:=array_append(source_ids,item_id);
 END LOOP;
 FOR item IN SELECT value FROM jsonb_array_elements(NEW.editable_items) LOOP
  item_id:=(item->>'item_id')::uuid;
  IF item_id IS NULL OR NOT item_id=ANY(source_ids) OR item_id=ANY(edit_ids) THEN
   RAISE EXCEPTION 'Editable item must reference one source item' USING ERRCODE='23514'; END IF;
  edit_ids:=array_append(edit_ids,item_id);
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER task_import_batch_guard BEFORE INSERT OR UPDATE OR DELETE ON workflow.task_import_batch
 FOR EACH ROW EXECUTE FUNCTION workflow.guard_task_import_batch();

CREATE FUNCTION workflow.guard_task_import_item() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE batch workflow.task_import_batch%ROWTYPE; original jsonb; snapshot jsonb;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Dispatched task import items are immutable' USING ERRCODE='23514'; END IF;
 -- All confirmations lock the same parent before creating links. A second
 -- confirmation observes confirmed status after the first transaction commits.
 SELECT * INTO batch FROM workflow.task_import_batch WHERE id=NEW.batch_id FOR UPDATE;
 IF batch.workspace_id IS DISTINCT FROM NEW.workspace_id OR batch.status IS DISTINCT FROM 'draft'
  OR batch.workspace_id IS DISTINCT FROM common.current_workspace_id()
  OR batch.creator_user_ref_id IS DISTINCT FROM common.current_user_ref_id()
  OR NOT security.authorization_task_created(NEW.task_id) THEN
  RAISE EXCEPTION 'Task import batch is not an owned open draft' USING ERRCODE='42501'; END IF;
 SELECT value INTO original FROM jsonb_array_elements(batch.items)
  WHERE value->>'item_id'=NEW.item_id::text;
 IF original IS NULL OR (original->>'source_line')::integer<>NEW.source_line
  OR NEW.confirmed_item->>'item_id' IS DISTINCT FROM NEW.item_id::text THEN
  RAISE EXCEPTION 'Task import link does not match source item' USING ERRCODE='23514'; END IF;
 SELECT jsonb_build_object('name',u.display_name,'account_code',u.account_code,'team_id',a.assignee_team_id,'team',team.name)
 INTO snapshot FROM workflow.task t
 JOIN workflow.task_assignee a ON a.task_id=t.id AND a.workspace_id=t.workspace_id AND a.responsibility='owner'
 JOIN platform.user_ref u ON u.id=a.assignee_user_ref_id AND u.workspace_id=t.workspace_id
 LEFT JOIN platform.team team ON team.id=a.assignee_team_id AND team.workspace_id=t.workspace_id
 WHERE t.id=NEW.task_id AND t.workspace_id=NEW.workspace_id AND t.creator_user_ref_id=batch.creator_user_ref_id
  AND t.source_code='meeting_import' AND t.deleted_at IS NULL
  AND a.assignee_user_ref_id=NEW.original_assignee_user_ref_id;
 IF snapshot IS NULL THEN RAISE EXCEPTION 'Task import must link its newly dispatched owner' USING ERRCODE='23514'; END IF;
 NEW.confirmed_item:=NEW.confirmed_item||jsonb_build_object('original_assignee',snapshot);
 RETURN NEW;
END $$;
CREATE TRIGGER task_import_item_guard BEFORE INSERT OR UPDATE OR DELETE ON workflow.task_import_item
 FOR EACH ROW EXECUTE FUNCTION workflow.guard_task_import_item();

CREATE FUNCTION workflow.check_task_import_confirmation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM workflow.task_import_batch WHERE id=NEW.batch_id
  AND workspace_id=NEW.workspace_id AND status='confirmed') THEN
  RAISE EXCEPTION 'Task dispatch must confirm the whole batch in the same transaction' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER task_import_link_confirmed AFTER INSERT ON workflow.task_import_item
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION workflow.check_task_import_confirmation();

CREATE FUNCTION ops.audit_task_import() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prior jsonb; current_value jsonb; original_prior jsonb; original_current jsonb; source jsonb; changed text[];
BEGIN
 IF TG_OP<>'INSERT' THEN original_prior:=to_jsonb(OLD); END IF;
 IF TG_OP<>'DELETE' THEN original_current:=to_jsonb(NEW); END IF;
 IF original_prior IS NOT DISTINCT FROM original_current THEN RETURN NULL; END IF;
 -- Store identities, lifecycle, source hash and edit fingerprints, not meeting
 -- narratives, parsed fragments, descriptions or warnings in the audit log.
 prior:=original_prior-ARRAY['source_text','items','editable_items','parse_warnings','confirmed_item'];
 current_value:=original_current-ARRAY['source_text','items','editable_items','parse_warnings','confirmed_item'];
 IF original_prior IS NOT NULL THEN prior:=prior||jsonb_build_object('content_hash',
  encode(sha256(convert_to((original_prior-ARRAY['updated_at','version_no'])::text,'UTF8')),'hex')); END IF;
 IF original_current IS NOT NULL THEN current_value:=current_value||jsonb_build_object('content_hash',
  encode(sha256(convert_to((original_current-ARRAY['updated_at','version_no'])::text,'UTF8')),'hex')); END IF;
 SELECT array_agg(key ORDER BY key) INTO changed
 FROM jsonb_object_keys(COALESCE(original_prior,'{}')||COALESCE(original_current,'{}')) key
 WHERE original_prior->key IS DISTINCT FROM original_current->key AND key NOT IN ('updated_at','created_at','version_no');
 source:=COALESCE(current_value,prior);
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,
  object_type,object_id,object_label,request_id,before_snapshot,after_snapshot,changed_fields)
 VALUES((source->>'workspace_id')::uuid,common.current_user_ref_id(),COALESCE(common.current_role_code(),'system'),
  'workflow.'||TG_TABLE_NAME||'.'||lower(TG_OP),'workflow',TG_TABLE_NAME,
  COALESCE(source->>'id',source->>'batch_id')::uuid,COALESCE(source->>'batch_name','周会任务来源'),
  NULLIF(current_setting('app.request_id',true),'')::uuid,prior,current_value,COALESCE(changed,'{}'));
 RETURN NULL;
END $$;
CREATE TRIGGER business_audit AFTER INSERT OR UPDATE OR DELETE ON workflow.task_import_batch
 FOR EACH ROW EXECUTE FUNCTION ops.audit_task_import();
CREATE TRIGGER business_audit AFTER INSERT OR UPDATE OR DELETE ON workflow.task_import_item
 FOR EACH ROW EXECUTE FUNCTION ops.audit_task_import();

CREATE FUNCTION security.task_import_batch_summary(p_batch uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg(header ORDER BY created_at DESC,id),'[]'::jsonb) FROM (
  SELECT b.id,b.created_at,jsonb_build_object('id',b.id,'workspace_id',b.workspace_id,
   'creator_user_ref_id',b.creator_user_ref_id,'creator_name',u.display_name,'batch_name',b.batch_name,
   'meeting_date',b.meeting_date,'status',b.status,'version_no',b.version_no,'parse_method',b.parse_method,
   'created_at',b.created_at,'updated_at',b.updated_at,'confirmed_at',b.confirmed_at,
   'task_ids',visible.task_ids,'task_items',visible.task_items) AS header
  FROM workflow.task_import_batch b JOIN platform.user_ref u ON u.id=b.creator_user_ref_id AND u.workspace_id=b.workspace_id
  CROSS JOIN LATERAL (SELECT jsonb_agg(i.task_id ORDER BY i.source_line,i.item_id) task_ids,
    jsonb_agg(jsonb_build_object('item_id',i.item_id,'task_id',i.task_id,'source_line',i.source_line,
      'original_assignee',i.confirmed_item->'original_assignee') ORDER BY i.source_line,i.item_id) task_items
   FROM workflow.task_import_item i JOIN workflow.task t ON t.id=i.task_id AND t.workspace_id=i.workspace_id
   WHERE i.batch_id=b.id AND i.workspace_id=b.workspace_id AND t.deleted_at IS NULL
    AND (security.authorization_task('task.read',t.id) OR
     security.authorization_allows('task.read',t.workspace_id,t.creator_user_ref_id,ARRAY[t.creator_team_id],false))) visible
  WHERE b.workspace_id=common.current_workspace_id() AND security.workspace_is_active(b.workspace_id)
   AND b.status='confirmed' AND (p_batch IS NULL OR b.id=p_batch) AND visible.task_ids IS NOT NULL
 ) permitted;
$$;

-- List pages use existence checks rather than expanding every historical
-- batch's task IDs. Per-page statistics/details use the single-batch summary.
CREATE FUNCTION security.task_import_batch_page(p_status text,p_limit integer,p_offset integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF (p_status IS NOT NULL AND p_status NOT IN ('draft','confirmed'))
  OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 51 OR p_offset IS NULL OR p_offset<0 THEN
  RAISE EXCEPTION 'Invalid task import pagination' USING ERRCODE='22023'; END IF;
 RETURN (SELECT COALESCE(jsonb_agg(header ORDER BY created_at DESC,id DESC),'[]'::jsonb) FROM (
  SELECT b.id,b.created_at,jsonb_build_object('id',b.id,'workspace_id',b.workspace_id,
   'creator_user_ref_id',b.creator_user_ref_id,'creator_name',u.display_name,'batch_name',b.batch_name,
   'meeting_date',b.meeting_date,'status',b.status,'version_no',b.version_no,'parse_method',b.parse_method,
   'created_at',b.created_at,'updated_at',b.updated_at,'confirmed_at',b.confirmed_at) AS header
  FROM workflow.task_import_batch b JOIN platform.user_ref u ON u.id=b.creator_user_ref_id AND u.workspace_id=b.workspace_id
  WHERE b.workspace_id=common.current_workspace_id() AND security.workspace_is_active(b.workspace_id)
   AND (p_status IS NULL OR b.status=p_status)
   AND ((b.status='draft' AND b.creator_user_ref_id=common.current_user_ref_id()
    AND (security.authorization_has('task.read') OR security.authorization_has('task.create_daily')
      OR security.authorization_has('task.create_customer')))
    OR (b.status='confirmed' AND EXISTS(SELECT 1 FROM workflow.task_import_item i
     JOIN workflow.task t ON t.id=i.task_id AND t.workspace_id=i.workspace_id
     WHERE i.batch_id=b.id AND i.workspace_id=b.workspace_id AND t.deleted_at IS NULL
      AND (security.authorization_task('task.read',t.id) OR
       security.authorization_allows('task.read',t.workspace_id,t.creator_user_ref_id,ARRAY[t.creator_team_id],false)))))
  ORDER BY b.created_at DESC,b.id DESC LIMIT p_limit OFFSET p_offset
 ) permitted);
END $$;

-- Clone the prior body into a private wrapper target, retaining the public
-- entrypoint's OID/owner/ACL for pre-existing limited worker roles.
DO $$ BEGIN
 EXECUTE replace(pg_get_functiondef('ops.feishu_source(uuid,text,uuid)'::regprocedure),
  'CREATE OR REPLACE FUNCTION ops.feishu_source(',
  'CREATE OR REPLACE FUNCTION ops.feishu_source_before_task_import(');
END $$;
CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; extra jsonb; ws uuid;
BEGIN
 result:=ops.feishu_source_before_task_import(p_connection,p_kind,p_id);
 IF p_kind<>'task' OR result->>'excluded'='true' OR result->>'deleted'='true' THEN RETURN result; END IF;
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 SELECT jsonb_build_object('source_code',t.source_code,'association_kind',t.association_kind,
  'accepted_at',t.accepted_at,'completion_submitted_at',t.completion_submitted_at,'completed_at',t.completed_at,
  'verification_status',t.verification_status,'owner_name',owner.display_name,'owner_account',owner.account_code,
  'batch_id',b.id,'batch_name',b.batch_name,'meeting_date',b.meeting_date,'dispatched_at',b.confirmed_at,
  'original_owner_id',i.original_assignee_user_ref_id,'original_owner_name',i.confirmed_item#>>'{original_assignee,name}',
  'original_dispatcher_id',t.creator_user_ref_id,'original_dispatcher_name',creator.display_name)
 INTO extra FROM workflow.task t
 LEFT JOIN platform.user_ref owner ON owner.id=(result->>'owner_id')::uuid AND owner.workspace_id=t.workspace_id
 LEFT JOIN platform.user_ref creator ON creator.id=t.creator_user_ref_id AND creator.workspace_id=t.workspace_id
 LEFT JOIN workflow.task_import_item i ON i.task_id=t.id AND i.workspace_id=t.workspace_id
 LEFT JOIN workflow.task_import_batch b ON b.id=i.batch_id AND b.workspace_id=i.workspace_id AND b.status='confirmed'
 WHERE t.id=p_id AND t.workspace_id=ws;
 RETURN result||COALESCE(extra,'{}'::jsonb);
END $$;

-- Reject deployment-specific default privileges too. Runtime families are
-- restored explicitly below; worker reads go only through Feishu source.
DO $$ DECLARE signature text; grantee record; relation text;
BEGIN
 FOREACH signature IN ARRAY ARRAY['security.task_import_is_creator(uuid)',
  'security.task_import_batch_summary(uuid)','security.task_import_batch_page(text,integer,integer)',
  'workflow.guard_task_import_batch()',
  'workflow.guard_task_import_item()','workflow.check_task_import_confirmation()',
  'ops.audit_task_import()',
  'ops.feishu_source_before_task_import(uuid,text,uuid)'] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',signature);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',signature,grantee.rolname);
  END LOOP;
 END LOOP;
 FOREACH relation IN ARRAY ARRAY['workflow.task_import_batch','workflow.task_import_item'] LOOP
  EXECUTE format('REVOKE ALL ON TABLE %s FROM PUBLIC',relation);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_class c
   CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
   JOIN pg_roles r ON r.oid=a.grantee WHERE c.oid=relation::regclass AND a.grantee<>c.relowner LOOP
   EXECUTE format('REVOKE ALL ON TABLE %s FROM %I',relation,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION ops.feishu_source(uuid,text,uuid) TO salegent_feishu_worker;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_task_import;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_before_task_import();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper
  AND NOT pg_has_role(oid,'salegent_feishu_worker','member')
  AND has_schema_privilege(oid,'security','USAGE')
  AND has_function_privilege(oid,'security.save_account_authorization(uuid,jsonb)','EXECUTE') LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON workflow.task_import_batch TO %I',r.rolname);
  EXECUTE format('GRANT SELECT,INSERT ON workflow.task_import_item TO %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.task_import_is_creator(uuid),security.task_import_batch_summary(uuid),security.task_import_batch_page(text,integer,integer) TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_task_import()
 FROM PUBLIC,salegent_feishu_worker;
DO $$ DECLARE signature text; grantee record; BEGIN
 FOREACH signature IN ARRAY ARRAY['security.reconcile_runtime_grants()',
  'security.reconcile_runtime_grants_before_task_import()'] LOOP
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',signature,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();

COMMENT ON TABLE workflow.task_import_batch IS '本人周会任务导入原文与持久草稿；人工确认后不可改，不扩展任务读取权限';
COMMENT ON TABLE workflow.task_import_item IS '每条已确认导入与普通任务的一对一来源链接；原负责人快照只作追溯，当前负责人仍取 task_assignee';
COMMENT ON FUNCTION security.task_import_batch_summary(uuid) IS 'API-only confirmed batch headers and task links filtered by existing task.read scopes; no source, hidden items or global counts';
COMMENT ON FUNCTION security.task_import_batch_page(text,integer,integer) IS 'API-only bounded safe headers; creator drafts and task.read-visible confirmed batches, no source, task IDs or hidden counts';
COMMENT ON FUNCTION ops.feishu_source(uuid,text,uuid) IS 'System-to-Feishu projection; task progress and minimal import metadata, never meeting source text or draft items';

-- Keep the reviewed policy allowlist explicit. Only this new capability joins
-- the existing revisioned/published company-rule workflow.
CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT p_code IN (
 'opportunity_amount','customer_quadrant','visit_admission','home_display','task_schedule','fde_capabilities',
 'score.maturity','score.efficiency','score.competency',
 'agent_execution.battle_map_review','agent_execution.opportunity_draft','agent_execution.personal_risks',
 'agent_execution.visit_entry','agent_execution.visit_quality','agent_execution.today_tasks',
 'agent_execution.operating_report','agent_execution.chatbi','agent_execution.customer_advice',
 'agent_execution.opportunity_advice','agent_execution.visit_advice','agent_execution.competency_review',
 'agent_execution.task_import',
 'agent_business.battle_map_review','agent_business.opportunity_draft','agent_business.personal_risks',
 'agent_business.visit_entry','agent_business.visit_quality','agent_business.today_tasks',
 'agent_business.operating_report','agent_business.chatbi','agent_business.customer_advice',
 'agent_business.opportunity_advice','agent_business.visit_advice','agent_business.competency_review',
 'agent_business.task_import');
$$;
