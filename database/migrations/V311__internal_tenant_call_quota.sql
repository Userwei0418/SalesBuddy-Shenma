-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V163__tenant_call_quota.sql.
-- Original SHA-256: 534e8d7e8e30fe695284b169b9ff387639d1f4bbf24c10bd4ac71f2d22c914cd. See shenma_whole_121_manifest.json.
BEGIN;
-- Quotas count logical operations. Provider attempts and reported usage remain
-- separate: retries, fallbacks and ASR chunks are not separate business calls.
CREATE TABLE control.tenant_call_quota(
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 resource text NOT NULL CHECK(resource IN ('ai_calls','asr_calls','tts_calls')),
 call_limit bigint CHECK(call_limit BETWEEN 0 AND 9007199254740991),
 period text NOT NULL DEFAULT 'lifetime' CHECK(period IN ('lifetime','month_utc')),
 PRIMARY KEY(workspace_id,resource)
);
CREATE TABLE control.call_event(
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 resource text NOT NULL,
 operation_code text NOT NULL,
 event_id uuid NOT NULL,
 actor_id uuid NOT NULL,
 state text NOT NULL CHECK(state IN ('reserved','used','released')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,resource,operation_code,event_id),
 FOREIGN KEY(workspace_id,resource) REFERENCES control.tenant_call_quota
);
CREATE INDEX call_event_usage_idx ON control.call_event(workspace_id,resource,created_at) INCLUDE(state);
CREATE TABLE control.call_attempt(
 invocation_id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL,
 resource text NOT NULL,
 operation_code text NOT NULL,
 event_id uuid NOT NULL,
 receipt uuid NOT NULL UNIQUE,
 state text NOT NULL CHECK(state IN ('reserved','dispatched','unknown','settled','released')),
 completion jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(workspace_id,resource,operation_code,event_id) REFERENCES control.call_event
);
CREATE INDEX call_attempt_event_idx ON control.call_attempt(workspace_id,resource,operation_code,event_id,state);
INSERT INTO control.tenant_call_quota(workspace_id,resource)
 SELECT id,r FROM platform.workspace CROSS JOIN unnest(ARRAY['ai_calls','asr_calls','tts_calls']) r;
CREATE FUNCTION control.initialize_call_quota() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO control.tenant_call_quota(workspace_id,resource)
 SELECT NEW.id,r FROM unnest(ARRAY['ai_calls','asr_calls','tts_calls']) r;
 RETURN NEW;
END $$;
CREATE TRIGGER initialize_call_quota AFTER INSERT ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION control.initialize_call_quota();

CREATE FUNCTION security.reserve_model_call(p_invocation uuid,p_receipt uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); actor uuid:=common.current_user_ref_id();
 i agent.model_invocation; q control.tenant_call_quota; e control.call_event; a control.call_attempt;
 resource_code text; logical_id uuid; boundary timestamptz; consumed bigint;
