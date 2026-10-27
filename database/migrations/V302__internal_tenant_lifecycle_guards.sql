-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V154__tenant_lifecycle_guards.sql.
-- Original SHA-256: e93c3b9e28b72d55f4479f1431842a8092a6c74c754c8a478dfb3b928dea660d. See shenma_whole_121_manifest.json.
BEGIN;
CREATE FUNCTION security.workspace_is_active(p_workspace uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM platform.workspace WHERE id=p_workspace AND status='active' AND deleted_at IS NULL);
$$;
GRANT EXECUTE ON FUNCTION security.workspace_is_active(uuid) TO PUBLIC;

CREATE OR REPLACE FUNCTION security.has_active_role(p_role_code text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.workspace_is_active(common.current_workspace_id()) AND EXISTS(
  SELECT 1 FROM platform.role_binding rb WHERE rb.workspace_id=common.current_workspace_id()
   AND rb.user_ref_id=common.current_user_ref_id() AND rb.role_code=p_role_code
   AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to);
$$;

-- A write transaction holds this row until commit. Suspension waits for prior
-- writes, then revokes leases; later transactions cannot reuse a captured scope.
CREATE OR REPLACE FUNCTION security.authorization_refresh() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE grants jsonb; ws uuid:=common.current_workspace_id(); current_status text;
BEGIN
 IF ws IS NOT NULL THEN
  IF current_setting('transaction_read_only')='on' THEN
   SELECT status INTO current_status FROM platform.workspace WHERE id=ws AND deleted_at IS NULL;
  ELSE
   SELECT status INTO current_status FROM platform.workspace WHERE id=ws AND deleted_at IS NULL FOR SHARE;
  END IF;
  IF current_status IS DISTINCT FROM 'active' THEN
   RAISE EXCEPTION 'TENANT_INACTIVE' USING ERRCODE='P7403'; END IF;
 END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(g) ORDER BY g.permission_code,g.effect,g.source_id,g.scope_code,g.team_ids),'[]') INTO grants
 FROM security.authorization_grants_for(ws,common.current_user_ref_id()) g;
 PERFORM set_config('app.authorization_context',jsonb_build_object('workspace_id',ws,
  'user_id',common.current_user_ref_id(),'grants',grants)::text,true);
END $$;

-- Evaluate the tenant state once per business statement, not once per CRM row.
DO $$ DECLARE t record; BEGIN
 FOR t IN SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname IN ('crm','activity','workflow','insight','agent') AND c.relkind='r' AND c.relrowsecurity
   AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='workspace_id' AND NOT a.attisdropped)
 LOOP
  EXECUTE format('CREATE POLICY active_tenant_guard ON %I.%I AS RESTRICTIVE FOR ALL TO PUBLIC USING ((SELECT security.workspace_is_active(common.current_workspace_id()))) WITH CHECK ((SELECT security.workspace_is_active(common.current_workspace_id())))',t.nspname,t.relname);
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION ops.claim_job(p_worker_id text,p_lock_seconds integer,
 p_job_types text[],p_exclude_types boolean DEFAULT false) RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ops AS $$
