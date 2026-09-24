BEGIN;

CREATE OR REPLACE FUNCTION security.agent_operation_rows(p_start timestamp with time zone, p_end timestamp with time zone)
 RETURNS TABLE(operation_id uuid, case_id uuid, started_at timestamp with time zone, record jsonb)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT previous.operation_id,COALESCE(a.id,previous.case_id),previous.started_at,
  previous.record || CASE WHEN a.id IS NULL THEN '{}'::jsonb ELSE jsonb_build_object(
   'case_id',a.id,'assessment_id',a.id,'customer_id',a.customer_id,'customer_name',c.name,
   'business_status',a.status,'business_completed_at',a.completed_at,
   'risk_assessment',jsonb_build_object('id',a.id,'status',a.status,'outcome',a.outcome,
    'risk_count',a.risk_count,'initiated_by',a.initiated_by_user_ref_id,
    'fact_scope_version',a.fact_scope_version,'data_as_of',a.data_as_of,'error_code',a.error_code)) END
 FROM security.agent_operation_rows_v066(p_start,p_end) previous
 LEFT JOIN LATERAL (
  SELECT receipt.* FROM insight.customer_risk_assessment receipt
  WHERE receipt.workspace_id=common.current_workspace_id()
   AND (receipt.inference_operation_id=previous.operation_id
    OR receipt.job_id::text=previous.record->>'job_id')
  ORDER BY receipt.created_at DESC,receipt.id DESC LIMIT 1
 ) a ON true
 LEFT JOIN crm.customer c ON c.id=a.customer_id AND c.workspace_id=a.workspace_id
 WHERE (security.authorization_has('ai.run_read') OR security.authorization_has('ai.run_export'));
$function$
;

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
 ), ids AS (
   SELECT id FROM receipts UNION SELECT operation_id FROM attempts WHERE operation_id IS NOT NULL
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
          'task_id',s.task_id,'decided_by',s.decided_by_user_ref_id,'decided_at',s.decided_at) ORDER BY s.ordinal)
          FROM insight.business_suggestion s WHERE s.advice_id=ba.id),'[]'::jsonb),
       'created_tasks',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',e.task_id,'event_id',e.id,'status',e.to_status))
         FROM workflow.task_event e WHERE e.workspace_id=common.current_workspace_id()
           AND e.event_type='created' AND (e.payload->>'run_id'=r.id::text OR EXISTS(
             SELECT 1 FROM insight.business_suggestion s WHERE s.advice_id=ba.id
               AND s.task_id=e.task_id AND s.decision='adopted'))),'[]'::jsonb),
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
 LEFT JOIN insight.business_advice ba ON ba.workspace_id=common.current_workspace_id()
   AND (ba.inference_trace->>'operation_id'=ids.id::text
        OR (j.aggregate_type='business_advice' AND j.aggregate_id=ba.id))
 LEFT JOIN crm.customer c ON c.workspace_id=common.current_workspace_id()
   AND c.id::text=COALESCE(ba.customer_id::text,r.business_context->>'customer_id',CASE WHEN j.aggregate_type='customer' THEN j.aggregate_id::text END)
 LEFT JOIN platform.user_ref u ON u.id=COALESCE(a.actor_user_ref_id,first_attempt.actor_user_ref_id)
                             AND u.workspace_id=common.current_workspace_id();
$function$
;

