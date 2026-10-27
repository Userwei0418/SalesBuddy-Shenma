BEGIN;
SET LOCAL check_function_bodies=on;

-- A narrow, actor-authorized projection of already captured business writes.
-- V401's activation epoch and deployed SQL are deliberately unchanged.
CREATE FUNCTION security.review_update_activity(p_kind text,p_subjects uuid[],p_start timestamptz,p_end timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE started timestamptz; healthy boolean; reason text; items jsonb;
BEGIN
 IF p_kind IS NULL OR p_kind NOT IN ('team','person') OR p_subjects IS NULL OR cardinality(p_subjects)=0
  OR cardinality(p_subjects)>100 OR array_position(p_subjects,NULL) IS NOT NULL
  OR p_start IS NULL OR p_end IS NULL OR p_end<p_start OR p_end-p_start>interval '366 days'
  OR p_end>statement_timestamp() OR NOT security.authorization_has('work_report.read') THEN
  RAISE EXCEPTION 'Invalid Review activity scope' USING ERRCODE='42501'; END IF;
 SELECT complete_from INTO started FROM ops.review_history_capture WHERE singleton;
 healthy:=ops.review_capture_healthy();
 reason:=CASE WHEN EXISTS(SELECT 1 FROM unnest(p_subjects) subject WHERE NOT (
   security.authorization_subject('work_report.read',p_kind,CASE WHEN p_kind='person' THEN subject END,CASE WHEN p_kind='team' THEN subject END)
   AND security.authorization_subject('opportunity.read',p_kind,CASE WHEN p_kind='person' THEN subject END,CASE WHEN p_kind='team' THEN subject END)
   AND security.authorization_subject('customer.read',p_kind,CASE WHEN p_kind='person' THEN subject END,CASE WHEN p_kind='team' THEN subject END)))
  THEN 'source_scope_not_fully_authorized'
  WHEN NOT healthy THEN 'capture_trigger_unavailable'
  WHEN started IS NULL THEN 'capture_not_activated'
  WHEN p_start<started THEN 'before_complete_capture' END;
 WITH authorized AS (
  SELECT o.id,o.name,o.customer_id,c.name AS customer_name,o.owner_user_ref_id,o.version_no
  FROM crm.opportunity o JOIN crm.customer c ON c.workspace_id=o.workspace_id AND c.id=o.customer_id
  WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL AND c.deleted_at IS NULL
   AND (CASE WHEN p_kind='team' THEN o.owner_team_id ELSE o.owner_user_ref_id END)=ANY(p_subjects)
   AND security.authorization_opportunity('opportunity.read',o.id)
   AND security.authorization_opportunity('work_report.read',o.id)
   AND security.authorization_customer('customer.read',c.id)
   AND security.authorization_customer('work_report.read',c.id)
 ), raw AS (
  SELECT a.*,o.id AS opportunity_id,o.name AS opportunity_name,o.customer_id,o.customer_name,
   o.owner_user_ref_id,o.version_no AS current_version,
   split_part(a.action_code,'.',2) AS relation_name,
   ARRAY(SELECT field FROM unnest(a.changed_fields) field WHERE field NOT IN
    ('id','workspace_id','opportunity_id','created_at','updated_at','version_no','created_by_user_ref_id',
     'updated_by_user_ref_id','assigned_by_user_ref_id','import_meta','external_opportunity_id',
     'field_edit_reason','material_reason','original_created_at','original_created_on')) AS business_fields
  FROM ops.audit_log a JOIN authorized o ON o.id=CASE
    WHEN a.action_code LIKE 'crm.opportunity_related_partner.%' OR a.object_type='opportunity' THEN a.object_id
    ELSE (COALESCE(a.after_snapshot,a.before_snapshot)->>'opportunity_id')::uuid END
  WHERE a.workspace_id=common.current_workspace_id()
   AND started IS NOT NULL AND a.occurred_at>=greatest(p_start,started) AND a.occurred_at<p_end
   AND a.action_code IN ('crm.opportunity.update',
    'crm.opportunity_forecast.insert','crm.opportunity_forecast.update','crm.opportunity_forecast.delete',
    'crm.opportunity_related_partner.insert','crm.opportunity_related_partner.delete',
    'crm.opportunity_project_participant.insert','crm.opportunity_project_participant.update','crm.opportunity_project_participant.delete',
    'crm.opportunity_material.insert','crm.opportunity_material.update','crm.opportunity_material.delete',
    'crm.opportunity_participant.insert','crm.opportunity_participant.update','crm.opportunity_participant.delete',
    'crm.opportunity_quote_reference.insert','crm.opportunity_quote_reference.update','crm.opportunity_quote_reference.delete')
   -- Child INSERTs in the parent creation transaction are initial state. A
   -- later UPDATE remains a real update even when the object was born today.
   AND NOT (a.action_code LIKE '%.insert' AND EXISTS(SELECT 1 FROM ops.audit_log birth
    WHERE birth.workspace_id=a.workspace_id AND birth.object_id=o.id
     AND birth.action_code='crm.opportunity.insert' AND birth.transaction_id=a.transaction_id))
 ), event_rows AS (
  SELECT a.*,changing_actor.id AS visible_actor,
   CASE WHEN changing_actor.id IS NOT NULL THEN COALESCE(a.actor_name_snapshot,changing_actor.display_name) END AS actor_name,
   COALESCE(changes.values,'[]'::jsonb) AS changes,
   cardinality(a.business_fields)-COALESCE(changes.shown,0) AS omitted_fields
  FROM raw a
  LEFT JOIN platform.user_ref changing_actor ON changing_actor.id=a.actor_user_ref_id
   AND changing_actor.workspace_id=a.workspace_id AND changing_actor.deleted_at IS NULL
   AND security.authorization_subject('work_report.read','person',changing_actor.id,NULL)
  LEFT JOIN LATERAL (
   SELECT jsonb_agg(jsonb_build_object('field',field,
    'before',CASE WHEN allowed AND before_known THEN before_value END,
    'after',CASE WHEN allowed AND after_known THEN after_value END,
    'before_known',allowed AND before_known,'after_known',allowed AND after_known,
    'year',CASE WHEN a.relation_name='opportunity_forecast' THEN COALESCE(a.after_snapshot,a.before_snapshot)->'year' END,
    'quarter',CASE WHEN a.relation_name='opportunity_forecast' THEN COALESCE(a.after_snapshot,a.before_snapshot)->'quarter' END,
    'value_status',CASE WHEN NOT allowed THEN 'restricted' WHEN NOT before_known OR NOT after_known THEN 'not_recorded' ELSE 'available' END)
    ORDER BY field) AS values,count(*)::integer AS shown
   FROM (
    SELECT field,
     CASE WHEN field='associated_partner_ids' THEN
      CASE WHEN a.before_snapshot IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(a.before_snapshot->'partner_id') END
      WHEN field=ANY(ARRAY['amount','probability','collection_amount','recognized_amount','unit_price','line_amount','quantity','expected_consumption_amount'])
       THEN to_jsonb(a.before_snapshot->>field) ELSE a.before_snapshot->field END AS before_value,
     CASE WHEN field='associated_partner_ids' THEN
      CASE WHEN a.after_snapshot IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(a.after_snapshot->'partner_id') END
      WHEN field=ANY(ARRAY['amount','probability','collection_amount','recognized_amount','unit_price','line_amount','quantity','expected_consumption_amount'])
       THEN to_jsonb(a.after_snapshot->>field) ELSE a.after_snapshot->field END AS after_value,
     (a.before_snapshot IS NULL OR a.before_snapshot ? CASE WHEN field='associated_partner_ids' THEN 'partner_id' ELSE field END) AS before_known,
     (a.after_snapshot IS NULL OR a.after_snapshot ? CASE WHEN field='associated_partner_ids' THEN 'partner_id' ELSE field END) AS after_known,
     (CASE WHEN field IN ('partner_id','associated_partner_ids') THEN security.authorization_has('partner.read')
       WHEN field IN ('owner_user_ref_id','primary_fde_id','user_ref_id') THEN NOT EXISTS(
        SELECT 1 FROM (VALUES(a.before_snapshot->>field),(a.after_snapshot->>field)) person(id)
        WHERE id IS NOT NULL AND NOT security.authorization_subject('work_report.read','person',id::uuid,NULL))
       WHEN field='customer_id' THEN NOT EXISTS(
        SELECT 1 FROM (VALUES(a.before_snapshot->>field),(a.after_snapshot->>field)) customer(id)
        WHERE id IS NOT NULL AND NOT (security.authorization_customer('customer.read',id::uuid)
         AND security.authorization_customer('work_report.read',id::uuid)))
       ELSE true END)
       AND (a.relation_name<>'opportunity_material' OR security.authorization_has('material.read')) AS allowed
    FROM unnest(a.business_fields) field
    WHERE field=ANY(CASE a.relation_name
     WHEN 'opportunity' THEN ARRAY['name','customer_id','amount','currency','status','probability','stage_code',
      'owner_user_ref_id','owner_team_id','expected_close_date','expected_close_year','expected_close_quarter',
      'partner_id','partner_name','sales_channel','product_line','follow_up_plan','closed_at','source_code',
      'customer_source','priority','is_key','trial_status','summary','note','ppl_status','external_system_status',
      'primary_fde_id','fde_support_stage','fde_progress','ownership_resolution']
     WHEN 'opportunity_forecast' THEN ARRAY['year','quarter','collection_amount','recognized_amount','collection_confidence']
     WHEN 'opportunity_related_partner' THEN ARRAY['associated_partner_ids']
     WHEN 'opportunity_project_participant' THEN ARRAY['user_ref_id','participant_role']
     WHEN 'opportunity_participant' THEN ARRAY['user_ref_id','participant_role','valid_from','valid_to','source_code','note']
     WHEN 'opportunity_quote_reference' THEN ARRAY['reference_no','title','url','amount']
     WHEN 'opportunity_material' THEN ARRAY['material_id','line_no','quantity','unit_price','line_amount','usage',
      'expected_delivery_on','expected_start_on','expected_oa_on','service_months','expected_account_count',
      'expected_consumption_amount','expected_consumption_year','expected_consumption_quarter','note','is_active']
     ELSE '{}'::text[] END)
   ) fields
  ) changes ON true
  WHERE cardinality(a.business_fields)>0
 ), grouped AS (
  SELECT opportunity_id,max(opportunity_name) AS name,customer_id,max(customer_name) AS customer_name,
   owner_user_ref_id,max(current_version) AS version_no,max(occurred_at) AS occurred_at,
   jsonb_agg(jsonb_build_object('kind','opportunity_audit','id','audit:'||id,'event_id','audit:'||id,
    'occurred_at',occurred_at,'version_no',NULL,'relation',relation_name,'operation',split_part(action_code,'.',3),
    'actor_id',visible_actor,'actor_display_name',actor_name,
    'actor_status',CASE WHEN actor_user_ref_id IS NULL THEN 'not_recorded' WHEN visible_actor IS NULL THEN 'restricted' ELSE 'available' END,
    'actor_name_basis',CASE WHEN visible_actor IS NOT NULL THEN CASE WHEN actor_name_snapshot IS NOT NULL THEN 'recorded_audit' ELSE 'current_authorized_directory' END END,
    'changes',changes,'omitted_field_count',omitted_fields,'observed_business_change',true)
    ORDER BY occurred_at,id) AS events
  FROM event_rows GROUP BY opportunity_id,customer_id,owner_user_ref_id
 ) SELECT COALESCE(jsonb_agg(jsonb_build_object('id',opportunity_id,'opportunity_id',opportunity_id,
   'name',name,'customer_id',customer_id,'customer_name',customer_name,'owner_id',owner_user_ref_id,
   'version_no',version_no,'occurred_at',occurred_at,'events',events) ORDER BY occurred_at DESC,opportunity_id),'[]'::jsonb)
 INTO items FROM grouped;
 RETURN jsonb_build_object('items',items,'history_coverage',jsonb_build_object(
  'status',CASE WHEN reason IS NULL THEN 'complete' WHEN jsonb_array_length(items)>0 THEN 'partial' ELSE 'unavailable' END,
  'complete_from',started,'reason_codes',CASE WHEN reason IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(reason) END,
  'message',CASE WHEN reason IS NULL THEN '所选期间更新商机按完整数据库写入捕获去重计数；字段及操作人缺口单独标注'
   ELSE '所选期间没有连续完整捕获，仅展示有证据的更新；不把已知数量当作总数' END));
END $$;

-- Match the already reviewed API/ordinary Worker projection recipients only.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT a.grantee,p.proowner,role.rolname FROM pg_proc p
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 LEFT JOIN pg_roles role ON role.oid=a.grantee
 WHERE p.oid='security.review_update_activity(text,uuid[],timestamptz,timestamptz)'::regprocedure
 AND a.grantee<>p.proowner LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION security.review_update_activity(text,uuid[],timestamptz,timestamptz) FROM %s CASCADE',
  CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END); END LOOP;
END $$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_review_update_activity;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_before_review_update_activity();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND NOT rolbypassrls
  AND rolname NOT LIKE 'pg\_%' ESCAPE '\' LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_review_update_activity() FROM %I',r.rolname);
  IF has_function_privilege(r.rolname,'security.review_day_update_status(text,uuid[],timestamptz,timestamptz)','EXECUTE')
   AND NOT pg_has_role(r.rolname,'salegent_feishu_worker','member') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.review_update_activity(text,uuid[],timestamptz,timestamptz) TO %I',r.rolname);
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_review_update_activity() FROM PUBLIC;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description)
VALUES('V402','Actor-scoped audited Review update counts and changed fields');
COMMIT;
