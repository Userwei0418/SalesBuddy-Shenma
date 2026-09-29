BEGIN;

-- V156's column REVOKE did not override V152's table-level UPDATE.  Convert
-- every non-owner table grant (including PUBLIC and inherited grant sources)
-- to the pre-publication column set, and repeat this after runtime reconciliation.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v156;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE reconciled integer; permission record; grants jsonb; column_grants jsonb; columns_sql text;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v156();
 SELECT string_agg(quote_ident(attname),',' ORDER BY attnum) INTO columns_sql
  FROM pg_attribute WHERE attrelid='insight.weekly_report'::regclass
   AND attnum>0 AND NOT attisdropped
   AND attname NOT IN ('feishu_publish_event_id','feishu_publish_requested_at','feishu_publish_snapshot');
 -- Capture the complete grant graph before CASCADE removes dependent grants.
 SELECT COALESCE(jsonb_agg(jsonb_build_object('grantee',a.grantee,
   'role',r.rolname,'grantable',a.is_grantable)),'[]'::jsonb) INTO grants
  FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE c.oid='insight.weekly_report'::regclass AND a.privilege_type='UPDATE'
   AND a.grantee<>c.relowner;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('grantee',acl.grantee,'role',r.rolname,
   'column_name',a.attname,'grantable',acl.is_grantable)),'[]'::jsonb) INTO column_grants
  FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
  CROSS JOIN LATERAL aclexplode(a.attacl) acl LEFT JOIN pg_roles r ON r.oid=acl.grantee
  WHERE c.oid='insight.weekly_report'::regclass AND acl.privilege_type='UPDATE'
   AND acl.grantee<>c.relowner AND a.attnum>0 AND NOT a.attisdropped
   AND a.attname NOT IN ('feishu_publish_event_id','feishu_publish_requested_at','feishu_publish_snapshot');
 FOR permission IN SELECT * FROM jsonb_to_recordset(grants)
   AS g(grantee oid,role text,grantable boolean)
 LOOP
  EXECUTE format('REVOKE UPDATE ON insight.weekly_report FROM %s CASCADE',
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.role) END);
 END LOOP;
 FOR permission IN SELECT * FROM jsonb_to_recordset(grants)
   AS g(grantee oid,role text,grantable boolean)
 LOOP
  EXECUTE format('GRANT UPDATE (%s) ON insight.weekly_report TO %s%s',columns_sql,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.role) END,
   CASE WHEN permission.grantable THEN ' WITH GRANT OPTION' ELSE '' END);
  reconciled:=reconciled+1;
 END LOOP;
 -- Preserve narrower legacy column grants if their grant-option ancestor was
 -- removed by CASCADE. Regranting from the owner removes that dependency.
 FOR permission IN SELECT * FROM jsonb_to_recordset(column_grants)
   AS g(grantee oid,role text,column_name text,grantable boolean)
 LOOP
  EXECUTE format('GRANT UPDATE (%I) ON insight.weekly_report TO %s%s',permission.column_name,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.role) END,
   CASE WHEN permission.grantable THEN ' WITH GRANT OPTION' ELSE '' END);
 END LOOP;
 -- Also remove explicit column grants, which survive a table-level REVOKE.
 FOR permission IN SELECT DISTINCT acl.grantee,r.rolname FROM pg_attribute a
  JOIN pg_class c ON c.oid=a.attrelid CROSS JOIN LATERAL aclexplode(a.attacl) acl
  LEFT JOIN pg_roles r ON r.oid=acl.grantee
  WHERE c.oid='insight.weekly_report'::regclass AND acl.privilege_type='UPDATE'
   AND acl.grantee<>c.relowner
   AND a.attname IN ('feishu_publish_event_id','feishu_publish_requested_at','feishu_publish_snapshot')
 LOOP
  EXECUTE format('REVOKE UPDATE (feishu_publish_event_id,feishu_publish_requested_at,feishu_publish_snapshot) ON insight.weekly_report FROM %s CASCADE',
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
 END LOOP;
 RETURN reconciled;
END $$;

-- Preserve the maintenance function's owner and exact execution ACL.
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='security.reconcile_runtime_grants_v156()'::regprocedure;
 FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END); END LOOP;
 EXECUTE format('ALTER FUNCTION security.reconcile_runtime_grants() OWNER TO %I',function_owner);
 FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants_v156()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO %s%s',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
  CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END); END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();