CREATE OR REPLACE FUNCTION security.ai_invocation_report(p_start timestamp with time zone, p_end timestamp with time zone, p_role text, p_user uuid)
 RETURNS TABLE(id uuid, actor_user_ref_id uuid, actor_name text, actor_role_code text, operation_code text, operation_id uuid, model_id text, endpoint_code text, status text, attempt_no integer, input_tokens integer, output_tokens integer, audio_seconds numeric, latency_ms integer, http_status integer, error_code text, started_at timestamp with time zone, completed_at timestamp with time zone, record_kind text, request_summary jsonb, request_id uuid, provider_code text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT i.id,i.actor_user_ref_id,u.display_name,i.actor_role_code,i.operation_code,i.operation_id,i.model_id,i.endpoint_code,
 i.status,i.attempt_no,i.input_tokens,i.output_tokens,i.audio_seconds,i.latency_ms,i.http_status,i.error_code,i.started_at,i.completed_at,
 i.record_kind,i.request_snapshot,i.request_id,i.provider_code FROM agent.model_invocation i LEFT JOIN platform.user_ref u ON u.id=i.actor_user_ref_id
 WHERE (security.authorization_has('ai.usage_read') OR security.authorization_has('ai.usage_export')) AND i.workspace_id=common.current_workspace_id() AND i.started_at>=p_start AND i.started_at<p_end
 AND (p_role IS NULL OR i.actor_role_code=p_role) AND (p_user IS NULL OR i.actor_user_ref_id=p_user);
$function$
;

CREATE OR REPLACE FUNCTION security.business_activity_rows(p_start timestamp with time zone, p_end timestamp with time zone)
 RETURNS TABLE(event_id text, occurred_at timestamp with time zone, actor_id uuid, actor_name text, actor_role text, actor_department text, action_code text, object_type text, object_id uuid, object_name text, customer_id uuid, customer_name text, execution_kind text, evidence_kind text, payload jsonb)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
WITH raw AS (
 SELECT 'customer:'||c.id AS eid,c.workspace_id AS ws,c.created_at AS at,c.created_by_user_ref_id AS actor,
 'customer.create'::text AS action,'customer'::text AS kind,c.id AS oid,c.name AS label,c.id AS cid,
 'saved_record'::text AS evidence,'human'::text AS executor,
 jsonb_build_object('data_source',c.data_source,'source_workbook',c.import_meta->>'sourceWorkbook','company_reference',c.company_reference,
 'source_note',CASE WHEN c.data_source='excel_import' THEN '历史导入：创建人是系统登记人，不代表原始文件上传人' ELSE '根据已保存的创建人和创建时间展示；对象名称为当前名称' END) AS body
 FROM crm.customer c
 UNION ALL
 SELECT 'opportunity:'||o.id,o.workspace_id,o.created_at,o.created_by_user_ref_id,'opportunity.create','opportunity',o.id,o.name,o.customer_id,'saved_record','human',
 jsonb_build_object('source_note','根据已保存的创建人和创建时间展示；对象名称为当前名称') FROM crm.opportunity o
 UNION ALL
 SELECT 'change:'||b.id,b.workspace_id,b.created_at,b.actor_user_ref_id,
 CASE WHEN b.opportunity_id IS NULL THEN 'customer.update' ELSE 'opportunity.update' END,
 CASE WHEN b.opportunity_id IS NULL THEN 'customer' ELSE 'opportunity' END,COALESCE(b.opportunity_id,b.customer_id),COALESCE(o.name,c.name),b.customer_id,'business_event','human',
 jsonb_build_object('changes',b.changes) FROM crm.business_change b LEFT JOIN crm.opportunity o ON o.id=b.opportunity_id LEFT JOIN crm.customer c ON c.id=b.customer_id
 WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(b.changes) d WHERE d->>'before'='新建')
 UNION ALL
 SELECT 'visit:'||v.id,v.workspace_id,v.archived_at,v.confirmed_by_user_ref_id,'visit.archive','visit',v.id,
 COALESCE(v.archived_fields->>'customer_name',c.name)||' · 拜访记录',v.customer_id,'saved_record','human',
 jsonb_build_object('visit_date',v.interaction_at::date,'recorded_on',v.recorded_on,'communication',v.follow_up_record,'next_action',v.next_action,
 'score',v.follow_up_score,'opportunity_name',o.name,'confirmer',u.display_name,
 'materials',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',i.id,'filename',i.filename,'uploaded_at',i.created_at,'uploader',up.display_name,'status',i.status))
 FROM activity.visit_import i LEFT JOIN platform.user_ref up ON up.id=i.created_by_user_ref_id WHERE i.id=v.source_import_id),'[]'::jsonb))
 FROM activity.visit v LEFT JOIN crm.customer c ON c.id=v.customer_id LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id LEFT JOIN platform.user_ref u ON u.id=v.confirmed_by_user_ref_id
 WHERE v.archived_at IS NOT NULL
 UNION ALL
 SELECT 'upload:'||i.id,i.workspace_id,i.created_at,i.created_by_user_ref_id,'material.upload','visit_import',i.id,i.filename,NULL,'saved_record','human',
 jsonb_build_object('filename',i.filename,'file_size',i.file_size,'processing_status',i.status,'error_message',i.error_message,
 'visits',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',v.id,'customer_id',v.customer_id,'customer_name',c.name,'archived_at',v.archived_at,'confirmer',u.display_name))
 FROM activity.visit v LEFT JOIN crm.customer c ON c.id=v.customer_id LEFT JOIN platform.user_ref u ON u.id=v.confirmed_by_user_ref_id WHERE v.source_import_id=i.id),'[]'::jsonb)) FROM activity.visit_import i
 UNION ALL
 SELECT 'task:'||e.id,e.workspace_id,e.occurred_at,e.actor_user_ref_id,'task.'||e.event_type,'task',e.task_id,t.title,t.customer_id,'business_event','human',
 jsonb_build_object('note',e.note,'from_status',e.from_status,'to_status',e.to_status,'recipient',
 (SELECT string_agg(u.display_name,'、' ORDER BY u.display_name) FROM workflow.task_assignee a JOIN platform.user_ref u ON u.id=a.assignee_user_ref_id WHERE a.task_id=t.id AND a.responsibility='owner'))
 FROM workflow.task_event e JOIN workflow.task t ON t.id=e.task_id
 UNION ALL
 SELECT 'claim-request:'||r.id,r.workspace_id,r.requested_at,r.applicant_user_ref_id,'claim.request','customer',r.customer_id,c.name,r.customer_id,'business_event','human',
 jsonb_build_object('current_status',r.status) FROM crm.customer_claim_request r JOIN crm.customer c ON c.id=r.customer_id
 UNION ALL
 SELECT 'claim-review:'||r.id,r.workspace_id,r.reviewed_at,r.reviewer_user_ref_id,'claim.'||r.status,'customer',r.customer_id,c.name,r.customer_id,'business_event','human',
 jsonb_build_object('reason',r.decision_reason,'applicant',u.display_name) FROM crm.customer_claim_request r JOIN crm.customer c ON c.id=r.customer_id LEFT JOIN platform.user_ref u ON u.id=r.applicant_user_ref_id
 WHERE r.status IN ('approved','rejected') AND r.reviewed_at IS NOT NULL
 UNION ALL
 SELECT 'ownership:'||e.id,e.workspace_id,e.occurred_at,e.actor_user_ref_id,'claim.'||e.event_type,'customer',e.customer_id,c.name,e.customer_id,'business_event','human',
 jsonb_build_object('reason',e.reason,'previous_owner',old.display_name,'owner',n.display_name)
 FROM crm.customer_ownership_event e JOIN crm.customer c ON c.id=e.customer_id LEFT JOIN platform.user_ref old ON old.id=e.previous_owner_user_ref_id LEFT JOIN platform.user_ref n ON n.id=e.owner_user_ref_id
 WHERE e.event_type<>'approved'
 UNION ALL
 SELECT 'audio:'||a.id,a.workspace_id,a.created_at,a.created_by_user_ref_id,'material.transcribe','audio_transcript',a.id,'语音转写',NULL,'saved_record','system',
 jsonb_build_object('purpose',a.payload->>'purpose','duration_seconds',a.payload->'duration_seconds','source_note','已保存转写结果，不代表已经人工确认归档')
 FROM agent.artifact a WHERE a.artifact_type='audio_transcript'
 UNION ALL
 SELECT 'advice:'||s.id,s.workspace_id,s.decided_at,s.decided_by_user_ref_id,'advice.'||s.decision,
 'business_suggestion',s.id,s.title,a.customer_id,'saved_record','human',
 jsonb_build_object('advice_id',a.id,'subject_kind',a.subject_kind,'suggestion',s.title,'evidence',s.evidence,
 'action',s.action,'decision_note',s.decision_note,'task_id',s.task_id,'task_description',t.description,
 'actor_snapshot',snap.actor_name_snapshot,'role_snapshot',snap.actor_role_code,'team_snapshot',snap.actor_team_snapshot)
 FROM insight.business_suggestion s JOIN insight.business_advice a ON a.id=s.advice_id
 LEFT JOIN workflow.task t ON t.id=s.task_id
 LEFT JOIN LATERAL (SELECT l.actor_name_snapshot,l.actor_role_code,l.actor_team_snapshot FROM ops.audit_log l
 WHERE l.workspace_id=s.workspace_id AND l.object_type='business_suggestion' AND l.object_id=s.id
 AND l.changed_fields @> ARRAY['decision'] AND l.after_snapshot->>'decision'=s.decision
 ORDER BY l.id LIMIT 1) snap ON true
 WHERE s.decision IN ('adopted','no_task') AND s.decided_at IS NOT NULL
), audit_candidates AS (
 SELECT a.*,
 CASE WHEN a.object_type IN ('user_ref','role_binding','team_membership','password_credential') THEN 'account'
 WHEN a.object_type IN ('customer','contact') THEN 'customer' ELSE a.object_type END AS family,
 CASE WHEN a.object_type IN ('role_binding','team_membership','password_credential') THEN COALESCE(a.after_snapshot,a.before_snapshot)->>'user_ref_id'
 WHEN a.object_type='contact' THEN COALESCE(a.after_snapshot,a.before_snapshot)->>'customer_id' ELSE a.object_id::text END AS subject
 FROM ops.audit_log a WHERE a.occurred_at>=p_start AND a.occurred_at<p_end AND a.workspace_id=common.current_workspace_id() AND (
 (a.object_type IN ('customer','contact','visit') AND a.action_code LIKE '%.update') OR
 a.object_type IN ('user_ref','role_binding','team_membership','password_credential','team','customer_actual','sales_target','opportunity_quote_reference','ai_usage_rule','partner','opportunity_participant','visit_participant','company_rule') OR
 (a.object_type='visit_import' AND a.action_code LIKE '%.update' AND a.changed_fields @> ARRAY['status'] AND a.after_snapshot->>'status' IN ('succeeded','failed')) OR
 a.action_code='http.export')
 AND (a.object_type<>'customer' OR cardinality(array_remove(a.changed_fields,'owner_user_ref_id'))>0)
 AND NOT (a.object_type IN ('customer','contact') AND a.request_id IS NOT NULL AND EXISTS(
 SELECT 1 FROM ops.audit_log b WHERE b.workspace_id=a.workspace_id AND b.request_id=a.request_id AND b.object_type='business_change'
 AND b.after_snapshot->>'customer_id'=CASE WHEN a.object_type='customer' THEN a.object_id::text ELSE COALESCE(a.after_snapshot,a.before_snapshot)->>'customer_id' END))
), grouped AS (
 SELECT min(id) AS first_id,max(occurred_at) AS at,workspace_id AS ws,actor_user_ref_id AS actor,family,subject,
 max(actor_name_snapshot) AS actor_label,max(actor_role_code) AS role_label,max(actor_team_snapshot) AS team_label,
 max(execution_kind) AS executor,
 jsonb_agg(jsonb_build_object('type',object_type,'operation',action_code,'label',object_label,'before',before_snapshot,'after',after_snapshot,'fields',changed_fields,'result',result_code) ORDER BY id) AS entries
 FROM audit_candidates GROUP BY workspace_id,actor_user_ref_id,family,subject,COALESCE('tx:'||transaction_id::text||':'||COALESCE(request_id::text,''),'request:'||request_id::text,'row:'||id::text)
), audit_raw AS (
 SELECT 'audit:'||g.first_id AS eid,g.ws,g.at,g.actor,
 CASE WHEN g.family='account' THEN 'account.change' WHEN g.family='customer' THEN 'customer.update'
 WHEN g.family='visit' THEN 'visit.supplement' WHEN g.family='visit_import' THEN 'material.process'
 WHEN g.family='api_request' THEN 'data.export' WHEN g.family='team' THEN 'department.change'
 WHEN g.family='customer_actual' THEN 'actual.change' WHEN g.family='sales_target' THEN 'target.change'
 WHEN g.family='opportunity_participant' THEN 'opportunity.fde_members' WHEN g.family='visit_participant' THEN 'visit.fde_participants'
 WHEN g.family='company_rule' THEN 'company_rule.change' WHEN g.family='opportunity_quote_reference' THEN 'quote.change' WHEN g.family='partner' THEN 'partner.change' ELSE 'ai_rule.change' END AS action,
 g.family AS kind,g.subject::uuid AS oid,
 COALESCE(u.display_name,c.name,project.name,visit_customer.name,rule.name,CASE WHEN g.family IN ('visit','visit_import') THEN NULL ELSE g.entries->0->>'label' END,'业务记录') AS label,
 CASE WHEN g.family='opportunity_participant' THEN project.customer_id::text
 WHEN g.family='visit_participant' THEN visit.customer_id::text WHEN g.family='customer' THEN g.subject ELSE COALESCE(g.entries->0->'after',g.entries->0->'before')->>'customer_id' END::uuid AS cid,
 'row_audit'::text AS evidence,CASE WHEN g.family='visit_import' THEN 'system' ELSE COALESCE(g.executor,'unknown') END AS executor,
 jsonb_build_object('audit_id',g.first_id,'entries',g.entries,'actor_snapshot',g.actor_label,'role_snapshot',g.role_label,'team_snapshot',g.team_label,
 'changes',CASE WHEN g.family IN ('opportunity_participant','visit_participant') THEN
 (SELECT jsonb_agg(jsonb_build_object('label',COALESCE(entry->>'label','参与同事'),
 'before',CASE WHEN entry->'before' IS NULL OR entry->'before'='null'::jsonb THEN '未参与' ELSE '参与中' END,
 'after',CASE WHEN entry->'after' IS NULL OR entry->'after'='null'::jsonb THEN '已移除'
 WHEN g.family='opportunity_participant' AND entry->'after'->>'valid_to'<>'infinity' THEN '已结束协助' ELSE '参与中' END))
 FROM jsonb_array_elements(g.entries) entry) ELSE '[]'::jsonb END,
 'rule_name',rule.name,'rule_version',rule.version_no,'change_reason',rule.change_reason) AS body
 FROM grouped g LEFT JOIN platform.user_ref u ON g.family='account' AND u.id::text=g.subject LEFT JOIN crm.customer c ON g.family='customer' AND c.id::text=g.subject
 LEFT JOIN crm.opportunity project ON g.family='opportunity_participant' AND project.id::text=g.subject
 LEFT JOIN activity.visit visit ON g.family='visit_participant' AND visit.id::text=g.subject
 LEFT JOIN crm.customer visit_customer ON visit_customer.id=visit.customer_id
 LEFT JOIN config.rule_set rule ON g.family='company_rule' AND rule.id::text=g.subject
), combined AS (SELECT * FROM raw UNION ALL SELECT * FROM audit_raw)
SELECT r.eid,r.at,r.actor,COALESCE(r.body->>'actor_snapshot',snapshot.actor_name_snapshot,u.display_name,'未记录'),COALESCE(r.body->>'role_snapshot',snapshot.actor_role_code),COALESCE(r.body->>'team_snapshot',snapshot.actor_team_snapshot),
 r.action,r.kind,r.oid,r.label,r.cid,c.name,r.executor,r.evidence,security.audit_redact(r.body)
