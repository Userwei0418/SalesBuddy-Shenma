-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V221__exact_visit_agent_run_claim.sql.
-- Original SHA-256: 32a1f5eb1493927d13a9d07da4d9522f93aec0ad90960af2e5f6f2953dd3d978. See shenma_whole_121_manifest.json.
BEGIN;

-- Fresh, explicitly reviewed visit_flow work only. V217 and broad claim paths
-- remain unchanged; this function never recovers or modifies another queue row.
CREATE FUNCTION ops.claim_exact_visit_agent_run(
 p_worker_id text,p_lock_seconds integer,p_job_id uuid,p_workspace_id uuid,
 p_run_id uuid,p_user_id uuid,p_visit_stage text,p_input_sha256 text)
RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE capacity integer; target record; input record; source_import uuid; source_run uuid;
BEGIN
 IF p_lock_seconds IS NULL OR p_lock_seconds NOT BETWEEN 3 AND 3600
  OR COALESCE(length(btrim(p_worker_id)),0) NOT BETWEEN 1 AND 200
  OR p_job_id IS NULL OR p_workspace_id IS NULL OR p_run_id IS NULL OR p_user_id IS NULL
  OR '00000000-0000-0000-0000-000000000000'::uuid IN (p_job_id,p_workspace_id,p_run_id,p_user_id)
  OR p_visit_stage IS NULL OR p_visit_stage NOT IN ('structure','quality')
  OR p_input_sha256 IS NULL OR p_input_sha256 !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'INVALID_EXACT_VISIT_AGENT_CLAIM' USING ERRCODE='22023';
 END IF;

 -- Match the existing lifecycle/scheduler lock order. Count live leases only;
 -- do not sweep expired/retry/other-type work, even when this job is unavailable.
 PERFORM 1 FROM platform.workspace WHERE id=p_workspace_id AND status='active'
  AND deleted_at IS NULL AND security.workspace_is_active(id)
  AND security.workspace_job_enabled(id,'agent.run') FOR SHARE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT concurrency_limit INTO capacity FROM control.tenant_job_schedule
  WHERE workspace_id=p_workspace_id FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 IF (SELECT count(*) FROM ops.job WHERE workspace_id=p_workspace_id
     AND status='running' AND locked_until>clock_timestamp())>=capacity THEN RETURN; END IF;

 SELECT j.* INTO target FROM ops.job j
  WHERE j.id=p_job_id AND j.workspace_id=p_workspace_id
   AND j.job_type='agent.run' AND j.aggregate_type='agent_run' AND j.aggregate_id=p_run_id
   AND j.correlation_id=p_run_id AND jsonb_typeof(j.payload)='object'
   AND j.payload->>'workspace_id'=p_workspace_id::text AND j.payload->>'user_id'=p_user_id::text
   AND j.payload->>'run_id'=p_run_id::text
   AND j.status='queued' AND j.attempts=0 AND j.max_attempts>0
   AND j.available_at<=clock_timestamp()
   AND j.locked_by IS NULL AND j.locked_until IS NULL AND j.lease_token IS NULL
   AND j.started_at IS NULL AND j.completed_at IS NULL
   AND j.last_error_code IS NULL AND j.last_error_detail IS NULL
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id)
   AND NOT EXISTS(SELECT 1 FROM ops.job other WHERE other.workspace_id=p_workspace_id
    AND other.job_type='agent.run' AND other.aggregate_id=p_run_id AND other.id<>j.id)
  FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;

 -- These row locks last through the claim transaction. Skip locks rather than
 -- waiting on services that lock run/message before job. Live authorization is
 -- still rechecked by AgentRunHandler inside the ordinary leased transaction.
 SELECT r.business_context,m.text_content,
  encode(sha256(convert_to(jsonb_build_object(
   'contract','visit-agent-input-v1',
   'run_id',r.id,'workspace_id',r.workspace_id,
   'conversation_id',r.conversation_id,'trigger_message_id',r.trigger_message_id,
   'intent_code',r.intent_code,'identity_context',r.identity_context,
   'business_context',r.business_context,'model_ref',r.model_ref,
   'prompt_version',r.prompt_version,'orchestrator_definition_id',r.orchestrator_definition_id,
   'text_content',m.text_content,'structured_content',m.structured_content,
   'conversation_context',c.context
  )::text,'UTF8')),'hex') AS input_sha256
 INTO input FROM agent.run r
 JOIN agent.conversation c ON c.id=r.conversation_id AND c.workspace_id=r.workspace_id
 JOIN agent.message m ON m.id=r.trigger_message_id AND m.workspace_id=r.workspace_id
  AND m.conversation_id=r.conversation_id
 WHERE r.id=p_run_id AND r.workspace_id=p_workspace_id
  AND r.intent_code='visit_entry' AND r.business_context->>'mode'='visit_entry'
  AND jsonb_typeof(r.business_context->'visit_request')='object'
  AND r.business_context #>> '{visit_request,stage}'=p_visit_stage
  AND r.business_context->>'customer_id'=r.business_context #>> '{visit_request,customer_id}'
  AND r.status='queued' AND r.retry_count=0
  AND r.started_at IS NULL AND r.completed_at IS NULL
  AND r.error_code IS NULL AND r.error_detail IS NULL
  AND r.token_usage='{}'::jsonb AND r.cost_amount IS NULL
  AND jsonb_typeof(r.identity_context)='object'
  AND r.identity_context->>'workspace_id'=p_workspace_id::text
  AND r.identity_context->>'user_id'=p_user_id::text
  AND r.identity_context->'role'=target.payload->'role'
  AND r.identity_context->'data_scope'=target.payload->'data_scope'
  AND r.identity_context->'team_ids'=target.payload->'team_ids'
  AND jsonb_typeof(r.identity_context->'team_ids')='array'
  AND length(btrim(r.identity_context->>'permission_version'))>0
  AND c.user_ref_id=p_user_id AND c.status='active' AND c.context->>'mode'='visit_entry'
  AND m.sender_type='user' AND m.content_type='text' AND length(btrim(m.text_content))>0
  AND m.structured_content->>'input_source'='visit_flow'
  AND NOT EXISTS(SELECT 1 FROM agent.run_step s WHERE s.run_id=r.id)
  AND NOT EXISTS(SELECT 1 FROM agent.artifact a WHERE a.run_id=r.id)
  AND NOT EXISTS(SELECT 1 FROM agent.model_invocation v WHERE v.run_id=r.id)
  AND NOT EXISTS(SELECT 1 FROM agent.tool_invocation t WHERE t.run_id=r.id)
 FOR SHARE OF r,c,m SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 IF input.input_sha256 IS DISTINCT FROM p_input_sha256 THEN RETURN; END IF;

 -- Optional import remains optional (plain-text structure is supported).
 -- Non-null lineage must be an actual completed import owned by this actor.
 IF input.business_context #>> '{visit_request,source_import_id}' IS NOT NULL THEN
  IF (input.business_context #>> '{visit_request,source_import_id}')
     !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN; END IF;
  source_import:=(input.business_context #>> '{visit_request,source_import_id}')::uuid;
  PERFORM 1 FROM activity.visit_import i WHERE i.id=source_import AND i.workspace_id=p_workspace_id
   AND i.created_by_user_ref_id=p_user_id AND i.status='succeeded' FOR SHARE SKIP LOCKED;
  IF NOT FOUND THEN RETURN; END IF;
 END IF;
 IF p_visit_stage='quality' THEN
  IF COALESCE(input.business_context #>> '{visit_request,source_run_id}','')
     !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN; END IF;
  source_run:=(input.business_context #>> '{visit_request,source_run_id}')::uuid;
  -- Successful structure waits for human review. It is not a formal archive.
  PERFORM 1 FROM agent.run s JOIN agent.message sm ON sm.id=s.trigger_message_id
    AND sm.workspace_id=s.workspace_id AND sm.conversation_id=s.conversation_id
   WHERE s.id=source_run AND s.id<>p_run_id AND s.workspace_id=p_workspace_id
    AND s.identity_context->>'workspace_id'=p_workspace_id::text
    AND s.identity_context->>'user_id'=p_user_id::text
    AND s.status='waiting_human' AND s.intent_code='visit_entry'
    AND s.business_context->>'mode'='visit_entry'
    AND s.business_context #>> '{visit_request,stage}'='structure'
    AND s.business_context->>'customer_id'=input.business_context->>'customer_id'
    AND (s.business_context #>> '{visit_request,source_import_id}') IS NOT DISTINCT FROM source_import::text
    AND sm.text_content=input.text_content AND sm.structured_content->>'input_source'='visit_flow'
   FOR SHARE OF s,sm SKIP LOCKED;
  IF NOT FOUND THEN RETURN; END IF;
 ELSIF input.business_context #>> '{visit_request,source_run_id}' IS NOT NULL THEN
  RETURN;
 END IF;
 IF NOT security.workspace_is_active(p_workspace_id)
  OR NOT security.workspace_job_enabled(p_workspace_id,'agent.run') THEN RETURN; END IF;

 RETURN QUERY UPDATE ops.job j
  SET status='running',attempts=1,locked_by=p_worker_id,lease_token=gen_random_uuid(),
      locked_until=clock_timestamp()+make_interval(secs=>p_lock_seconds),
      started_at=clock_timestamp(),updated_at=clock_timestamp()
  WHERE j.id=target.id AND j.workspace_id=p_workspace_id
  RETURNING j.*;
 IF FOUND THEN
  UPDATE control.tenant_job_schedule SET last_claimed_at=clock_timestamp()
   WHERE workspace_id=p_workspace_id;
 END IF;
END $$;

-- Only existing direct V217 restricted Worker grantees may gain this exact
-- capability. No API/Feishu/platform/table/schema/membership grants are added.
DO $$ DECLARE permission record; function_owner name;
 signature text:='ops.claim_exact_visit_agent_run(text,integer,uuid,uuid,uuid,uuid,text,text)';
BEGIN
 SELECT r.rolname INTO STRICT function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='ops.claim_exact_visit_import(text,integer,uuid,uuid,uuid,uuid,text)'::regprocedure;
 FOR permission IN
  SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',signature,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
 END LOOP;
 EXECUTE format('ALTER FUNCTION %s OWNER TO %I',signature,function_owner);
 FOR permission IN
  SELECT DISTINCT r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='ops.claim_exact_visit_import(text,integer,uuid,uuid,uuid,uuid,text)'::regprocedure
   AND a.privilege_type='EXECUTE' AND a.grantee<>p.proowner
   AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolbypassrls
   AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND NOT r.rolreplication
   AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid)
   AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
   AND has_schema_privilege(r.oid,'ops','USAGE')
   AND has_table_privilege(r.oid,'ops.job','UPDATE')
   AND has_table_privilege(r.oid,'agent.run','SELECT')
   AND has_table_privilege(r.oid,'agent.run','UPDATE')
   AND has_table_privilege(r.oid,'agent.message','SELECT')
   AND has_table_privilege(r.oid,'agent.conversation','SELECT')
   AND NOT has_any_column_privilege(r.oid,'activity.visit_import','INSERT')
   AND NOT has_any_column_privilege(r.oid,'agent.run','INSERT')
   AND NOT has_function_privilege(r.oid,'control.tenants(text)','EXECUTE')
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',signature,permission.rolname);
 END LOOP;
END $$;
COMMENT ON FUNCTION ops.claim_exact_visit_agent_run(text,integer,uuid,uuid,uuid,uuid,text,text) IS
 '仅领取指定新拜访结构化或质检agent.run首次租约；绑定租户、用户、阶段、原始输入SHA与来源；不维护旧队列';
INSERT INTO ops.schema_migration(version,description)
 VALUES('V369','Exact first-attempt visit structure/quality Agent run claim for restricted workers');
COMMIT;
