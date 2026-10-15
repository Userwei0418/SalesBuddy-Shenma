-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V194__task_import_draft_reparse.sql.
-- Original SHA-256: 99591055d41bb437f1d1e15b5d5aa55cfd0b11ae6004cd5eb600ccb85fe1183b. See shenma_whole_121_manifest.json.
BEGIN;

-- Private comparison only: normalize draft defaults and equivalent timezone
-- spellings without granting an API permission to read other people's drafts.
CREATE FUNCTION workflow.task_import_editable_projection(p_items jsonb,p_source boolean)
RETURNS jsonb LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object(
  'item_id',(item->>'item_id')::uuid,
  'selected',CASE WHEN p_source THEN true ELSE COALESCE((item->>'selected')::boolean,true) END,
  'description',item->>'description','assignee_account_code',item->>'assignee_account_code',
  'due_at',(item->>'due_at')::timestamptz,
  'priority_code',COALESCE(item->>'priority_code','normal'),
  'association_kind',COALESCE(item->>'association_kind','daily'),
  'customer_id',(item->>'customer_id')::uuid,'opportunity_id',(item->>'opportunity_id')::uuid,
  'duplicate_acknowledged',CASE WHEN p_source THEN false ELSE COALESCE((item->>'duplicate_acknowledged')::boolean,false) END
 ) ORDER BY position),'[]'::jsonb)
 FROM jsonb_array_elements(p_items) WITH ORDINALITY source(item,position);
$$;
REVOKE ALL ON FUNCTION workflow.task_import_editable_projection(jsonb,boolean) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION workflow.guard_task_import_batch() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE item jsonb; source_ids uuid[]:='{}'; edit_ids uuid[]:='{}'; item_id uuid;
 reparse boolean:=COALESCE(current_setting('app.task_import_reparse',true),'')='true';
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Task import source is immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'draft' OR NEW.version_no<>1 THEN
   RAISE EXCEPTION 'Task import must start as a draft' USING ERRCODE='23514'; END IF;
  IF NEW.source_hash IS DISTINCT FROM encode(sha256(convert_to(NEW.source_text,'UTF8')),'hex') THEN
   RAISE EXCEPTION 'Task import source hash mismatch' USING ERRCODE='23514'; END IF;
  IF NEW.editable_items='[]'::jsonb THEN NEW.editable_items:=NEW.items; END IF;
 ELSIF reparse THEN
  -- The flag only selects this operation. Every authorization and immutable
  -- source check remains mandatory even when a caller sets the flag itself.
  IF OLD.workspace_id IS DISTINCT FROM common.current_workspace_id()
   OR OLD.creator_user_ref_id IS DISTINCT FROM common.current_user_ref_id()
   OR NOT security.workspace_is_active(OLD.workspace_id)
   OR NOT (security.authorization_has('task.create_daily') OR security.authorization_has('task.create_customer')) THEN
   RAISE EXCEPTION 'Task import reparse is not authorized' USING ERRCODE='42501'; END IF;
  IF OLD.status<>'draft' OR NEW.status<>'draft' OR NEW.version_no<>OLD.version_no+1
   OR NEW.parse_method<>'ai'
   OR (to_jsonb(NEW)-ARRAY['items','editable_items','parse_method','parse_warnings','version_no','updated_at'])
    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['items','editable_items','parse_method','parse_warnings','version_no','updated_at'])
   OR workflow.task_import_editable_projection(OLD.editable_items,false)
    IS DISTINCT FROM workflow.task_import_editable_projection(OLD.items,true)
   OR workflow.task_import_editable_projection(NEW.editable_items,false)
    IS DISTINCT FROM workflow.task_import_editable_projection(NEW.items,true)
   OR EXISTS(SELECT 1 FROM workflow.task_import_item WHERE batch_id=OLD.id)
   THEN RAISE EXCEPTION 'Task import reparse would overwrite reviewed content or source' USING ERRCODE='23514'; END IF;
  NEW.updated_at:=clock_timestamp();
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
-- CREATE OR REPLACE retains a historical function owner. This updated guard
-- now depends on the new private helper, so keep both under this migration's
-- maintenance identity instead of granting the dependency to old/runtime roles.
ALTER FUNCTION workflow.guard_task_import_batch() OWNER TO CURRENT_USER;