FROM combined r LEFT JOIN platform.user_ref u ON u.id=r.actor LEFT JOIN crm.customer c ON c.id=r.cid
LEFT JOIN LATERAL (SELECT a.actor_name_snapshot,a.actor_role_code,a.actor_team_snapshot FROM ops.audit_log a
 WHERE r.evidence<>'row_audit' AND a.workspace_id=r.ws AND a.actor_user_ref_id=r.actor AND a.object_id::text=split_part(r.eid,':',2)
 AND a.object_type=CASE split_part(r.eid,':',1) WHEN 'customer' THEN 'customer' WHEN 'opportunity' THEN 'opportunity'
 WHEN 'change' THEN 'business_change' WHEN 'visit' THEN 'visit' WHEN 'upload' THEN 'visit_import' WHEN 'task' THEN 'task_event'
 WHEN 'claim-request' THEN 'customer_claim_request' WHEN 'claim-review' THEN 'customer_claim_request' WHEN 'ownership' THEN 'customer_ownership_event' ELSE '' END
 AND CASE WHEN r.action='visit.archive' THEN a.after_snapshot->>'status'='archived' AND a.before_snapshot->>'status' IS DISTINCT FROM 'archived'
 WHEN split_part(r.eid,':',1)='claim-review' THEN a.after_snapshot->>'status'=split_part(r.action,'.',2) AND a.action_code LIKE '%.update'
 ELSE a.action_code LIKE '%.insert' END
 ORDER BY a.id LIMIT 1) snapshot ON true
