BEGIN;
ALTER TABLE activity.visit ADD CONSTRAINT visit_workspace_key UNIQUE(id,workspace_id);
CREATE TABLE insight.business_advice (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 actor_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id), actor_role_code text NOT NULL,
 subject_kind text NOT NULL CHECK(subject_kind IN ('customer','opportunity','visit')),
 subject_id uuid NOT NULL, customer_id uuid NOT NULL, opportunity_id uuid, visit_id uuid,
 section text NOT NULL CHECK(section IN ('overview','tasks','visits','opportunity')),
 cache_key char(64) NOT NULL, facts_fingerprint char(64) NOT NULL, configuration_fingerprint char(64) NOT NULL,
 identity_snapshot jsonb NOT NULL, facts_snapshot jsonb NOT NULL, configuration_snapshot jsonb NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed','superseded')),
 summary text, inference_trace jsonb, error_code text, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id), UNIQUE(workspace_id,cache_key),
 FOREIGN KEY(customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(visit_id,workspace_id) REFERENCES activity.visit(id,workspace_id),
 CHECK((subject_kind='customer' AND subject_id=customer_id AND opportunity_id IS NULL AND visit_id IS NULL)
    OR (subject_kind='opportunity' AND subject_id=opportunity_id AND visit_id IS NULL)
    OR (subject_kind='visit' AND subject_id=visit_id))
);
COMMENT ON TABLE insight.business_advice IS '按对象、操作者权限、事实和提示词规则版本缓存的经营建议；仅候选，不写经营事实';
CREATE INDEX business_advice_subject ON insight.business_advice(workspace_id,subject_kind,subject_id,created_at DESC);
CREATE TABLE insight.business_suggestion (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, advice_id uuid NOT NULL,
 ordinal smallint NOT NULL CHECK(ordinal BETWEEN 1 AND 3), title text NOT NULL, evidence text NOT NULL,
 action text NOT NULL, evidence_refs jsonb NOT NULL,
 decision text NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','adopted','no_task')),
 decision_note text, decided_by_user_ref_id uuid REFERENCES platform.user_ref(id), decided_at timestamptz,
 task_id uuid, version_no integer NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(advice_id,ordinal), UNIQUE(id,workspace_id),
 FOREIGN KEY(advice_id,workspace_id) REFERENCES insight.business_advice(id,workspace_id),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task(id,workspace_id),
 CHECK((decision='pending' AND decided_at IS NULL AND task_id IS NULL)
   OR (decision='no_task' AND decided_at IS NOT NULL AND decided_by_user_ref_id IS NOT NULL AND task_id IS NULL)
   OR (decision='adopted' AND decided_at IS NOT NULL AND decided_by_user_ref_id IS NOT NULL AND task_id IS NOT NULL))
);
ALTER TABLE workflow.task ADD COLUMN source_suggestion_id uuid;
ALTER TABLE workflow.task ADD CONSTRAINT task_suggestion_source
 FOREIGN KEY(source_suggestion_id,workspace_id) REFERENCES insight.business_suggestion(id,workspace_id);
CREATE UNIQUE INDEX task_one_per_suggestion ON workflow.task(source_suggestion_id) WHERE source_suggestion_id IS NOT NULL;
COMMENT ON COLUMN workflow.task.source_suggestion_id IS '人工采纳的建议来源，与建议决定及任务在同一事务提交';
ALTER TABLE insight.business_advice ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.business_advice FORCE ROW LEVEL SECURITY;
CREATE POLICY advice_actor ON insight.business_advice USING(
 workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
 AND actor_role_code=common.current_role_code()) WITH CHECK(
 workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
 AND actor_role_code=common.current_role_code());
CREATE POLICY advice_subject ON insight.business_advice AS RESTRICTIVE USING(
 CASE subject_kind WHEN 'customer' THEN security.has_customer_access(customer_id)
 WHEN 'opportunity' THEN security.has_opportunity_access(opportunity_id)
 ELSE EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=visit_id AND v.deleted_at IS NULL) END);
