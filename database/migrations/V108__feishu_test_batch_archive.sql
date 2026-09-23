BEGIN;
SET LOCAL check_function_bodies=on;

-- This entry point is deliberately not a generic delete API. Its owner must see
-- FORCE RLS rows; callers never inherit that owner's table privileges.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_user AND (rolsuper OR rolbypassrls)) THEN
  RAISE EXCEPTION 'V108 requires the audited migration owner with complete RLS visibility';
 END IF;
END $$;

-- Extract exact typed references, not substring matches or business text. This
-- helper has no table access and is not exposed to application/worker roles.
CREATE FUNCTION security.feishu_test_reference_ids(p_value jsonb) RETURNS uuid[]
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 WITH RECURSIVE nodes(value,key) AS (
  SELECT p_value,''::text
  UNION ALL
  SELECT child.value,child.key FROM nodes n CROSS JOIN LATERAL (
   SELECT e.value,CASE WHEN e.key='id' AND (
     n.key=ANY(ARRAY['customer','customers','opportunity','opportunities','visit','visits','contact','contacts',
      'risk','risks','task','tasks','artifact','artifacts','run','runs'])
     OR COALESCE(n.value->>'object_type',n.value->>'type')=ANY(ARRAY['customer','opportunity','visit','contact','risk','task','artifact','run']))
    THEN 'subject_id' ELSE e.key END
   FROM jsonb_each(CASE WHEN jsonb_typeof(n.value)='object' THEN n.value ELSE '{}'::jsonb END) e
   UNION ALL
   SELECT a.value,n.key FROM jsonb_array_elements(CASE WHEN jsonb_typeof(n.value)='array' THEN n.value ELSE '[]'::jsonb END) a
  ) child
 )
 SELECT COALESCE(array_agg(DISTINCT (value#>>'{}')::uuid),'{}'::uuid[]) FROM nodes
 WHERE key=ANY(ARRAY['customer_id','opportunity_id','visit_id','source_visit_id','contact_id',
  'subject_id','aggregate_id','trigger_id','customerId','opportunityId','visitId','contactId',
  'risk_id','task_id','artifact_id','source_artifact_id','run_id','event_id','assessment_id',
  'business_change_id','correlation_id','source_id','object_id','source_run_id','source_import_id',
  'customer_ids','opportunity_ids','visit_ids','contact_ids'])
 AND jsonb_typeof(value)='string'
 AND (value#>>'{}') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
$$;
REVOKE ALL ON FUNCTION security.feishu_test_reference_ids(jsonb) FROM PUBLIC,salegent_feishu_worker;

CREATE FUNCTION security.feishu_test_archive(p_manifest jsonb,p_expected_plan text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET timezone='UTC' AS $$
DECLARE
 ws uuid:=common.current_workspace_id(); actor uuid:=common.current_user_ref_id();
 creator uuid; record_creator uuid; run uuid; started timestamptz; ended timestamptz; prefix text; reason text;
 ids uuid[]; cid uuid; oid uuid; vid uuid; contact_ids uuid[];
 receipt jsonb; mutation jsonb; item record; birth record; own_birth record; customer_birth record;
 row_data record; root_metadata jsonb:='[]'; dependencies jsonb:='[]';
 manifest_hash text; plan_hash text; previous jsonb; stamp timestamptz; changed integer;
 expected_count integer; graph_ids uuid[]; deps jsonb;
 marker jsonb; record_kind text; table_label text; authorized_ops jsonb:='[]'; mutation_ops jsonb;
BEGIN
 IF common.current_role_code() IS DISTINCT FROM 'administrator'
  OR NOT security.has_active_role('administrator') OR ws IS NULL OR actor IS NULL
  OR NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
    WHERE u.id=actor AND u.workspace_id=ws AND u.status='active' AND u.deleted_at IS NULL
      AND w.status='active' AND w.deleted_at IS NULL) THEN
  RETURN jsonb_build_object('code','WORKSPACE_OR_ROLE_MISMATCH');
 END IF;
 -- The function must fail, rather than return an incomplete inventory, if its
 -- owner/ACL changes or a later schema has not had this fixed graph reviewed.
 IF row_security_active('insight.business_advice'::regclass)
  OR row_security_active('workflow.notification'::regclass)
  OR EXISTS(SELECT 1 FROM ops.schema_migration WHERE version ~ '^V[0-9]+$' AND substring(version FROM 2)::int>108) THEN
  RETURN jsonb_build_object('code','ARCHIVE_SCHEMA_OR_OWNER_REVIEW_REQUIRED');
 END IF;
 IF jsonb_typeof(p_manifest) IS DISTINCT FROM 'object'
  OR NOT p_manifest ?& ARRAY['workspace_id','creator_id','run_id','started_at','ended_at','records','reason']
  OR p_manifest-ARRAY['workspace_id','creator_id','run_id','started_at','ended_at','records','reason','mutations']<>'{}'::jsonb
  OR jsonb_typeof(p_manifest->'records') IS DISTINCT FROM 'array'
  OR jsonb_typeof(p_manifest->'reason') IS DISTINCT FROM 'string'
  OR jsonb_typeof(COALESCE(p_manifest->'mutations','[]'::jsonb)) IS DISTINCT FROM 'array' THEN
  RETURN jsonb_build_object('code','INVALID_ARCHIVE_MANIFEST');
 END IF;
 BEGIN
  IF (p_manifest->>'workspace_id')::uuid IS DISTINCT FROM ws THEN
   RETURN jsonb_build_object('code','WORKSPACE_OR_ROLE_MISMATCH');
  END IF;
  creator:=(p_manifest->>'creator_id')::uuid; run:=(p_manifest->>'run_id')::uuid;
  started:=(p_manifest->>'started_at')::timestamptz; ended:=(p_manifest->>'ended_at')::timestamptz;
  reason:=p_manifest->>'reason'; expected_count:=jsonb_array_length(p_manifest->'records');
  IF creator IS NULL OR run IS NULL OR NOT isfinite(started) OR NOT isfinite(ended)
   OR started IS NULL OR ended IS NULL OR ended<=started OR ended-started>interval '24 hours'
   OR p_manifest->>'started_at' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
   OR p_manifest->>'ended_at' !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
   OR reason IS NULL OR length(btrim(reason)) NOT BETWEEN 10 AND 300 OR expected_count NOT BETWEEN 1 AND 8 THEN
   RETURN jsonb_build_object('code','INVALID_ARCHIVE_MANIFEST');
  END IF;
  FOR receipt IN SELECT value FROM jsonb_array_elements(p_manifest->'records') LOOP
   IF jsonb_typeof(receipt) IS DISTINCT FROM 'object'
    OR NOT receipt ?& ARRAY['kind','id','version','creation_request_id','creator_id']
    OR receipt-ARRAY['kind','id','version','creation_request_id','creator_id']<>'{}'::jsonb
    OR jsonb_typeof(receipt->'kind') IS DISTINCT FROM 'string'
    OR receipt->>'kind' NOT IN ('customer','opportunity','visit','contact')
    OR (receipt->>'id')::uuid IS NULL OR (receipt->>'creation_request_id')::uuid IS NULL OR (receipt->>'creator_id')::uuid IS NULL
    OR jsonb_typeof(receipt->'version') IS DISTINCT FROM 'number'
    OR (receipt->>'version')::int<1 THEN
    RETURN jsonb_build_object('code','INVALID_ARCHIVE_MANIFEST');
   END IF;
  END LOOP;
  SELECT array_agg((value->>'id')::uuid) INTO ids FROM jsonb_array_elements(p_manifest->'records');
  IF cardinality(ids)<>(SELECT count(DISTINCT id) FROM unnest(ids) id)
   OR (SELECT count(*) FROM jsonb_array_elements(p_manifest->'records') r WHERE r->>'kind'='customer')<>1
   OR (SELECT count(*) FROM jsonb_array_elements(p_manifest->'records') r WHERE r->>'kind'='opportunity')>1
   OR (SELECT count(*) FROM jsonb_array_elements(p_manifest->'records') r WHERE r->>'kind'='visit')>1 THEN
   RETURN jsonb_build_object('code','INVALID_ARCHIVE_MANIFEST');
  END IF;
  SELECT (value->>'id')::uuid INTO cid FROM jsonb_array_elements(p_manifest->'records') WHERE value->>'kind'='customer';
  SELECT (value->>'id')::uuid INTO oid FROM jsonb_array_elements(p_manifest->'records') WHERE value->>'kind'='opportunity';
  SELECT (value->>'id')::uuid INTO vid FROM jsonb_array_elements(p_manifest->'records') WHERE value->>'kind'='visit';
  SELECT COALESCE(array_agg((value->>'id')::uuid),'{}'::uuid[]) INTO contact_ids
   FROM jsonb_array_elements(p_manifest->'records') WHERE value->>'kind'='contact';
  IF vid IS NOT NULL AND oid IS NULL THEN RETURN jsonb_build_object('code','INVALID_ARCHIVE_MANIFEST'); END IF;
  IF jsonb_array_length(COALESCE(p_manifest->'mutations','[]'))>16 THEN RETURN jsonb_build_object('code','INVALID_ARCHIVE_MANIFEST'); END IF;
  FOR mutation IN SELECT value FROM jsonb_array_elements(COALESCE(p_manifest->'mutations','[]')) LOOP
   IF jsonb_typeof(mutation) IS DISTINCT FROM 'object' OR NOT mutation ?& ARRAY['request_id','actor_id']
    OR mutation-ARRAY['request_id','actor_id']<>'{}'::jsonb
    OR (mutation->>'request_id')::uuid IS NULL OR (mutation->>'actor_id')::uuid IS NULL THEN
    RETURN jsonb_build_object('code','INVALID_ARCHIVE_MANIFEST');
   END IF;
  END LOOP;
 EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format OR datetime_field_overflow OR numeric_value_out_of_range THEN
  RETURN jsonb_build_object('code','INVALID_ARCHIVE_MANIFEST');
 END;
 prefix:='飞书联调测试-'||left(replace(run::text,'-',''),8);
 manifest_hash:=encode(sha256(convert_to(p_manifest::text,'UTF8')),'hex');
 -- Serialize the same manifest, even when it has already become invisible to
 -- regular RLS. The UUID is exact and this lock is never a global write bypass.
 PERFORM pg_advisory_xact_lock(hashtextextended(ws::text||':'||run::text,108));
 SELECT after_snapshot INTO previous FROM ops.audit_log WHERE workspace_id=ws
  AND action_code='feishu.integration_test.archive' AND object_type='integration_test_batch' AND object_id=run;
 IF FOUND THEN
  IF previous->>'manifest_hash' IS DISTINCT FROM manifest_hash THEN
   RETURN jsonb_build_object('code','ARCHIVE_RECEIPT_MISMATCH');
  END IF;
  RETURN jsonb_build_object('already_archived',true,'manifest_hash',manifest_hash,'plan_hash',previous->>'plan_hash');
 END IF;

 -- Apply alone takes short, non-waiting table locks before reading the graph.
 -- These prevent hidden/JSON-only dependent writes from racing the negative
 -- check. They affect writers across tenants until the caller COMMITs, so the
 -- maintenance caller must commit immediately and do no network work here.
 IF p_expected_plan IS NOT NULL THEN
  BEGIN
   LOCK TABLE crm.customer,crm.opportunity,activity.visit,crm.contact,
    crm.customer_ownership,crm.customer_sales_member,crm.customer_assignment,
    crm.customer_claim_request,crm.customer_ownership_event,crm.customer_duplicate_candidate,
    crm.customer_product,crm.customer_actual,crm.opportunity_forecast,crm.business_change,
    crm.opportunity_participant,crm.opportunity_quote_reference,crm.opportunity_demo_scenes,
    activity.visit_contact,activity.visit_field_value,activity.visit_participant,activity.action_item,
    activity.visit_import,activity.visit_import_content,
    insight.quadrant_score,insight.risk,insight.risk_event,insight.recommendation,
    insight.business_advice,insight.business_suggestion,insight.customer_risk_assessment,
    insight.sales_competency_review,insight.report,
    workflow.task,workflow.task_assignee,workflow.task_candidate,workflow.task_event,
    workflow.notification,workflow.notification_delivery,
    agent.run,agent.artifact,agent.confirmation,agent.conversation,agent.message,
    agent.run_step,agent.tool_invocation,agent.model_invocation,agent.inference_operation,
    ops.job,ops.job_effect,ops.outbox_event,ops.audit_log IN SHARE ROW EXCLUSIVE MODE NOWAIT;
  EXCEPTION WHEN lock_not_available THEN RETURN jsonb_build_object('code','ARCHIVE_BUSY_RETRY');
  END;
 END IF;
 -- Static source branches: no caller-provided SQL, table names or query paths.
 FOR receipt IN SELECT value FROM jsonb_array_elements(p_manifest->'records')
  ORDER BY CASE value->>'kind' WHEN 'customer' THEN 1 WHEN 'opportunity' THEN 2 WHEN 'visit' THEN 3 ELSE 4 END,value->>'id'
 LOOP
  record_kind:=receipt->>'kind'; record_creator:=(receipt->>'creator_id')::uuid;
  IF NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE id=record_creator AND workspace_id=ws) THEN
   RETURN jsonb_build_object('code','CREATION_RECEIPT_NOT_PROVEN');
  END IF;
  IF record_kind='customer' THEN
   SELECT c.id,c.workspace_id,c.created_by_user_ref_id,c.created_at,c.version_no,c.deleted_at,c.name AS label,
    NULL::uuid AS customer_id,NULL::uuid AS opportunity_id INTO row_data
    FROM crm.customer c WHERE c.id=(receipt->>'id')::uuid AND c.workspace_id=ws FOR UPDATE;
   table_label:='crm.customer';
  ELSIF record_kind='opportunity' THEN
   SELECT o.id,o.workspace_id,o.created_by_user_ref_id,o.created_at,o.version_no,o.deleted_at,o.name AS label,
    o.customer_id,NULL::uuid AS opportunity_id INTO row_data
    FROM crm.opportunity o WHERE o.id=(receipt->>'id')::uuid AND o.workspace_id=ws FOR UPDATE;
   table_label:='crm.opportunity';
  ELSIF record_kind='visit' THEN
   SELECT v.id,v.workspace_id,v.created_by_user_ref_id,v.created_at,v.version_no,v.deleted_at,v.follow_up_record AS label,
    v.customer_id,v.opportunity_id INTO row_data
    FROM activity.visit v WHERE v.id=(receipt->>'id')::uuid AND v.workspace_id=ws FOR UPDATE;
   table_label:='activity.visit';
  ELSE
   SELECT c.id,c.workspace_id,c.created_by_user_ref_id,c.created_at,c.version_no,c.deleted_at,c.name AS label,
    c.customer_id,NULL::uuid AS opportunity_id INTO row_data
    FROM crm.contact c WHERE c.id=(receipt->>'id')::uuid AND c.workspace_id=ws FOR UPDATE;
   table_label:='crm.contact';
  END IF;
  IF NOT FOUND OR row_data.deleted_at IS NOT NULL OR row_data.version_no<>(receipt->>'version')::int THEN
   RETURN jsonb_build_object('code','MISSING_RETIRED_OR_CHANGED_RECORD');
  END IF;
  IF row_data.created_at NOT BETWEEN started AND ended
   OR (row_data.created_by_user_ref_id IS NOT NULL AND row_data.created_by_user_ref_id<>record_creator)
   OR (CASE WHEN record_kind='visit' THEN position(prefix IN COALESCE(row_data.label,''))=0
           ELSE left(COALESCE(row_data.label,''),length(prefix))<>prefix END) THEN
   RETURN jsonb_build_object('code','RECORD_OUTSIDE_AUTHORIZED_BATCH');
  END IF;
  IF record_kind<>'customer' AND row_data.customer_id IS DISTINCT FROM cid THEN
   RETURN jsonb_build_object('code','CUSTOMER_LINK_MISMATCH');
  END IF;
  SELECT a.id,a.transaction_id,a.occurred_at,a.actor_user_ref_id,a.after_snapshot->'version_no' AS initial_version INTO birth
   FROM ops.audit_log a WHERE a.workspace_id=ws AND a.object_id=row_data.id
    AND a.object_type=record_kind AND a.action_code=table_label||'.insert' AND a.before_snapshot IS NULL
    AND a.request_id=(receipt->>'creation_request_id')::uuid AND a.actor_user_ref_id=record_creator
    AND a.occurred_at BETWEEN started AND ended AND a.transaction_id IS NOT NULL
    AND a.after_snapshot->>'id'=row_data.id::text AND a.after_snapshot->>'workspace_id'=ws::text
    AND CASE WHEN record_kind='visit' THEN position(prefix IN COALESCE(a.after_snapshot->>'follow_up_record',''))>0
         ELSE left(COALESCE(a.after_snapshot->>'name',''),length(prefix))=prefix END
   ORDER BY a.id LIMIT 1;
  IF NOT FOUND THEN RETURN jsonb_build_object('code','CREATION_RECEIPT_NOT_PROVEN'); END IF;
  authorized_ops:=authorized_ops||jsonb_build_array(jsonb_build_object('request_id',receipt->>'creation_request_id',
   'actor_id',record_creator,'transaction_id',birth.transaction_id));
  IF record_kind='customer' THEN customer_birth:=birth; END IF;
  IF record_kind='visit' AND (row_data.opportunity_id IS DISTINCT FROM oid OR NOT EXISTS(
   SELECT 1 FROM activity.visit v WHERE v.id=vid AND v.workspace_id=ws AND v.status='archived'
    AND v.archived_at IS NOT NULL AND v.confirmed_at IS NOT NULL
    AND v.recorder_user_ref_id=record_creator AND v.confirmed_by_user_ref_id=record_creator)) THEN
   RETURN jsonb_build_object('code','VISIT_IDENTITY_OR_CONFIRMATION_MISMATCH');
  END IF;
  root_metadata:=root_metadata||jsonb_build_array(jsonb_build_object('kind',record_kind,'id',row_data.id,
   'version',row_data.version_no,'creator_id',record_creator,'birth_audit',birth.id,'transaction_id',birth.transaction_id));
 END LOOP;
 FOR mutation IN SELECT value FROM jsonb_array_elements(COALESCE(p_manifest->'mutations','[]')) LOOP
  SELECT jsonb_agg(DISTINCT jsonb_build_object('request_id',a.request_id,'actor_id',a.actor_user_ref_id,
    'transaction_id',a.transaction_id)) INTO mutation_ops FROM ops.audit_log a
   WHERE a.workspace_id=ws AND a.request_id=(mutation->>'request_id')::uuid
    AND a.actor_user_ref_id=(mutation->>'actor_id')::uuid AND a.object_id=ANY(ids)
    AND a.action_code IN ('crm.customer.update','crm.opportunity.update','crm.contact.update','activity.visit.update')
    AND a.transaction_id IS NOT NULL AND a.occurred_at BETWEEN started AND ended;
  IF mutation_ops IS NULL OR EXISTS(SELECT 1 FROM ops.audit_log a WHERE a.workspace_id=ws
    AND a.request_id=(mutation->>'request_id')::uuid AND a.actor_user_ref_id=(mutation->>'actor_id')::uuid
    AND a.action_code IN ('crm.customer.insert','crm.opportunity.insert','crm.contact.insert','activity.visit.insert',
     'crm.customer.update','crm.opportunity.update','crm.contact.update','activity.visit.update')
    AND NOT a.object_id=ANY(ids)) THEN
   RETURN jsonb_build_object('code','MUTATION_RECEIPT_NOT_PROVEN');
  END IF;
  authorized_ops:=authorized_ops||mutation_ops;
 END LOOP;
 IF EXISTS(SELECT 1 FROM ops.audit_log a WHERE a.workspace_id=ws AND a.object_id=ANY(ids)
   AND a.action_code IN ('crm.customer.update','crm.opportunity.update','crm.contact.update','activity.visit.update')
   AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(authorized_ops) op
    WHERE a.request_id=(op->>'request_id')::uuid AND a.actor_user_ref_id=(op->>'actor_id')::uuid
     AND a.transaction_id=(op->>'transaction_id')::bigint AND a.occurred_at BETWEEN started AND ended)) THEN
  RETURN jsonb_build_object('code','UNLISTED_RECORD_MUTATION');
 END IF;
 IF EXISTS(SELECT 1 FROM crm.opportunity WHERE customer_id=cid AND NOT id=ANY(ids))
  OR EXISTS(SELECT 1 FROM activity.visit WHERE (customer_id=cid OR opportunity_id=oid) AND NOT id=ANY(ids))
  OR EXISTS(SELECT 1 FROM crm.contact WHERE customer_id=cid AND NOT id=ANY(ids)) THEN
  RETURN jsonb_build_object('code','UNLISTED_CHILD_RECORD');
 END IF;
 -- A queued/running daily competency review has no visit_ids snapshot yet. It
 -- is shared by that user, so never infer independence from its empty JSON and
 -- never cancel/delete it as if it belonged exclusively to this test batch.
 IF vid IS NOT NULL AND EXISTS(SELECT 1 FROM insight.sales_competency_review r
   JOIN activity.visit v ON v.workspace_id=r.workspace_id AND v.recorder_user_ref_id=r.subject_user_ref_id
   WHERE v.id=vid AND v.workspace_id=ws AND r.status IN ('queued','running','failed')
    AND r.review_date>=timezone('Asia/Shanghai',v.archived_at)::date) THEN
  RETURN jsonb_build_object('code','SHARED_COMPETENCY_REVIEW_REQUIRES_REVIEW');
 END IF;
 -- Ownership has NO created_at. Only the same immutable INSERT transaction is
 -- proof of the customer's automatic initial ownership, never updated_at.
 SELECT a.id,a.after_snapshot INTO own_birth FROM ops.audit_log a
 WHERE a.workspace_id=ws AND a.object_id=cid AND a.object_type='customer_ownership'
  AND a.action_code='crm.customer_ownership.insert' AND a.before_snapshot IS NULL
  AND a.actor_user_ref_id=customer_birth.actor_user_ref_id AND a.transaction_id=customer_birth.transaction_id
  AND a.request_id=(SELECT (r->>'creation_request_id')::uuid FROM jsonb_array_elements(p_manifest->'records') r WHERE r->>'kind'='customer')
  AND a.occurred_at BETWEEN started AND ended
  AND a.after_snapshot->>'customer_id'=cid::text AND a.after_snapshot->>'workspace_id'=ws::text
 ORDER BY a.id LIMIT 1;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM crm.customer_ownership o WHERE o.customer_id=cid AND o.workspace_id=ws
    AND to_jsonb(o.version_no)=own_birth.after_snapshot->'version_no'
    AND to_jsonb(o.state)=own_birth.after_snapshot->'state'
    AND to_jsonb(o.owner_user_ref_id) IS NOT DISTINCT FROM NULLIF(own_birth.after_snapshot->'owner_user_ref_id','null'::jsonb)) THEN
  RETURN jsonb_build_object('code','OWNERSHIP_CREATION_RECEIPT_NOT_PROVEN');
 END IF;
 dependencies:=jsonb_build_array(jsonb_build_object('table','crm.customer_ownership','id',cid,'birth_audit',own_birth.id));


 -- Closed graph of fixed typed references; no information_schema visibility
 -- inference and no full-row/notification text/model payload is returned.
 SELECT ids||COALESCE(array_agg(x),'{}'::uuid[]) INTO graph_ids
 FROM activity.visit v CROSS JOIN LATERAL unnest(ARRAY[v.source_artifact_id,v.source_import_id]) x
 WHERE v.workspace_id=ws AND v.id=vid AND x IS NOT NULL;
 WITH RECURSIVE nodes AS MATERIALIZED (
 SELECT 'crm.customer'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,(lifecycle_status)::text AS state,
 'block'::text AS retention,jsonb_build_object('id',id) AS audit_key FROM crm.customer WHERE NOT id=ANY(ids)
 UNION ALL
 SELECT 'crm.opportunity'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,(status)::text AS state,
 'block'::text AS retention,jsonb_build_object('id',id) AS audit_key FROM crm.opportunity WHERE NOT id=ANY(ids)
 UNION ALL
 SELECT 'crm.contact'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,('contact')::text AS state,
 'block'::text AS retention,jsonb_build_object('id',id) AS audit_key FROM crm.contact WHERE NOT id=ANY(ids)
 UNION ALL
 SELECT 'activity.visit'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,opportunity_id,source_artifact_id,source_import_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,(status)::text AS state,
 'block'::text AS retention,jsonb_build_object('id',id) AS audit_key FROM activity.visit WHERE NOT id=ANY(ids)
 UNION ALL
 SELECT 'crm.customer_sales_member'::text AS tab,(customer_id::text||':'||user_ref_id::text)::text AS key,NULL::uuid AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id]::uuid[],NULL::uuid) AS refs,joined_at AS created,(joined_at)::text AS rev,('history')::text AS state,
 'audit'::text AS retention,jsonb_build_object('customer_id',customer_id,'user_ref_id',user_ref_id) AS audit_key FROM crm.customer_sales_member WHERE true
 UNION ALL
 SELECT 'crm.customer_assignment'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(status)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM crm.customer_assignment WHERE true
 UNION ALL
 SELECT 'crm.customer_claim_request'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id]::uuid[],NULL::uuid) AS refs,requested_at AS created,(status)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM crm.customer_claim_request WHERE true
 UNION ALL
 SELECT 'crm.customer_ownership_event'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,claim_request_id]::uuid[],NULL::uuid) AS refs,occurred_at AS created,(event_type)::text AS rev,(event_type)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM crm.customer_ownership_event WHERE true
 UNION ALL
 SELECT 'crm.customer_duplicate_candidate'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,candidate_customer_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(status)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM crm.customer_duplicate_candidate WHERE true
 UNION ALL
 SELECT 'crm.customer_product'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,opportunity_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,(relationship_status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM crm.customer_product WHERE true
 UNION ALL
 SELECT 'crm.customer_actual'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,opportunity_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(voided_at)::text AS rev,(kind)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM crm.customer_actual WHERE true
 UNION ALL
 SELECT 'crm.opportunity_forecast'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[opportunity_id]::uuid[],NULL::uuid) AS refs,NULL::timestamptz AS created,(updated_at)::text AS rev,('history')::text AS state,
 'audit'::text AS retention,jsonb_build_object('id',id,'opportunity_id',opportunity_id) AS audit_key FROM crm.opportunity_forecast WHERE true
 UNION ALL
 SELECT 'crm.business_change'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,opportunity_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,('history')::text AS state,
 'audit'::text AS retention,jsonb_build_object('id',id,'version_no',version_no) AS audit_key FROM crm.business_change WHERE true
 UNION ALL
 SELECT 'crm.opportunity_participant'::text AS tab,(opportunity_id::text||':'||user_ref_id::text||':'||valid_from::text)::text AS key,NULL::uuid AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[opportunity_id,source_visit_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(valid_to)::text AS rev,('history')::text AS state,
 'audit'::text AS retention,jsonb_build_object('opportunity_id',opportunity_id,'user_ref_id',user_ref_id,'valid_from',valid_from) AS audit_key FROM crm.opportunity_participant WHERE true
 UNION ALL
 SELECT 'crm.opportunity_quote_reference'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[opportunity_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(created_at)::text AS rev,('quote')::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM crm.opportunity_quote_reference WHERE true
 UNION ALL
 SELECT 'crm.opportunity_demo_scenes'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[opportunity_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,('demo_scene')::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM crm.opportunity_demo_scenes WHERE true
 UNION ALL
 SELECT 'activity.visit_contact'::text AS tab,(visit_id::text||':'||contact_id::text)::text AS key,NULL::uuid AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[visit_id,contact_id]::uuid[],NULL::uuid) AS refs,NULL::timestamptz AS created,(participation_role)::text AS rev,('history')::text AS state,
 'audit'::text AS retention,jsonb_build_object('visit_id',visit_id,'contact_id',contact_id) AS audit_key FROM activity.visit_contact WHERE true
 UNION ALL
 SELECT 'activity.visit_field_value'::text AS tab,(visit_id::text||':'||field_definition_id::text)::text AS key,NULL::uuid AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[visit_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,('history')::text AS state,
 'audit'::text AS retention,jsonb_build_object('visit_id',visit_id,'field_definition_id',field_definition_id) AS audit_key FROM activity.visit_field_value WHERE true
 UNION ALL
 SELECT 'activity.visit_participant'::text AS tab,(visit_id::text||':'||user_ref_id::text)::text AS key,NULL::uuid AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[visit_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,('history')::text AS state,
 'audit'::text AS retention,jsonb_build_object('visit_id',visit_id,'user_ref_id',user_ref_id) AS audit_key FROM activity.visit_participant WHERE true
 UNION ALL
 SELECT 'activity.action_item'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,opportunity_id,source_visit_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM activity.action_item WHERE true
 UNION ALL
 SELECT 'activity.visit_import'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[]::uuid[],NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM activity.visit_import WHERE true
 UNION ALL
 SELECT 'activity.visit_import_content'::text AS tab,(import_id::text||':'||chunk_no::text)::text AS key,NULL::uuid AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[import_id]::uuid[],NULL::uuid) AS refs,NULL::timestamptz AS created,(chunk_no)::text AS rev,('history')::text AS state,
 'inherited'::text AS retention,'{}'::jsonb AS audit_key FROM activity.visit_import_content WHERE true
 UNION ALL
 SELECT 'insight.quadrant_score'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id]::uuid[]||security.feishu_test_reference_ids(input_snapshot)||security.feishu_test_reference_ids(evidence),NULL::uuid) AS refs,created_at AS created,(calculated_at)::text AS rev,('history')::text AS state,
 'history'::text AS retention,'{}'::jsonb AS audit_key FROM insight.quadrant_score WHERE true
 UNION ALL
 SELECT 'insight.risk'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,opportunity_id,source_visit_id,source_run_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM insight.risk WHERE true
 UNION ALL
 SELECT 'insight.risk_event'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[risk_id]::uuid[],NULL::uuid) AS refs,occurred_at AS created,(occurred_at)::text AS rev,(event_type)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM insight.risk_event WHERE true
 UNION ALL
 SELECT 'insight.recommendation'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,opportunity_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM insight.recommendation WHERE true
 UNION ALL
 SELECT 'insight.business_advice'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,opportunity_id,visit_id]::uuid[]||security.feishu_test_reference_ids(facts_snapshot),NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM insight.business_advice WHERE true
 UNION ALL
 SELECT 'insight.business_suggestion'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[advice_id,task_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,(decision)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM insight.business_suggestion WHERE true
 UNION ALL
 SELECT 'insight.customer_risk_assessment'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,job_id,inference_operation_id]::uuid[]||security.feishu_test_reference_ids(coverage)||security.feishu_test_reference_ids(jsonb_build_object('trigger_id',trigger_id)),NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM insight.customer_risk_assessment WHERE true
 UNION ALL
 SELECT 'insight.sales_competency_review'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(security.feishu_test_reference_ids(input_snapshot)||security.feishu_test_reference_ids(evidence),NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM insight.sales_competency_review WHERE true
 UNION ALL
 SELECT 'insight.report'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[scope_ref_id]::uuid[]||security.feishu_test_reference_ids(source_snapshot),NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM insight.report WHERE true
 UNION ALL
 SELECT 'workflow.task'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[customer_id,opportunity_id,source_visit_id,source_artifact_id,source_suggestion_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(version_no)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM workflow.task WHERE true
 UNION ALL
 SELECT 'workflow.task_assignee'::text AS tab,(task_id::text||':'||assignee_user_ref_id::text)::text AS key,NULL::uuid AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[task_id]::uuid[],NULL::uuid) AS refs,assigned_at AS created,(accepted_at)::text AS rev,('assignee')::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM workflow.task_assignee WHERE true
 UNION ALL
 SELECT 'workflow.task_candidate'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[task_id]::uuid[],NULL::uuid) AS refs,assigned_at AS created,(decided_at)::text AS rev,(decision)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM workflow.task_candidate WHERE true
 UNION ALL
 SELECT 'workflow.task_event'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[task_id]::uuid[]||security.feishu_test_reference_ids(payload),NULL::uuid) AS refs,occurred_at AS created,(occurred_at)::text AS rev,(event_type)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM workflow.task_event WHERE true
 UNION ALL
 SELECT 'workflow.notification'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[object_id]::uuid[]||security.feishu_test_reference_ids(payload),NULL::uuid) AS refs,created_at AS created,(read_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM workflow.notification WHERE true
 UNION ALL
 SELECT 'workflow.notification_delivery'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[notification_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(attempted_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM workflow.notification_delivery WHERE true
 UNION ALL
 SELECT 'agent.run'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(security.feishu_test_reference_ids(business_context),NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM agent.run WHERE true
 UNION ALL
 SELECT 'agent.artifact'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[subject_id,run_id,supersedes_artifact_id]::uuid[]||security.feishu_test_reference_ids(payload)||security.feishu_test_reference_ids(evidence),NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM agent.artifact WHERE true
 UNION ALL
 SELECT 'agent.confirmation'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[artifact_id]::uuid[],NULL::uuid) AS refs,confirmed_at AS created,(confirmed_at)::text AS rev,(decision)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM agent.confirmation WHERE true
 UNION ALL
 SELECT 'agent.conversation'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(security.feishu_test_reference_ids(context),NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,(status)::text AS state,
 'block'::text AS retention,'{}'::jsonb AS audit_key FROM agent.conversation WHERE true
 UNION ALL
 SELECT 'agent.message'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[source_run_id]::uuid[]||security.feishu_test_reference_ids(structured_content),NULL::uuid) AS refs,created_at AS created,(created_at)::text AS rev,('history')::text AS state,
 'history'::text AS retention,'{}'::jsonb AS audit_key FROM agent.message WHERE true
 UNION ALL
 SELECT 'agent.run_step'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[run_id]::uuid[],NULL::uuid) AS refs,started_at AS created,(completed_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM agent.run_step WHERE true
 UNION ALL
 SELECT 'agent.tool_invocation'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[run_id,run_step_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(completed_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM agent.tool_invocation WHERE true
 UNION ALL
 SELECT 'agent.model_invocation'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[run_id,run_step_id,operation_id]::uuid[],NULL::uuid) AS refs,created_at AS created,(completed_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM agent.model_invocation WHERE true
 UNION ALL
 SELECT 'agent.inference_operation'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[run_id,job_id]::uuid[]||security.feishu_test_reference_ids(scope_snapshot),NULL::uuid) AS refs,started_at AS created,(completed_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM agent.inference_operation WHERE true
 UNION ALL
 SELECT 'ops.job'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[aggregate_id,correlation_id]::uuid[]||security.feishu_test_reference_ids(payload),NULL::uuid) AS refs,created_at AS created,(updated_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM ops.job WHERE true
 UNION ALL
 SELECT 'ops.job_effect'::text AS tab,(job_id)::text AS key,NULL::uuid AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[job_id]::uuid[],NULL::uuid) AS refs,completed_at AS created,(completed_at)::text AS rev,('history')::text AS state,
 'history'::text AS retention,'{}'::jsonb AS audit_key FROM ops.job_effect WHERE true
 UNION ALL
 SELECT 'ops.outbox_event'::text AS tab,(id)::text AS key,id AS entity_id,workspace_id AS node_ws,
 array_remove(ARRAY[aggregate_id,correlation_id,causation_id]::uuid[]||security.feishu_test_reference_ids(payload),NULL::uuid) AS refs,created_at AS created,(published_at)::text AS rev,(status)::text AS state,
 'terminal'::text AS retention,'{}'::jsonb AS audit_key FROM ops.outbox_event WHERE true
 ), walk(id) AS (
  SELECT unnest(graph_ids)
  UNION
  SELECT x.id FROM walk w JOIN nodes n ON n.entity_id=w.id OR w.id=ANY(n.refs)
   CROSS JOIN LATERAL unnest(n.refs||ARRAY[n.entity_id]) x(id) WHERE x.id IS NOT NULL
 ), matched AS (
  SELECT DISTINCT n.* FROM nodes n WHERE n.entity_id IN (SELECT id FROM walk)
   OR EXISTS(SELECT 1 FROM walk w WHERE w.id=ANY(n.refs))
 )
 SELECT COALESCE(jsonb_agg(to_jsonb(m) ORDER BY tab,key),'[]'::jsonb) INTO deps
 FROM (SELECT * FROM matched ORDER BY tab,key LIMIT 1001) m;
 IF jsonb_array_length(deps)>1000 THEN RETURN jsonb_build_object('code','DEPENDENCY_INVENTORY_TOO_LARGE'); END IF;
 FOR item IN SELECT * FROM jsonb_to_recordset(deps)
  AS d(tab text,key text,entity_id uuid,node_ws uuid,refs uuid[],created timestamptz,rev text,state text,retention text,audit_key jsonb)
 LOOP
  IF item.node_ws IS DISTINCT FROM ws THEN RETURN jsonb_build_object('code','CROSS_WORKSPACE_DEPENDENCY'); END IF;
  IF item.retention='block' THEN
   RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||item.tab);
  END IF;
  IF item.retention='terminal' AND NOT (CASE item.tab
    WHEN 'activity.visit_import' THEN item.state IN ('succeeded','failed')
    WHEN 'insight.business_advice' THEN item.state IN ('succeeded','failed','superseded')
    WHEN 'insight.business_suggestion' THEN item.state='no_task'
    WHEN 'insight.customer_risk_assessment' THEN item.state IN ('succeeded','failed','superseded')
    WHEN 'workflow.notification' THEN item.state IN ('read','cancelled')
    WHEN 'workflow.notification_delivery' THEN item.state IN ('delivered','cancelled')
    WHEN 'agent.run' THEN item.state IN ('succeeded','failed','cancelled')
    WHEN 'agent.artifact' THEN item.state IN ('applied','rejected','superseded')
    WHEN 'agent.confirmation' THEN item.state IN ('confirmed','edited_and_confirmed','rejected')
    WHEN 'agent.run_step' THEN item.state IN ('succeeded','failed','skipped')
    WHEN 'agent.tool_invocation' THEN item.state IN ('succeeded','failed','cancelled')
    WHEN 'agent.model_invocation' THEN item.state IN ('succeeded','failed','cancelled')
    WHEN 'agent.inference_operation' THEN item.state IN ('accepted','failed','cancelled')
    WHEN 'ops.job' THEN item.state IN ('succeeded','cancelled')
    WHEN 'ops.outbox_event' THEN item.state='published'
    ELSE false END) THEN
   RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||item.tab);
  END IF;
  IF item.retention='audit' THEN
   SELECT a.id,a.occurred_at INTO birth FROM ops.audit_log a
    WHERE a.workspace_id=ws AND a.action_code=item.tab||'.insert' AND a.before_snapshot IS NULL
     AND a.after_snapshot @> item.audit_key AND a.occurred_at BETWEEN started AND ended
     AND EXISTS(SELECT 1 FROM jsonb_array_elements(authorized_ops) op
       WHERE a.request_id=(op->>'request_id')::uuid AND a.actor_user_ref_id=(op->>'actor_id')::uuid
        AND a.transaction_id=(op->>'transaction_id')::bigint)
    ORDER BY a.id LIMIT 1;
   IF NOT FOUND OR EXISTS(SELECT 1 FROM ops.audit_log a
     WHERE a.workspace_id=ws AND a.action_code IN (item.tab||'.update',item.tab||'.delete')
      AND (a.after_snapshot @> item.audit_key OR a.before_snapshot @> item.audit_key)
      AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(authorized_ops) op
       WHERE a.request_id=(op->>'request_id')::uuid AND a.actor_user_ref_id=(op->>'actor_id')::uuid
        AND a.transaction_id=(op->>'transaction_id')::bigint AND a.occurred_at BETWEEN started AND ended)) THEN
    RETURN jsonb_build_object('code','DEPENDENCY_CREATION_RECEIPT_NOT_PROVEN:'||item.tab);
   END IF;
  ELSIF item.retention<>'inherited' AND (item.created IS NULL OR item.created NOT BETWEEN started AND ended) THEN
   RETURN jsonb_build_object('code','DEPENDENCY_OUTSIDE_BATCH:'||item.tab);
  END IF;
  dependencies:=dependencies||jsonb_build_array(jsonb_build_object('table',item.tab,'id',item.key,
   'state',item.state,'fingerprint',encode(sha256(convert_to(jsonb_build_object('refs',item.refs,
    'created',item.created,'revision',item.rev,'state',item.state)::text,'UTF8')),'hex')));
 END LOOP;


 plan_hash:=encode(sha256(convert_to(jsonb_build_object('contract','feishu-test-archive/v1',
  'manifest',manifest_hash,'roots',root_metadata,'retained',dependencies)::text,'UTF8')),'hex');
 IF p_expected_plan IS NULL THEN
  RETURN jsonb_build_object('dry_run',true,'plan_hash',plan_hash,'records',expected_count,'retained',dependencies);
 END IF;
 IF p_expected_plan IS DISTINCT FROM plan_hash THEN RETURN jsonb_build_object('code','PLAN_CHANGED'); END IF;
 IF NULLIF(current_setting('app.request_id',true),'') IS NULL THEN
  RETURN jsonb_build_object('code','ARCHIVE_REQUEST_ID_REQUIRED');
 END IF;
 stamp:=clock_timestamp();
 marker:=jsonb_build_object('run_id',run,'maintenance_actor',actor,'archived_at',stamp,'reason',reason);
 -- Business facts, recorders and confirmations remain unchanged. Existing
 -- version/audit/Feishu triggers execute normally, in one caller transaction.
 UPDATE activity.visit SET deleted_at=stamp,attributes=attributes||jsonb_build_object('feishu_integration_test_archive',marker)
  WHERE workspace_id=ws AND id=vid AND deleted_at IS NULL;
 GET DIAGNOSTICS changed=ROW_COUNT;
 IF changed<>(CASE WHEN vid IS NULL THEN 0 ELSE 1 END) THEN RAISE EXCEPTION 'ARCHIVE_COUNT_MISMATCH'; END IF;
 UPDATE crm.contact SET deleted_at=stamp,attributes=attributes||jsonb_build_object('feishu_integration_test_archive',marker)
  WHERE workspace_id=ws AND id=ANY(contact_ids) AND deleted_at IS NULL;
 GET DIAGNOSTICS changed=ROW_COUNT;
 IF changed<>cardinality(contact_ids) THEN RAISE EXCEPTION 'ARCHIVE_COUNT_MISMATCH'; END IF;
 UPDATE crm.opportunity SET deleted_at=stamp,attributes=attributes||jsonb_build_object('feishu_integration_test_archive',marker)
  WHERE workspace_id=ws AND id=oid AND deleted_at IS NULL;
 GET DIAGNOSTICS changed=ROW_COUNT;
 IF changed<>(CASE WHEN oid IS NULL THEN 0 ELSE 1 END) THEN RAISE EXCEPTION 'ARCHIVE_COUNT_MISMATCH'; END IF;
 UPDATE crm.customer SET deleted_at=stamp,attributes=attributes||jsonb_build_object('feishu_integration_test_archive',marker)
  WHERE workspace_id=ws AND id=cid AND deleted_at IS NULL;
 GET DIAGNOSTICS changed=ROW_COUNT;
 IF changed<>1 THEN RAISE EXCEPTION 'ARCHIVE_COUNT_MISMATCH'; END IF;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,
  object_type,object_id,object_label,request_id,after_snapshot)
 VALUES(ws,actor,'administrator','feishu.integration_test.archive','maintenance','integration_test_batch',run,prefix,
  NULLIF(current_setting('app.request_id',true),'')::uuid,jsonb_build_object('contract','feishu-test-archive/v1',
   'manifest_hash',manifest_hash,'plan_hash',plan_hash,'record_ids',ids,'retained',dependencies,'archived_at',stamp));
 RETURN jsonb_build_object('archived',true,'records',expected_count,'plan_hash',plan_hash);