WHERE (security.authorization_has('audit.business_read') OR security.authorization_has('audit.business_export')) AND r.ws=common.current_workspace_id() AND r.at>=p_start AND r.at<p_end;
$function$
;

CREATE OR REPLACE FUNCTION security.feishu_initialize(p_connection uuid)
 RETURNS bigint
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE ws uuid; item text[]; n bigint; total bigint:=0;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND workspace_id=common.current_workspace_id();
 IF ws IS NULL OR NOT security.authorization_has('feishu.control') THEN RAISE insufficient_privilege; END IF;
 UPDATE ops.feishu_event SET force_remote_check=true WHERE connection_id=p_connection
 AND workspace_id=ws AND status IN ('pending','failed');
 -- Running work may already have read its flags; enqueue a forced successor instead.
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact'],['crm.opportunity_forecast','forecast'],['crm.opportunity_period_actual_snapshot','period_actual_snapshot']] LOOP
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin,force_remote_check) SELECT $1,$2,$3,id,true,''reconcile'',true FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,action,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'initialize',jsonb_build_object('queued',total));
 RETURN total;
END $function$
;

CREATE OR REPLACE FUNCTION security.feishu_recover(p_connection uuid, p_event uuid, p_action text, p_key text, p_note text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE ws uuid; live boolean; event_row ops.feishu_event%ROWTYPE; previous text;
BEGIN
 SELECT workspace_id,enabled INTO ws,live FROM config.feishu_connection
 WHERE id=p_connection AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF ws IS NULL OR NOT security.authorization_has('feishu.recover') THEN
  RAISE insufficient_privilege;
 END IF;
 IF live OR EXISTS(SELECT 1 FROM ops.feishu_event WHERE connection_id=p_connection AND status='running') THEN
  RAISE EXCEPTION 'Pause and wait for running operations before recovery' USING ERRCODE='22023';
 END IF;
 IF p_note IS NULL OR length(trim(p_note)) NOT BETWEEN 1 AND 1000 THEN
  RAISE EXCEPTION 'Recovery note is required' USING ERRCODE='22023';
 END IF;
 SELECT * INTO event_row FROM ops.feishu_event WHERE id=p_event AND connection_id=p_connection FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 IF p_action='retry' THEN
  IF event_row.status NOT IN ('failed','dead_letter') OR event_row.error_code='TARGET_MIGRATED'
    OR EXISTS(SELECT 1 FROM ops.feishu_delivery WHERE event_id=p_event AND status='unknown') THEN
   RAISE EXCEPTION 'Event is not eligible for retry' USING ERRCODE='22023';
  END IF;
  UPDATE ops.feishu_event SET status='pending',attempts=0,error_code=NULL,completed_at=NULL,
   lease_token=NULL,locked_until=NULL,available_at=clock_timestamp() WHERE id=p_event;
 ELSIF p_action IN ('confirm_sent','suppress') THEN
  SELECT status INTO previous FROM ops.feishu_delivery
   WHERE event_id=p_event AND connection_id=p_connection AND dedupe_key=p_key FOR UPDATE;
  IF previous IS DISTINCT FROM 'unknown' THEN
   RAISE EXCEPTION 'Notification is not awaiting verification' USING ERRCODE='22023';
  END IF;
  UPDATE ops.feishu_delivery SET status=CASE WHEN p_action='confirm_sent' THEN 'sent' ELSE 'failed' END,
   error_code=CASE WHEN p_action='suppress' THEN 'OPERATOR_SUPPRESSED' ELSE NULL END,
   sent_at=CASE WHEN p_action='confirm_sent' THEN clock_timestamp() ELSE sent_at END WHERE dedupe_key=p_key;
 ELSE RAISE EXCEPTION 'Unknown recovery action' USING ERRCODE='22023'; END IF;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,
  action,before_snapshot,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'recovery',
  jsonb_build_object('event_status',event_row.status,'notification_status',previous),
  jsonb_build_object('event_id',p_event,'action',p_action,'delivery_key',p_key,'note',trim(p_note)));
