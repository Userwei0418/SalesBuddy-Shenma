BEGIN;

-- A reviewer handover ends the self-created/self-assigned exception.  The
-- executor remains the original owner, but a different current reviewer must
-- confirm requirement changes and approve completion.  V410 determined
-- self_task from the creator/owner relationship only, which let the original
-- owner keep the self-review shortcut after reviewer responsibility moved.
-- Keep the command contract and all existing history unchanged; this migration
-- only replaces the atomic command with the corrected guard.


CREATE OR REPLACE FUNCTION workflow.task_execution_command(p_task uuid,p_action text,p_body jsonb,p_key uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; next_task workflow.task; committed_version integer; submission_snapshot jsonb; s jsonb; old_s jsonb; a uuid:=common.current_user_ref_id(); owner_id uuid; reviewer_id uuid;
 prior workflow.task_execution_request; cap jsonb; e uuid:=gen_random_uuid(); note text; target uuid; person jsonb;
 h jsonb; change jsonb; i jsonb; previous jsonb; items jsonb; required jsonb; confirmations jsonb;
 req jsonb; diffs jsonb; contribution jsonb; result jsonb; response jsonb; sid uuid; next_status text; event_kind text:=p_action;
 changed boolean:=true; apply_req boolean:=false; self_task boolean; permission text; allowed text[];
BEGIN
 IF p_key IS NULL THEN RAISE EXCEPTION 'TASK_EXECUTION_IDEMPOTENCY_KEY_REQUIRED' USING ERRCODE='22023'; END IF;
 -- Null and omission have the pre-contribution command identity, including direct SQL callers.
 IF p_action='submit_completion' AND (p_body->'actual_contribution' IS NULL OR p_body->'actual_contribution'='null'::jsonb)
 THEN p_body:=p_body-'actual_contribution'; END IF;
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.authorization_task('task.read',p_task) OR workflow.task_execution_person(p_task,a) IS NULL
 THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF t.execution_mode<>'execution_v1' THEN RAISE EXCEPTION 'TASK_EXECUTION_LEGACY_MODE' USING ERRCODE='40001'; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_EXECUTION_NOT_INITIALIZED' USING ERRCODE='40001'; END IF;
 old_s:=s; next_task:=t; next_status:=t.status; reviewer_id:=(s->>'current_reviewer')::uuid;
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 self_task:=a=owner_id AND owner_id=t.creator_user_ref_id AND reviewer_id=owner_id AND NOT (s->>'executor_transferred')::boolean;
 permission:=CASE WHEN p_action IN ('set_item_progress','submit_completion','withdraw_completion') THEN 'task.complete'
 WHEN p_action='append_feedback' THEN 'task.feedback' WHEN p_action='initiate_executor_handover' THEN 'task.coordinate'
 WHEN p_action='initiate_reviewer_handover' THEN 'task.reviewer_handover'
 WHEN p_action IN ('review_completion','respond_reviewer_handover') THEN 'task.review'
 WHEN p_action IN ('accept_task','respond_executor_handover') THEN 'task.accept'
 WHEN p_action='decline_task' THEN 'task.decline' ELSE NULL END;
 IF permission IS NOT NULL AND NOT (CASE WHEN p_action='respond_reviewer_handover' THEN workflow.task_execution_user_allows(p_task,a,'task.review',a,true) ELSE security.authorization_task(permission,p_task) END)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 -- Authorization is checked before replay. Replay may return a terminal receipt,
 -- but a revoked participant cannot use it as an authorization/cache bypass.
 IF (p_action IN ('set_item_progress','submit_completion','withdraw_completion') AND a IS DISTINCT FROM owner_id)
 OR (p_action='review_completion' AND (a IS DISTINCT FROM reviewer_id OR a=owner_id))
 OR (p_action='respond_executor_handover' AND a IS DISTINCT FROM (s->'execution_handover'->>'target_user_id')::uuid)
 OR (p_action='respond_reviewer_handover' AND a IS DISTINCT FROM (s->'reviewer_handover'->>'target_user_id')::uuid)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_RESPONSIBILITY_CHANGED' USING ERRCODE='42501'; END IF;
 IF p_action='append_feedback' AND NOT (a=owner_id OR a=reviewer_id OR security.authorization_task('task.coordinate',p_task)
  OR EXISTS(SELECT 1 FROM workflow.task_assignee WHERE task_id=p_task AND assignee_user_ref_id=a))
 THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF p_action='respond_requirements' AND NOT ((a=owner_id AND security.authorization_task('task.complete',p_task))
  OR (a=reviewer_id AND security.authorization_task('task.review',p_task)))
 THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_CONFIRMATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM workflow.task_execution_request WHERE workspace_id=t.workspace_id AND key=p_key;
 IF FOUND THEN
  IF prior.task_id<>p_task OR prior.actor_user_ref_id<>a OR prior.action<>p_action OR prior.body<>p_body
  THEN RAISE EXCEPTION 'TASK_EXECUTION_IDEMPOTENCY_CONFLICT' USING ERRCODE='40001'; END IF;
  -- General commands retain a fresh action grant, not only task visibility.
  IF permission IS NULL AND NOT (security.authorization_task('task.coordinate',p_task)
   OR (a=owner_id AND security.authorization_task('task.complete',p_task))
   OR (a=reviewer_id AND security.authorization_task('task.review',p_task)))
  THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
  RETURN prior.response;
 END IF;
 allowed:=CASE p_action
 WHEN 'set_item_progress' THEN ARRAY['item_id','definition_version','state','note']
 WHEN 'append_feedback' THEN ARRAY['text','reply_to_event_id','corrects_event_id']
 WHEN 'pause' THEN ARRAY['reason','recheck_at'] WHEN 'recheck_pause' THEN ARRAY['pause_id','reason','recheck_at']
 WHEN 'resume' THEN ARRAY['pause_id','reason']
 WHEN 'initiate_executor_handover' THEN ARRAY['target_user_id','reason'] WHEN 'initiate_reviewer_handover' THEN ARRAY['target_user_id','reason']
 WHEN 'respond_executor_handover' THEN ARRAY['handover_id','decision','reason'] WHEN 'respond_reviewer_handover' THEN ARRAY['handover_id','decision','reason']
 WHEN 'propose_requirements' THEN ARRAY['requirements','reason'] WHEN 'respond_requirements' THEN ARRAY['change_id','decision','reason']
 WHEN 'withdraw_requirements' THEN ARRAY['change_id','reason'] WHEN 'submit_completion' THEN ARRAY['note','actual_contribution']
 WHEN 'withdraw_completion' THEN ARRAY['submission_id','reason'] WHEN 'review_completion' THEN ARRAY['submission_id','decision','note','rework_item_ids']
 WHEN 'accept_task' THEN ARRAY['reason'] WHEN 'decline_task' THEN ARRAY['reason'] ELSE NULL END;
 IF allowed IS NULL OR jsonb_typeof(p_body)<>'object' OR p_body-(allowed||ARRAY['version_no','requirements_version','responsibility_version'])<>'{}'::jsonb
 OR EXISTS(SELECT 1 FROM unnest(ARRAY['version_no','requirements_version','responsibility_version']) v WHERE jsonb_typeof(p_body->v) IS DISTINCT FROM 'number' OR (p_body->>v)!~'^[1-9][0-9]*$')
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
 IF (p_body->>'version_no')::int<>t.version_no OR (p_body->>'requirements_version')::int<>(s->>'requirements_version')::int
 OR (p_body->>'responsibility_version')::int<>(s->>'responsibility_version')::int
 THEN RAISE EXCEPTION 'TASK_EXECUTION_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 cap:=workflow.task_execution_capabilities(p_task);
 IF NOT COALESCE((cap->>p_action)::boolean,false) THEN RAISE EXCEPTION 'TASK_EXECUTION_ACTION_UNAVAILABLE' USING ERRCODE='40001'; END IF;
 note:=btrim(COALESCE(p_body->>'reason',p_body->>'note',p_body->>'text',''));
 IF p_action NOT IN ('accept_task','respond_executor_handover','respond_reviewer_handover','respond_requirements','review_completion') AND (length(note)<1 OR length(note)>4000)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_REASON_REQUIRED' USING ERRCODE='22023'; END IF;
 IF p_action IN ('respond_executor_handover','respond_reviewer_handover','respond_requirements') AND
  (p_body->>'decision' IS NULL OR p_body->>'decision' NOT IN ('accept','reject') OR (p_body->>'decision'='reject' AND length(note)=0))
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_DECISION' USING ERRCODE='22023'; END IF;

 IF p_action='set_item_progress' THEN
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
   s:=s||jsonb_build_object('last_execution_feedback_at',clock_timestamp(),'first_started_at',COALESCE(s->>'first_started_at',clock_timestamp()::text));
   next_status:='in_progress';
  END IF;
 ELSIF p_action='append_feedback' THEN
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
   IF target=owner_id OR target=reviewer_id OR NOT workflow.task_execution_user_allows(p_task,target,'task.accept',target,true)
   THEN RAISE EXCEPTION 'TASK_EXECUTOR_TARGET_CONFLICT' USING ERRCODE='40001'; END IF;
   h:=h||jsonb_build_object('previous_user_id',owner_id,'prior_status',t.status);
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
   IF a=reviewer_id THEN RAISE EXCEPTION 'TASK_SELF_REVIEW_FORBIDDEN' USING ERRCODE='42501'; END IF;
   IF p_body->>'decision'='accept' AND NOT workflow.task_execution_user_allows(p_task,a,'task.complete',a,true) THEN RAISE EXCEPTION 'TASK_HANDOVER_TARGET_INELIGIBLE' USING ERRCODE='42501'; END IF;
   s:=jsonb_set(s,'{execution_handover}',h);
   IF p_body->>'decision'='accept' THEN
    next_status:='pending_execution';
    next_task.accepted_at:=clock_timestamp();
    UPDATE workflow.task_assignee SET accepted_at=clock_timestamp() WHERE task_id=p_task AND responsibility='owner';
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
  req:=p_body->'requirements'; PERFORM workflow.task_execution_validate_requirements(req);
  IF req=s->'requirements' THEN changed:=false;
  ELSE
   FOR i IN SELECT value FROM jsonb_array_elements(req->'checklist') LOOP
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
  IF NOT self_task AND (owner_id=reviewer_id OR workflow.task_execution_person(p_task,reviewer_id) IS NULL
   OR NOT workflow.task_execution_user_allows(p_task,reviewer_id,'task.review',reviewer_id,true))
  THEN RAISE EXCEPTION 'TASK_REVIEWER_HANDOVER_REQUIRED' USING ERRCODE='40001'; END IF;
  -- The existing declaration function checks metric links/precision and writes
  -- history at the single upcoming task version. It never books KR/financial actuals.
  IF p_body ? 'actual_contribution' THEN
   PERFORM security.record_task_contribution(p_task,p_body->'actual_contribution',t.version_no);
  END IF;
  SELECT actual_contribution INTO contribution FROM workflow.task_alignment WHERE task_id=p_task;
  sid:=gen_random_uuid();
  submission_snapshot:=jsonb_build_object('requirements',s->'requirements','items',s->'items','note',note,'actual_contribution',contribution,
   'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version','executor_user_id',owner_id,'reviewer_user_id',reviewer_id);
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
    IF jsonb_array_length(s->'items')>0 AND jsonb_array_length(COALESCE(p_body->'rework_item_ids','[]'))=0
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
  IF next_status IN ('completed','cancelled') THEN s:=s||jsonb_build_object('pause',NULL,'pending_requirements',NULL); END IF;
  UPDATE workflow.task_execution SET state=s,updated_at=clock_timestamp() WHERE task_id=p_task;
  INSERT INTO workflow.task_execution_event(id,task_id,workspace_id,actor_user_ref_id,kind,body)
   VALUES(e,p_task,t.workspace_id,a,p_action,jsonb_build_object('command',p_body,'before',old_s,'after',s,'from_status',t.status,'to_status',next_status,
    'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'version_no',committed_version));
  INSERT INTO workflow.task_event(id,workspace_id,task_id,event_type,from_status,to_status,actor_user_ref_id,note,payload)
   VALUES(e,t.workspace_id,p_task,event_kind,t.status,next_status,a,note,jsonb_build_object('execution_protocol',1,'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version','submission_id',COALESCE(s->'current_submission',old_s->'current_submission')));
  -- In-app/outbox notification, in the same transaction; no provider invocation.
  IF p_action<>'set_item_progress' THEN
   FOR target IN SELECT DISTINCT id FROM (VALUES(owner_id),((s->>'current_reviewer')::uuid),((s->'execution_handover'->>'target_user_id')::uuid),(CASE WHEN s->'reviewer_handover'->>'status'='pending' THEN (s->'reviewer_handover'->>'target_user_id')::uuid ELSE NULL END)) r(id)
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
 'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version','execution',result);
 INSERT INTO workflow.task_execution_request(task_id,workspace_id,actor_user_ref_id,key,action,body,response) VALUES(p_task,t.workspace_id,a,p_key,p_action,p_body,response);
 RETURN response;
END $$;

COMMIT;
