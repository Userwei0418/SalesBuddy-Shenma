-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V164__call_quota_job_identity.sql.
-- Original SHA-256: 5151d9b42da822270e8a6721a25f91d5d77d9b0d096c9a192d83a42eeb3d0c07. See shenma_whole_121_manifest.json.
BEGIN;
-- Preserve the function owner/ACL while linking non-agent jobs to their stable business aggregate.
CREATE OR REPLACE FUNCTION security.reserve_model_call(p_invocation uuid,p_receipt uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); actor uuid:=common.current_user_ref_id();
 i agent.model_invocation; q control.tenant_call_quota; e control.call_event; a control.call_attempt;
 resource_code text; logical_id uuid; job_event uuid; job_id uuid:=NULLIF(current_setting('app.job_id',true),'')::uuid; boundary timestamptz; consumed bigint;
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
 IF job_id IS NOT NULL THEN
  SELECT COALESCE(j.aggregate_id,j.id) INTO job_event FROM ops.job j WHERE j.id=job_id AND j.workspace_id=ws
   AND j.status='running' AND j.locked_until>clock_timestamp();
  IF NOT FOUND THEN RAISE EXCEPTION 'CALL_JOB_LEASE_INVALID' USING ERRCODE='P7409'; END IF;
 END IF;
 logical_id:=COALESCE(i.run_id,job_event,i.operation_id);
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

INSERT INTO ops.schema_migration(version,description) VALUES('V312','Stable logical call identity across typed worker retries');
COMMIT;