END $function$
;

CREATE OR REPLACE FUNCTION security.has_feishu_credential(p_connection uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT EXISTS(SELECT 1 FROM security.feishu_credential c WHERE c.connection_id=p_connection AND c.workspace_id=common.current_workspace_id()
 AND security.authorization_has('feishu.read'));
$function$
;

CREATE OR REPLACE FUNCTION security.set_feishu_credential(p_connection uuid, p_cipher bytea, p_key text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF NOT security.authorization_has('feishu.configure')
 OR NOT EXISTS(SELECT 1 FROM config.feishu_connection WHERE id=p_connection AND workspace_id=common.current_workspace_id())
 THEN RAISE insufficient_privilege; END IF;
 INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id)
 VALUES(p_connection,common.current_workspace_id(),p_cipher,p_key)
 ON CONFLICT(connection_id) DO UPDATE SET ciphertext=excluded.ciphertext,encryption_key_id=excluded.encryption_key_id,updated_at=clock_timestamp();
 UPDATE config.feishu_connection SET enabled=false,validated_revision=NULL WHERE id=p_connection;
END $function$
;

CREATE OR REPLACE FUNCTION security.prepare_feishu_migration(p_connection uuid)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE ws uuid; live boolean;
BEGIN
 SELECT workspace_id,enabled INTO ws,live FROM config.feishu_connection
 WHERE id=p_connection AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF ws IS NULL OR NOT security.authorization_has('feishu.configure') THEN
  RAISE insufficient_privilege;
 END IF;
 IF live OR EXISTS(SELECT 1 FROM ops.feishu_event WHERE connection_id=p_connection AND status='running') THEN
  RAISE EXCEPTION 'Pause synchronization and wait for running operations before migrating' USING ERRCODE='22023';
 END IF;
 IF EXISTS(SELECT 1 FROM ops.feishu_delivery WHERE connection_id=p_connection AND status='unknown') THEN
  RAISE EXCEPTION 'Resolve unknown notification results before migrating' USING ERRCODE='22023';
 END IF;
 UPDATE ops.feishu_event SET status='dead_letter',error_code='TARGET_MIGRATED',
 completed_at=clock_timestamp(),notification_planned=true
 WHERE connection_id=p_connection AND status IN ('pending','failed','dead_letter');
 UPDATE ops.feishu_delivery SET status='failed',error_code='TARGET_MIGRATED'
 WHERE connection_id=p_connection AND status='pending';
 DELETE FROM ops.feishu_record_map WHERE connection_id=p_connection;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,action,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'target_migration',
 jsonb_build_object('old_remote_records_preserved',true,'backfill_notification',false));