DECLARE expired record;
BEGIN
 IF p_lock_seconds IS NULL OR p_exclude_types IS NULL
  OR (p_job_types IS NOT NULL AND array_position(p_job_types,NULL) IS NOT NULL)
  OR p_lock_seconds < 3 OR p_lock_seconds > 3600 OR NULLIF(btrim(p_worker_id),'') IS NULL THEN
  RAISE EXCEPTION 'invalid worker lease settings';
 END IF;
 -- A process may stop after the result commits but before acknowledging its queue row.
 UPDATE ops.job j SET status='succeeded',completed_at=COALESCE(j.completed_at,clock_timestamp()),
   locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=clock_timestamp()
 WHERE security.workspace_is_active(j.workspace_id) AND j.status IN ('queued','failed','running')
 AND (p_job_types IS NULL OR (j.job_type=ANY(p_job_types))<>p_exclude_types)
 AND (j.locked_until IS NULL OR j.locked_until<=clock_timestamp())
 AND (EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id)
   OR (j.job_type='agent.run' AND EXISTS(SELECT 1 FROM agent.run r
       WHERE r.id=j.aggregate_id AND r.status IN ('succeeded','waiting_human')))
   OR (j.job_type='visit.import' AND EXISTS(SELECT 1 FROM activity.visit_import i
       WHERE i.id=j.aggregate_id AND i.status='succeeded'))
   OR (j.job_type='sales_competency.review' AND EXISTS(SELECT 1 FROM insight.sales_competency_review r
       WHERE r.id=j.aggregate_id AND r.status='succeeded')));
 -- Bounded attempts also cover processes that disappeared without an exception handler.
 FOR expired IN
  UPDATE ops.job SET status='dead_letter',completed_at=clock_timestamp(),locked_by=NULL,
   locked_until=NULL,lease_token=NULL,last_error_code='LEASE_ATTEMPTS_EXHAUSTED',
   last_error_detail='后台任务中断且已达到重试上限，请重试或联系管理员',updated_at=clock_timestamp()
  WHERE security.workspace_is_active(workspace_id) AND status IN ('queued','failed','running') AND attempts>=max_attempts
    AND (p_job_types IS NULL OR (job_type=ANY(p_job_types))<>p_exclude_types)
    AND (locked_until IS NULL OR locked_until<=clock_timestamp()) RETURNING *
 LOOP
  IF expired.job_type='agent.run' THEN
   UPDATE agent.run SET status='failed',completed_at=clock_timestamp(),
    error_code='LEASE_ATTEMPTS_EXHAUSTED',error_detail=expired.last_error_detail
    WHERE id=expired.aggregate_id AND status IN ('queued','running');
  ELSIF expired.job_type='visit.import' THEN
   UPDATE activity.visit_import SET status='failed',error_message=expired.last_error_detail,updated_at=clock_timestamp()
    WHERE id=expired.aggregate_id AND status IN ('queued','processing');
  ELSIF expired.job_type='business.advice' THEN
   UPDATE insight.business_advice SET status='failed',error_code='LEASE_ATTEMPTS_EXHAUSTED',
     completed_at=clock_timestamp() WHERE id=expired.aggregate_id AND status IN ('queued','running');
  ELSIF expired.job_type='sales_competency.review' THEN
   UPDATE insight.sales_competency_review SET status='failed',error_code='LEASE_ATTEMPTS_EXHAUSTED',
    error_detail=expired.last_error_detail WHERE id=expired.aggregate_id AND status IN ('queued','running');
  END IF;
 END LOOP;
 RETURN QUERY
 UPDATE ops.job j SET status='running',attempts=j.attempts+1,locked_by=p_worker_id,
  lease_token=gen_random_uuid(),locked_until=clock_timestamp()+make_interval(secs=>p_lock_seconds),
  started_at=COALESCE(j.started_at,clock_timestamp()),updated_at=clock_timestamp()
 WHERE j.id=(SELECT candidate.id FROM ops.job candidate
  WHERE security.workspace_is_active(candidate.workspace_id) AND candidate.status IN ('queued','failed','running') AND candidate.attempts<candidate.max_attempts
   AND (p_job_types IS NULL OR (candidate.job_type=ANY(p_job_types))<>p_exclude_types)
   AND candidate.available_at<=clock_timestamp()
   AND (candidate.locked_until IS NULL OR candidate.locked_until<=clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=candidate.id)
  ORDER BY candidate.priority DESC,candidate.available_at,candidate.created_at,candidate.id
  FOR UPDATE SKIP LOCKED LIMIT 1)
 RETURNING j.*;
END $$;


CREATE OR REPLACE FUNCTION security.password_login_identifier(p_workspace text,p_identifier text)
 RETURNS TABLE(workspace text,account_code text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH candidates AS (
  SELECT w.external_workspace_id AS workspace,u.account_code
  FROM platform.workspace w JOIN platform.user_ref u ON u.workspace_id=w.id
  JOIN platform.password_credential p ON p.user_ref_id=u.id AND p.workspace_id=u.workspace_id
  WHERE w.status='active' AND w.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL
   AND (NULLIF(btrim(p_workspace),'') IS NULL OR lower(w.external_workspace_id)=lower(btrim(p_workspace)))
   AND (u.account_code=upper(btrim(p_identifier)) OR u.phone_number=btrim(p_identifier))
 ) SELECT c.workspace,c.account_code FROM candidates c WHERE (SELECT count(*) FROM candidates)=1;
$$;

CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; result jsonb; extra jsonb; original_value jsonb; original_source text; opportunity_meta jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL OR NOT security.workspace_is_active(ws) THEN RAISE insufficient_privilege; END IF;
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

CREATE OR REPLACE FUNCTION ops.feishu_reconcile(p_connection uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; item text[]; total bigint:=0; n bigint;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND enabled AND security.workspace_is_active(workspace_id);
 IF ws IS NULL THEN RETURN 0; END IF;
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact'],['crm.opportunity_forecast','forecast'],['crm.opportunity_period_actual_snapshot','period_actual_snapshot']] LOOP
  IF NOT EXISTS(SELECT 1 FROM config.feishu_connection c WHERE c.id=p_connection AND (c.settings->'mappings'->item[2]->>'enabled')::boolean IS TRUE) THEN CONTINUE; END IF;
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin) SELECT $1,$2,$3,id,true,''reconcile'' FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''running'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 RETURN total;
END $$;

CREATE FUNCTION security.feishu_lock_lease(p_event uuid,p_token uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; state text;
BEGIN
 SELECT workspace_id INTO ws FROM ops.feishu_event WHERE id=p_event;
 SELECT status INTO state FROM platform.workspace WHERE id=ws AND deleted_at IS NULL FOR SHARE;
 IF state IS DISTINCT FROM 'active' THEN RAISE EXCEPTION 'SYNC_LEASE_LOST' USING ERRCODE='P7403'; END IF;
 PERFORM 1 FROM ops.feishu_event WHERE id=p_event AND lease_token=p_token AND status='running'
  AND locked_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'SYNC_LEASE_LOST' USING ERRCODE='P7403'; END IF;
END $$;
REVOKE ALL ON FUNCTION security.feishu_lock_lease(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION security.feishu_lock_lease(uuid,uuid) TO salegent_feishu_worker;

COMMIT;
