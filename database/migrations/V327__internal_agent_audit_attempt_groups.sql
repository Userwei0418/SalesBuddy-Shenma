-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V179__agent_audit_attempt_groups.sql.
-- Original SHA-256: 9577f69b22d9ff52ba8a7afc6c24a981ae57904290a34316b4ed9c47741eea3a. See shenma_whole_121_manifest.json.
-- Build each operation's attempts once, including ordered first-attempt metadata.
-- Keep V146 multi-task audit fields, output shape, tenant checks, owner and ACLs.
-- CREATE OR REPLACE retains the existing function identity and grants.
BEGIN;

CREATE OR REPLACE FUNCTION security.agent_operation_rows_v066(p_start timestamp with time zone, p_end timestamp with time zone)
 RETURNS TABLE(operation_id uuid, case_id uuid, started_at timestamp with time zone, record jsonb)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH attempts AS MATERIALIZED (
   SELECT i.* FROM agent.model_invocation i
    WHERE (security.authorization_has('ai.run_read') OR security.authorization_has('ai.run_export')) AND i.workspace_id=common.current_workspace_id()
      AND i.record_kind='provider_attempt' AND i.started_at>=p_start AND i.started_at<p_end
 ), receipts AS MATERIALIZED (
   SELECT a.* FROM agent.inference_operation a
    WHERE (security.authorization_has('ai.run_read') OR security.authorization_has('ai.run_export')) AND a.workspace_id=common.current_workspace_id()
      AND a.started_at>=p_start AND a.started_at<p_end
 ), attempt_groups AS MATERIALIZED (
   SELECT i.operation_id,
     (array_agg(i.run_id ORDER BY i.started_at,i.id))[1] AS run_id,
     (array_agg(i.operation_code ORDER BY i.started_at,i.id))[1] AS operation_code,
     (array_agg(i.actor_user_ref_id ORDER BY i.started_at,i.id))[1] AS actor_user_ref_id,
     (array_agg(i.actor_role_code ORDER BY i.started_at,i.id))[1] AS actor_role_code,
     min(i.started_at) AS started_at,
     jsonb_agg(jsonb_build_object(
         'id',i.id,'provider',i.provider_code,'model',i.model_id,'endpoint',i.endpoint_code,
         'status',i.status,'attempt_no',i.attempt_no,'http_status',i.http_status,'error_code',i.error_code,
         'started_at',i.started_at,'completed_at',i.completed_at,'latency_ms',i.latency_ms,
         'input_tokens',i.input_tokens,'output_tokens',i.output_tokens,'upstream_trace_id',i.upstream_trace_id,
         'request_id',i.request_id,'network_dispatch_suppressed',i.request_snapshot->'network_dispatch_suppressed',
         'test_fault_injected',i.request_snapshot->'test_fault_injected',
         'transport',i.response_snapshot->'transport') ORDER BY i.started_at,i.id) AS attempts
   FROM attempts i WHERE i.operation_id IS NOT NULL GROUP BY i.operation_id
 ), ids AS (
   SELECT id FROM receipts UNION SELECT operation_id FROM attempt_groups
 )
 SELECT ids.id,COALESCE(ba.id,a.run_id,first_attempt.run_id,a.job_id,ids.id),
        COALESCE(a.started_at,first_attempt.started_at),
   jsonb_build_object(
     'id',ids.id,'case_id',COALESCE(ba.id,a.run_id,first_attempt.run_id,a.job_id,ids.id),
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
     'business_status',CASE WHEN ba.id IS NULL THEN r.status
       WHEN ba.status='succeeded' AND EXISTS(SELECT 1 FROM insight.business_suggestion s
         WHERE s.advice_id=ba.id AND s.decision='pending') THEN 'waiting_human' ELSE ba.status END,
     'business_completed_at',COALESCE(ba.completed_at,r.completed_at),
     'advice_id',ba.id,'subject_kind',ba.subject_kind,'subject_id',ba.subject_id,
     'customer_id',c.id,'customer_name',c.name,
     'job_status',j.status,'job_effect_recorded',EXISTS(SELECT 1 FROM ops.job_effect e
                WHERE e.job_id=a.job_id AND e.workspace_id=common.current_workspace_id()),
     'artifacts',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',f.id,'type',f.artifact_type,'status',f.status))
                 FROM agent.artifact f WHERE f.run_id=r.id AND f.workspace_id=common.current_workspace_id()),'[]'::jsonb),
     'assistant_results',(SELECT count(*) FROM agent.message m WHERE m.source_run_id=r.id
                           AND m.workspace_id=common.current_workspace_id() AND m.sender_type='assistant'),
     'business_effects',jsonb_build_object(
       'suggestions',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',s.id,'decision',s.decision,
          'task_id',s.task_id,'task_ids',(SELECT COALESCE(jsonb_agg(st.id ORDER BY st.created_at,st.id),'[]'::jsonb) FROM workflow.task st WHERE st.source_suggestion_id=s.id AND st.workspace_id=s.workspace_id),'decided_by',s.decided_by_user_ref_id,'decided_at',s.decided_at) ORDER BY s.ordinal)
          FROM insight.business_suggestion s WHERE s.advice_id=ba.id),'[]'::jsonb),
       'created_tasks',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',e.task_id,'event_id',e.id,'status',e.to_status))
         FROM workflow.task_event e WHERE e.workspace_id=common.current_workspace_id()
           AND e.event_type='created' AND (e.payload->>'run_id'=r.id::text OR EXISTS(
             SELECT 1 FROM insight.business_suggestion s WHERE s.advice_id=ba.id
               AND EXISTS(SELECT 1 FROM workflow.task st WHERE st.id=e.task_id AND st.source_suggestion_id=s.id AND st.workspace_id=s.workspace_id) AND s.decision='adopted'))),'[]'::jsonb),
       'risk_events',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',e.risk_id,'event_id',e.id,'status',e.to_status))
         FROM insight.risk_event e WHERE e.workspace_id=common.current_workspace_id()
           AND e.payload->>'run_id'=r.id::text),'[]'::jsonb),
       'scores',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',q.id,'customer_id',q.customer_id,
           'potential_score',q.potential_score,'relationship_score',q.relationship_score,'quadrant_code',q.quadrant_code,
           'rule_set_id',q.rule_set_id))
         FROM insight.quadrant_score q WHERE q.workspace_id=common.current_workspace_id()
           AND q.input_snapshot->'inference_route'->>'operation_id'=ids.id::text),'[]'::jsonb)),
     'attempts',COALESCE(first_attempt.attempts,'[]'::jsonb)
   )
 FROM ids LEFT JOIN receipts a ON a.id=ids.id
 LEFT JOIN attempt_groups first_attempt ON first_attempt.operation_id=ids.id
 LEFT JOIN agent.run r ON r.id=COALESCE(a.run_id,first_attempt.run_id) AND r.workspace_id=common.current_workspace_id()
 LEFT JOIN ops.job j ON j.id=a.job_id AND j.workspace_id=common.current_workspace_id()
 LEFT JOIN insight.business_advice ba ON ba.workspace_id=common.current_workspace_id()
   AND (ba.inference_trace->>'operation_id'=ids.id::text
        OR (j.aggregate_type='business_advice' AND j.aggregate_id=ba.id))
 LEFT JOIN crm.customer c ON c.workspace_id=common.current_workspace_id()
   AND c.id::text=COALESCE(ba.customer_id::text,r.business_context->>'customer_id',CASE WHEN j.aggregate_type='customer' THEN j.aggregate_id::text END)
 LEFT JOIN platform.user_ref u ON u.id=COALESCE(a.actor_user_ref_id,first_attempt.actor_user_ref_id)
                             AND u.workspace_id=common.current_workspace_id();
$function$
;

COMMIT;