END $function$
;

CREATE OR REPLACE FUNCTION security.publish_company_rule(p_id uuid, p_revision integer)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v_rule config.rule_set; v_current uuid;
BEGIN
 IF NOT security.authorization_has('rule.publish') THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO v_rule FROM config.rule_set WHERE id=p_id AND workspace_id=common.current_workspace_id();
 IF NOT FOUND THEN RAISE no_data_found; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(common.current_workspace_id()::text || ':rule:' || v_rule.rule_code,0));
 SELECT * INTO v_rule FROM config.rule_set WHERE id=p_id AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT security.company_rule_supported(v_rule.rule_code) THEN RAISE insufficient_privilege; END IF;
 IF v_rule.status<>'draft' OR v_rule.revision_no<>p_revision THEN RAISE EXCEPTION '草稿已变化或已发布，请刷新'; END IF;
 v_current:=security.active_company_rule(v_rule.rule_code);
 IF v_rule.base_rule_id IS DISTINCT FROM v_current THEN RAISE EXCEPTION '生效规则已变化，请重新核对草稿'; END IF;
 UPDATE config.rule_set SET status='retired',effective_to=clock_timestamp()
 WHERE id=v_current AND workspace_id=common.current_workspace_id();
 UPDATE config.rule_set SET status='active',effective_from=clock_timestamp(),effective_to='infinity',
   published_at=clock_timestamp(),published_by_user_ref_id=common.current_user_ref_id(),revision_no=revision_no+1 WHERE id=p_id;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,object_type,object_id,
     before_snapshot,after_snapshot,result_code,sensitivity)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),common.current_role_code(),
     'company_rule.publish','company_rule',p_id,jsonb_build_object('active_rule_id',v_current),
     jsonb_build_object('rule_code',v_rule.rule_code,'version',v_rule.version_no,'reason',v_rule.change_reason,
                       'restored_from_id',v_rule.restored_from_id),'success','internal');
 RETURN p_id;
END; $function$
;

CREATE OR REPLACE FUNCTION security.save_company_rule(p_code text, p_name text, p_definition jsonb, p_reason text, p_id uuid, p_revision integer, p_base uuid, p_restored uuid DEFAULT NULL::uuid)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v_id uuid; v_current uuid; v_old config.rule_set;
BEGIN
 IF NOT security.authorization_has(CASE WHEN p_restored IS NULL THEN 'rule.draft' ELSE 'rule.restore' END) THEN RAISE insufficient_privilege; END IF;
 IF p_code LIKE 'agent_execution.%' AND NOT security.authorization_has('ai.config_publish') THEN RAISE insufficient_privilege; END IF;
 IF NOT security.company_rule_supported(p_code) OR jsonb_typeof(p_definition)<>'object'
 OR octet_length(p_definition::text)>16000 OR length(trim(p_reason)) NOT BETWEEN 1 AND 500 THEN
 RAISE EXCEPTION '规则或变更原因不合法'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(common.current_workspace_id()::text || ':rule:' || p_code,0));
 v_current:=security.active_company_rule(p_code);
 IF p_base IS DISTINCT FROM v_current THEN RAISE EXCEPTION '生效规则已变化，请刷新并重新核对'; END IF;
 IF p_restored IS NOT NULL AND NOT EXISTS(SELECT 1 FROM config.rule_set WHERE id=p_restored
   AND rule_code=p_code AND status IN ('active','retired')
   AND (workspace_id=common.current_workspace_id() OR workspace_id IS NULL)) THEN RAISE no_data_found; END IF;
 IF p_id IS NULL THEN
   INSERT INTO config.rule_set(workspace_id,rule_code,name,rule_type,version_no,status,definition,
      created_by_user_ref_id,updated_by_user_ref_id,change_reason,base_rule_id,restored_from_id)
   SELECT common.current_workspace_id(),p_code,p_name,'company_policy',COALESCE(max(version_no),0)+1,'draft',
      p_definition,common.current_user_ref_id(),common.current_user_ref_id(),p_reason,p_base,p_restored
   FROM config.rule_set WHERE workspace_id=common.current_workspace_id() AND rule_code=p_code RETURNING id INTO v_id;
 ELSE
   SELECT * INTO v_old FROM config.rule_set WHERE id=p_id AND workspace_id=common.current_workspace_id() FOR UPDATE;
   IF NOT FOUND THEN RAISE no_data_found; END IF;
   IF v_old.status<>'draft' OR v_old.rule_code<>p_code OR v_old.revision_no<>p_revision THEN
     RAISE EXCEPTION '草稿已变化或已发布，请刷新'; END IF;
   UPDATE config.rule_set SET definition=p_definition,change_reason=p_reason,revision_no=revision_no+1,
       updated_by_user_ref_id=common.current_user_ref_id(),base_rule_id=p_base WHERE id=p_id;
   v_id:=p_id;
 END IF;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,object_type,object_id,
     after_snapshot,result_code,sensitivity)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),common.current_role_code(),
     'company_rule.draft','company_rule',v_id,jsonb_build_object('rule_code',p_code,'reason',p_reason),'success','internal');
 RETURN v_id;
