BEGIN;
SET LOCAL check_function_bodies=on;
-- Add provenance to future technical receipts without rewriting historical evidence.
ALTER TABLE ops.audit_log ADD COLUMN actor_name_snapshot text;
ALTER TABLE ops.audit_log ADD COLUMN actor_team_snapshot text;
ALTER TABLE ops.audit_log ADD COLUMN execution_kind text;
ALTER TABLE ops.audit_log ADD COLUMN transaction_id bigint;
CREATE FUNCTION ops.capture_audit_identity() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 SELECT display_name INTO NEW.actor_name_snapshot FROM platform.user_ref WHERE id=NEW.actor_user_ref_id AND workspace_id=NEW.workspace_id;
 SELECT string_agg(DISTINCT t.name,'、' ORDER BY t.name) INTO NEW.actor_team_snapshot
 FROM platform.team_membership m JOIN platform.team t ON t.id=m.team_id
 WHERE m.user_ref_id=NEW.actor_user_ref_id AND m.workspace_id=NEW.workspace_id AND m.valid_to>clock_timestamp() AND m.valid_from<=clock_timestamp();
 NEW.execution_kind=CASE WHEN NULLIF(current_setting('app.job_id',true),'') IS NOT NULL THEN 'system' ELSE 'human' END;
 NEW.transaction_id=txid_current();
 RETURN NEW;
END $$;
CREATE TRIGGER audit_identity BEFORE INSERT ON ops.audit_log FOR EACH ROW EXECUTE FUNCTION ops.capture_audit_identity();

-- Canonical evidence projection: reuse business events, do not manufacture historical audit rows.
-- Management-only SECURITY DEFINER is needed for cross-user imports; both role and workspace
-- are checked in the final SELECT. No broader access to source tables is granted.
CREATE FUNCTION security.business_activity_rows(p_start timestamptz,p_end timestamptz)
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
), audit_candidates AS (
 SELECT a.*,
 CASE WHEN a.object_type IN ('user_ref','role_binding','team_membership','password_credential') THEN 'account'
 WHEN a.object_type IN ('customer','contact') THEN 'customer' ELSE a.object_type END AS family,
 CASE WHEN a.object_type IN ('role_binding','team_membership','password_credential') THEN COALESCE(a.after_snapshot,a.before_snapshot)->>'user_ref_id'
 WHEN a.object_type='contact' THEN COALESCE(a.after_snapshot,a.before_snapshot)->>'customer_id' ELSE a.object_id::text END AS subject
 FROM ops.audit_log a WHERE a.occurred_at>=p_start AND a.occurred_at<p_end AND a.workspace_id=common.current_workspace_id() AND (
 (a.object_type IN ('customer','contact','visit') AND a.action_code LIKE '%.update') OR
 a.object_type IN ('user_ref','role_binding','team_membership','password_credential','team','customer_actual','sales_target','opportunity_quote_reference','ai_usage_rule') OR
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
 WHEN g.family='opportunity_quote_reference' THEN 'quote.change' ELSE 'ai_rule.change' END AS action,
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
CREATE INDEX audit_object_history ON ops.audit_log(workspace_id,object_id,occurred_at);
COMMENT ON FUNCTION security.business_activity_rows(timestamptz,timestamptz) IS '管理视角业务操作证据：原有事件/已保存记录/事务行审计投影；不把HTTP轮询计为操作，不反推缺失历史';
COMMIT;