CREATE FUNCTION security.reparse_task_import_batch(p_batch uuid,p_version integer,p_items jsonb,p_warnings jsonb,p_editable jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE batch workflow.task_import_batch; previous_flag text; changed uuid;
BEGIN
 IF p_version IS NULL OR p_version<1 OR jsonb_typeof(p_items) IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_editable) IS DISTINCT FROM 'array' OR jsonb_typeof(p_warnings) IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_items) NOT BETWEEN 1 AND 100 OR jsonb_array_length(p_editable) NOT BETWEEN 1 AND 100
  OR jsonb_array_length(p_warnings)>100 OR octet_length(p_items::text)>8388608
  OR octet_length(p_editable::text)>8388608 OR octet_length(p_warnings::text)>65536 THEN
  RAISE EXCEPTION 'Invalid task import reparse payload' USING ERRCODE='22023'; END IF;
 IF NOT security.workspace_is_active(common.current_workspace_id())
  OR NOT (security.authorization_has('task.create_daily') OR security.authorization_has('task.create_customer')) THEN
  RETURN NULL; END IF;
 SELECT * INTO batch FROM workflow.task_import_batch WHERE id=p_batch
  AND workspace_id=common.current_workspace_id() AND creator_user_ref_id=common.current_user_ref_id() FOR UPDATE;
 IF NOT FOUND OR batch.status<>'draft' OR batch.version_no<>p_version
  OR EXISTS(SELECT 1 FROM workflow.task_import_item WHERE batch_id=p_batch)
  OR workflow.task_import_editable_projection(batch.editable_items,false)
   IS DISTINCT FROM workflow.task_import_editable_projection(batch.items,true) THEN RETURN NULL; END IF;
 previous_flag:=COALESCE(current_setting('app.task_import_reparse',true),'');
 PERFORM set_config('app.task_import_reparse','true',true);
 UPDATE workflow.task_import_batch SET parse_method='ai',parse_warnings=p_warnings,items=p_items,
  editable_items=p_editable,version_no=version_no+1 WHERE id=p_batch RETURNING id INTO changed;
 PERFORM set_config('app.task_import_reparse',previous_flag,true);
 RETURN changed;
END $$;
REVOKE ALL ON FUNCTION security.reparse_task_import_batch(uuid,integer,jsonb,jsonb,jsonb) FROM PUBLIC,salegent_feishu_worker;
COMMENT ON FUNCTION security.reparse_task_import_batch(uuid,integer,jsonb,jsonb,jsonb)
 IS 'Replace only an owned unedited undispatched draft after successful AI parsing; original meeting source is immutable';

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_task_reparse;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; runtime record;
BEGIN
 total:=security.reconcile_runtime_grants_before_task_reparse();
 FOR runtime IN SELECT r.rolname FROM pg_roles r WHERE NOT r.rolsuper AND NOT r.rolbypassrls AND left(r.rolname,3)<>'pg_'
  AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
  AND r.oid<>(SELECT relowner FROM pg_class WHERE oid='workflow.task_import_batch'::regclass)
  AND has_schema_privilege(r.oid,'security','USAGE')
  AND has_table_privilege(r.oid,'workflow.task_import_batch','INSERT')
  AND has_table_privilege(r.oid,'workflow.task_import_batch','UPDATE')
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.reparse_task_import_batch(uuid,integer,jsonb,jsonb,jsonb) TO %I',runtime.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_task_reparse()
 FROM PUBLIC,salegent_feishu_worker;
DO $$ DECLARE signature text; grantee record; BEGIN
 FOREACH signature IN ARRAY ARRAY['workflow.task_import_editable_projection(jsonb,boolean)',
  'security.reparse_task_import_batch(uuid,integer,jsonb,jsonb,jsonb)',
  'security.reconcile_runtime_grants()','security.reconcile_runtime_grants_before_task_reparse()'] LOOP
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',signature,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;
-- First application changes only this migration's narrow permission contract.
-- Existing installations may already have reviewed canonical runtime ACLs;
-- replaying historical reconciliation here would restore deliberately omitted
-- permissions. The wrapper above remains available for explicit maintenance.
DO $$ DECLARE runtime record; BEGIN
 FOR runtime IN SELECT r.rolname FROM pg_roles r WHERE NOT r.rolsuper AND NOT r.rolbypassrls AND left(r.rolname,3)<>'pg_'
  AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
  AND r.oid<>(SELECT relowner FROM pg_class WHERE oid='workflow.task_import_batch'::regclass)
  AND has_schema_privilege(r.oid,'security','USAGE')
  AND has_table_privilege(r.oid,'workflow.task_import_batch','INSERT')
  AND has_table_privilege(r.oid,'workflow.task_import_batch','UPDATE')
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.reparse_task_import_batch(uuid,integer,jsonb,jsonb,jsonb) TO %I',runtime.rolname);
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V342','Guarded AI reparse of untouched meeting-task drafts without changing source or dispatched tasks');
COMMIT;