END; $function$
;

CREATE OR REPLACE FUNCTION security.model_usage_summary(p_days integer)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE result jsonb;
BEGIN
  IF p_days IS NULL OR p_days < 1 OR p_days > 90 THEN
    RAISE EXCEPTION 'INVALID_PERIOD' USING ERRCODE = '22023';
  END IF;
  IF NOT security.authorization_has('ai.usage_read')
     OR NOT EXISTS (
       SELECT 1 FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
       WHERE u.id=common.current_user_ref_id() AND u.workspace_id=common.current_workspace_id()
         AND u.status='active' AND u.deleted_at IS NULL AND w.status='active' AND w.deleted_at IS NULL
     ) THEN
    RAISE EXCEPTION 'MANAGER_ONLY' USING ERRCODE = '42501';
  END IF;
  SELECT COALESCE(jsonb_agg(to_jsonb(usage_row)), '[]'::jsonb) INTO result FROM (
            SELECT i.provider_code, i.model_id, i.endpoint_code,
                   COALESCE(r.intent_code, 'unknown') AS agent_code,
                   count(*) AS calls,
                   count(*) FILTER (WHERE i.status = 'succeeded') AS succeeded,
                   count(*) FILTER (WHERE i.status = 'failed') AS failed,
                   count(*) FILTER (WHERE i.status = 'running') AS running,
                   count(*) FILTER (WHERE i.status = 'cancelled') AS cancelled,
                   count(*) FILTER (WHERE i.attempt_no > 1) AS recorded_retries,
                   avg(i.latency_ms)::float8 AS average_latency_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY i.latency_ms) AS p95_latency_ms,
                   sum(i.input_tokens) AS input_tokens,
                   sum(i.output_tokens) AS output_tokens,
                   count(i.input_tokens) AS input_usage_records,
                   count(i.output_tokens) AS output_usage_records
              FROM agent.model_invocation i
              JOIN agent.run r ON r.id = i.run_id AND r.workspace_id = i.workspace_id
             WHERE i.workspace_id = common.current_workspace_id()
               AND i.started_at >= clock_timestamp() - make_interval(days => p_days)
             GROUP BY i.provider_code, i.model_id, i.endpoint_code, r.intent_code
             ORDER BY count(*) DESC, i.model_id, r.intent_code

  ) usage_row;
  RETURN result;
END;
$function$
;

