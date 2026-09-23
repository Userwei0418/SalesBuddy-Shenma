BEGIN;

CREATE TABLE agent.inference_operation (
 id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 actor_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 actor_role_code text NOT NULL,
 capability text NOT NULL,
 run_id uuid REFERENCES agent.run(id),
 job_id uuid REFERENCES ops.job(id),
 request_id uuid,
 status text NOT NULL DEFAULT 'running'
   CHECK(status IN ('running','accepted','failed','cancelled','reconciliation_required')),
 scope_snapshot jsonb NOT NULL DEFAULT '{}',
 configuration jsonb NOT NULL DEFAULT '{}',
 input_summary jsonb NOT NULL DEFAULT '{}',
 events jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(events)='array'),
 trace jsonb NOT NULL DEFAULT '{}',
 error_code text,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz
);
CREATE INDEX inference_operation_workspace_time ON agent.inference_operation(workspace_id,started_at DESC);
CREATE INDEX inference_operation_run ON agent.inference_operation(run_id) WHERE run_id IS NOT NULL;
ALTER TABLE agent.inference_operation ENABLE ROW LEVEL SECURITY;
CREATE POLICY inference_operation_read ON agent.inference_operation FOR SELECT
 USING(workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id());
CREATE POLICY inference_operation_start ON agent.inference_operation FOR INSERT
 WITH CHECK(workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
            AND actor_role_code=common.current_role_code() AND status='running');
CREATE POLICY inference_operation_finish ON agent.inference_operation FOR UPDATE
 USING(workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id() AND status='running')
 WITH CHECK(workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
            AND actor_role_code=common.current_role_code());
COMMENT ON TABLE agent.inference_operation IS '推理审计回执：accepted仅表示结果契约通过；业务完成以run/artifact/job事实为准';
CREATE INDEX model_invocation_operation_audit ON agent.model_invocation(workspace_id,operation_id,started_at)
 WHERE record_kind='provider_attempt';