CREATE OR REPLACE FUNCTION insight.freeze_weekly_publish() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
 -- Reports are always born unpublished, even for a privileged importer.
 IF TG_OP='INSERT' THEN
  IF NEW.feishu_publish_event_id IS NOT NULL OR NEW.feishu_publish_requested_at IS NOT NULL
     OR NEW.feishu_publish_snapshot IS NOT NULL THEN
   RAISE EXCEPTION 'WEEKLY_FEISHU_INITIAL_SNAPSHOT_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
 END IF;
 IF OLD.feishu_publish_event_id IS NOT NULL AND (
   NEW.feishu_publish_event_id IS DISTINCT FROM OLD.feishu_publish_event_id OR
   NEW.feishu_publish_requested_at IS DISTINCT FROM OLD.feishu_publish_requested_at OR
   NEW.feishu_publish_snapshot IS DISTINCT FROM OLD.feishu_publish_snapshot
 ) THEN
  RAISE EXCEPTION 'WEEKLY_FEISHU_SNAPSHOT_IMMUTABLE';
 END IF;
 IF OLD.feishu_publish_event_id IS NULL AND (
   NEW.feishu_publish_event_id IS DISTINCT FROM OLD.feishu_publish_event_id OR
   NEW.feishu_publish_requested_at IS DISTINCT FROM OLD.feishu_publish_requested_at OR
   NEW.feishu_publish_snapshot IS DISTINCT FROM OLD.feishu_publish_snapshot
 ) THEN
  -- A SECURITY INVOKER trigger observes the definer identity only while the
  -- controlled publication function runs. A caller-set GUC is not authority.
  IF current_user IS DISTINCT FROM (SELECT pg_get_userbyid(proowner) FROM pg_proc
       WHERE oid='security.publish_weekly_report_feishu(uuid,integer)'::regprocedure) THEN
   RAISE EXCEPTION 'WEEKLY_FEISHU_PUBLICATION_WRITE_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF NEW.feishu_publish_event_id IS NULL OR NEW.feishu_publish_requested_at IS NULL
     OR NEW.feishu_publish_snapshot IS NULL THEN
   RAISE EXCEPTION 'WEEKLY_FEISHU_SNAPSHOT_INCOMPLETE';
  END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER freeze_weekly_publish ON insight.weekly_report;
CREATE TRIGGER freeze_weekly_publish BEFORE INSERT OR UPDATE ON insight.weekly_report
 FOR EACH ROW EXECUTE FUNCTION insight.freeze_weekly_publish();