DROP POLICY administrator_runtime_config ON config.agent_runtime_config;
DROP POLICY agent_runtime_config_workspace ON config.agent_runtime_config;
DROP POLICY fde_admin_delete ON config.agent_runtime_config;
DROP POLICY fde_admin_insert ON config.agent_runtime_config;
DROP POLICY fde_admin_update ON config.agent_runtime_config;
DROP POLICY runtime_config_admin_delete ON config.agent_runtime_config;
DROP POLICY runtime_config_admin_insert ON config.agent_runtime_config;
DROP POLICY runtime_config_admin_update ON config.agent_runtime_config;
DROP POLICY administrator_runtime_release ON config.agent_runtime_release;
DROP POLICY agent_runtime_release_workspace ON config.agent_runtime_release;
DROP POLICY fde_admin_delete ON config.agent_runtime_release;
DROP POLICY fde_admin_insert ON config.agent_runtime_release;
DROP POLICY fde_admin_update ON config.agent_runtime_release;
DROP POLICY runtime_release_admin_insert ON config.agent_runtime_release;
DROP POLICY company_management ON config.feishu_connection;
DROP POLICY model_api_current_insert ON config.model_api_current;
DROP POLICY model_api_current_read ON config.model_api_current;
DROP POLICY model_api_current_update ON config.model_api_current;
DROP POLICY model_api_release_read ON config.model_api_release;
DROP POLICY model_api_release_write ON config.model_api_release;
DROP POLICY model_api_test_admin ON config.model_api_test;
DROP POLICY usage_alert_management ON ops.ai_usage_alert;
DROP POLICY usage_rule_management ON ops.ai_usage_rule;
DROP POLICY audit_management_read ON ops.audit_log;
DROP POLICY system_event_read ON ops.system_event;
CREATE POLICY permission_select ON config.agent_runtime_config FOR SELECT USING(workspace_id=common.current_workspace_id() AND true);
CREATE POLICY permission_insert ON config.agent_runtime_config FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_publish') OR security.authorization_has('ai.config_rollback')));
CREATE POLICY permission_update ON config.agent_runtime_config FOR UPDATE USING(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_publish') OR security.authorization_has('ai.config_rollback'))) WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_publish') OR security.authorization_has('ai.config_rollback')));
CREATE POLICY permission_select ON config.agent_runtime_release FOR SELECT USING(workspace_id=common.current_workspace_id() AND true);
CREATE POLICY permission_insert ON config.agent_runtime_release FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_publish') OR security.authorization_has('ai.config_rollback')));
CREATE POLICY permission_select ON config.model_api_current FOR SELECT USING(workspace_id=common.current_workspace_id() AND true);
CREATE POLICY permission_insert ON config.model_api_current FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_publish')));
CREATE POLICY permission_update ON config.model_api_current FOR UPDATE USING(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_publish'))) WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_publish')));
CREATE POLICY permission_select ON config.model_api_release FOR SELECT USING(workspace_id=common.current_workspace_id() AND true);
CREATE POLICY permission_insert ON config.model_api_release FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_publish')));
CREATE POLICY permission_select ON config.model_api_test FOR SELECT USING(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_test') OR security.authorization_has('ai.config_publish')));
CREATE POLICY permission_insert ON config.model_api_test FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_test')));
CREATE POLICY permission_update ON config.model_api_test FOR UPDATE USING(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_test'))) WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.config_test')));
CREATE POLICY permission_select ON ops.ai_usage_rule FOR SELECT USING(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.usage_read') OR security.authorization_has('ai.usage_export') OR security.authorization_has('ai.usage_rules_manage')));
CREATE POLICY permission_insert ON ops.ai_usage_rule FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.usage_rules_manage')));
CREATE POLICY permission_update ON ops.ai_usage_rule FOR UPDATE USING(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.usage_rules_manage'))) WITH CHECK(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.usage_rules_manage')));
CREATE POLICY permission_select ON ops.ai_usage_alert FOR SELECT USING(workspace_id=common.current_workspace_id() AND (security.authorization_has('ai.usage_read') OR security.authorization_has('ai.usage_export')));
CREATE POLICY permission_select ON ops.audit_log AS RESTRICTIVE FOR SELECT USING(workspace_id=common.current_workspace_id() AND (security.authorization_has('audit.read') OR security.authorization_has('audit.export')));
CREATE POLICY permission_select ON ops.system_event FOR SELECT USING(workspace_id=common.current_workspace_id() AND (security.authorization_has('audit.events_read') OR security.authorization_has('audit.events_export')));
CREATE POLICY permission_select ON config.feishu_connection FOR SELECT USING(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (workspace_id=common.current_workspace_id() AND (security.authorization_has('feishu.read') OR security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control') OR security.authorization_has('feishu.recover'))) END);
CREATE POLICY permission_insert ON config.feishu_connection FOR INSERT WITH CHECK(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (workspace_id=common.current_workspace_id() AND (security.authorization_has('feishu.configure'))) END);
CREATE POLICY permission_update ON config.feishu_connection FOR UPDATE USING(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (workspace_id=common.current_workspace_id() AND (security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control'))) END) WITH CHECK(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (workspace_id=common.current_workspace_id() AND (security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control'))) END);
ALTER POLICY company_management ON ops.feishu_config_audit USING(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (((workspace_id = common.current_workspace_id()) AND (security.authorization_has('feishu.read') OR security.authorization_has('feishu.recover') OR security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control')))) END);
ALTER POLICY company_management ON ops.feishu_delivery USING(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (((workspace_id = common.current_workspace_id()) AND (security.authorization_has('feishu.read') OR security.authorization_has('feishu.recover') OR security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control')))) END) WITH CHECK(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (((workspace_id = common.current_workspace_id()) AND (security.authorization_has('feishu.read') OR security.authorization_has('feishu.recover') OR security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control')))) END);
ALTER POLICY company_management ON ops.feishu_event USING(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (((workspace_id = common.current_workspace_id()) AND (security.authorization_has('feishu.read') OR security.authorization_has('feishu.recover') OR security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control')))) END) WITH CHECK(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (((workspace_id = common.current_workspace_id()) AND (security.authorization_has('feishu.read') OR security.authorization_has('feishu.recover') OR security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control')))) END);
ALTER POLICY company_management ON ops.feishu_record_map USING(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (((workspace_id = common.current_workspace_id()) AND (security.authorization_has('feishu.read') OR security.authorization_has('feishu.recover') OR security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control')))) END) WITH CHECK(CASE WHEN pg_has_role(current_user,'salegent_feishu_worker','member') THEN false ELSE (((workspace_id = common.current_workspace_id()) AND (security.authorization_has('feishu.read') OR security.authorization_has('feishu.recover') OR security.authorization_has('feishu.configure') OR security.authorization_has('feishu.control')))) END);

-- The dedicated worker keeps its existing strict callable-function allowlist.
-- Public permission policies short-circuit for it; its dedicated policy applies.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v131;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer;
BEGIN
 total:=security.reconcile_runtime_grants_v131();
 REVOKE ALL ON FUNCTION security.authorization_has(text) FROM salegent_feishu_worker;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v131() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();

INSERT INTO ops.schema_migration(version,description) VALUES('V132','Console configuration and audit feature permissions');
COMMIT;