ALTER TABLE insight.business_suggestion ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.business_suggestion FORCE ROW LEVEL SECURITY;
CREATE POLICY suggestion_advice ON insight.business_suggestion USING(
 workspace_id=common.current_workspace_id() AND EXISTS(SELECT 1 FROM insight.business_advice a WHERE a.id=advice_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND EXISTS(SELECT 1 FROM insight.business_advice a WHERE a.id=advice_id));
CREATE TRIGGER business_audit AFTER INSERT OR UPDATE ON insight.business_suggestion
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
CREATE TRIGGER suggestion_version BEFORE UPDATE ON insight.business_suggestion
 FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
   WHERE table_schema='workflow' AND table_name='task' AND privilege_type='INSERT' AND grantee<>'PUBLIC'
 LOOP EXECUTE format('GRANT SELECT,INSERT,UPDATE ON insight.business_advice,insight.business_suggestion TO %I',r.grantee);
 END LOOP;
END $$;
CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
SELECT p_code IN ('customer_quadrant','visit_admission','home_display','task_schedule',
'agent_execution.battle_map_review','agent_execution.opportunity_draft','agent_execution.personal_risks',
'agent_execution.visit_entry','agent_execution.today_tasks','agent_execution.operating_report','agent_execution.chatbi',
'score.maturity','score.efficiency','score.competency','agent_execution.customer_advice',
'agent_execution.opportunity_advice','agent_execution.visit_advice');
$$;
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition)
SELECT 'agent_execution.'||code,label,'company_policy',1,'active',
 '{"schema_version":1,"strategy":"inherit","override_budget":true,"platform_seconds":30,"total_seconds":60}'::jsonb
FROM (VALUES('customer_advice','客户经营建议'),('opportunity_advice','商机经营建议'),('visit_advice','单次拜访建议')) v(code,label);
CREATE OR REPLACE FUNCTION ops.claim_job(p_worker_id text,p_lock_seconds integer) RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ops AS $$
DECLARE expired record;
BEGIN
 IF p_lock_seconds < 3 OR p_lock_seconds > 3600 OR NULLIF(btrim(p_worker_id),'') IS NULL THEN
  RAISE EXCEPTION 'invalid worker lease settings';
 END IF;
 -- A process may stop after the result commits but before acknowledging its queue row.
 UPDATE ops.job j SET status='succeeded',completed_at=COALESCE(j.completed_at,clock_timestamp()),
   locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=clock_timestamp()
 WHERE j.status IN ('queued','failed','running')
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
  WHERE status IN ('queued','failed','running') AND attempts>=max_attempts
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
  WHERE candidate.status IN ('queued','failed','running') AND candidate.attempts<candidate.max_attempts
   AND candidate.available_at<=clock_timestamp()
   AND (candidate.locked_until IS NULL OR candidate.locked_until<=clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=candidate.id)
  ORDER BY candidate.priority DESC,candidate.available_at,candidate.created_at
  FOR UPDATE SKIP LOCKED LIMIT 1)
 RETURNING j.*;
END $$;

-- Human decisions appear in the readable business activity feed; polling and candidate generation do not.
CREATE OR REPLACE FUNCTION security.business_activity_rows(p_start timestamptz,p_end timestamptz)
RETURNS TABLE(event_id text,occurred_at timestamptz,actor_id uuid,actor_name text,actor_role text,actor_department text,
 action_code text,object_type text,object_id uuid,object_name text,customer_id uuid,customer_name text,
 execution_kind text,evidence_kind text,payload jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
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
 a.object_type IN ('user_ref','role_binding','team_membership','password_credential','team','customer_actual','sales_target','opportunity_quote_reference','ai_usage_rule','partner') OR
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
 WHEN g.family='opportunity_quote_reference' THEN 'quote.change' WHEN g.family='partner' THEN 'partner.change' ELSE 'ai_rule.change' END AS action,
 g.family AS kind,g.subject::uuid AS oid,
 COALESCE(u.display_name,c.name,CASE WHEN g.family IN ('visit','visit_import') THEN NULL ELSE g.entries->0->>'label' END,'业务记录') AS label,
 CASE WHEN g.family='customer' THEN g.subject ELSE COALESCE(g.entries->0->'after',g.entries->0->'before')->>'customer_id' END::uuid AS cid,
 'row_audit'::text AS evidence,CASE WHEN g.family='visit_import' THEN 'system' ELSE COALESCE(g.executor,'unknown') END AS executor,
 jsonb_build_object('audit_id',g.first_id,'entries',g.entries,'actor_snapshot',g.actor_label,'role_snapshot',g.role_label,'team_snapshot',g.team_label) AS body
 FROM grouped g LEFT JOIN platform.user_ref u ON g.family='account' AND u.id::text=g.subject LEFT JOIN crm.customer c ON g.family='customer' AND c.id::text=g.subject
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
WHERE security.management_actor() AND r.ws=common.current_workspace_id() AND r.at>=p_start AND r.at<p_end;
$$;


INSERT INTO ops.schema_migration(version,description) VALUES('V066','经营建议版本缓存、人工决定与待办来源');
-- Native advice receipts join the same management projection, without exposing generated text.
CREATE OR REPLACE FUNCTION security.agent_operation_rows(p_start timestamptz,p_end timestamptz)
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
$$;

COMMIT;