END $$;
REVOKE ALL ON FUNCTION security.feishu_test_archive(jsonb,text) FROM PUBLIC,salegent_feishu_worker;
DO $$ DECLARE r record; BEGIN
 -- Existing normal business runtime roles only. Future runtime roles need an
 -- explicit EXECUTE grant; neither PUBLIC nor worker gets this maintenance API.
 FOR r IN SELECT oid,rolname FROM pg_roles
  WHERE NOT rolsuper AND NOT rolbypassrls AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
   AND NOT pg_has_role(oid,'salegent_feishu_worker','member')
   AND has_table_privilege(oid,'crm.customer','UPDATE')
   AND has_table_privilege(oid,'ops.audit_log','INSERT')
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.feishu_test_archive(jsonb,text) TO %I',r.rolname); END LOOP;
 IF has_function_privilege('salegent_feishu_worker','security.feishu_test_archive(jsonb,text)','EXECUTE') THEN
  RAISE EXCEPTION 'Feishu worker must not execute test archival';
 END IF;
END $$;
COMMENT ON FUNCTION security.feishu_test_archive(jsonb,text) IS
 'Exact receipt-bound Feishu test retirement; active same-company administrator only; no general cleanup or ordinary RLS expansion. Apply briefly locks fixed dependency tables NOWAIT; commit immediately.';
INSERT INTO ops.schema_migration(version,description) VALUES('V108','精确创建回执约束的飞书联调测试归档入口');
COMMIT;
