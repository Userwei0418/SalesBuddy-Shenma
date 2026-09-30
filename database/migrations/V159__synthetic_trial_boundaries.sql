BEGIN;
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='30s';

-- The private resolver has an explicit tenant argument for narrow worker
-- projections. Application callers receive only the current-tenant boolean API.
CREATE FUNCTION security.synthetic_trial_marker(p_workspace uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; marker jsonb; linked uuid;
BEGIN
 IF p_workspace IS NULL OR p_id IS NULL THEN RETURN NULL; END IF;
 CASE p_kind
 WHEN 'customer' THEN SELECT to_jsonb(c) INTO r FROM crm.customer c WHERE id=p_id AND workspace_id=p_workspace;
 WHEN 'opportunity' THEN SELECT to_jsonb(o) INTO r FROM crm.opportunity o WHERE id=p_id AND workspace_id=p_workspace;
 WHEN 'visit' THEN SELECT to_jsonb(v) INTO r FROM activity.visit v WHERE id=p_id AND workspace_id=p_workspace;
 WHEN 'task' THEN SELECT to_jsonb(t) INTO r FROM workflow.task t WHERE id=p_id AND workspace_id=p_workspace;
 ELSE RAISE invalid_parameter_value; END CASE;
 IF r IS NULL THEN RETURN NULL; END IF;
 IF r->'import_meta' ? 'synthetic_trial' THEN
  marker:=r->'import_meta'->'synthetic_trial';
  IF jsonb_typeof(marker)='object' AND NULLIF(marker->>'batch_id','') IS NOT NULL
    AND NULLIF(marker->>'root_customer_id','') IS NOT NULL THEN RETURN marker; END IF;
  -- Malformed marker is still synthetic. Never treat it as a way to opt out.
  RETURN jsonb_build_object('batch_id','legacy-marked:'||p_id,'root_customer_id',
    COALESCE(r->>'customer_id',p_id::text),'source','legacy_marker');
 END IF;
 IF p_kind='customer' THEN
  IF r->>'data_kind' IN ('demo','test') THEN
   RETURN jsonb_build_object('batch_id','legacy-'||(r->>'data_kind')||':'||p_id,
     'root_customer_id',p_id,'source','data_kind');
  END IF;
  RETURN NULL;
 END IF;
 marker:=security.synthetic_trial_marker(p_workspace,'customer',(r->>'customer_id')::uuid);
 IF marker IS NOT NULL THEN RETURN marker; END IF;
 IF p_kind IN ('visit','task') THEN
  marker:=security.synthetic_trial_marker(p_workspace,'opportunity',(r->>'opportunity_id')::uuid);
  IF marker IS NOT NULL THEN RETURN marker; END IF;
 END IF;
 IF p_kind='task' THEN RETURN security.synthetic_trial_marker(p_workspace,'visit',(r->>'source_visit_id')::uuid); END IF;
 IF p_kind='visit' THEN
  FOR linked IN SELECT opportunity_id FROM activity.visit_opportunity WHERE workspace_id=p_workspace AND visit_id=p_id LOOP
   marker:=security.synthetic_trial_marker(p_workspace,'opportunity',linked);
   IF marker IS NOT NULL THEN RETURN marker; END IF;
  END LOOP;
 END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION security.synthetic_trial_marker(uuid,text,uuid) FROM PUBLIC;

CREATE FUNCTION security.is_synthetic_trial_subject(p_kind text,p_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.synthetic_trial_marker(common.current_workspace_id(),p_kind,p_id) IS NOT NULL;
$$;
CREATE FUNCTION security.is_synthetic_trial_visit(p_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.is_synthetic_trial_subject('visit',p_id);
$$;
REVOKE ALL ON FUNCTION security.is_synthetic_trial_subject(text,uuid),security.is_synthetic_trial_visit(uuid) FROM PUBLIC;

-- A descendant keeps its origin even after a user clears optional associations.
-- Markers may be added but not removed/replaced. Mixed real/synthetic roots and
-- different trial roots cannot be combined into a single narrative or task.
CREATE FUNCTION security.protect_synthetic_trial_origin() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb:=to_jsonb(NEW); prior jsonb; inherited jsonb; candidate jsonb;
 marker jsonb; ws uuid:=(r->>'workspace_id')::uuid; relation text; linked uuid; real_parent boolean:=false;
BEGIN
 IF TG_OP='UPDATE' THEN
  prior:=to_jsonb(OLD);
  IF prior->'import_meta' ? 'synthetic_trial' THEN
   IF r->'import_meta'->'synthetic_trial' IS DISTINCT FROM prior->'import_meta'->'synthetic_trial' THEN
    RAISE EXCEPTION 'SYNTHETIC_TRIAL_ORIGIN_IMMUTABLE' USING ERRCODE='22023'; END IF;
   IF r->>'workspace_id' IS DISTINCT FROM prior->>'workspace_id' THEN
    RAISE EXCEPTION 'SYNTHETIC_TRIAL_WORKSPACE_IMMUTABLE' USING ERRCODE='22023'; END IF;
  END IF;
 END IF;
 marker:=r->'import_meta'->'synthetic_trial';
 IF marker IS NOT NULL AND (jsonb_typeof(marker)<>'object' OR NULLIF(marker->>'batch_id','') IS NULL
   OR NULLIF(marker->>'root_customer_id','') IS NULL OR NULLIF(marker->>'source','') IS NULL) THEN
  RAISE EXCEPTION 'SYNTHETIC_TRIAL_MARKER_INVALID' USING ERRCODE='22023'; END IF;
 IF TG_TABLE_NAME='customer' THEN
  IF marker IS NOT NULL AND (r->>'data_kind' NOT IN ('demo','test')
     OR marker->>'root_customer_id' IS DISTINCT FROM r->>'id') THEN
   RAISE EXCEPTION 'SYNTHETIC_TRIAL_CUSTOMER_KIND_REQUIRED' USING ERRCODE='22023'; END IF;
  RETURN NEW;
 END IF;
 FOREACH relation IN ARRAY ARRAY['customer','opportunity','source_visit'] LOOP
  linked:=(r->>(relation||'_id'))::uuid;
  IF linked IS NULL THEN CONTINUE; END IF;
  candidate:=security.synthetic_trial_marker(ws,CASE WHEN relation='source_visit' THEN 'visit' ELSE relation END,linked);
  IF candidate IS NULL THEN real_parent:=true;
  ELSE
   IF inherited IS NOT NULL AND (candidate->>'batch_id' IS DISTINCT FROM inherited->>'batch_id'
      OR candidate->>'root_customer_id' IS DISTINCT FROM inherited->>'root_customer_id') THEN
    RAISE EXCEPTION 'SYNTHETIC_TRIAL_MIXED_ROOTS' USING ERRCODE='22023'; END IF;
   inherited:=candidate;
  END IF;
 END LOOP;
 IF inherited IS NOT NULL THEN
  IF marker IS NOT NULL AND (marker->>'batch_id' IS DISTINCT FROM inherited->>'batch_id'
    OR marker->>'root_customer_id' IS DISTINCT FROM inherited->>'root_customer_id') THEN
   RAISE EXCEPTION 'SYNTHETIC_TRIAL_MIXED_ROOTS' USING ERRCODE='22023'; END IF;
  IF marker IS NULL THEN marker:=inherited; END IF;
 END IF;
 IF marker IS NOT NULL AND real_parent THEN RAISE EXCEPTION 'SYNTHETIC_TRIAL_MIXED_ROOTS' USING ERRCODE='22023'; END IF;
 IF marker IS NOT NULL THEN NEW.import_meta:=COALESCE(NEW.import_meta,'{}'::jsonb)||jsonb_build_object('synthetic_trial',marker); END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION security.protect_synthetic_trial_origin() FROM PUBLIC;
CREATE TRIGGER synthetic_trial_origin BEFORE INSERT OR UPDATE ON crm.customer FOR EACH ROW EXECUTE FUNCTION security.protect_synthetic_trial_origin();
CREATE TRIGGER synthetic_trial_origin BEFORE INSERT OR UPDATE ON crm.opportunity FOR EACH ROW EXECUTE FUNCTION security.protect_synthetic_trial_origin();
CREATE TRIGGER synthetic_trial_origin BEFORE INSERT OR UPDATE ON activity.visit FOR EACH ROW EXECUTE FUNCTION security.protect_synthetic_trial_origin();
CREATE TRIGGER synthetic_trial_origin BEFORE INSERT OR UPDATE ON workflow.task FOR EACH ROW EXECUTE FUNCTION security.protect_synthetic_trial_origin();

CREATE FUNCTION security.protect_synthetic_trial_visit_link() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v activity.visit%ROWTYPE; candidate jsonb; marker jsonb;
BEGIN
 SELECT * INTO v FROM activity.visit WHERE id=NEW.visit_id AND workspace_id=NEW.workspace_id FOR UPDATE;
 IF NOT FOUND THEN RETURN NEW; END IF; -- The existing FK retains its own error.
 candidate:=security.synthetic_trial_marker(NEW.workspace_id,'opportunity',NEW.opportunity_id);
 marker:=security.synthetic_trial_marker(NEW.workspace_id,'visit',NEW.visit_id);
 IF candidate IS NULL AND marker IS NULL THEN RETURN NEW; END IF;
 IF candidate IS NULL OR (marker IS NOT NULL AND (candidate->>'batch_id' IS DISTINCT FROM marker->>'batch_id'
   OR candidate->>'root_customer_id' IS DISTINCT FROM marker->>'root_customer_id'))
   OR (v.customer_id IS NOT NULL AND security.synthetic_trial_marker(NEW.workspace_id,'customer',v.customer_id) IS NULL)
   OR (v.opportunity_id IS NOT NULL AND security.synthetic_trial_marker(NEW.workspace_id,'opportunity',v.opportunity_id) IS NULL)
   OR EXISTS(SELECT 1 FROM activity.visit_opportunity x WHERE x.visit_id=NEW.visit_id AND x.workspace_id=NEW.workspace_id
     AND x.opportunity_id<>NEW.opportunity_id AND security.synthetic_trial_marker(x.workspace_id,'opportunity',x.opportunity_id) IS NULL) THEN
  RAISE EXCEPTION 'SYNTHETIC_TRIAL_MIXED_ROOTS' USING ERRCODE='22023'; END IF;
 IF NOT (v.import_meta ? 'synthetic_trial') THEN
  UPDATE activity.visit SET import_meta=import_meta||jsonb_build_object('synthetic_trial',candidate) WHERE id=v.id;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION security.protect_synthetic_trial_visit_link() FROM PUBLIC;
CREATE TRIGGER synthetic_trial_origin BEFORE INSERT OR UPDATE ON activity.visit_opportunity FOR EACH ROW EXECUTE FUNCTION security.protect_synthetic_trial_visit_link();

-- The immutable weekly input is the provenance source, never the prose or
-- display name. Check records and both context arrays, including queued output.
CREATE FUNCTION security.synthetic_trial_weekly_source(p_workspace uuid,p_id uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE source jsonb; item jsonb; group_key text; kind text;
BEGIN
 SELECT input_snapshot::jsonb INTO source FROM insight.weekly_report WHERE id=p_id AND workspace_id=p_workspace;
 IF source IS NULL THEN RETURN false; END IF;
 FOREACH group_key IN ARRAY ARRAY['records','customers','opportunities'] LOOP
  kind:=CASE group_key WHEN 'records' THEN 'visit' WHEN 'customers' THEN 'customer' ELSE 'opportunity' END;
  FOR item IN SELECT value FROM jsonb_array_elements(CASE WHEN group_key='records' THEN source->'records' ELSE source->'context'->group_key END) LOOP
   IF security.synthetic_trial_marker(p_workspace,kind,(item->>'id')::uuid) IS NOT NULL THEN RETURN true; END IF;
  END LOOP;
 END LOOP;
 RETURN false;
END $$;
REVOKE ALL ON FUNCTION security.synthetic_trial_weekly_source(uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION security.weekly_source_count(p_start timestamptz,p_end timestamptz,p_waterline timestamptz)
RETURNS bigint LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN security.authorization_has('weekly_report.generate') AND security.authorization_has('visit.read')
 THEN (SELECT count(*) FROM activity.visit v WHERE workspace_id=common.current_workspace_id()
 AND recorder_user_ref_id=common.current_user_ref_id() AND deleted_at IS NULL
 AND status IN ('confirmed','archived') AND created_at>=p_start AND created_at<p_end AND created_at<=p_waterline
 AND NOT security.is_synthetic_trial_visit(v.id)) ELSE NULL END;
$$;

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
 IF security.synthetic_trial_weekly_source(r.workspace_id,r.id) THEN
  RAISE EXCEPTION 'WEEKLY_SYNTHETIC_TRIAL_SOURCE' USING ERRCODE='22023'; END IF;
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


CREATE OR REPLACE FUNCTION ops.feishu_weekly_source(p_connection uuid,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
 ws uuid;
 raw jsonb;
 stats jsonb;
 snapshot jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL THEN RAISE insufficient_privilege; END IF;
 SELECT r.feishu_publish_snapshot INTO raw FROM insight.weekly_report r
  WHERE r.id=p_id AND r.workspace_id=ws AND r.feishu_publish_event_id IS NOT NULL;
 IF raw IS NULL THEN RETURN jsonb_build_object('id',p_id,'deleted',true); END IF;
 IF security.synthetic_trial_weekly_source(ws,p_id) THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true); END IF;
 -- Only a completed, contract-accepted report can be published.  A report
 -- that is cancelled, failed, or still queued is excluded without a remote
 -- write, preserving the existing worker's fail-closed behavior.
 IF raw->>'status' <> 'succeeded' OR raw->>'result_status' <> 'ready'
    OR NULLIF(raw->>'draft_markdown','') IS NULL THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 stats:=COALESCE(raw->'statistics','{}'::jsonb);
 RETURN jsonb_build_object(
   'id',raw->>'id',
   'title',COALESCE(raw->>'title','销售周报'),
   'report_week',raw->>'report_week',
   'report_week_end',to_jsonb((raw->>'report_week')::date+6),
   'author_name',(SELECT display_name FROM platform.user_ref WHERE workspace_id=ws AND id=(raw->>'author_id')::uuid),
   'author_account',(SELECT account_code FROM platform.user_ref WHERE workspace_id=ws AND id=(raw->>'author_id')::uuid),
   'status',raw->>'status',
   'result_status',raw->>'result_status',
   'body_markdown',raw->>'draft_markdown',
   'stats_summary',format('跟进 %s 条｜客户 %s 家｜商机 %s 个',
      COALESCE(stats->>'record_count','0'),COALESCE(stats->>'customer_count','0'),
      COALESCE(stats->>'opportunity_count','0')),
   'record_count',COALESCE(stats->>'record_count','0')::integer,
   'customer_count',COALESCE(stats->>'customer_count','0')::integer,
   'opportunity_count',COALESCE(stats->>'opportunity_count','0')::integer,
   'source_cutoff_at',raw->>'source_cutoff_at',
   'snapshot_at',raw->>'snapshot_at',
   'draft_version',COALESCE(raw->>'draft_version','0')::integer,
   'generated_at',raw->>'finished_at',
   'updated_at',raw->>'updated_at',
   'published_at',raw->>'published_at',
   'feishu_push_status',CASE
      WHEN raw->>'published_at' IS NULL THEN '未推送'
      ELSE COALESCE((SELECT CASE d.status WHEN 'sent' THEN '已推送'
                                         WHEN 'unknown' THEN '待核对'
                                         WHEN 'failed' THEN '推送失败'
                                         ELSE '排队中' END
                       FROM ops.feishu_delivery d
                       JOIN ops.feishu_event e ON e.id=d.event_id
                       WHERE e.object_kind='weekly_report' AND e.object_id=p_id
                       ORDER BY d.created_at DESC LIMIT 1),'排队中') END,
   'feishu_message_id',(SELECT d.message_id FROM ops.feishu_delivery d
                         JOIN ops.feishu_event e ON e.id=d.event_id
                         WHERE e.object_kind='weekly_report' AND e.object_id=p_id
                           AND d.message_id IS NOT NULL ORDER BY d.created_at DESC LIMIT 1),
   'created_at',raw->>'created_at',
   'detail_url',NULL::text
 );
END $$;


CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; result jsonb; extra jsonb; original_value jsonb; original_source text; opportunity_meta jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL THEN RAISE insufficient_privilege; END IF;
 IF p_kind IN ('customer','opportunity','visit','task') THEN
  IF security.synthetic_trial_marker(ws,p_kind,p_id) IS NOT NULL THEN
   RETURN jsonb_build_object('id',p_id,'excluded',true); END IF;
 END IF;
 IF p_kind='period_actual_snapshot' THEN
  SELECT jsonb_build_object('id',s.id,'opportunity_id',s.opportunity_id,'customer_id',o.customer_id,
   'opportunity_name',o.name,'customer_name',c.name,'year',s.year,'quarter',s.quarter,'kind',s.kind,
   'source_field',s.source_field,'raw_amount',s.raw_amount,'source_unit',s.source_unit,'tax_basis',s.tax_basis,
   'source_record_id',s.source_record_id,'import_batch_id',s.import_batch_id,'created_at',s.created_at,
   'source_system',b.source_system,'source_base_id',b.source_base_id,
   'source_table_id',r.source_table_id,'source_external_record_id',r.source_record_id,
   'company_name',(SELECT name FROM platform.workspace WHERE id=ws)) INTO result
  FROM crm.opportunity_period_actual_snapshot s
  JOIN crm.opportunity o ON o.id=s.opportunity_id AND o.workspace_id=s.workspace_id
  JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
  JOIN ops.crm_import_record r ON r.id=s.source_record_id AND r.workspace_id=s.workspace_id
  JOIN ops.crm_import_batch b ON b.id=s.import_batch_id AND b.workspace_id=s.workspace_id
  WHERE s.id=p_id AND s.workspace_id=ws AND o.deleted_at IS NULL AND c.deleted_at IS NULL AND c.data_kind='production';
  IF result IS NULL THEN RETURN jsonb_build_object('id',p_id,'excluded',true); END IF;
  RETURN result;
 END IF;
 result:=ops.feishu_source_v120(p_connection,p_kind,p_id);
 IF result->>'excluded'='true' OR result->>'deleted'='true' THEN RETURN result; END IF;
 IF p_kind='opportunity' THEN
  SELECT jsonb_build_object('original_owner_name',original_owner_name,'ownership_resolution',ownership_resolution,
   'ownership_resolution_evidence',ownership_resolution_evidence,'expected_close_year',expected_close_year,
   'expected_close_quarter',expected_close_quarter,
   'associated_partner_ids',(SELECT COALESCE(jsonb_agg(r.partner_id ORDER BY r.partner_id),'[]'::jsonb)
    FROM crm.opportunity_related_partner r WHERE r.workspace_id=ws AND r.opportunity_id=p_id),
   'associated_partner_names',(SELECT COALESCE(jsonb_agg(p.name ORDER BY r.partner_id),'[]'::jsonb)
    FROM crm.opportunity_related_partner r JOIN crm.partner p ON p.id=r.partner_id AND p.workspace_id=r.workspace_id
    WHERE r.workspace_id=ws AND r.opportunity_id=p_id)) INTO extra FROM crm.opportunity WHERE id=p_id AND workspace_id=ws;
  -- Additional raw provenance only. Never replace the existing system creation
  -- timestamp, parse dates, or use an import timestamp as missing historical data.
  IF result->>'deleted_at' IS NULL THEN
   SELECT import_meta INTO opportunity_meta FROM crm.opportunity WHERE id=p_id AND workspace_id=ws;
   IF opportunity_meta->>'import_type'='crm_history' THEN
    original_value:=opportunity_meta->'source_fields'->'商机创建时间';
    original_source:='history_source_fields';
    IF original_value IS NULL OR original_value='null'::jsonb OR
      (jsonb_typeof(original_value)='string' AND (original_value #>> '{}') !~ '[^[:space:]]') THEN
     original_value:=opportunity_meta->'raw_fields'->'商机创建时间';
     original_source:='history_legacy_raw_fields';
    END IF;
    IF original_value IS NULL OR original_value='null'::jsonb OR
      (jsonb_typeof(original_value)='string' AND (original_value #>> '{}') !~ '[^[:space:]]') THEN
     original_value:=NULL; original_source:='historical_unknown';
    END IF;
   ELSE
    original_value:=result->'created_at'; original_source:='system_entry';
   END IF;
   -- A text target preserves strings byte-for-byte; unexpected structured source
   -- values remain JSON text instead of being silently coerced into a date.
   extra:=extra||jsonb_build_object('original_created_at_raw',
    CASE WHEN original_value IS NULL THEN NULL
      WHEN jsonb_typeof(original_value)='string' THEN original_value #>> '{}'
      ELSE original_value::text END,'original_created_at_source',original_source);
  END IF;
 ELSIF p_kind='forecast' THEN
  SELECT jsonb_build_object('collection_confidence',collection_confidence) INTO extra
   FROM crm.opportunity_forecast WHERE id=p_id AND workspace_id=ws;
 ELSIF p_kind='partner' THEN
  SELECT jsonb_build_object('short_name',p.short_name,'principal_name',p.principal_name,
   'channel_manager_user_ref_id',p.channel_manager_user_ref_id,'channel_manager_name',u.display_name,
   'original_channel_manager_name',p.original_channel_manager_name,'priority',p.priority,'progress',p.progress,
   'grade',p.grade,'signed_on',p.signed_on,'partner_type',p.partner_type,'region',p.region,'province',p.province,'note',p.note,
   'last_interaction_at',(SELECT max(v.interaction_at) FROM activity.visit v WHERE v.workspace_id=ws
    AND v.partner_id=p.id AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')))
   INTO extra FROM crm.partner p LEFT JOIN platform.user_ref u ON u.id=p.channel_manager_user_ref_id AND u.workspace_id=p.workspace_id
   WHERE p.id=p_id AND p.workspace_id=ws;
 ELSIF p_kind='visit' THEN
  -- A multi-link narrative is not exported if any linked parent is excluded.
  IF EXISTS(SELECT 1 FROM activity.visit_opportunity x LEFT JOIN crm.opportunity o
    ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id
    LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
    WHERE x.visit_id=p_id AND x.workspace_id=ws AND
     (o.id IS NULL OR o.deleted_at IS NOT NULL OR c.id IS NULL OR c.deleted_at IS NOT NULL OR c.data_kind<>'production')) THEN
   RETURN jsonb_build_object('id',p_id,'excluded',true);
  END IF;
  SELECT jsonb_build_object('partner_id',v.partner_id,'partner_name',p.name,
   'original_recorder_name',v.original_recorder_name,'manager_user_ref_id',v.manager_user_ref_id,
   'manager_name',u.display_name,'manager_account',u.account_code,
   'opportunity_ids',(SELECT COALESCE(jsonb_agg(x.opportunity_id ORDER BY x.opportunity_id),'[]'::jsonb)
    FROM activity.visit_opportunity x WHERE x.visit_id=v.id AND x.workspace_id=ws),
   'opportunity_names',(SELECT COALESCE(jsonb_agg(o.name ORDER BY o.id),'[]'::jsonb)
    FROM activity.visit_opportunity x JOIN crm.opportunity o ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id
    WHERE x.visit_id=v.id AND x.workspace_id=ws),
   'customer_ids',(SELECT COALESCE(jsonb_agg(cid ORDER BY cid),'[]'::jsonb) FROM
    (SELECT v.customer_id AS cid WHERE v.customer_id IS NOT NULL UNION
     SELECT o.customer_id FROM activity.visit_opportunity x JOIN crm.opportunity o
     ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id WHERE x.visit_id=v.id AND x.workspace_id=ws) customers),
   'customer_names',(SELECT COALESCE(jsonb_agg(c.name ORDER BY c.id),'[]'::jsonb) FROM crm.customer c
    WHERE c.workspace_id=ws AND c.id IN (
     SELECT v.customer_id WHERE v.customer_id IS NOT NULL UNION
     SELECT o.customer_id FROM activity.visit_opportunity x JOIN crm.opportunity o
      ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id WHERE x.visit_id=v.id AND x.workspace_id=ws)))
   INTO extra FROM activity.visit v LEFT JOIN crm.partner p ON p.id=v.partner_id AND p.workspace_id=v.workspace_id
   LEFT JOIN platform.user_ref u ON u.id=v.manager_user_ref_id AND u.workspace_id=v.workspace_id WHERE v.id=p_id AND v.workspace_id=ws;
 END IF;
 RETURN result||COALESCE(extra,'{}'::jsonb);
END $$;

CREATE OR REPLACE FUNCTION ops.capture_feishu_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; prior jsonb; ws uuid; cid uuid; kind text:=TG_ARGV[0]; oid uuid; fresh boolean:=false; live boolean:=false; origin text;
BEGIN
 r:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 ws:=(r->>'workspace_id')::uuid;
 -- A new trial object has never had a remote record. Suppress its initial
 -- event before it can compete with formal sync work. Updates/deletes retain
 -- the established reconciliation path, including production-to-trial exits.
 IF TG_OP='INSERT' AND (
   r->'import_meta'->'synthetic_trial'->>'source'='trial_seed'
   OR EXISTS(SELECT 1 FROM crm.customer c WHERE c.id=(r->>'customer_id')::uuid
     AND c.workspace_id=ws AND c.import_meta->'synthetic_trial'->>'source'='trial_seed')
   OR EXISTS(SELECT 1 FROM crm.opportunity o JOIN crm.customer c
     ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
     WHERE o.id=(r->>'opportunity_id')::uuid AND o.workspace_id=ws
       AND (o.import_meta->'synthetic_trial'->>'source'='trial_seed' OR c.import_meta->'synthetic_trial'->>'source'='trial_seed'))
 ) THEN RETURN NULL; END IF;
 SELECT id,enabled AND COALESCE((settings->'notification'->>'enabled')::boolean,false) INTO cid,live FROM config.feishu_connection WHERE workspace_id=ws;
 IF cid IS NULL THEN RETURN NULL; END IF;
 IF TG_OP='UPDATE' AND r=prior THEN RETURN NULL; END IF;
 oid:=(r->>'id')::uuid;
 -- Scheduling source is independent of notification eligibility and old import metadata.
 origin:=CASE WHEN COALESCE(current_setting('app.feishu_historical_import',true),'')='on'
   OR (TG_OP='INSERT' AND COALESCE(r->'import_meta'->>'import_type','')='crm_history')
   THEN 'reconcile' ELSE 'business' END;
 IF kind='visit' THEN
  IF r->>'archived_at' IS NULL THEN RETURN NULL; END IF;
  fresh:=TG_OP='INSERT' OR prior->>'archived_at' IS NULL;
 ELSIF kind='refresh' THEN
  oid:=ws;
 ELSE fresh:=TG_OP='INSERT'; END IF;
 -- Keep exits from production: the worker emits a status-only tombstone.
 IF kind IN ('member','customer','opportunity') AND (TG_OP='DELETE' OR (TG_OP='UPDATE' AND (
  kind='member' OR r->>'data_kind' IS DISTINCT FROM prior->>'data_kind'
  OR r->>'name' IS DISTINCT FROM prior->>'name' OR r->>'deleted_at' IS DISTINCT FROM prior->>'deleted_at'
  OR r->>'customer_id' IS DISTINCT FROM prior->>'customer_id'))) THEN
  INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin)
  VALUES(cid,ws,'refresh',ws,true,origin);
 END IF;
 INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical,queue_origin)
 VALUES(cid,ws,kind,oid,fresh AND TG_OP<>'DELETE',NOT live OR kind='refresh'
 OR COALESCE(r->'import_meta'->>'import_type','')='crm_history'
 OR COALESCE(prior->'import_meta'->>'import_type','')='crm_history'
 OR COALESCE(current_setting('app.feishu_historical_import',true),'')='on',origin);
 RETURN NULL;
END $$;

DO $$ DECLARE function_owner name; identity text; BEGIN
 SELECT pg_get_userbyid(proowner) INTO function_owner FROM pg_proc WHERE oid='security.weekly_source_count(timestamptz,timestamptz,timestamptz)'::regprocedure;
 FOREACH identity IN ARRAY ARRAY['security.synthetic_trial_marker(uuid,text,uuid)','security.is_synthetic_trial_subject(text,uuid)',
 'security.is_synthetic_trial_visit(uuid)','security.protect_synthetic_trial_origin()',
 'security.protect_synthetic_trial_visit_link()','security.synthetic_trial_weekly_source(uuid,uuid)'] LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO %I',identity,function_owner);
 END LOOP;
END $$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v158;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v158();
 FOR r IN SELECT DISTINCT role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles role ON role.oid=a.grantee
  WHERE n.nspname='platform' AND c.relname='user_ref'
   AND a.privilege_type='SELECT' AND a.grantee<>c.relowner
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.is_synthetic_trial_subject(text,uuid),security.is_synthetic_trial_visit(uuid) TO %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.synthetic_trial_marker(uuid,text,uuid),security.synthetic_trial_weekly_source(uuid,uuid),security.protect_synthetic_trial_origin(),security.protect_synthetic_trial_visit_link() FROM %I',r.rolname);
 END LOOP;
 RETURN reconciled;
END $$;
-- Preserve the maintenance function's owner and exact ACL across upgrades.
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='security.reconcile_runtime_grants_v158()'::regprocedure;
 FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid='security.reconcile_runtime_grants()'::regprocedure
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM %s CASCADE',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END); END LOOP;
 EXECUTE format('ALTER FUNCTION security.reconcile_runtime_grants() OWNER TO %I',function_owner);
 FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.reconcile_runtime_grants_v158()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION security.reconcile_runtime_grants() TO %s%s',
  CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
  CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END); END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();

INSERT INTO ops.schema_migration(version,description) VALUES('V159','Synthetic trial provenance and formal report publication boundaries');
COMMIT;
