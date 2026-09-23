BEGIN;
SET LOCAL check_function_bodies=on;

-- This one-time entry point is installed only by the audited migration owner.
-- Runtime callers do not inherit the owner's visibility or table privileges.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_user AND (rolsuper OR rolbypassrls)) THEN
  RAISE EXCEPTION 'V110 requires the audited migration owner with complete RLS visibility';
 END IF;
END $$;

-- One-time compatibility boundary for the notification smoke-test batch created
-- before V108 was available. The descriptor is accepted only when its canonical
-- fingerprint matches the reviewed batch; callers cannot choose an arbitrary
-- customer, name, date, or SQL statement.
CREATE FUNCTION security.feishu_notification_test_archive_v2(p_descriptor jsonb,p_expected_plan text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET timezone='UTC' AS $$
DECLARE
  ws uuid:=common.current_workspace_id();
  actor uuid:=common.current_user_ref_id();
  cid uuid; oid uuid; vid uuid; contact_id uuid;
  customer_version integer; opportunity_version integer; visit_version integer; contact_version integer;
  plan_hash text; stamp timestamptz; marker jsonb; previous jsonb; changed integer;
  expected_id uuid:='00000000-0000-0000-0000-000000010901'::uuid;
  batch_key text:='feishu-notification-20260919';
  records jsonb; dependent_table text; descriptor_hash text;
  v_customer_name text; v_company_reference text; v_industry text; v_opportunity_name text;
  v_contact_name text; v_visit_marker text; day_start timestamptz; day_end timestamptz;
BEGIN
  IF common.current_role_code() IS DISTINCT FROM 'administrator'
   OR NOT security.has_active_role('administrator') OR ws IS NULL OR actor IS NULL
   OR NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
     WHERE u.id=actor AND u.workspace_id=ws AND u.status='active' AND u.deleted_at IS NULL
       AND w.status='active' AND w.deleted_at IS NULL) THEN
    RETURN jsonb_build_object('code','WORKSPACE_OR_ROLE_MISMATCH');
  END IF;
  IF row_security_active('insight.business_advice'::regclass)
   OR row_security_active('workflow.notification'::regclass)
   OR EXISTS(SELECT 1 FROM ops.schema_migration WHERE version ~ '^V[0-9]+$'
     AND substring(version FROM 2)::int>110) THEN
    RETURN jsonb_build_object('code','ARCHIVE_SCHEMA_OR_OWNER_REVIEW_REQUIRED');
  END IF;

  IF jsonb_typeof(p_descriptor) IS DISTINCT FROM 'object'
   OR p_descriptor-ARRAY['company_reference','contact_name','customer_name','created_day',
       'industry','opportunity_name','visit_marker']<>'{}'::jsonb
   OR NOT p_descriptor ?& ARRAY['company_reference','contact_name','customer_name','created_day',
       'industry','opportunity_name','visit_marker'] THEN
    RETURN jsonb_build_object('code','INVALID_NOTIFICATION_TEST_DESCRIPTOR');
  END IF;
  descriptor_hash:=encode(sha256(convert_to(p_descriptor::text,'UTF8')),'hex');
  IF descriptor_hash<>'d0ca45656178b3331b9f3a4de8013f0b0f7e314c0c176d7a80c127e15708963b' THEN
    RETURN jsonb_build_object('code','NOTIFICATION_TEST_DESCRIPTOR_NOT_AUTHORIZED');
  END IF;
  BEGIN
    v_customer_name:=p_descriptor->>'customer_name'; v_company_reference:=p_descriptor->>'company_reference';
    v_industry:=p_descriptor->>'industry'; v_opportunity_name:=p_descriptor->>'opportunity_name';
    v_contact_name:=p_descriptor->>'contact_name'; v_visit_marker:=p_descriptor->>'visit_marker';
    day_start:=((p_descriptor->>'created_day')::date)::timestamp AT TIME ZONE 'Asia/Shanghai';
    day_end:=day_start+interval '1 day';
  EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow THEN
    RETURN jsonb_build_object('code','INVALID_NOTIFICATION_TEST_DESCRIPTOR');
  END;

  SELECT after_snapshot INTO previous FROM ops.audit_log
   WHERE workspace_id=ws AND action_code='feishu.integration_test.named_archive'
     AND object_type='integration_test_notification_batch' AND object_id=expected_id;
  IF FOUND THEN
    RETURN jsonb_build_object('already_archived',true,'batch_key',batch_key,
      'plan_hash',previous->>'plan_hash');
  END IF;

  -- Exact identifiers of the authorized notification smoke-test batch.
  SELECT c.id,c.version_no INTO cid,customer_version
    FROM crm.customer c
   WHERE c.workspace_id=ws AND c.deleted_at IS NULL
     AND c.name=v_customer_name
     AND c.company_reference=v_company_reference
     AND c.industry_code=v_industry
     AND c.data_kind IN ('production','test','demo','unclassified')
     AND c.created_at>=day_start
     AND c.created_at<day_end;
  IF NOT FOUND OR (SELECT count(*) FROM crm.customer c
    WHERE c.workspace_id=ws AND c.deleted_at IS NULL
      AND c.name=v_customer_name
      AND c.company_reference=v_company_reference
      AND c.created_at>=day_start
      AND c.created_at<day_end)<>1 THEN
    RETURN jsonb_build_object('code','NOTIFICATION_TEST_CUSTOMER_NOT_UNIQUE');
  END IF;

  SELECT o.id,o.version_no INTO oid,opportunity_version
    FROM crm.opportunity o
   WHERE o.workspace_id=ws AND o.customer_id=cid AND o.deleted_at IS NULL
     AND o.name=v_opportunity_name
     AND o.created_at>=day_start
     AND o.created_at<day_end;
  IF NOT FOUND OR (SELECT count(*) FROM crm.opportunity o
    WHERE o.workspace_id=ws AND o.customer_id=cid AND o.deleted_at IS NULL)<>1 THEN
    RETURN jsonb_build_object('code','NOTIFICATION_TEST_OPPORTUNITY_NOT_UNIQUE');
  END IF;

  SELECT c.id,c.version_no INTO contact_id,contact_version
    FROM crm.contact c
   WHERE c.workspace_id=ws AND c.customer_id=cid AND c.deleted_at IS NULL
     AND c.name=v_contact_name;
  IF NOT FOUND OR (SELECT count(*) FROM crm.contact c
    WHERE c.workspace_id=ws AND c.customer_id=cid AND c.deleted_at IS NULL)<>1 THEN
    RETURN jsonb_build_object('code','NOTIFICATION_TEST_CONTACT_NOT_UNIQUE');
  END IF;

  SELECT v.id,v.version_no INTO vid,visit_version
    FROM activity.visit v
   WHERE v.workspace_id=ws AND v.customer_id=cid AND v.opportunity_id=oid
     AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
     AND v.follow_up_record ILIKE '%'||v_visit_marker||'%'
     AND v.created_at>=day_start
     AND v.created_at<day_end;
  IF NOT FOUND OR (SELECT count(*) FROM activity.visit v
    WHERE v.workspace_id=ws AND v.customer_id=cid AND v.deleted_at IS NULL)<>1 THEN
    RETURN jsonb_build_object('code','NOTIFICATION_TEST_VISIT_NOT_UNIQUE');
  END IF;

  -- The batch is smoke-test data only. Any business-derived object means that
  -- a real record was attached and requires a separate reviewable procedure.
  FOREACH dependent_table IN ARRAY ARRAY[
    'crm.customer_actual','crm.business_change','workflow.task','insight.risk',
    'insight.business_advice','insight.business_suggestion','insight.sales_competency_review',
    'agent.conversation'
  ] LOOP
    IF dependent_table='crm.customer_actual' AND EXISTS(SELECT 1 FROM crm.customer_actual
      WHERE workspace_id=ws AND (customer_id=cid OR opportunity_id=oid) AND voided_at IS NULL) THEN
      RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||dependent_table);
    ELSIF dependent_table='crm.business_change' AND EXISTS(SELECT 1 FROM crm.business_change
      WHERE workspace_id=ws AND (customer_id=cid OR opportunity_id=oid)) THEN
      RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||dependent_table);
    ELSIF dependent_table='workflow.task' AND EXISTS(SELECT 1 FROM workflow.task
      WHERE workspace_id=ws AND (customer_id=cid OR opportunity_id=oid) AND deleted_at IS NULL) THEN
      RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||dependent_table);
    ELSIF dependent_table='insight.risk' AND EXISTS(SELECT 1 FROM insight.risk
      WHERE workspace_id=ws AND (customer_id=cid OR opportunity_id=oid) AND deleted_at IS NULL) THEN
      RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||dependent_table);
    ELSIF dependent_table='insight.business_advice' AND EXISTS(SELECT 1 FROM insight.business_advice
      WHERE workspace_id=ws AND (customer_id=cid OR opportunity_id=oid OR visit_id=vid)) THEN
      RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||dependent_table);
    ELSIF dependent_table='insight.business_suggestion' AND EXISTS(SELECT 1 FROM insight.business_suggestion s
      JOIN insight.business_advice a ON a.id=s.advice_id
      WHERE a.workspace_id=ws AND (a.customer_id=cid OR a.opportunity_id=oid OR a.visit_id=vid)) THEN
      RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||dependent_table);
    ELSIF dependent_table='insight.sales_competency_review' AND EXISTS(SELECT 1 FROM insight.sales_competency_review r
      JOIN activity.visit v ON v.workspace_id=r.workspace_id AND v.recorder_user_ref_id=r.subject_user_ref_id
      WHERE r.workspace_id=ws AND v.id=vid) THEN
      RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||dependent_table);
    ELSIF dependent_table='agent.conversation' AND EXISTS(SELECT 1 FROM agent.conversation
      WHERE workspace_id=ws AND (context @> jsonb_build_object('customer_id',cid::text)
        OR context @> jsonb_build_object('opportunity_id',oid::text)
        OR context @> jsonb_build_object('visit_id',vid::text))) THEN
      RETURN jsonb_build_object('code','DEPENDENCY_REQUIRES_REVIEW:'||dependent_table);
    END IF;
  END LOOP;

  records:=jsonb_build_array(
    jsonb_build_object('table','crm.customer','id',cid,'version',customer_version),
    jsonb_build_object('table','crm.opportunity','id',oid,'version',opportunity_version),
    jsonb_build_object('table','activity.visit','id',vid,'version',visit_version),
    jsonb_build_object('table','crm.contact','id',contact_id,'version',contact_version));
  plan_hash:=encode(sha256(convert_to(jsonb_build_object(
    'contract','feishu-notification-test-archive/v1','batch_key',batch_key,
    'workspace_id',ws,'records',records)::text,'UTF8')),'hex');
  IF p_expected_plan IS NULL THEN
    RETURN jsonb_build_object('dry_run',true,'batch_key',batch_key,'records',records,'plan_hash',plan_hash);
  END IF;
  IF p_expected_plan IS DISTINCT FROM plan_hash THEN
    RETURN jsonb_build_object('code','PLAN_CHANGED');
  END IF;
  IF NULLIF(current_setting('app.request_id',true),'') IS NULL THEN
    RETURN jsonb_build_object('code','ARCHIVE_REQUEST_ID_REQUIRED');
  END IF;
  BEGIN
    LOCK TABLE crm.customer,crm.opportunity,activity.visit,crm.contact IN SHARE ROW EXCLUSIVE MODE NOWAIT;
  EXCEPTION WHEN lock_not_available THEN
    RETURN jsonb_build_object('code','ARCHIVE_BUSY_RETRY');
  END;
  stamp:=clock_timestamp();
  marker:=jsonb_build_object('feishu_integration_test_archive',true,'batch_key',batch_key,
    'maintenance_actor',actor,'archived_at',stamp,'reason','通知联调演示数据清理');
  UPDATE activity.visit SET deleted_at=stamp,attributes=attributes||marker
   WHERE workspace_id=ws AND id=vid AND deleted_at IS NULL;
  GET DIAGNOSTICS changed=ROW_COUNT;
  IF changed<>1 THEN RAISE EXCEPTION 'ARCHIVE_COUNT_MISMATCH'; END IF;
  UPDATE crm.contact SET deleted_at=stamp,attributes=attributes||marker
   WHERE workspace_id=ws AND id=contact_id AND deleted_at IS NULL;
  GET DIAGNOSTICS changed=ROW_COUNT;
  IF changed<>1 THEN RAISE EXCEPTION 'ARCHIVE_COUNT_MISMATCH'; END IF;
  UPDATE crm.opportunity SET deleted_at=stamp,attributes=attributes||marker
   WHERE workspace_id=ws AND id=oid AND deleted_at IS NULL;
  GET DIAGNOSTICS changed=ROW_COUNT;
  IF changed<>1 THEN RAISE EXCEPTION 'ARCHIVE_COUNT_MISMATCH'; END IF;
  UPDATE crm.customer SET deleted_at=stamp,attributes=attributes||marker
   WHERE workspace_id=ws AND id=cid AND deleted_at IS NULL;
  GET DIAGNOSTICS changed=ROW_COUNT;
  IF changed<>1 THEN RAISE EXCEPTION 'ARCHIVE_COUNT_MISMATCH'; END IF;
  INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,
    object_type,object_id,object_label,request_id,after_snapshot)
  VALUES(ws,actor,'administrator','feishu.integration_test.named_archive','maintenance',
    'integration_test_notification_batch',expected_id,batch_key,
    NULLIF(current_setting('app.request_id',true),'')::uuid,
    jsonb_build_object('contract','feishu-notification-test-archive/v1','batch_key',batch_key,
      'plan_hash',plan_hash,'record_ids',records,'archived_at',stamp));
  RETURN jsonb_build_object('archived',true,'batch_key',batch_key,'records',records,'plan_hash',plan_hash);
END $$;

REVOKE ALL ON FUNCTION security.feishu_notification_test_archive_v2(jsonb,text) FROM PUBLIC,salegent_feishu_worker;
DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT oid,rolname FROM pg_roles
   WHERE NOT rolsuper AND NOT rolbypassrls AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
     AND NOT pg_has_role(oid,'salegent_feishu_worker','member')
     AND has_table_privilege(oid,'crm.customer','UPDATE')
     AND has_table_privilege(oid,'ops.audit_log','INSERT')
  LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.feishu_notification_test_archive_v2(jsonb,text) TO %I',r.rolname); END LOOP;
END $$;
COMMENT ON FUNCTION security.feishu_notification_test_archive_v2(jsonb,text) IS
 'One-time exact archive boundary for a reviewed Feishu notification smoke-test descriptor; the descriptor fingerprint is fixed and no IDs or SQL are accepted.';
INSERT INTO ops.schema_migration(version,description)
 VALUES('V110','固定标识飞书通知联调演示数据的一次性受控归档入口（保留原数据分类）');
COMMIT;
