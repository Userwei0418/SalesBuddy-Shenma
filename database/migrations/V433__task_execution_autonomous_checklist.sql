BEGIN;

-- V2 is an explicit per-task contract. No tenant flags, old task modes, states,
-- grants or historical completion facts are mass-converted by this migration.
ALTER TABLE workflow.task DROP CONSTRAINT task_execution_mode_check;
ALTER TABLE workflow.task ADD CONSTRAINT task_execution_mode_check
 CHECK(execution_mode IN ('legacy','execution_v1','execution_v2'));


CREATE OR REPLACE FUNCTION security.task_actor_recipient(p_task uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=p_task
  AND a.workspace_id=common.current_workspace_id() AND a.assignee_user_ref_id=common.current_user_ref_id()
  AND security.task_recipient_eligible(p_task,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=p_task
  AND c.workspace_id=common.current_workspace_id() AND c.user_ref_id=common.current_user_ref_id()
  AND security.task_recipient_eligible(p_task,c.user_ref_id,c.role_code,c.team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_execution x JOIN workflow.task t ON t.id=x.task_id AND t.execution_mode IN ('execution_v1','execution_v2') WHERE x.task_id=p_task AND x.workspace_id=common.current_workspace_id()
  AND (common.current_user_ref_id()=(x.state->>'current_reviewer')::uuid
   OR (x.state->'reviewer_handover'->>'status'='pending' AND common.current_user_ref_id()=(x.state->'reviewer_handover'->>'target_user_id')::uuid))
  AND workflow.task_execution_person(p_task,common.current_user_ref_id()) IS NOT NULL);
$$;

CREATE OR REPLACE FUNCTION security.authorization_task(p_permission text,p_task uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE((SELECT security.authorization_allows(p_permission,t.workspace_id,
  CASE WHEN p_permission IN ('task.review','task.reviewer_handover') THEN COALESCE((x.state->>'current_reviewer')::uuid,t.creator_user_ref_id)
   WHEN p_permission NOT IN ('task.coordinate','task.cancel') AND security.task_actor_recipient(t.id) THEN common.current_user_ref_id()
   ELSE t.creator_user_ref_id END,
  ARRAY[t.creator_team_id] || ARRAY(SELECT a.assignee_team_id FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.workspace_id=t.workspace_id)
  || ARRAY(SELECT c.team_id FROM workflow.task_candidate c WHERE c.task_id=t.id AND c.workspace_id=t.workspace_id),
  security.task_actor_recipient(t.id)) OR
  ((p_permission NOT LIKE 'task.%' OR p_permission='task.read') AND t.opportunity_id IS NOT NULL AND security.authorization_opportunity(p_permission,t.opportunity_id))
  OR (p_permission='task.read' AND (common.current_user_ref_id()=(x.state->>'current_reviewer')::uuid
      OR (x.state->'reviewer_handover'->>'status'='pending' AND common.current_user_ref_id()=(x.state->'reviewer_handover'->>'target_user_id')::uuid))
      AND workflow.task_execution_user_allows(t.id,common.current_user_ref_id(),'task.read',common.current_user_ref_id(),true))
 FROM workflow.task t LEFT JOIN workflow.task_execution x ON x.task_id=t.id AND t.execution_mode IN ('execution_v1','execution_v2')
 WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL),false);
$$;

CREATE FUNCTION workflow.task_execution_validate_definition_v2(p_requirements jsonb,p_checklist jsonb) RETURNS void
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE i jsonb; k text;
BEGIN
 IF jsonb_typeof(p_requirements) IS DISTINCT FROM 'object'
 OR p_requirements-ARRAY['title','description','deliverable','acceptance_criteria','due_at','priority_code']<>'{}'::jsonb
 OR jsonb_typeof(p_checklist) IS DISTINCT FROM 'array'
 THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_INVALID' USING ERRCODE='22023'; END IF;
 FOREACH k IN ARRAY ARRAY['title','description','deliverable','acceptance_criteria','due_at','priority_code'] LOOP
  IF jsonb_typeof(p_requirements->k) IS DISTINCT FROM 'string'
  THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_INVALID' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF length(btrim(p_requirements->>'title')) NOT BETWEEN 1 AND 500
 OR length(p_requirements->>'description')>10000
 OR length(btrim(p_requirements->>'deliverable')) NOT BETWEEN 1 AND 4000
 OR length(btrim(p_requirements->>'acceptance_criteria')) NOT BETWEEN 1 AND 4000
 OR p_requirements->>'priority_code' NOT IN ('normal','medium','high','urgent')
 OR jsonb_array_length(p_checklist)>100
 OR (SELECT count(*)<>count(DISTINCT value->>'item_id') FROM jsonb_array_elements(p_checklist))
 THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_INVALID' USING ERRCODE='22023'; END IF;
 PERFORM (p_requirements->>'due_at')::timestamptz;
 FOR i IN SELECT value FROM jsonb_array_elements(p_checklist) LOOP
  IF jsonb_typeof(i) IS DISTINCT FROM 'object' OR i-ARRAY['item_id','definition_version','title','acceptance_criteria']<>'{}'::jsonb
   OR jsonb_typeof(i->'item_id') IS DISTINCT FROM 'string'
   OR jsonb_typeof(i->'definition_version') IS DISTINCT FROM 'number' OR (i->>'definition_version')!~'^[1-9][0-9]*$'
   OR jsonb_typeof(i->'title') IS DISTINCT FROM 'string' OR length(btrim(i->>'title')) NOT BETWEEN 1 AND 500
   OR jsonb_typeof(i->'acceptance_criteria') IS DISTINCT FROM 'string' OR length(btrim(i->>'acceptance_criteria')) NOT BETWEEN 1 AND 2000
  THEN RAISE EXCEPTION 'TASK_CHECKLIST_INVALID' USING ERRCODE='22023'; END IF;
  PERFORM (i->>'item_id')::uuid; PERFORM (i->>'definition_version')::integer;
 END LOOP;
END $$;

CREATE FUNCTION workflow.task_execution_initialize_v2(p_task uuid,p_definition jsonb,p_confirmed_self boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; a uuid:=common.current_user_ref_id(); owner_id uuid; owner_count int; self_accept boolean; v int; e uuid:=gen_random_uuid();
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND OR t.execution_origin_xid<>pg_current_xact_id() OR NOT security.authorization_task_created(p_task)
 OR t.execution_mode<>'legacy' OR t.status<>'pending_confirm'
 THEN RAISE EXCEPTION 'TASK_INITIALIZE_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF NOT security.workspace_module_enabled(t.workspace_id,'task') THEN RAISE EXCEPTION 'TASK_EXECUTION_ROLLOUT_DISABLED' USING ERRCODE='40001'; END IF;
 IF EXISTS(SELECT 1 FROM workflow.task_execution WHERE task_id=p_task) THEN RAISE EXCEPTION 'TASK_ALREADY_INITIALIZED' USING ERRCODE='40001'; END IF;
 IF jsonb_typeof(p_definition) IS DISTINCT FROM 'object' OR p_definition-ARRAY['requirements','checklist']<>'{}'::jsonb
 THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_INVALID' USING ERRCODE='22023'; END IF;
 PERFORM workflow.task_execution_validate_definition_v2(p_definition->'requirements',p_definition->'checklist');
 IF p_definition->'requirements'->>'title' IS DISTINCT FROM t.title
 OR p_definition->'requirements'->>'description' IS DISTINCT FROM t.description
 OR (p_definition->'requirements'->>'due_at')::timestamptz IS DISTINCT FROM t.due_at
 OR p_definition->'requirements'->>'priority_code' IS DISTINCT FROM t.priority_code
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_definition->'checklist') i WHERE (i->>'definition_version')::int<>1)
 THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_INVALID' USING ERRCODE='22023'; END IF;
 SELECT count(*) INTO owner_count FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' LIMIT 1;
 IF owner_count>1 THEN RAISE EXCEPTION 'TASK_EXECUTION_MULTIPLE_OWNERS' USING ERRCODE='40001'; END IF;
 self_accept:=COALESCE(p_confirmed_self,false) AND owner_count=1 AND owner_id=a AND t.creator_user_ref_id=a
  AND t.target_position IS NULL AND t.source_code IN ('mini_program','business_advice')
  AND (t.source_code<>'business_advice' OR t.source_suggestion_id IS NOT NULL);
 IF p_confirmed_self AND NOT self_accept THEN RAISE EXCEPTION 'TASK_SELF_ACCEPT_SOURCE_INVALID' USING ERRCODE='22023'; END IF;
 IF self_accept AND (NOT security.authorization_task('task.accept',p_task) OR workflow.task_execution_person(p_task,a) IS NULL)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 s:=workflow.task_execution_initial(p_task)||jsonb_build_object('protocol_version',2,'checklist_revision',1,'requirements',p_definition->'requirements',
  'items',COALESCE((SELECT jsonb_agg(value||jsonb_build_object('state','not_started','note','','requires_revalidation',false)) FROM jsonb_array_elements(p_definition->'checklist')),'[]'::jsonb),
  'self_accept_source',CASE WHEN self_accept THEN t.source_code END);
 INSERT INTO workflow.task_execution(task_id,workspace_id,state) VALUES(p_task,t.workspace_id,s);
 UPDATE workflow.task SET execution_mode='execution_v2',status=CASE WHEN self_accept THEN 'pending_execution' ELSE status END,
  accepted_at=CASE WHEN self_accept THEN clock_timestamp() ELSE accepted_at END WHERE id=p_task RETURNING version_no INTO v;
 IF self_accept THEN
  UPDATE workflow.task_assignee SET accepted_at=clock_timestamp() WHERE task_id=p_task AND responsibility='owner';
  INSERT INTO workflow.task_execution_event(id,task_id,workspace_id,actor_user_ref_id,kind,body)
   VALUES(e,p_task,t.workspace_id,a,'created_self_accepted',jsonb_build_object('actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'source_code',t.source_code,'source_suggestion_id',t.source_suggestion_id,'after',s,'version_no',v));
  INSERT INTO workflow.task_event(id,workspace_id,task_id,event_type,from_status,to_status,actor_user_ref_id,note,payload)
   VALUES(e,t.workspace_id,p_task,'accept','pending_confirm','pending_execution',a,'本人确认创建并接收任务',jsonb_build_object('execution_protocol',2,'source',t.source_code,'version_no',v));
 END IF;
 RETURN workflow.task_execution_read(p_task);
END $$;

CREATE OR REPLACE FUNCTION workflow.task_execution_capabilities(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; a uuid:=common.current_user_ref_id(); owner_id uuid; owner_ok boolean; review_ok boolean;
 coordinate_ok boolean; feedback_ok boolean; executing boolean; live boolean; handover boolean; self_task boolean; owner_count int; v2 boolean;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND OR t.execution_mode NOT IN ('execution_v1','execution_v2') OR NOT security.authorization_task('task.read',p_task) THEN RETURN '{}'::jsonb; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 v2:=t.execution_mode='execution_v2';
 SELECT count(*) INTO owner_count FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 self_task:=owner_count=1 AND owner_id=t.creator_user_ref_id;
 owner_ok:=a=owner_id AND workflow.task_execution_person(p_task,a) IS NOT NULL AND security.authorization_task('task.complete',p_task);
 review_ok:=a=(s->>'current_reviewer')::uuid AND a IS DISTINCT FROM owner_id AND security.authorization_task('task.review',p_task);
 coordinate_ok:=security.authorization_task('task.coordinate',p_task);
 feedback_ok:=security.authorization_task('task.feedback',p_task) AND (a=owner_id OR a=(s->>'current_reviewer')::uuid OR coordinate_ok
  OR EXISTS(SELECT 1 FROM workflow.task_assignee WHERE task_id=p_task AND assignee_user_ref_id=a));
 IF v2 THEN feedback_ok:=(feedback_ok OR owner_ok OR review_ok OR coordinate_ok) AND NOT EXISTS(SELECT 1 FROM security.authorization_current_grants() WHERE permission_code='task.feedback' AND effect='deny'); END IF;
 live:=t.status NOT IN ('completed','cancelled');
 handover:=COALESCE(s->'execution_handover'->>'status' IN ('pending','rejected'),false);
 executing:=live AND t.status IN ('pending_execution','in_progress') AND NOT handover AND s->>'pause' IS NULL;
 RETURN jsonb_build_object('save_checklist',COALESCE(v2 AND owner_ok AND t.status IN ('pending_execution','in_progress') AND NOT handover,false),
 'initialize_execution',COALESCE(NOT v2 AND live AND (owner_ok OR coordinate_ok) AND s->>'pending_requirements' IS NULL AND NOT handover AND s->'reviewer_handover'->>'status' IS DISTINCT FROM 'pending',false),
 'complete_self',COALESCE(v2 AND self_task AND owner_ok AND t.status='pending_review' AND s->>'current_submission' IS NOT NULL,false),
 'cancel_task',COALESCE(NOT EXISTS(SELECT 1 FROM security.authorization_current_grants() WHERE permission_code='task.cancel' AND effect='deny') AND v2 AND live AND t.status<>'pending_review' AND ((a=t.creator_user_ref_id) OR (security.authorization_task('task.cancel',p_task) AND security.fde_can_coordinate_task(p_task))),false),
 'set_item_progress',COALESCE(owner_ok AND executing,false),
 'append_feedback',COALESCE(feedback_ok AND live,false),
 'pause',COALESCE((owner_ok OR coordinate_ok) AND t.status IN ('pending_execution','in_progress') AND s->>'pause' IS NULL,false),
 'recheck_pause',COALESCE((owner_ok OR coordinate_ok) AND live AND s->>'pause' IS NOT NULL,false),
 'resume',COALESCE((owner_ok OR coordinate_ok) AND live AND NOT handover AND s->>'pause' IS NOT NULL
  AND workflow.task_execution_person(p_task,owner_id) IS NOT NULL
  AND workflow.task_execution_user_allows(p_task,owner_id,'task.complete',owner_id,true),false),
 'initiate_executor_handover',COALESCE(coordinate_ok AND live AND t.status<>'pending_review',false),
 'initiate_reviewer_handover',COALESCE(security.authorization_task('task.reviewer_handover',p_task) AND live AND (NOT v2 OR NOT self_task),false),
 'respond_executor_handover',COALESCE(a=(s->'execution_handover'->>'target_user_id')::uuid AND s->'execution_handover'->>'status'='pending' AND live,false),
 'respond_reviewer_handover',COALESCE(a=(s->'reviewer_handover'->>'target_user_id')::uuid AND s->'reviewer_handover'->>'status'='pending' AND live,false),
 'propose_requirements',COALESCE((owner_ok OR review_ok OR coordinate_ok OR (a=(s->>'current_reviewer')::uuid AND security.authorization_task('task.review',p_task)))
  AND live AND t.status<>'pending_review' AND NOT handover AND s->'reviewer_handover'->>'status' IS DISTINCT FROM 'pending' AND s->>'pending_requirements' IS NULL,false),
 'respond_requirements',COALESCE(live AND t.status<>'pending_review' AND (owner_ok OR review_ok OR (a=(s->>'current_reviewer')::uuid AND security.authorization_task('task.review',p_task))),false),
 'withdraw_requirements',COALESCE(live AND (a=(s->'pending_requirements'->>'proposer_id')::uuid OR coordinate_ok),false),
 'submit_completion',COALESCE(owner_ok AND executing AND s->>'pending_requirements' IS NULL AND (workflow.task_execution_progress(s)->>'percent')::int=100 AND (NOT v2 OR (length(btrim(s->'requirements'->>'deliverable'))>0 AND length(btrim(s->'requirements'->>'acceptance_criteria'))>0)),false),
 'review_completion',COALESCE(review_ok AND t.status='pending_review',false),
 'withdraw_completion',COALESCE(owner_ok AND t.status='pending_review',false),
 'accept_task',COALESCE(live AND t.status='pending_confirm' AND NOT handover AND security.task_actor_recipient(p_task) AND security.authorization_task('task.accept',p_task),false),
 'decline_task',COALESCE(live AND t.status='pending_confirm' AND NOT handover AND security.task_actor_recipient(p_task) AND security.authorization_task('task.decline',p_task),false));
END $$;

CREATE OR REPLACE FUNCTION workflow.task_execution_summary(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; owner_id uuid; owner_count int;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.authorization_task('task.read',p_task) THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF t.execution_mode='legacy' THEN RETURN NULL; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 s:=COALESCE(s,workflow.task_execution_initial(p_task));
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 SELECT count(*) INTO owner_count FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 RETURN s || jsonb_build_object('execution_mode',t.execution_mode,'task_id',p_task,'status',t.status,'version_no',t.version_no,'creator_user_ref_id',t.creator_user_ref_id,
 'checklist_revision',COALESCE((s->>'checklist_revision')::int,1),'self_managed',owner_count=1 AND owner_id=t.creator_user_ref_id,
 'review_required',NOT (owner_count=1 AND owner_id=t.creator_user_ref_id),'requirements_revalidation',EXISTS(SELECT 1 FROM jsonb_array_elements(s->'items') i WHERE COALESCE((i->>'requires_revalidation')::boolean,false)),
 'requirements_incomplete',length(btrim(COALESCE(s->'requirements'->>'deliverable','')))=0 OR length(btrim(COALESCE(s->'requirements'->>'acceptance_criteria','')))=0,
 'executor',workflow.task_execution_person(p_task,owner_id),'executor_user_id',owner_id,
 'reviewer',workflow.task_execution_person(p_task,(s->>'current_reviewer')::uuid),
 'coordination_required',CASE WHEN t.execution_mode='execution_v2' THEN workflow.task_execution_coordination_status(p_task) ELSE '{}'::jsonb END,
 'progress',workflow.task_execution_progress(s),'checklist',s->'items','capabilities',workflow.task_execution_capabilities(p_task));
END $$;

CREATE FUNCTION workflow.task_execution_adopt_v2(p_task uuid,p_body jsonb,p_key uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; old_s jsonb; a uuid:=common.current_user_ref_id(); owner_id uuid; owner_count int; prior workflow.task_execution_request;
 v int; e uuid:=gen_random_uuid(); sid uuid; result jsonb; adopt_status text; accepted_owner boolean;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND OR NOT security.authorization_task('task.read',p_task) OR workflow.task_execution_person(p_task,a) IS NULL
 THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT count(*) INTO owner_count FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' LIMIT 1;
 IF owner_count>1 THEN RAISE EXCEPTION 'TASK_EXECUTION_MULTIPLE_OWNERS' USING ERRCODE='40001'; END IF;
 IF NOT ((owner_count=1 AND owner_id=a AND security.authorization_task('task.complete',p_task)) OR security.authorization_task('task.coordinate',p_task))
 THEN RAISE EXCEPTION 'TASK_INITIALIZE_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF p_body ? 'resume_status' AND NOT security.authorization_task('task.coordinate',p_task) THEN RAISE EXCEPTION 'TASK_INITIALIZE_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM workflow.task_execution_request WHERE workspace_id=t.workspace_id AND key=p_key;
 IF FOUND THEN
  IF prior.task_id<>p_task OR prior.actor_user_ref_id<>a OR prior.action<>'initialize_execution' OR prior.body<>p_body
  THEN RAISE EXCEPTION 'TASK_EXECUTION_IDEMPOTENCY_CONFLICT' USING ERRCODE='40001'; END IF;
  RETURN prior.response;
 END IF;
 IF p_key IS NULL OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' OR p_body-ARRAY['version_no','requirements_version','responsibility_version','checklist_revision','reason','resume_status']<>'{}'::jsonb
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['version_no','requirements_version','responsibility_version','checklist_revision']) k WHERE jsonb_typeof(p_body->k) IS DISTINCT FROM 'number' OR (p_body->>k)!~'^[1-9][0-9]*$')
 OR length(btrim(COALESCE(p_body->>'reason',''))) NOT BETWEEN 1 AND 4000
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
 IF t.status IN ('completed','cancelled') OR t.execution_mode='execution_v2' OR NOT security.workspace_module_enabled(t.workspace_id,'task')
 THEN RAISE EXCEPTION 'TASK_EXECUTION_ACTION_UNAVAILABLE' USING ERRCODE='40001'; END IF;
 adopt_status:=t.status;
 IF t.status='deferred' THEN
  IF NOT security.authorization_task('task.coordinate',p_task) OR p_body->>'resume_status' IS NULL THEN RAISE EXCEPTION 'TASK_LEGACY_RESUME_REQUIRED' USING ERRCODE='40001'; END IF;
  accepted_owner:=owner_count=1 AND t.accepted_at IS NOT NULL AND EXISTS(SELECT 1 FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' AND accepted_at IS NOT NULL);
  IF (accepted_owner AND p_body->>'resume_status' NOT IN ('pending_execution','in_progress')) OR (NOT accepted_owner AND p_body->>'resume_status'<>'pending_confirm') THEN RAISE EXCEPTION 'TASK_LEGACY_RESUME_INVALID' USING ERRCODE='22023'; END IF;
  adopt_status:=p_body->>'resume_status';
 ELSIF p_body ? 'resume_status' THEN RAISE EXCEPTION 'TASK_LEGACY_RESUME_INVALID' USING ERRCODE='22023'; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task FOR UPDATE;
 s:=COALESCE(s,workflow.task_execution_initial(p_task)); old_s:=s;
 IF (p_body->>'version_no')::int<>t.version_no OR (p_body->>'requirements_version')::int<>(s->>'requirements_version')::int
 OR (p_body->>'responsibility_version')::int<>(s->>'responsibility_version')::int OR (p_body->>'checklist_revision')::int<>COALESCE((s->>'checklist_revision')::int,1)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 IF s->>'pending_requirements' IS NOT NULL OR s->'execution_handover'->>'status' IN ('pending','rejected') OR s->'reviewer_handover'->>'status'='pending'
 THEN RAISE EXCEPTION 'TASK_EXECUTION_PENDING_FLOW' USING ERRCODE='40001'; END IF;
 s:=s||jsonb_build_object('protocol_version',2,'checklist_revision',1,'requirements',(s->'requirements')-'checklist','adopted_from',t.execution_mode);
 IF t.status='pending_review' AND s->>'current_submission' IS NULL THEN
  IF owner_count<>1 OR t.completion_submitted_at IS NULL OR length(btrim(COALESCE(t.completion_note,'')))=0
  THEN RAISE EXCEPTION 'TASK_SUBMISSION_MISSING' USING ERRCODE='40001'; END IF;
  sid:=gen_random_uuid();
  INSERT INTO workflow.task_execution_submission(id,task_id,workspace_id,actor_user_ref_id,snapshot)
   VALUES(sid,p_task,t.workspace_id,owner_id,jsonb_build_object('legacy',true,'note',t.completion_note,'submitted_at',t.completion_submitted_at,'requirements',s->'requirements','items',s->'items'));
  s:=jsonb_set(s,'{current_submission}',to_jsonb(sid));
 END IF;
 INSERT INTO workflow.task_execution(task_id,workspace_id,state) VALUES(p_task,t.workspace_id,s)
 ON CONFLICT(task_id) DO UPDATE SET state=EXCLUDED.state,updated_at=clock_timestamp();
 UPDATE workflow.task SET execution_mode='execution_v2',status=adopt_status WHERE id=p_task RETURNING version_no INTO v;
 INSERT INTO workflow.task_execution_event(id,task_id,workspace_id,actor_user_ref_id,kind,body)
 VALUES(e,p_task,t.workspace_id,a,'initialize_execution',jsonb_build_object('command',p_body,'before',old_s,'after',s,'from_status',t.status,'to_status',adopt_status,'version_no',v,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a)));
 result:=jsonb_build_object('changed',true,'event_id',e,'version_no',v,'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version','checklist_revision',1,'execution',workflow.task_execution_read(p_task));
 INSERT INTO workflow.task_execution_request(task_id,workspace_id,actor_user_ref_id,key,action,body,response)
 VALUES(p_task,t.workspace_id,a,p_key,'initialize_execution',p_body,result);
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION workflow.task_execution_candidate(p_task uuid,p_user uuid,p_purpose text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s jsonb; owner_id uuid; creator_id uuid; mode text; person jsonb;
BEGIN
 SELECT creator_user_ref_id,execution_mode INTO creator_id,mode FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id();
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 person:=workflow.task_execution_person(p_task,p_user);
 IF p_purpose NOT IN ('executor','reviewer') OR person IS NULL OR p_user=owner_id OR (p_user=(s->>'current_reviewer')::uuid AND NOT (mode='execution_v2' AND p_purpose='executor' AND p_user=creator_id))
  OR NOT workflow.task_execution_user_allows(p_task,p_user,'task.read',p_user,true)
 THEN RETURN NULL; END IF;
 IF p_purpose='executor' AND (NOT workflow.task_execution_user_allows(p_task,p_user,'task.accept',p_user,true)
  OR NOT workflow.task_execution_user_allows(p_task,p_user,'task.complete',p_user,true)) THEN RETURN NULL; END IF;
 IF p_purpose='reviewer' AND NOT workflow.task_execution_user_allows(p_task,p_user,'task.review',p_user,true) THEN RETURN NULL; END IF;
 RETURN person;
END $$;

CREATE OR REPLACE FUNCTION workflow.task_execution_command(p_task uuid,p_action text,p_body jsonb,p_key uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; next_task workflow.task; committed_version integer; submission_snapshot jsonb; s jsonb; old_s jsonb; a uuid:=common.current_user_ref_id(); owner_id uuid; reviewer_id uuid;
 prior workflow.task_execution_request; cap jsonb; e uuid:=gen_random_uuid(); note text; target uuid; person jsonb;
 h jsonb; change jsonb; i jsonb; previous jsonb; items jsonb; required jsonb; confirmations jsonb;
 req jsonb; diffs jsonb; contribution jsonb; result jsonb; response jsonb; sid uuid; next_status text; event_kind text:=p_action;
 changed boolean:=true; apply_req boolean:=false; self_task boolean; permission text; allowed text[]; versions text[]; v2 boolean; owner_count int; old_definition jsonb; definition_changed boolean;
BEGIN
 IF p_key IS NULL THEN RAISE EXCEPTION 'TASK_EXECUTION_IDEMPOTENCY_KEY_REQUIRED' USING ERRCODE='22023'; END IF;
 -- Null and omission have the pre-contribution command identity, including direct SQL callers.
 IF p_action='submit_completion' AND (p_body->'actual_contribution' IS NULL OR p_body->'actual_contribution'='null'::jsonb)
 THEN p_body:=p_body-'actual_contribution'; END IF;
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.authorization_task('task.read',p_task) OR workflow.task_execution_person(p_task,a) IS NULL
 THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF p_action='initialize_execution' THEN RETURN workflow.task_execution_adopt_v2(p_task,p_body,p_key); END IF;
 v2:=t.execution_mode='execution_v2';
 IF t.execution_mode NOT IN ('execution_v1','execution_v2') THEN RAISE EXCEPTION 'TASK_EXECUTION_LEGACY_MODE' USING ERRCODE='40001'; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_EXECUTION_NOT_INITIALIZED' USING ERRCODE='40001'; END IF;
 old_s:=s; next_task:=t; next_status:=t.status; reviewer_id:=(s->>'current_reviewer')::uuid;
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 SELECT count(*) INTO owner_count FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 self_task:=a=owner_id AND owner_count=1 AND owner_id=t.creator_user_ref_id AND (v2 OR (reviewer_id=owner_id AND NOT (s->>'executor_transferred')::boolean));
 permission:=CASE WHEN p_action IN ('set_item_progress','submit_completion','withdraw_completion','save_checklist','complete_self') THEN 'task.complete'
 WHEN p_action='append_feedback' AND NOT v2 THEN 'task.feedback' WHEN p_action='initiate_executor_handover' THEN 'task.coordinate'
 WHEN p_action='initiate_reviewer_handover' THEN 'task.reviewer_handover'
 WHEN p_action IN ('review_completion','respond_reviewer_handover') THEN 'task.review'
 WHEN p_action IN ('accept_task','respond_executor_handover') THEN 'task.accept'
 WHEN p_action='decline_task' THEN 'task.decline' ELSE NULL END;
 IF permission IS NOT NULL AND NOT (CASE WHEN p_action='respond_reviewer_handover' THEN workflow.task_execution_user_allows(p_task,a,'task.review',a,true) ELSE security.authorization_task(permission,p_task) END)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 -- Authorization is checked before replay. Replay may return a terminal receipt,
 -- but a revoked participant cannot use it as an authorization/cache bypass.
 IF (p_action IN ('set_item_progress','submit_completion','withdraw_completion','save_checklist','complete_self') AND a IS DISTINCT FROM owner_id)
 OR (p_action='review_completion' AND (a IS DISTINCT FROM reviewer_id OR a=owner_id))
 OR (p_action='respond_executor_handover' AND a IS DISTINCT FROM (s->'execution_handover'->>'target_user_id')::uuid)
 OR (p_action='respond_reviewer_handover' AND a IS DISTINCT FROM (s->'reviewer_handover'->>'target_user_id')::uuid)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_RESPONSIBILITY_CHANGED' USING ERRCODE='42501'; END IF;
 IF p_action='append_feedback' AND v2 AND EXISTS(SELECT 1 FROM security.authorization_current_grants() WHERE permission_code='task.feedback' AND effect='deny') THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF p_action='append_feedback' AND NOT (a=owner_id OR a=reviewer_id OR security.authorization_task('task.coordinate',p_task)
  OR EXISTS(SELECT 1 FROM workflow.task_assignee WHERE task_id=p_task AND assignee_user_ref_id=a))
 THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF p_action='respond_requirements' AND NOT ((a=owner_id AND security.authorization_task('task.complete',p_task))
  OR (a=reviewer_id AND security.authorization_task('task.review',p_task)))
 THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_CONFIRMATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF p_action='cancel_task' AND NOT (NOT EXISTS(SELECT 1 FROM security.authorization_current_grants() WHERE permission_code='task.cancel' AND effect='deny') AND v2 AND (a=t.creator_user_ref_id OR (security.authorization_task('task.cancel',p_task) AND security.fde_can_coordinate_task(p_task))))
 THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM workflow.task_execution_request WHERE workspace_id=t.workspace_id AND key=p_key;
 IF FOUND THEN
  IF prior.task_id<>p_task OR prior.actor_user_ref_id<>a OR prior.action<>p_action OR prior.body<>p_body
  THEN RAISE EXCEPTION 'TASK_EXECUTION_IDEMPOTENCY_CONFLICT' USING ERRCODE='40001'; END IF;
  -- General commands retain a fresh action grant, not only task visibility.
  IF permission IS NULL AND NOT (p_action='append_feedback' AND v2 AND security.authorization_task('task.feedback',p_task)) AND NOT (p_action='cancel_task' AND v2 AND a=t.creator_user_ref_id) AND NOT (security.authorization_task('task.coordinate',p_task)
   OR (a=owner_id AND security.authorization_task('task.complete',p_task))
   OR (a=reviewer_id AND security.authorization_task('task.review',p_task)))
  THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
  RETURN prior.response;
 END IF;
 allowed:=CASE p_action
 WHEN 'set_item_progress' THEN ARRAY['item_id','definition_version','state','note']
 WHEN 'append_feedback' THEN CASE WHEN v2 THEN ARRAY['text','reply_to_event_id','corrects_event_id','feedback_kind','item_id','next_step','blocker','evidence_urls'] ELSE ARRAY['text','reply_to_event_id','corrects_event_id'] END
 WHEN 'pause' THEN ARRAY['reason','recheck_at'] WHEN 'recheck_pause' THEN ARRAY['pause_id','reason','recheck_at']
 WHEN 'resume' THEN ARRAY['pause_id','reason']
 WHEN 'initiate_executor_handover' THEN ARRAY['target_user_id','reason'] WHEN 'initiate_reviewer_handover' THEN ARRAY['target_user_id','reason']
 WHEN 'respond_executor_handover' THEN ARRAY['handover_id','decision','reason'] WHEN 'respond_reviewer_handover' THEN ARRAY['handover_id','decision','reason']
 WHEN 'propose_requirements' THEN ARRAY['requirements','reason'] WHEN 'respond_requirements' THEN ARRAY['change_id','decision','reason']
 WHEN 'withdraw_requirements' THEN ARRAY['change_id','reason'] WHEN 'submit_completion' THEN CASE WHEN v2 THEN ARRAY['note','actual_contribution','missing_deliverables_resolution'] ELSE ARRAY['note','actual_contribution'] END
 WHEN 'withdraw_completion' THEN ARRAY['submission_id','reason'] WHEN 'review_completion' THEN CASE WHEN v2 THEN ARRAY['submission_id','decision','note','rework_item_ids','missing_deliverables'] ELSE ARRAY['submission_id','decision','note','rework_item_ids'] END
 WHEN 'save_checklist' THEN CASE WHEN v2 THEN ARRAY['checklist','reason'] END
 WHEN 'complete_self' THEN CASE WHEN v2 THEN ARRAY['note','submission_id'] END
 WHEN 'cancel_task' THEN CASE WHEN v2 THEN ARRAY['reason'] END
 WHEN 'accept_task' THEN ARRAY['reason'] WHEN 'decline_task' THEN ARRAY['reason'] ELSE NULL END;
 versions:=ARRAY['version_no','requirements_version','responsibility_version']||CASE WHEN v2 THEN ARRAY['checklist_revision'] ELSE ARRAY[]::text[] END;
 IF allowed IS NULL OR jsonb_typeof(p_body)<>'object' OR p_body-(allowed||versions)<>'{}'::jsonb
 OR EXISTS(SELECT 1 FROM unnest(versions) v WHERE jsonb_typeof(p_body->v) IS DISTINCT FROM 'number' OR (p_body->>v)!~'^[1-9][0-9]*$')
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
 IF (p_body->>'version_no')::int<>t.version_no OR (p_body->>'requirements_version')::int<>(s->>'requirements_version')::int
 OR (p_body->>'responsibility_version')::int<>(s->>'responsibility_version')::int OR (v2 AND (p_body->>'checklist_revision')::int<>(s->>'checklist_revision')::int)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 cap:=workflow.task_execution_capabilities(p_task);
 IF NOT COALESCE((cap->>p_action)::boolean,false) THEN RAISE EXCEPTION 'TASK_EXECUTION_ACTION_UNAVAILABLE' USING ERRCODE='40001'; END IF;
 note:=btrim(COALESCE(p_body->>'reason',p_body->>'note',p_body->>'text',''));
 IF p_action NOT IN ('accept_task','respond_executor_handover','respond_reviewer_handover','respond_requirements','review_completion') AND (length(note)<1 OR length(note)>4000)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_REASON_REQUIRED' USING ERRCODE='22023'; END IF;
 IF p_action IN ('respond_executor_handover','respond_reviewer_handover','respond_requirements') AND
  (p_body->>'decision' IS NULL OR p_body->>'decision' NOT IN ('accept','reject') OR (p_body->>'decision'='reject' AND length(note)=0))
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_DECISION' USING ERRCODE='22023'; END IF;

 IF p_action='save_checklist' THEN
  IF jsonb_typeof(p_body->'checklist') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'TASK_CHECKLIST_INVALID' USING ERRCODE='22023'; END IF;
  -- A newly adopted old task may still lack overall criteria. Validate its list
  -- with a temporary validator-only placeholder, never persisted as requirements.
  PERFORM workflow.task_execution_validate_definition_v2(s->'requirements'||jsonb_build_object(
   'deliverable',COALESCE(NULLIF(btrim(s->'requirements'->>'deliverable'),''),'validation only'),
   'acceptance_criteria',COALESCE(NULLIF(btrim(s->'requirements'->>'acceptance_criteria'),''),'validation only')),p_body->'checklist');
  items:='[]'::jsonb;
  FOR i IN SELECT value FROM jsonb_array_elements(p_body->'checklist') LOOP
   SELECT value INTO previous FROM jsonb_array_elements(s->'items') WHERE value->>'item_id'=i->>'item_id';
   old_definition:=CASE WHEN previous IS NULL THEN NULL ELSE previous-ARRAY['state','note','requires_revalidation','updated_at','actor_user_id','review_note'] END;
   definition_changed:=previous IS NOT NULL AND (i-'definition_version') IS DISTINCT FROM (old_definition-'definition_version');
   IF (previous IS NULL AND (i->>'definition_version')::int<>1) OR (previous IS NOT NULL AND (i->>'definition_version')::int<>(previous->>'definition_version')::int+CASE WHEN definition_changed THEN 1 ELSE 0 END)
   THEN RAISE EXCEPTION 'TASK_ITEM_DEFINITION_CONFLICT' USING ERRCODE='40001'; END IF;
   IF previous IS NULL THEN i:=i||jsonb_build_object('state','not_started','note','','requires_revalidation',false);
   ELSE i:=previous||i||jsonb_build_object('requires_revalidation',COALESCE((previous->>'requires_revalidation')::boolean,false) OR (definition_changed AND previous->>'state'='completed'));
   END IF;
   items:=items||jsonb_build_array(i);
  END LOOP;
  changed:=items IS DISTINCT FROM s->'items';
  IF changed THEN s:=s||jsonb_build_object('items',items,'checklist_revision',(s->>'checklist_revision')::int+1); END IF;
 ELSIF p_action='complete_self' THEN
  IF NOT self_task OR s->>'current_submission' IS DISTINCT FROM p_body->>'submission_id'
   OR NOT EXISTS(SELECT 1 FROM workflow.task_execution_submission WHERE id=(s->>'current_submission')::uuid AND task_id=p_task AND actor_user_ref_id=a)
  THEN RAISE EXCEPTION 'TASK_SUBMISSION_CHANGED' USING ERRCODE='40001'; END IF;
  next_status:='completed'; event_kind:='complete'; next_task.completed_at:=clock_timestamp(); next_task.verification_status:='not_required';
 ELSIF p_action='cancel_task' THEN
  next_status:='cancelled'; event_kind:='cancel';
 ELSIF p_action='set_item_progress' THEN
  SELECT value INTO previous FROM jsonb_array_elements(s->'items') WHERE value->>'item_id'=p_body->>'item_id';
  IF previous IS NULL OR previous->>'definition_version' IS DISTINCT FROM p_body->>'definition_version'
  THEN RAISE EXCEPTION 'TASK_ITEM_DEFINITION_CONFLICT' USING ERRCODE='40001'; END IF;
  IF p_body->>'state' IS NULL OR p_body->>'state' NOT IN ('not_started','in_progress','completed','rework')
  THEN RAISE EXCEPTION 'TASK_ITEM_STATE_INVALID' USING ERRCODE='22023'; END IF;
  changed:=previous->>'state' IS DISTINCT FROM p_body->>'state' OR previous->>'note' IS DISTINCT FROM note OR (previous->>'requires_revalidation')::boolean;
  IF changed THEN
   s:=jsonb_set(s,'{items}',(SELECT jsonb_agg(CASE WHEN value->>'item_id'=p_body->>'item_id' THEN
    value||jsonb_build_object('state',p_body->>'state','note',note,'requires_revalidation',false,'updated_at',clock_timestamp(),'actor_user_id',a)
    ELSE value END) FROM jsonb_array_elements(s->'items')));
   s:=s||jsonb_build_object('last_execution_feedback_at',clock_timestamp(),'first_started_at',CASE WHEN NOT v2 OR p_body->>'state'='in_progress' THEN COALESCE(s->>'first_started_at',clock_timestamp()::text) ELSE s->>'first_started_at' END);
   next_status:='in_progress';
  END IF;
 ELSIF p_action='append_feedback' THEN
  IF v2 THEN
   IF COALESCE(p_body->>'feedback_kind','comment') NOT IN ('comment','progress')
    OR length(COALESCE(p_body->>'next_step',''))>4000 OR length(COALESCE(p_body->>'blocker',''))>4000
    OR (p_body ? 'evidence_urls' AND (jsonb_typeof(p_body->'evidence_urls') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'evidence_urls')>20))
   THEN RAISE EXCEPTION 'TASK_FEEDBACK_INVALID' USING ERRCODE='22023'; END IF;
   IF p_body ? 'evidence_urls' AND EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'evidence_urls') u WHERE jsonb_typeof(u)<>'string' OR u#>>'{}' !~ '^https?://' OR length(u#>>'{}')>2000)
   THEN RAISE EXCEPTION 'TASK_FEEDBACK_INVALID' USING ERRCODE='22023'; END IF;
   IF p_body->>'item_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s->'items') item_ref WHERE item_ref->>'item_id'=p_body->>'item_id')
   THEN RAISE EXCEPTION 'TASK_FEEDBACK_REFERENCE_INVALID' USING ERRCODE='22023'; END IF;
   IF p_body->>'feedback_kind'='progress' THEN
    IF a IS DISTINCT FROM owner_id OR NOT COALESCE((cap->>'set_item_progress')::boolean,false)
    THEN RAISE EXCEPTION 'TASK_EXECUTION_ACTION_UNAVAILABLE' USING ERRCODE='40001'; END IF;
    s:=jsonb_set(s,'{last_execution_feedback_at}',to_jsonb(clock_timestamp()));
   END IF;
  END IF;
  IF p_body->>'reply_to_event_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workflow.task_execution_event WHERE id=(p_body->>'reply_to_event_id')::uuid AND task_id=p_task)
  THEN RAISE EXCEPTION 'TASK_FEEDBACK_REFERENCE_INVALID' USING ERRCODE='22023'; END IF;
  IF p_body->>'corrects_event_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workflow.task_execution_event WHERE id=(p_body->>'corrects_event_id')::uuid
   AND task_id=p_task AND actor_user_ref_id=a AND kind='append_feedback')
  THEN RAISE EXCEPTION 'TASK_FEEDBACK_CORRECTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 ELSIF p_action IN ('pause','recheck_pause') THEN
  IF p_body->>'recheck_at' IS NULL OR (p_body->>'recheck_at')::timestamptz<=clock_timestamp()
  THEN RAISE EXCEPTION 'TASK_PAUSE_RECHECK_MUST_BE_FUTURE' USING ERRCODE='22023'; END IF;
  IF p_action='pause' THEN
   s:=jsonb_set(s,'{pause}',jsonb_build_object('id',e,'reason',note,'recheck_at',p_body->>'recheck_at','started_at',clock_timestamp(),
    'actor_user_id',a,'original_status',t.status,'requirements_version',s->'requirements_version','progress',workflow.task_execution_progress(s),'recheck_version',1));
  ELSE
   IF s->'pause'->>'id' IS DISTINCT FROM p_body->>'pause_id' THEN RAISE EXCEPTION 'TASK_PAUSE_CHANGED' USING ERRCODE='40001'; END IF;
   s:=jsonb_set(s,'{pause}',s->'pause'||jsonb_build_object('recheck_at',p_body->>'recheck_at','recheck_note',note,'recheck_version',(s->'pause'->>'recheck_version')::int+1));
  END IF;
 ELSIF p_action='resume' THEN
  IF s->'pause'->>'id' IS DISTINCT FROM p_body->>'pause_id' THEN RAISE EXCEPTION 'TASK_PAUSE_CHANGED' USING ERRCODE='40001'; END IF;
  s:=jsonb_set(s,'{pause}','null'::jsonb);
 ELSIF p_action IN ('initiate_executor_handover','initiate_reviewer_handover') THEN
  target:=(p_body->>'target_user_id')::uuid; person:=workflow.task_execution_candidate(p_task,target,CASE WHEN p_action='initiate_executor_handover' THEN 'executor' ELSE 'reviewer' END);
  IF person IS NULL OR NOT workflow.task_execution_user_allows(p_task,target,'task.read',target,true)
  THEN RAISE EXCEPTION 'TASK_HANDOVER_TARGET_INELIGIBLE' USING ERRCODE='42501'; END IF;
  h:=jsonb_build_object('id',e,'target_user_id',target,'target_name',person->>'name','reason',note,'status','pending','created_at',clock_timestamp(),'initiator_id',a);
  IF p_action='initiate_executor_handover' THEN
   IF target=owner_id OR (target=reviewer_id AND NOT (v2 AND target=t.creator_user_ref_id)) OR NOT workflow.task_execution_user_allows(p_task,target,'task.accept',target,true)
   THEN RAISE EXCEPTION 'TASK_EXECUTOR_TARGET_CONFLICT' USING ERRCODE='40001'; END IF;
   h:=h||jsonb_build_object('previous_user_id',owner_id,'prior_status',CASE WHEN v2 AND t.status='pending_confirm' AND s->'execution_handover'->>'prior_status' IN ('pending_execution','in_progress','deferred') THEN s->'execution_handover'->>'prior_status' ELSE t.status END);
   DELETE FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
   INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility)
    VALUES(p_task,t.workspace_id,target,(person->>'team_id')::uuid,person->>'role_code','owner');
   UPDATE workflow.task_candidate SET decision='declined',decided_at=clock_timestamp(),decision_note=note WHERE task_id=p_task AND decision='pending';
   next_task.accepted_at:=NULL; next_task.target_position:=NULL;
   owner_id:=target;
   s:=s||jsonb_build_object('execution_handover',h,'executor_transferred',true,'responsibility_version',(s->>'responsibility_version')::int+1,'pending_requirements',NULL);
   next_status:='pending_confirm';
  ELSE
   IF target=reviewer_id OR target=owner_id OR NOT workflow.task_execution_user_allows(p_task,target,'task.review',target,true)
   THEN RAISE EXCEPTION 'TASK_REVIEWER_TARGET_INELIGIBLE' USING ERRCODE='42501'; END IF;
   s:=jsonb_set(s,'{reviewer_handover}',h||jsonb_build_object('previous_user_id',reviewer_id));
  END IF;
 ELSIF p_action IN ('respond_executor_handover','respond_reviewer_handover') THEN
  h:=s->CASE WHEN p_action='respond_executor_handover' THEN 'execution_handover' ELSE 'reviewer_handover' END;
  IF h->>'id' IS DISTINCT FROM p_body->>'handover_id' OR h->>'status'<>'pending' THEN RAISE EXCEPTION 'TASK_HANDOVER_CHANGED' USING ERRCODE='40001'; END IF;
  h:=h||jsonb_build_object('status',CASE WHEN p_body->>'decision'='accept' THEN 'accepted' ELSE 'rejected' END,'responded_at',clock_timestamp(),'response_reason',note);
  IF p_action='respond_executor_handover' THEN
   IF a=reviewer_id AND NOT (v2 AND a=t.creator_user_ref_id) THEN RAISE EXCEPTION 'TASK_SELF_REVIEW_FORBIDDEN' USING ERRCODE='42501'; END IF;
   IF p_body->>'decision'='accept' AND NOT workflow.task_execution_user_allows(p_task,a,'task.complete',a,true) THEN RAISE EXCEPTION 'TASK_HANDOVER_TARGET_INELIGIBLE' USING ERRCODE='42501'; END IF;
   s:=jsonb_set(s,'{execution_handover}',h);
   IF p_body->>'decision'='accept' THEN
    next_status:=CASE WHEN v2 AND h->>'prior_status' IN ('pending_execution','in_progress','deferred') THEN h->>'prior_status' ELSE 'pending_execution' END;
    next_task.accepted_at:=clock_timestamp();
    UPDATE workflow.task_assignee SET accepted_at=clock_timestamp() WHERE task_id=p_task AND responsibility='owner';
    IF v2 AND a=t.creator_user_ref_id THEN
     IF s->'reviewer_handover'->>'status'='pending' THEN
      s:=jsonb_set(s,'{reviewer_handover}',s->'reviewer_handover'||jsonb_build_object('status','invalidated','closed_at',clock_timestamp(),'close_reason','self_managed'));
     END IF;
     UPDATE workflow.notification SET status='cancelled' WHERE workspace_id=t.workspace_id AND object_id=p_task AND status NOT IN ('read','cancelled')
      AND ((template_code='task_execution_changed' AND payload->>'action'='initiate_reviewer_handover')
       OR (template_code='task_coordination_required' AND payload->'issues' ? 'reviewer_unavailable'));
    END IF;
   END IF;
  ELSE
   IF p_body->>'decision'='accept' THEN
    IF a=owner_id OR NOT workflow.task_execution_user_allows(p_task,a,'task.review',a,true)
    THEN RAISE EXCEPTION 'TASK_REVIEWER_TARGET_INELIGIBLE' USING ERRCODE='42501'; END IF;
    s:=s||jsonb_build_object('current_reviewer',a,'responsibility_version',(s->>'responsibility_version')::int+1,'pending_requirements',NULL);
   END IF;
   s:=jsonb_set(s,'{reviewer_handover}',h);
  END IF;
 ELSIF p_action='propose_requirements' THEN
  req:=p_body->'requirements';
  IF v2 THEN PERFORM workflow.task_execution_validate_definition_v2(req,'[]'::jsonb); ELSE PERFORM workflow.task_execution_validate_requirements(req); END IF;
  IF req=s->'requirements' THEN changed:=false;
  ELSE
   FOR i IN SELECT value FROM jsonb_array_elements(CASE WHEN v2 THEN '[]'::jsonb ELSE req->'checklist' END) LOOP
    SELECT value INTO previous FROM jsonb_array_elements(s->'requirements'->'checklist') WHERE value->>'item_id'=i->>'item_id';
    IF (previous IS NULL AND (i->>'definition_version')::int<>1) OR
     (previous IS NOT NULL AND (i->>'definition_version')::int<>(previous->>'definition_version')::int+CASE WHEN (i- 'definition_version')<>(previous-'definition_version') THEN 1 ELSE 0 END)
    THEN RAISE EXCEPTION 'TASK_ITEM_DEFINITION_CONFLICT' USING ERRCODE='40001'; END IF;
   END LOOP;
   required:=CASE WHEN t.accepted_at IS NULL AND NOT (s->>'executor_transferred')::boolean THEN jsonb_build_array(reviewer_id)
    WHEN self_task THEN jsonb_build_array(owner_id) ELSE jsonb_build_array(owner_id,reviewer_id) END;
   IF required @> '[null]'::jsonb OR (NOT self_task AND t.accepted_at IS NOT NULL AND owner_id=reviewer_id)
   THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_RESPONSIBILITY_INVALID' USING ERRCODE='40001'; END IF;
   confirmations:=CASE WHEN required @> jsonb_build_array(a) THEN jsonb_build_array(a) ELSE '[]'::jsonb END;
   SELECT COALESCE(jsonb_object_agg(k,jsonb_build_object('before',s->'requirements'->k,'after',req->k)),'{}'::jsonb) INTO diffs
    FROM jsonb_object_keys(req) k WHERE req->k IS DISTINCT FROM s->'requirements'->k;
   change:=jsonb_build_object('id',e,'proposer_id',a,'reason',note,'before',s->'requirements','after',req,'diff',diffs,
    'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version','required_user_ids',required,'confirmed_user_ids',confirmations,'created_at',clock_timestamp());
   s:=jsonb_set(s,'{pending_requirements}',change); apply_req:=confirmations @> required;
  END IF;
 ELSIF p_action IN ('respond_requirements','withdraw_requirements') THEN
  change:=s->'pending_requirements';
  IF change->>'id' IS DISTINCT FROM p_body->>'change_id' OR change->>'responsibility_version' IS DISTINCT FROM s->>'responsibility_version'
   OR change->>'requirements_version' IS DISTINCT FROM s->>'requirements_version'
  THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_CHANGED' USING ERRCODE='40001'; END IF;
  IF p_action='withdraw_requirements' THEN s:=jsonb_set(s,'{pending_requirements}','null'::jsonb);
  ELSE
   IF NOT (change->'required_user_ids' @> jsonb_build_array(a)) THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_CONFIRMATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
   IF p_body->>'decision'='reject' THEN s:=jsonb_set(s,'{pending_requirements}','null'::jsonb);
   ELSE
    changed:=NOT (change->'confirmed_user_ids' @> jsonb_build_array(a));
    change:=jsonb_set(change,'{confirmed_user_ids}',CASE WHEN changed THEN change->'confirmed_user_ids'||jsonb_build_array(a) ELSE change->'confirmed_user_ids' END);
    s:=jsonb_set(s,'{pending_requirements}',change); apply_req:=(change->'confirmed_user_ids') @> (change->'required_user_ids');
   END IF;
  END IF;
 ELSIF p_action='submit_completion' THEN
  IF v2 AND NOT self_task AND owner_id=reviewer_id THEN RAISE EXCEPTION 'TASK_REVIEWER_HANDOVER_REQUIRED' USING ERRCODE='40001'; END IF;
  IF NOT v2 AND NOT self_task AND (owner_id=reviewer_id OR workflow.task_execution_person(p_task,reviewer_id) IS NULL
   OR NOT workflow.task_execution_user_allows(p_task,reviewer_id,'task.review',reviewer_id,true))
  THEN RAISE EXCEPTION 'TASK_REVIEWER_HANDOVER_REQUIRED' USING ERRCODE='40001'; END IF;
  IF v2 AND length(COALESCE(p_body->>'missing_deliverables_resolution',''))>4000 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
  IF v2 AND length(btrim(COALESCE(s->>'missing_deliverables','')))>0 AND length(btrim(COALESCE(p_body->>'missing_deliverables_resolution',''))) NOT BETWEEN 1 AND 4000
  THEN RAISE EXCEPTION 'TASK_MISSING_DELIVERABLES_RESOLUTION_REQUIRED' USING ERRCODE='22023'; END IF;
  -- The existing declaration function checks metric links/precision and writes
  -- history at the single upcoming task version. It never books KR/financial actuals.
  IF p_body ? 'actual_contribution' THEN
   PERFORM security.record_task_contribution(p_task,p_body->'actual_contribution',t.version_no);
  END IF;
  SELECT actual_contribution INTO contribution FROM workflow.task_alignment WHERE task_id=p_task;
  sid:=gen_random_uuid();
  submission_snapshot:=jsonb_build_object('requirements',s->'requirements','items',s->'items','note',note,'actual_contribution',contribution,'missing_deliverables',s->>'missing_deliverables','missing_deliverables_resolution',p_body->>'missing_deliverables_resolution',
   'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version','checklist_revision',s->'checklist_revision','executor_user_id',owner_id,'reviewer_user_id',reviewer_id);
  s:=jsonb_set(s,'{current_submission}',to_jsonb(sid)); next_status:=CASE WHEN self_task THEN 'completed' ELSE 'pending_review' END;
  event_kind:=CASE WHEN self_task THEN 'complete' ELSE 'submit_completion' END;
  next_task.completion_note:=note; next_task.completion_submitted_at:=clock_timestamp(); next_task.completion_review_note:=NULL;
  next_task.verification_status:=CASE WHEN self_task THEN 'not_required' ELSE 'pending' END;
  next_task.completed_at:=CASE WHEN self_task THEN clock_timestamp() ELSE NULL END;
 ELSIF p_action IN ('review_completion','withdraw_completion') THEN
  IF s->>'current_submission' IS NULL THEN
   -- A migrated pending-review task has a real legacy submission, even without
   -- a checklist. Freeze it once and retain that evidence after a rejection.
   IF t.status<>'pending_review' OR t.completion_submitted_at IS NULL OR length(COALESCE(t.completion_note,''))=0
   THEN RAISE EXCEPTION 'TASK_SUBMISSION_MISSING' USING ERRCODE='40001'; END IF;
   sid:=p_task;
   INSERT INTO workflow.task_execution_submission(id,task_id,workspace_id,actor_user_ref_id,snapshot)
    VALUES(sid,p_task,t.workspace_id,owner_id,jsonb_build_object('legacy',true,'note',t.completion_note,'submitted_at',t.completion_submitted_at,'requirements',s->'requirements','items',s->'items'));
   s:=jsonb_set(s,'{current_submission}',to_jsonb(sid));
  END IF;
  IF s->>'current_submission' IS DISTINCT FROM p_body->>'submission_id' THEN RAISE EXCEPTION 'TASK_SUBMISSION_CHANGED' USING ERRCODE='40001'; END IF;
  IF p_action='withdraw_completion' THEN
   IF NOT EXISTS(SELECT 1 FROM workflow.task_execution_submission WHERE id=(s->>'current_submission')::uuid AND actor_user_ref_id=a)
   THEN RAISE EXCEPTION 'TASK_SUBMISSION_WITHDRAW_FORBIDDEN' USING ERRCODE='42501'; END IF;
   next_status:='in_progress'; s:=jsonb_set(s,'{current_submission}','null'::jsonb);
  ELSE
   IF p_body->>'decision' IS NULL OR p_body->>'decision' NOT IN ('approve','reject') OR (p_body->>'decision'='reject' AND length(note)=0)
   THEN RAISE EXCEPTION 'TASK_REVIEW_DECISION_INVALID' USING ERRCODE='22023'; END IF;
   IF p_body->>'decision'='approve' THEN next_status:='completed'; event_kind:='approve_completion';
   ELSE
    next_status:='in_progress'; event_kind:='reject_completion';
    IF v2 THEN
     IF length(COALESCE(p_body->>'missing_deliverables',''))>4000 OR (jsonb_array_length(COALESCE(p_body->'rework_item_ids','[]'))=0 AND length(btrim(COALESCE(p_body->>'missing_deliverables','')))=0)
     THEN RAISE EXCEPTION 'TASK_REWORK_ITEMS_REQUIRED' USING ERRCODE='22023'; END IF;
     s:=jsonb_set(s,'{missing_deliverables}',to_jsonb(COALESCE(p_body->>'missing_deliverables','')));
     IF length(btrim(COALESCE(p_body->>'missing_deliverables','')))>0 THEN
      s:=jsonb_set(s,'{items}',COALESCE((SELECT jsonb_agg(value||jsonb_build_object('requires_revalidation',COALESCE((value->>'requires_revalidation')::boolean,false) OR value->>'state'='completed')) FROM jsonb_array_elements(s->'items')),'[]'::jsonb));
     END IF;
    END IF;
    IF NOT v2 AND jsonb_array_length(s->'items')>0 AND jsonb_array_length(COALESCE(p_body->'rework_item_ids','[]'))=0
    THEN RAISE EXCEPTION 'TASK_REWORK_ITEMS_REQUIRED' USING ERRCODE='22023'; END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(p_body->'rework_item_ids','[]')) x WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s->'items') y WHERE y->>'item_id'=x))
    THEN RAISE EXCEPTION 'TASK_REWORK_ITEM_INVALID' USING ERRCODE='22023'; END IF;
    s:=jsonb_set(s,'{items}',COALESCE((SELECT jsonb_agg(CASE WHEN p_body->'rework_item_ids' @> jsonb_build_array(value->>'item_id')
      THEN value||jsonb_build_object('state','rework','requires_revalidation',false,'review_note',note) ELSE value END) FROM jsonb_array_elements(s->'items')),'[]'));
    s:=jsonb_set(s,'{current_submission}','null'::jsonb);
   END IF;
   next_task.completion_review_note:=note;
   next_task.verification_status:=CASE WHEN next_status='completed' THEN 'passed' ELSE 'rejected' END;
   next_task.completed_at:=CASE WHEN next_status='completed' THEN clock_timestamp() ELSE NULL END;
  END IF;
 ELSIF p_action IN ('accept_task','decline_task') THEN
  IF p_action='accept_task' THEN
   IF owner_id IS NULL THEN
    SELECT jsonb_build_object('role_code',role_code,'team_id',team_id) INTO person FROM workflow.task_candidate WHERE task_id=p_task AND user_ref_id=a AND decision='pending';
    IF person IS NULL OR NOT security.task_recipient_eligible(p_task,a,person->>'role_code',(person->>'team_id')::uuid)
    THEN RAISE EXCEPTION 'TASK_CANDIDATE_INELIGIBLE' USING ERRCODE='42501'; END IF;
    UPDATE workflow.task_candidate SET decision='claimed',decided_at=clock_timestamp() WHERE task_id=p_task AND user_ref_id=a;
    INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility,accepted_at)
     VALUES(p_task,t.workspace_id,a,(person->>'team_id')::uuid,person->>'role_code','owner',clock_timestamp());
   ELSIF owner_id<>a THEN RAISE EXCEPTION 'TASK_OWNER_REQUIRED' USING ERRCODE='42501'; END IF;
   next_status:='pending_execution'; next_task.accepted_at:=clock_timestamp();
   UPDATE workflow.task_assignee SET accepted_at=clock_timestamp() WHERE task_id=p_task AND responsibility='owner';
  ELSE
   IF owner_id=a THEN next_status:='cancelled';
   ELSE
    UPDATE workflow.task_candidate SET decision='declined',decided_at=clock_timestamp(),decision_note=note WHERE task_id=p_task AND user_ref_id=a AND decision='pending';
    IF NOT FOUND THEN RAISE EXCEPTION 'TASK_CANDIDATE_CHANGED' USING ERRCODE='40001'; END IF;
    IF NOT EXISTS(SELECT 1 FROM workflow.task_candidate WHERE task_id=p_task AND decision='pending') THEN next_status:='cancelled'; END IF;
   END IF;
  END IF;
 END IF;

 IF apply_req THEN
  change:=s->'pending_requirements'; req:=change->'after'; items:='[]'::jsonb;
  FOR target IN SELECT value::uuid FROM jsonb_array_elements_text(change->'required_user_ids') LOOP
   IF workflow.task_execution_person(p_task,target) IS NULL OR NOT workflow.task_execution_user_allows(p_task,target,'task.read',target,true)
    OR NOT workflow.task_execution_user_allows(p_task,target,CASE WHEN target=owner_id THEN 'task.complete' ELSE 'task.review' END,target,true)
   THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_CONFIRMATION_EXPIRED' USING ERRCODE='40001'; END IF;
  END LOOP;
  IF v2 THEN
   items:=COALESCE((SELECT jsonb_agg(value||jsonb_build_object('requires_revalidation',COALESCE((value->>'requires_revalidation')::boolean,false) OR (value->>'state'='completed' AND (req-ARRAY['due_at','priority_code']) IS DISTINCT FROM ((s->'requirements')-ARRAY['due_at','priority_code'])))) FROM jsonb_array_elements(s->'items')),'[]'::jsonb);
  ELSE
  FOR i IN SELECT value FROM jsonb_array_elements(req->'checklist') LOOP
   SELECT value INTO previous FROM jsonb_array_elements(s->'items') WHERE value->>'item_id'=i->>'item_id';
   IF previous IS NULL THEN i:=i||jsonb_build_object('state','not_started','note','','requires_revalidation',false);
   ELSE
    i:=previous||i||jsonb_build_object('requires_revalidation',COALESCE((previous->>'requires_revalidation')::boolean,false)
     OR ((previous->>'state'='completed') AND (i->>'definition_version'<>previous->>'definition_version'
      OR (req-ARRAY['checklist','due_at','priority_code'])<>((s->'requirements')-ARRAY['checklist','due_at','priority_code']))));
   END IF;
   items:=items||jsonb_build_array(i);
  END LOOP;
  END IF;
  s:=s||jsonb_build_object('requirements',req,'items',items,'requirements_version',(s->>'requirements_version')::int+1,'pending_requirements',NULL);
  next_task.title:=req->>'title'; next_task.description:=req->>'description';
  next_task.due_at:=(req->>'due_at')::timestamptz; next_task.priority_code:=req->>'priority_code';
 END IF;
 IF changed THEN
  -- The baseline touch trigger increments once per UPDATE; stage all command
  -- metadata above and persist the task exactly once, then use its actual version.
  UPDATE workflow.task SET status=next_status,version_no=version_no+1,updated_at=clock_timestamp(),
   accepted_at=next_task.accepted_at,target_position=next_task.target_position,
   title=next_task.title,description=next_task.description,due_at=next_task.due_at,priority_code=next_task.priority_code,
   completion_note=next_task.completion_note,completion_submitted_at=next_task.completion_submitted_at,
   completion_review_note=next_task.completion_review_note,verification_status=next_task.verification_status,
   completed_at=next_task.completed_at
  WHERE id=p_task RETURNING version_no INTO committed_version;
  IF submission_snapshot IS NOT NULL THEN
   INSERT INTO workflow.task_execution_submission(id,task_id,workspace_id,actor_user_ref_id,snapshot)
    VALUES(sid,p_task,t.workspace_id,a,submission_snapshot||jsonb_build_object('version_no',committed_version));
  END IF;
  IF next_status IN ('completed','cancelled') THEN
   s:=s||jsonb_build_object('pause',NULL,'pending_requirements',NULL);
   IF v2 THEN s:=s||jsonb_build_object('execution_handover',CASE WHEN s->'execution_handover'->>'status' IN ('pending','rejected') THEN s->'execution_handover'||jsonb_build_object('status','invalidated','closed_at',clock_timestamp()) ELSE s->'execution_handover' END,
    'reviewer_handover',CASE WHEN s->'reviewer_handover'->>'status'='pending' THEN s->'reviewer_handover'||jsonb_build_object('status','invalidated','closed_at',clock_timestamp()) ELSE s->'reviewer_handover' END); END IF;
  END IF;
  UPDATE workflow.task_execution SET state=s,updated_at=clock_timestamp() WHERE task_id=p_task;
  INSERT INTO workflow.task_execution_event(id,task_id,workspace_id,actor_user_ref_id,kind,body)
   VALUES(e,p_task,t.workspace_id,a,p_action,jsonb_build_object('command',p_body,'before',old_s,'after',s,'from_status',t.status,'to_status',next_status,
    'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'version_no',committed_version));
  INSERT INTO workflow.task_event(id,workspace_id,task_id,event_type,from_status,to_status,actor_user_ref_id,note,payload)
   VALUES(e,t.workspace_id,p_task,event_kind,t.status,next_status,a,note,jsonb_build_object('execution_protocol',CASE WHEN v2 THEN 2 ELSE 1 END,'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version','submission_id',COALESCE(s->'current_submission',old_s->'current_submission')));
  -- In-app/outbox notification, in the same transaction; no provider invocation.
  IF p_action NOT IN ('set_item_progress','save_checklist','append_feedback') THEN
   FOR target IN SELECT DISTINCT id FROM (VALUES(owner_id),(CASE WHEN v2 AND owner_id=t.creator_user_ref_id THEN NULL ELSE (s->>'current_reviewer')::uuid END),((s->'execution_handover'->>'target_user_id')::uuid),(CASE WHEN s->'reviewer_handover'->>'status'='pending' THEN (s->'reviewer_handover'->>'target_user_id')::uuid ELSE NULL END)) r(id)
    WHERE id IS NOT NULL AND id<>a LOOP
    IF workflow.task_execution_person(p_task,target) IS NOT NULL AND workflow.task_execution_user_allows(p_task,target,'task.read',target,true) THEN
     INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,channel_code,template_code,title,body,
      object_type,object_id,status,dedupe_key,payload)
     VALUES(t.workspace_id,target,'in_app','task_execution_changed','任务有新的处理事项',t.title,'task',p_task,'pending',
      'task_execution:'||e::text||':'||target::text,jsonb_build_object('task_id',p_task,'event_id',e,'action',p_action,'event_version',committed_version))
     ON CONFLICT(workspace_id,recipient_user_ref_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING;
    END IF;
   END LOOP;
  END IF;
 END IF;
 result:=workflow.task_execution_read(p_task);
 response:=jsonb_build_object('changed',changed,'event_id',CASE WHEN changed THEN e ELSE NULL END,'version_no',result->'version_no',
 'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version','checklist_revision',s->'checklist_revision','execution',result);
 INSERT INTO workflow.task_execution_request(task_id,workspace_id,actor_user_ref_id,key,action,body,response) VALUES(p_task,t.workspace_id,a,p_key,p_action,p_body,response);
 RETURN response;
END $$;

CREATE OR REPLACE FUNCTION workflow.guard_task_execution_legacy_write() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE command_owner name; guarded boolean;
BEGIN
 SELECT pg_get_userbyid(proowner) INTO command_owner FROM pg_proc WHERE oid='workflow.task_execution_command(uuid,text,jsonb,uuid)'::regprocedure;
 IF current_user=command_owner THEN RETURN COALESCE(NEW,OLD); END IF;
 IF TG_TABLE_NAME='task' THEN
  IF TG_OP='INSERT' THEN
   IF NEW.execution_mode<>'legacy' OR NEW.execution_origin_xid<>pg_current_xact_id() THEN RAISE EXCEPTION 'TASK_EXECUTION_MODE_IMMUTABLE' USING ERRCODE='40001'; END IF;
   RETURN NEW;
  END IF;
  IF NEW.execution_mode IS DISTINCT FROM OLD.execution_mode OR NEW.execution_origin_xid IS DISTINCT FROM OLD.execution_origin_xid
  THEN RAISE EXCEPTION 'TASK_EXECUTION_MODE_IMMUTABLE' USING ERRCODE='40001'; END IF;
  IF OLD.execution_mode='legacy' THEN RETURN NEW; END IF;
  IF OLD.execution_mode='execution_v2' AND NEW.status='cancelled' AND NEW.status IS DISTINCT FROM OLD.status THEN RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001'; END IF;
 ELSE
  -- Inspect both old and new parents when an assignee is moved between tasks.
  guarded:=false;
  IF TG_OP<>'DELETE' THEN
   SELECT execution_mode IN ('execution_v1','execution_v2') INTO guarded FROM workflow.task WHERE id=NEW.task_id;
  END IF;
  IF TG_OP<>'INSERT' THEN
   guarded:=COALESCE(guarded,false) OR EXISTS(SELECT 1 FROM workflow.task WHERE id=OLD.task_id AND execution_mode IN ('execution_v1','execution_v2'));
  END IF;
  IF NOT COALESCE(guarded,false) THEN RETURN COALESCE(NEW,OLD); END IF;
 END IF;
 IF TG_TABLE_NAME='task' THEN
  IF NEW.title IS DISTINCT FROM OLD.title OR NEW.description IS DISTINCT FROM OLD.description OR NEW.due_at IS DISTINCT FROM OLD.due_at
   OR NEW.priority_code IS DISTINCT FROM OLD.priority_code OR NEW.creator_user_ref_id IS DISTINCT FROM OLD.creator_user_ref_id
   OR NEW.accepted_at IS DISTINCT FROM OLD.accepted_at OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
   OR NEW.completion_note IS DISTINCT FROM OLD.completion_note OR NEW.completion_review_note IS DISTINCT FROM OLD.completion_review_note
   OR NEW.completion_submitted_at IS DISTINCT FROM OLD.completion_submitted_at OR NEW.verification_status IS DISTINCT FROM OLD.verification_status
   OR NEW.target_position IS DISTINCT FROM OLD.target_position OR (NEW.status IS DISTINCT FROM OLD.status AND NEW.status<>'cancelled')
  THEN RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001'; END IF;
 ELSIF TG_TABLE_NAME='task_assignee' THEN
  IF TG_OP='INSERT' THEN
   IF NEW.responsibility='owner' AND NOT security.authorization_task_created(NEW.task_id)
   THEN RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001'; END IF;
  ELSIF OLD.responsibility='owner' OR (TG_OP='UPDATE' AND NEW.responsibility='owner') THEN
   -- Protect both sides: demoting/moving/deleting an owner is also an ownership
   -- change, even when the replacement row's responsibility is not owner.
   RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001';
  END IF;
 ELSIF TG_TABLE_NAME='task_event' THEN
  IF NEW.event_type IN ('complete','submit_completion','approve_completion','reject_completion','reassign') THEN
   RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001';
  END IF;
 END IF;
 RETURN COALESCE(NEW,OLD);
END $$;

CREATE OR REPLACE FUNCTION workflow.require_task_execution_initialization() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (SELECT execution_mode FROM workflow.task WHERE id=NEW.id)='legacy' THEN RETURN NEW; END IF;
 IF NOT EXISTS(SELECT 1 FROM workflow.task_execution WHERE task_id=NEW.id AND (jsonb_array_length(state->'items')>=1 OR (state->>'protocol_version'='2' AND jsonb_typeof(state->'items')='array')))
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INITIAL_CHECKLIST_REQUIRED' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION workflow.close_task_execution_on_cancel() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s jsonb;
BEGIN
 IF NEW.execution_mode IN ('execution_v1','execution_v2') AND NEW.status='cancelled' AND OLD.status<>'cancelled' THEN
  SELECT state INTO s FROM workflow.task_execution WHERE task_id=NEW.id;
  IF s IS NOT NULL THEN
   INSERT INTO workflow.task_execution_event(task_id,workspace_id,actor_user_ref_id,kind,body)
    VALUES(NEW.id,NEW.workspace_id,common.current_user_ref_id(),'cancel_flows',jsonb_build_object('before',s,'closed_by','cancel'));
   UPDATE workflow.task_execution SET state=state||jsonb_build_object('pause',NULL,'pending_requirements',NULL,
    'execution_handover',CASE WHEN state->>'execution_handover' IS NOT NULL THEN state->'execution_handover'||'{"status":"invalidated"}'::jsonb ELSE NULL END,
    'reviewer_handover',CASE WHEN state->>'reviewer_handover' IS NOT NULL THEN state->'reviewer_handover'||'{"status":"invalidated"}'::jsonb ELSE NULL END),updated_at=clock_timestamp() WHERE task_id=NEW.id;
  END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION workflow.task_execution_coordination_status(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET app.workspace_id='' AS $$
DECLARE t workflow.task; s jsonb; owner_id uuid; reviewer_id uuid; person_id uuid; owner_count int; result jsonb:='{}'::jsonb; self_task boolean;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND deleted_at IS NULL AND execution_mode='execution_v2' AND status NOT IN ('completed','cancelled');
 IF NOT FOUND THEN RETURN result; END IF;
 PERFORM set_config('app.workspace_id',t.workspace_id::text,true);
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 SELECT count(*) INTO owner_count FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' LIMIT 1;
 reviewer_id:=(s->>'current_reviewer')::uuid; self_task:=owner_count=1 AND owner_id=t.creator_user_ref_id;
 IF t.status<>'pending_review' AND t.target_position IS NULL AND (owner_count<>1 OR workflow.task_execution_person(p_task,owner_id) IS NULL
  OR NOT workflow.task_execution_user_allows(p_task,owner_id,CASE WHEN t.accepted_at IS NULL THEN 'task.accept' ELSE 'task.complete' END,owner_id,true))
 THEN result:=result||'{"executor_unavailable":true}'::jsonb; END IF;
 IF NOT COALESCE(self_task,false) AND (reviewer_id IS NULL OR reviewer_id=owner_id OR workflow.task_execution_person(p_task,reviewer_id) IS NULL
  OR NOT workflow.task_execution_user_allows(p_task,reviewer_id,'task.review',reviewer_id,true))
 THEN result:=result||'{"reviewer_unavailable":true}'::jsonb; END IF;
 IF s->'execution_handover'->>'status'='rejected' THEN result:=result||'{"executor_handover_rejected":true}'::jsonb; END IF;
 IF s->'reviewer_handover'->>'status'='pending' THEN
  person_id:=(s->'reviewer_handover'->>'target_user_id')::uuid;
  IF workflow.task_execution_person(p_task,person_id) IS NULL OR person_id=owner_id
   OR NOT workflow.task_execution_user_allows(p_task,person_id,'task.review',person_id,true)
  THEN result:=result||'{"reviewer_handover_unavailable":true}'::jsonb; END IF;
 END IF;
 IF s->'reviewer_handover'->>'status'='rejected' THEN result:=result||'{"reviewer_handover_rejected":true}'::jsonb; END IF;
 IF s->>'pending_requirements' IS NOT NULL THEN
  FOR person_id IN SELECT value::uuid FROM jsonb_array_elements_text(s->'pending_requirements'->'required_user_ids') LOOP
   IF workflow.task_execution_person(p_task,person_id) IS NULL OR NOT workflow.task_execution_user_allows(p_task,person_id,CASE WHEN person_id=owner_id THEN 'task.complete' ELSE 'task.review' END,person_id,true)
   THEN result:=result||'{"requirements_confirmation_unavailable":true}'::jsonb; END IF;
  END LOOP;
 END IF;
 RETURN result;
END $$;

CREATE FUNCTION workflow.task_execution_coordination_recipients(p_task uuid) RETURNS SETOF uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET app.workspace_id='' AS $$
DECLARE t workflow.task; target uuid;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND deleted_at IS NULL AND execution_mode='execution_v2' AND status NOT IN ('completed','cancelled');
 IF NOT FOUND OR workflow.task_execution_coordination_status(p_task)='{}'::jsonb THEN RETURN; END IF;
 PERFORM set_config('app.workspace_id',t.workspace_id::text,true);
 FOR target IN SELECT id FROM platform.user_ref WHERE workspace_id=t.workspace_id AND status='active' AND deleted_at IS NULL LOOP
  IF workflow.task_execution_person(p_task,target) IS NOT NULL
   AND workflow.task_execution_user_allows(p_task,target,'task.read',t.creator_user_ref_id,target=t.creator_user_ref_id)
   AND (workflow.task_execution_user_allows(p_task,target,'task.coordinate',t.creator_user_ref_id,target=t.creator_user_ref_id)
    OR workflow.task_execution_user_allows(p_task,target,'task.reviewer_handover',t.creator_user_ref_id,target=t.creator_user_ref_id))
  THEN RETURN NEXT target; END IF;
 END LOOP;
END $$;

-- Private scheduling cursor contains no customer/task payload. No runtime role
-- gets table access. A task UUID cursor avoids rescanning every task/user per tick.
CREATE TABLE ops.task_coordination_scan_cursor (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 last_task_id uuid,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON ops.task_coordination_scan_cursor FROM PUBLIC,salegent_feishu_worker;
CREATE INDEX task_execution_v2_scan ON workflow.task(id) WHERE execution_mode='execution_v2';

-- Bounded worker entrypoint: select at most p_limit tasks BEFORE checking people
-- or alerts. Include terminal/deleted tasks to retire their obsolete notices.
-- Cursor and alerts commit together; a failed tick rolls back and retries the
-- same page. UUID wraparound eventually revisits each task, including new tasks.
CREATE FUNCTION workflow.task_execution_enqueue_coordination_checks(p_limit integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row record; target uuid; issues jsonb; stamp text; inserted int; total int:=0; cursor_id uuid; last_id uuid; recipients uuid[];
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'TASK_COORDINATION_LIMIT_INVALID' USING ERRCODE='22023'; END IF;
 INSERT INTO ops.task_coordination_scan_cursor(singleton) VALUES(true) ON CONFLICT DO NOTHING;
 SELECT last_task_id INTO cursor_id FROM ops.task_coordination_scan_cursor WHERE singleton FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN 0; END IF;
 FOR row IN SELECT id,workspace_id,title FROM workflow.task
  WHERE execution_mode='execution_v2' AND (cursor_id IS NULL OR id>cursor_id)
  ORDER BY id LIMIT p_limit FOR UPDATE SKIP LOCKED
 LOOP
  last_id:=row.id;
  issues:=workflow.task_execution_coordination_status(row.id);
  SELECT state->>'responsibility_version' INTO stamp FROM workflow.task_execution WHERE task_id=row.id;
  recipients:=ARRAY(SELECT workflow.task_execution_coordination_recipients(row.id));
  UPDATE workflow.notification n SET status='cancelled'
   WHERE n.object_id=row.id AND n.workspace_id=row.workspace_id AND n.template_code='task_coordination_required' AND n.status<>'cancelled'
   AND (issues='{}'::jsonb OR n.payload->'issues' IS DISTINCT FROM issues
    OR n.payload->>'responsibility_version' IS DISTINCT FROM stamp OR NOT(n.recipient_user_ref_id=ANY(recipients)));
  IF issues='{}'::jsonb THEN CONTINUE; END IF;
  FOREACH target IN ARRAY recipients LOOP
   IF EXISTS(SELECT 1 FROM workflow.notification n WHERE n.workspace_id=row.workspace_id AND n.object_id=row.id
    AND n.template_code='task_coordination_required' AND n.recipient_user_ref_id=target AND n.status<>'cancelled'
    AND n.payload->'issues'=issues AND n.payload->>'responsibility_version'=stamp) THEN CONTINUE; END IF;
   INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,channel_code,template_code,title,body,object_type,object_id,status,dedupe_key,payload)
    VALUES(row.workspace_id,target,'in_app','task_coordination_required','任务责任需要协调',row.title,'task',row.id,'pending',
     'task_coordination:'||row.id::text||':'||stamp||':'||md5(issues::text)||':'||target::text||':'||gen_random_uuid()::text,
     jsonb_build_object('task_id',row.id,'issues',issues,'responsibility_version',stamp,'action','coordinate','monitored_at',clock_timestamp()));
   GET DIAGNOSTICS inserted=ROW_COUNT; total:=total+inserted;
  END LOOP;
 END LOOP;
 -- An exhausted page resets the cursor; the next tick begins the next cycle.
 UPDATE ops.task_coordination_scan_cursor SET last_task_id=last_id,updated_at=clock_timestamp() WHERE singleton;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION workflow.task_execution_coordination_status(uuid),workflow.task_execution_coordination_recipients(uuid),
 workflow.task_execution_enqueue_coordination_checks(integer) FROM PUBLIC,salegent_feishu_worker;


-- New helpers remain owner-only. Only the explicit V2 initializer is added to
-- the API role's installation allowlist; all existing function ACLs are retained.
REVOKE ALL ON FUNCTION workflow.task_execution_validate_definition_v2(jsonb,jsonb),
 workflow.task_execution_initialize_v2(uuid,jsonb,boolean),workflow.task_execution_adopt_v2(uuid,jsonb,uuid)
 FROM PUBLIC,salegent_feishu_worker;
INSERT INTO ops.schema_migration(version,description) VALUES('V433','Explicit task V2 autonomous checklist self ownership completion and consistent handover');
COMMIT;