-- Only management within its current workspace can aggregate private runs.
-- Return evidence metadata, never raw model input/output, keys or customer text.
CREATE FUNCTION security.agent_operation_rows(p_start timestamptz,p_end timestamptz)
 RETURNS TABLE(operation_id uuid,case_id uuid,started_at timestamptz,record jsonb)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH attempts AS MATERIALIZED (
   SELECT i.* FROM agent.model_invocation i
    WHERE security.management_actor() AND i.workspace_id=common.current_workspace_id()
      AND i.record_kind='provider_attempt' AND i.started_at>=p_start AND i.started_at<p_end
 ), receipts AS MATERIALIZED (
   SELECT a.* FROM agent.inference_operation a
    WHERE security.management_actor() AND a.workspace_id=common.current_workspace_id()
      AND a.started_at>=p_start AND a.started_at<p_end
 ), ids AS (
   SELECT id FROM receipts UNION SELECT operation_id FROM attempts WHERE operation_id IS NOT NULL
 )
 SELECT ids.id,COALESCE(a.run_id,first_attempt.run_id,a.job_id,ids.id),
        COALESCE(a.started_at,first_attempt.started_at),
   jsonb_build_object(
     'id',ids.id,'case_id',COALESCE(a.run_id,first_attempt.run_id,a.job_id,ids.id),
     'run_id',COALESCE(a.run_id,first_attempt.run_id),'job_id',a.job_id,
     'capability',COALESCE(a.capability,first_attempt.operation_code),
     'actor_id',COALESCE(a.actor_user_ref_id,first_attempt.actor_user_ref_id),
     'actor_name',u.display_name,'actor_role',COALESCE(a.actor_role_code,first_attempt.actor_role_code),
     'started_at',COALESCE(a.started_at,first_attempt.started_at),'completed_at',a.completed_at,
     'inference_status',COALESCE(a.status,'unknown'),'audit_source',CASE WHEN a.id IS NULL THEN 'historical_attempts' ELSE 'inference_receipt' END,
     'scope',a.scope_snapshot,'configuration',a.configuration,'input_summary',a.input_summary,
     'events',COALESCE(a.events,'[]'::jsonb),'error_code',a.error_code,
     'trace',CASE WHEN a.id IS NOT NULL THEN a.trace
             WHEN r.business_context->'inference_route'->>'operation_id'=ids.id::text
             THEN r.business_context->'inference_route' ELSE '{}'::jsonb END,
     'business_status',r.status,'business_completed_at',r.completed_at,
     'customer_id',c.id,'customer_name',c.name,
     'job_status',j.status,'job_effect_recorded',EXISTS(SELECT 1 FROM ops.job_effect e
                WHERE e.job_id=a.job_id AND e.workspace_id=common.current_workspace_id()),
     'artifacts',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',f.id,'type',f.artifact_type,'status',f.status))
                 FROM agent.artifact f WHERE f.run_id=r.id AND f.workspace_id=common.current_workspace_id()),'[]'::jsonb),
     'assistant_results',(SELECT count(*) FROM agent.message m WHERE m.source_run_id=r.id
                           AND m.workspace_id=common.current_workspace_id() AND m.sender_type='assistant'),
     'business_effects',jsonb_build_object(
       'created_tasks',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',e.task_id,'event_id',e.id,'status',e.to_status))
         FROM workflow.task_event e WHERE e.workspace_id=common.current_workspace_id()
           AND e.event_type='created' AND e.payload->>'run_id'=r.id::text),'[]'::jsonb),
       'risk_events',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',e.risk_id,'event_id',e.id,'status',e.to_status))
         FROM insight.risk_event e WHERE e.workspace_id=common.current_workspace_id()
           AND e.payload->>'run_id'=r.id::text),'[]'::jsonb),
       'scores',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',q.id,'customer_id',q.customer_id,
           'potential_score',q.potential_score,'relationship_score',q.relationship_score,'quadrant_code',q.quadrant_code,
           'rule_set_id',q.rule_set_id))
         FROM insight.quadrant_score q WHERE q.workspace_id=common.current_workspace_id()
           AND q.input_snapshot->'inference_route'->>'operation_id'=ids.id::text),'[]'::jsonb)),
     'attempts',COALESCE((SELECT jsonb_agg(jsonb_build_object(
         'id',i.id,'provider',i.provider_code,'model',i.model_id,'endpoint',i.endpoint_code,
         'status',i.status,'attempt_no',i.attempt_no,'http_status',i.http_status,'error_code',i.error_code,
         'started_at',i.started_at,'completed_at',i.completed_at,'latency_ms',i.latency_ms,
         'input_tokens',i.input_tokens,'output_tokens',i.output_tokens,'upstream_trace_id',i.upstream_trace_id,
         'request_id',i.request_id,'network_dispatch_suppressed',i.request_snapshot->'network_dispatch_suppressed',
         'test_fault_injected',i.request_snapshot->'test_fault_injected',
         'transport',i.response_snapshot->'transport') ORDER BY i.started_at,i.id)
       FROM attempts i WHERE i.operation_id=ids.id),'[]'::jsonb)
   )
 FROM ids LEFT JOIN receipts a ON a.id=ids.id
 LEFT JOIN LATERAL (SELECT * FROM attempts i WHERE i.operation_id=ids.id ORDER BY i.started_at,i.id LIMIT 1) first_attempt ON true
 LEFT JOIN agent.run r ON r.id=COALESCE(a.run_id,first_attempt.run_id) AND r.workspace_id=common.current_workspace_id()
 LEFT JOIN ops.job j ON j.id=a.job_id AND j.workspace_id=common.current_workspace_id()
 LEFT JOIN crm.customer c ON c.workspace_id=common.current_workspace_id()
   AND c.id::text=COALESCE(r.business_context->>'customer_id',CASE WHEN j.aggregate_type='customer' THEN j.aggregate_id::text END)
 LEFT JOIN platform.user_ref u ON u.id=COALESCE(a.actor_user_ref_id,first_attempt.actor_user_ref_id)
                             AND u.workspace_id=common.current_workspace_id();
$$;

COMMIT;