CREATE OR REPLACE FUNCTION security.publish_weekly_report_feishu(p_id uuid,p_version integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r insight.weekly_report%ROWTYPE; c config.feishu_connection%ROWTYPE;
 event_id uuid; snapshot jsonb; source jsonb; source_id uuid;
 published_at timestamptz:=clock_timestamp();
BEGIN
 -- Publication must observe grants as of this action, including a revocation
 -- after the report/source was previously read on this database connection.
 PERFORM security.authorization_refresh();
 IF security.authorization_has('weekly_report.edit') IS NOT TRUE
    OR security.authorization_has('weekly_report.read') IS NOT TRUE THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO r FROM insight.weekly_report WHERE id=p_id
  AND workspace_id=common.current_workspace_id() AND author_id=common.current_user_ref_id() FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 IF security.authorization_has('visit.read') IS NOT TRUE THEN
  RAISE EXCEPTION 'WEEKLY_SOURCE_ACCESS_CHANGED' USING ERRCODE='42501'; END IF;
 source:=r.input_snapshot::jsonb;
 IF jsonb_typeof(source->'records') IS DISTINCT FROM 'array'
    OR jsonb_typeof(source->'context'->'customers') IS DISTINCT FROM 'array'
    OR jsonb_typeof(source->'context'->'opportunities') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'WEEKLY_SOURCE_ACCESS_CHANGED' USING ERRCODE='42501'; END IF;
 IF jsonb_array_length(source->'records')=0 OR jsonb_array_length(source->'context'->'customers')=0 THEN
  RAISE EXCEPTION 'WEEKLY_SOURCE_ACCESS_CHANGED' USING ERRCODE='42501'; END IF;
 -- SECURITY DEFINER bypasses table RLS, so repeat its authoritative read
 -- predicates explicitly, with literal resource permissions (no feature GUC).
 -- Check on replay as well: revocation must not return a prior publication.
 FOR source_id IN SELECT (value->>'id')::uuid FROM jsonb_array_elements(source->'records') LOOP
  IF NOT EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=source_id
    AND v.workspace_id=r.workspace_id AND v.deleted_at IS NULL AND (
     security.authorization_visit('visit.read',v.id)
     OR (v.created_by_user_ref_id=common.current_user_ref_id() AND v.recorder_user_ref_id=common.current_user_ref_id()
       AND security.authorization_visit_target('visit.read',v.customer_id,v.opportunity_id,v.recorder_user_ref_id,v.recorder_team_id))
     OR (v.opportunity_id IS NULL AND security.authorization_allows('visit.read',v.workspace_id,
       v.recorder_user_ref_id,ARRAY[v.recorder_team_id],false)))) THEN
   RAISE EXCEPTION 'WEEKLY_SOURCE_ACCESS_CHANGED' USING ERRCODE='42501'; END IF;
 END LOOP;
 FOR source_id IN SELECT (value->>'id')::uuid FROM jsonb_array_elements(source->'context'->'customers') LOOP
  IF NOT EXISTS(SELECT 1 FROM crm.customer customer WHERE customer.id=source_id
    AND customer.workspace_id=r.workspace_id AND customer.deleted_at IS NULL AND (
     security.authorization_customer('customer.read',customer.id)
     OR security.authorization_allows('customer.read',customer.workspace_id,customer.owner_user_ref_id,
       ARRAY[customer.owner_team_id],customer.owner_user_ref_id=common.current_user_ref_id()))) THEN
   RAISE EXCEPTION 'WEEKLY_SOURCE_ACCESS_CHANGED' USING ERRCODE='42501'; END IF;
 END LOOP;
 FOR source_id IN SELECT (value->>'id')::uuid FROM jsonb_array_elements(source->'context'->'opportunities') LOOP
  IF NOT EXISTS(SELECT 1 FROM crm.opportunity opportunity WHERE opportunity.id=source_id
    AND opportunity.workspace_id=r.workspace_id AND opportunity.deleted_at IS NULL AND (
     security.authorization_opportunity('opportunity.read',opportunity.id)
     OR security.authorization_allows('opportunity.read',opportunity.workspace_id,opportunity.owner_user_ref_id,
       ARRAY[opportunity.owner_team_id],opportunity.owner_user_ref_id=common.current_user_ref_id()))) THEN
   RAISE EXCEPTION 'WEEKLY_SOURCE_ACCESS_CHANGED' USING ERRCODE='42501'; END IF;
 END LOOP;
 -- A valid replay returns the same event even when configuration is paused.
 IF r.feishu_publish_event_id IS NOT NULL THEN RETURN r.feishu_publish_event_id; END IF;
 IF r.status IS DISTINCT FROM 'succeeded' OR r.result_status IS DISTINCT FROM 'ready'
    OR NULLIF(btrim(r.draft_markdown),'') IS NULL THEN
  RAISE EXCEPTION 'WEEKLY_DRAFT_NOT_READY' USING ERRCODE='22023'; END IF;
 IF p_version IS NULL OR r.draft_version<>p_version THEN
  RAISE EXCEPTION 'WEEKLY_DRAFT_VERSION_CONFLICT' USING ERRCODE='22023'; END IF;
 SELECT * INTO c FROM config.feishu_connection WHERE workspace_id=r.workspace_id FOR SHARE;
 IF NOT FOUND OR NOT c.enabled OR c.validated_revision IS DISTINCT FROM c.revision
   OR c.settings->>'direction' IS DISTINCT FROM 'system_to_base'
   OR COALESCE((c.settings->'mappings'->'weekly_report'->>'enabled')::boolean,false) IS NOT TRUE
   OR COALESCE((c.settings->'notification'->>'enabled')::boolean,false) IS NOT TRUE
   OR c.settings->'notification'->>'routing_mode' IS DISTINCT FROM 'single_group'
   OR NULLIF(c.settings->'notification'->>'default_chat_id','') IS NULL
   OR NOT (COALESCE(c.settings->'notification'->'on_create','[]'::jsonb) ? 'weekly_report') THEN
  RAISE EXCEPTION 'WEEKLY_FEISHU_NOT_READY' USING ERRCODE='22023'; END IF;
 event_id:=gen_random_uuid();
 snapshot:=jsonb_build_object('id',r.id,'author_id',r.author_id,'status',r.status,
  'result_status',r.result_status,'title',r.original_result->>'title','draft_markdown',r.draft_markdown,
  'draft_version',r.draft_version,'statistics',r.original_result->'statistics','report_week',r.report_week,
  'source_cutoff_at',r.source_cutoff_at,'snapshot_at',source->'context'->>'as_of',
  'created_at',r.created_at,'updated_at',r.updated_at,'finished_at',r.finished_at,'published_at',published_at);
 UPDATE insight.weekly_report SET feishu_publish_event_id=event_id,
  feishu_publish_requested_at=published_at,feishu_publish_snapshot=snapshot WHERE id=p_id;
 INSERT INTO ops.feishu_event(id,connection_id,workspace_id,object_kind,object_id,first_formal_create)
  VALUES(event_id,c.id,r.workspace_id,'weekly_report',p_id,true);
 RETURN event_id;
END $$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V157','Close weekly publication write bypass and recheck source authorization');
COMMIT;