BEGIN
 IF ws IS NULL OR actor IS NULL OR p_receipt IS NULL OR NOT security.workspace_is_active(ws) THEN
  RAISE EXCEPTION 'CALL_RESERVATION_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO i FROM agent.model_invocation WHERE id=p_invocation AND workspace_id=ws
  AND actor_user_ref_id=actor AND actor_role_code=current_setting('app.role_code',true)
  AND record_kind='provider_attempt' AND status='running';
 IF NOT FOUND THEN RAISE EXCEPTION 'CALL_RESERVATION_DENIED' USING ERRCODE='42501'; END IF;
 resource_code:=CASE i.endpoint_code WHEN '/v1/audio/transcriptions' THEN 'asr_calls'
  WHEN '/v1/chat/completions' THEN 'ai_calls' WHEN 'agent-chat-messages' THEN 'ai_calls'
  WHEN '/v1/t2a_v2' THEN 'tts_calls' END;
 logical_id:=COALESCE(i.run_id,i.operation_id);
 IF resource_code IS NULL OR logical_id IS NULL OR COALESCE(length(i.operation_code),0) NOT BETWEEN 1 AND 100 THEN
  RAISE EXCEPTION 'INVALID_CALL_OPERATION' USING ERRCODE='P7422'; END IF;
 SELECT * INTO q FROM control.tenant_call_quota WHERE workspace_id=ws AND resource=resource_code FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'CALL_QUOTA_NOT_INITIALIZED' USING ERRCODE='P7500'; END IF;
 SELECT * INTO a FROM control.call_attempt WHERE invocation_id=p_invocation;
 IF FOUND THEN
  IF a.receipt IS DISTINCT FROM p_receipt OR a.workspace_id<>ws OR a.state<>'reserved' THEN
   RAISE EXCEPTION 'CALL_ATTEMPT_CONFLICT' USING ERRCODE='P7409'; END IF;
  RETURN a.state;
 END IF;
 SELECT * INTO e FROM control.call_event WHERE workspace_id=ws AND resource=resource_code
  AND operation_code=i.operation_code AND event_id=logical_id;
 IF FOUND AND e.actor_id<>actor THEN RAISE EXCEPTION 'CALL_EVENT_CONFLICT' USING ERRCODE='P7409'; END IF;
 IF e.state IS NULL OR e.state='released' THEN
  boundary:=CASE q.period WHEN 'month_utc' THEN date_trunc('month',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' ELSE '-infinity'::timestamptz END;
  IF q.call_limit IS NOT NULL THEN
   SELECT count(*) INTO consumed FROM control.call_event WHERE workspace_id=ws AND resource=resource_code
    AND created_at>=boundary AND state IN ('reserved','used');
   IF consumed>=q.call_limit THEN RAISE EXCEPTION 'TENANT_CALL_QUOTA_EXCEEDED' USING ERRCODE='P7431'; END IF;
  END IF;
  INSERT INTO control.call_event(workspace_id,resource,operation_code,event_id,actor_id,state)
   VALUES(ws,resource_code,i.operation_code,logical_id,actor,'reserved')
   ON CONFLICT(workspace_id,resource,operation_code,event_id) DO UPDATE SET state='reserved',created_at=clock_timestamp();
 END IF;
 INSERT INTO control.call_attempt(invocation_id,workspace_id,resource,operation_code,event_id,receipt,state)
 VALUES(p_invocation,ws,resource_code,i.operation_code,logical_id,p_receipt,'reserved');
 RETURN 'reserved';
END $$;

CREATE FUNCTION security.dispatch_model_call(p_invocation uuid,p_receipt uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a control.call_attempt;
BEGIN
 SELECT * INTO a FROM control.call_attempt WHERE invocation_id=p_invocation;
 IF NOT FOUND OR p_receipt IS NULL OR a.receipt IS DISTINCT FROM p_receipt
  OR a.workspace_id IS DISTINCT FROM common.current_workspace_id()
  OR NOT security.workspace_is_active(a.workspace_id)
  OR NOT EXISTS(SELECT 1 FROM control.call_event e WHERE e.workspace_id=a.workspace_id AND e.resource=a.resource
   AND e.operation_code=a.operation_code AND e.event_id=a.event_id AND e.actor_id=common.current_user_ref_id()) THEN
  RAISE EXCEPTION 'CALL_DISPATCH_DENIED' USING ERRCODE='42501'; END IF;
 UPDATE control.call_attempt SET state='dispatched',updated_at=clock_timestamp()
 WHERE invocation_id=p_invocation AND state='reserved';
 IF NOT FOUND THEN RAISE EXCEPTION 'CALL_ALREADY_DISPATCHED' USING ERRCODE='P7409'; END IF;
 RETURN true;
END $$;

-- A private receipt may finish accounting after tenant/actor revocation. This
-- entry point cannot return facts, authorize a job, or update business results.
CREATE FUNCTION security.finish_model_call(p_invocation uuid,p_receipt uuid,p_sent boolean,p_result jsonb) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a control.call_attempt; target text; event_state text;
BEGIN
 SELECT * INTO a FROM control.call_attempt WHERE invocation_id=p_invocation;
 IF NOT FOUND OR p_receipt IS NULL OR a.receipt IS DISTINCT FROM p_receipt THEN
  RAISE EXCEPTION 'CALL_RECEIPT_DENIED' USING ERRCODE='42501'; END IF;
 IF p_sent IS NULL OR jsonb_typeof(p_result) IS DISTINCT FROM 'object'
  OR COALESCE(p_result->>'status','') NOT IN ('succeeded','failed','cancelled')
  OR octet_length(p_result::text)>16000 THEN RAISE EXCEPTION 'INVALID_CALL_COMPLETION' USING ERRCODE='P7422'; END IF;
 PERFORM 1 FROM control.tenant_call_quota WHERE workspace_id=a.workspace_id AND resource=a.resource FOR UPDATE;
 SELECT * INTO a FROM control.call_attempt WHERE invocation_id=p_invocation FOR UPDATE;
 IF a.completion IS NOT NULL THEN
  IF a.completion IS DISTINCT FROM jsonb_build_object('sent',p_sent,'result',p_result) THEN
   RAISE EXCEPTION 'CALL_COMPLETION_CONFLICT' USING ERRCODE='P7409'; END IF;
  RETURN a.state;
 END IF;
 IF p_sent AND a.state<>'dispatched' THEN RAISE EXCEPTION 'CALL_NOT_DISPATCHED' USING ERRCODE='P7409'; END IF;
 target:=CASE WHEN NOT p_sent THEN 'released' WHEN p_result->>'http_status' IS NULL THEN 'unknown' ELSE 'settled' END;
 UPDATE control.call_attempt SET state=target,completion=jsonb_build_object('sent',p_sent,'result',p_result),updated_at=clock_timestamp()
 WHERE invocation_id=p_invocation;
 SELECT CASE WHEN bool_or(state='settled') THEN 'used'
  WHEN bool_or(state IN ('reserved','dispatched','unknown')) THEN 'reserved' ELSE 'released' END
 INTO event_state FROM control.call_attempt WHERE workspace_id=a.workspace_id AND resource=a.resource
  AND operation_code=a.operation_code AND event_id=a.event_id;
 UPDATE control.call_event SET state=event_state WHERE workspace_id=a.workspace_id AND resource=a.resource
  AND operation_code=a.operation_code AND event_id=a.event_id;
 UPDATE agent.model_invocation SET status=p_result->>'status',http_status=(p_result->>'http_status')::integer,
  input_tokens=(p_result->>'input_tokens')::integer,output_tokens=(p_result->>'output_tokens')::integer,
  audio_seconds=(p_result->>'audio_seconds')::numeric,usage_detail=jsonb_build_object('characters',p_result->'characters'),
  upstream_trace_id=left(p_result->>'trace_id',200),error_code=left(p_result->>'error_code',100),
  response_snapshot=response_snapshot || COALESCE(p_result->'response_metadata','{}'::jsonb),
  completed_at=clock_timestamp(),latency_ms=LEAST(2147483647,GREATEST(0,extract(epoch FROM clock_timestamp()-started_at)*1000))::integer
 WHERE id=p_invocation AND workspace_id=a.workspace_id AND record_kind='provider_attempt';
 RETURN target;
END $$;

CREATE FUNCTION control.call_quotas(p_session text,p_workspace uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 PERFORM control.require_actor(p_session);
 SELECT jsonb_build_object('workspace_id',w.id,'version_no',w.version_no,'periods',jsonb_build_array(jsonb_build_object('code','lifetime','label','自启用计量起累计'),jsonb_build_object('code','month_utc','label','UTC 自然月')),'items',(
  SELECT jsonb_agg(jsonb_build_object('resource',q.resource,'label',CASE q.resource WHEN 'ai_calls' THEN 'AI 业务调用'
   WHEN 'asr_calls' THEN '录音转写' ELSE '语音合成' END,'call_limit',q.call_limit,'period',q.period,
   'used',u.used,'reserved',u.reserved,'pending_attempts',(
    SELECT count(*) FROM control.call_attempt a WHERE a.workspace_id=w.id AND a.resource=q.resource AND a.state IN ('reserved','dispatched','unknown')),
   'remaining',CASE WHEN q.call_limit IS NULL THEN NULL ELSE greatest(0,q.call_limit-u.used-u.reserved) END,
   'period_start',CASE WHEN q.period='month_utc' THEN date_trunc('month',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' END)
   ORDER BY q.resource) FROM control.tenant_call_quota q CROSS JOIN LATERAL (
    SELECT count(*) FILTER(WHERE state='used') AS used,count(*) FILTER(WHERE state='reserved') AS reserved
    FROM control.call_event e WHERE e.workspace_id=q.workspace_id AND e.resource=q.resource
     AND (q.period='lifetime' OR e.created_at>=date_trunc('month',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')) u
   WHERE q.workspace_id=w.id)) INTO result FROM platform.workspace w WHERE w.id=p_workspace AND w.deleted_at IS NULL;
 IF result IS NULL THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 RETURN result;
END $$;
CREATE FUNCTION control.save_call_quota(p_session text,p_workspace uuid,p_version integer,p_resource text,p_limit bigint,p_period text,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_sensitive(p_session); version integer; before_value jsonb; after_value jsonb;
BEGIN
 SELECT version_no INTO version FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 IF version IS DISTINCT FROM p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='P7409'; END IF;
 IF p_resource IS NULL OR p_resource NOT IN ('ai_calls','asr_calls','tts_calls') OR p_period IS NULL OR p_period NOT IN ('lifetime','month_utc')
  OR (p_limit IS NOT NULL AND p_limit NOT BETWEEN 0 AND 9007199254740991)
  OR COALESCE(length(btrim(p_reason)),0) NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'INVALID_CALL_QUOTA' USING ERRCODE='P7422'; END IF;
 PERFORM 1 FROM control.tenant_call_quota WHERE workspace_id=p_workspace AND resource=p_resource FOR UPDATE;
 before_value:=control.call_quotas(p_session,p_workspace);
 UPDATE control.tenant_call_quota SET call_limit=p_limit,period=p_period WHERE workspace_id=p_workspace AND resource=p_resource;
 UPDATE platform.workspace SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_workspace;
 after_value:=control.call_quotas(p_session,p_workspace);
 INSERT INTO control.audit(operator_id,workspace_id,action,reason,before_state,after_state)
 VALUES(actor,p_workspace,'tenant.call_quota',btrim(p_reason),before_value,after_value);
 RETURN after_value;
END $$;
REVOKE ALL ON control.tenant_call_quota,control.call_event,control.call_attempt FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON FUNCTION control.initialize_call_quota(),control.call_quotas(text,uuid),control.save_call_quota(text,uuid,integer,text,bigint,text,text),
 security.reserve_model_call(uuid,uuid),security.dispatch_model_call(uuid,uuid),security.finish_model_call(uuid,uuid,boolean,jsonb)
 FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_internal_v162;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_internal_v162();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_internal_v162() FROM %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.reserve_model_call(uuid,uuid),security.dispatch_model_call(uuid,uuid),security.finish_model_call(uuid,uuid,boolean,jsonb) TO %I',r.rolname);
 END LOOP;
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
 AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.call_quotas(text,uuid),control.save_call_quota(text,uuid,integer,text,bigint,text,text) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_internal_v162() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V311','Logical AI and audio call quotas with private settlement receipts');
COMMIT;
