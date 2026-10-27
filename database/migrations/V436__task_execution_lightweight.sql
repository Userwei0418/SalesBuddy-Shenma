BEGIN;

-- Explicit protocol, no rewriting of any historical task or deployed migration.
ALTER TABLE workflow.task DROP CONSTRAINT task_execution_mode_check;
ALTER TABLE workflow.task ADD CONSTRAINT task_execution_mode_check
 CHECK(execution_mode IN ('legacy','execution_v1','execution_v2','execution_v3'));
ALTER TABLE workflow.task DROP CONSTRAINT task_status_check;
ALTER TABLE workflow.task ADD CONSTRAINT task_status_check CHECK(
 (execution_mode='execution_v3' AND status IN ('in_progress','completed','deferred','needs_adjustment')) OR
 (execution_mode<>'execution_v3' AND status IN ('pending_confirm','pending_execution','in_progress','pending_review','completed','deferred','cancelled')));

-- Identity/grants are rechecked before idempotent replay as well as live actions.
CREATE FUNCTION workflow.task_execution_authorized_v3(p_task uuid,p_action text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; a uuid:=common.current_user_ref_id(); o uuid; n int; owner_ok boolean; creator_ok boolean;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND OR NOT security.authorization_task('task.read',p_task) OR workflow.task_execution_person(p_task,a) IS NULL THEN RETURN false; END IF;
 SELECT count(*),min(assignee_user_ref_id::text)::uuid INTO n,o FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 owner_ok:=n=1 AND o=a AND security.authorization_task('task.complete',p_task);
 creator_ok:=a=t.creator_user_ref_id AND security.authorization_task('task.coordinate',p_task);
 RETURN COALESCE(CASE p_action
  WHEN 'append_feedback' THEN owner_ok AND NOT EXISTS(SELECT 1 FROM security.authorization_current_grants() WHERE permission_code='task.feedback' AND effect='deny')
  WHEN 'defer' THEN owner_ok WHEN 'request_adjustment' THEN owner_ok WHEN 'submit_completion' THEN owner_ok
  WHEN 'pause' THEN owner_ok OR creator_ok WHEN 'resume' THEN owner_ok OR creator_ok
  WHEN 'revise' THEN creator_ok WHEN 'continue_original' THEN creator_ok WHEN 'adopt_v3' THEN creator_ok
  WHEN 'review_completion' THEN a=t.creator_user_ref_id AND a IS DISTINCT FROM o AND n=1 AND security.authorization_task('task.review',p_task)
  WHEN 'close' THEN a=t.creator_user_ref_id AND security.authorization_task('task.cancel',p_task)
  ELSE false END,false);
END $$;

CREATE FUNCTION workflow.task_execution_capabilities_v3(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; live boolean; running boolean; reason text; result jsonb:='{}'; act text; available boolean;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND OR t.execution_mode<>'execution_v3' OR NOT security.authorization_task('task.read',p_task) THEN RETURN result; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 reason:=s->>'processing_reason'; live:=t.status<>'completed'; running:=t.status IN ('in_progress','deferred');
 FOREACH act IN ARRAY ARRAY['append_feedback','defer','request_adjustment','pause','resume','submit_completion','review_completion','revise','continue_original','close'] LOOP
  available:=live AND CASE act
   WHEN 'defer' THEN running WHEN 'request_adjustment' THEN running OR reason='adjustment_request'
   WHEN 'pause' THEN running WHEN 'resume' THEN reason='paused'
   WHEN 'submit_completion' THEN running
   WHEN 'review_completion' THEN reason='awaiting_review' AND s->>'current_submission' IS NOT NULL
   WHEN 'continue_original' THEN reason='adjustment_request' ELSE true END;
  result:=result||jsonb_build_object(act,COALESCE(available AND workflow.task_execution_authorized_v3(p_task,act),false));
 END LOOP;
 RETURN result;
END $$;

CREATE FUNCTION workflow.task_execution_summary_v3(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; o uuid; n int;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.authorization_task('task.read',p_task) THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 IF t.execution_mode<>'execution_v3' OR s->>'protocol_version' IS DISTINCT FROM '3' THEN RAISE EXCEPTION 'TASK_EXECUTION_NOT_INITIALIZED' USING ERRCODE='40001'; END IF;
 SELECT count(*),min(assignee_user_ref_id::text)::uuid INTO n,o FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 RETURN s||jsonb_build_object('task_id',p_task,'execution_mode','execution_v3','status',t.status,'version_no',t.version_no,
  'creator_user_ref_id',t.creator_user_ref_id,'current_reviewer',t.creator_user_ref_id,
  'creator',workflow.task_execution_person(p_task,t.creator_user_ref_id),'reviewer',workflow.task_execution_person(p_task,t.creator_user_ref_id),
  'executor_user_id',o,'executor',workflow.task_execution_person(p_task,o),'self_managed',n=1 AND o=t.creator_user_ref_id,
  'review_required',NOT(n=1 AND o=t.creator_user_ref_id),'progress',NULL,'checklist',NULL,'completion_result',s->'completion_result'->>'note',
  'completion_action',s->'completion_result'->>'action','completion_detail',s->'completion_result',
  'capabilities',workflow.task_execution_capabilities_v3(p_task));
END $$;

CREATE FUNCTION workflow.task_execution_initialize_v3(p_task uuid,p_definition jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; o uuid; n int; a uuid:=common.current_user_ref_id(); v int;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND OR t.execution_origin_xid<>pg_current_xact_id() OR t.creator_user_ref_id IS DISTINCT FROM a
  OR NOT security.authorization_task_created(p_task) OR workflow.task_execution_person(p_task,a) IS NULL
  OR t.execution_mode<>'legacy' OR t.status<>'in_progress'
 THEN RAISE EXCEPTION 'TASK_INITIALIZE_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF p_definition IS DISTINCT FROM '{}'::jsonb THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_INVALID' USING ERRCODE='22023'; END IF;
 IF NOT security.workspace_module_enabled(t.workspace_id,'task') THEN RAISE EXCEPTION 'TASK_EXECUTION_ROLLOUT_DISABLED' USING ERRCODE='40001'; END IF;
 IF EXISTS(SELECT 1 FROM workflow.task_execution WHERE task_id=p_task) THEN RAISE EXCEPTION 'TASK_ALREADY_INITIALIZED' USING ERRCODE='40001'; END IF;
 SELECT count(*),min(assignee_user_ref_id::text)::uuid INTO n,o FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 IF n<>1 OR t.target_position IS NOT NULL OR EXISTS(SELECT 1 FROM workflow.task_candidate WHERE task_id=p_task)
  OR workflow.task_execution_person(p_task,o) IS NULL
  OR NOT workflow.task_execution_user_allows(p_task,o,'task.read',o,true)
  OR NOT workflow.task_execution_user_allows(p_task,o,'task.complete',o,true)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_EXECUTOR_INVALID' USING ERRCODE='22023'; END IF;
 IF t.accepted_at IS NOT NULL OR EXISTS(SELECT 1 FROM workflow.task_assignee WHERE task_id=p_task AND accepted_at IS NOT NULL)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
 s:=jsonb_build_object('protocol_version',3,'requirements_version',1,'responsibility_version',1,
  'current_reviewer',t.creator_user_ref_id,'requirements',jsonb_build_object('title',t.title,'description',t.description,'due_at',t.due_at),
  'processing_reason',NULL,'processing_detail',NULL,'current_submission',NULL,'submission_round',0,
  'latest_feedback',NULL,'next_step',NULL,'completion_result',NULL,'defer',NULL,'first_started_at',NULL);
 INSERT INTO workflow.task_execution(task_id,workspace_id,state) VALUES(p_task,t.workspace_id,s);
 UPDATE workflow.task SET execution_mode='execution_v3' WHERE id=p_task RETURNING version_no INTO v;
 INSERT INTO workflow.task_execution_event(task_id,workspace_id,actor_user_ref_id,kind,body)
 VALUES(p_task,t.workspace_id,a,'created_in_progress',jsonb_build_object('after',s,'to_status','in_progress','version_no',v,
  'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'executor_user_id',o));
 RETURN workflow.task_execution_summary_v3(p_task);
END $$;

CREATE FUNCTION workflow.task_execution_command_v3(p_task uuid,p_action text,p_body jsonb,p_key uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; nt workflow.task; s jsonb; before_s jsonb; a uuid:=common.current_user_ref_id(); o uuid; owner_count int;
 prior workflow.task_execution_request; allowed text[]; versions text[]:=ARRAY['version_no','requirements_version','responsibility_version'];
 event_id uuid:=gen_random_uuid(); submission_id uuid; snap jsonb; note text; target uuid; person jsonb; changed boolean:=true;
 result jsonb; v int; recipient uuid; recipients uuid[]:='{}'; decision text; now_at timestamptz:=clock_timestamp();
 req_changed boolean:=false; owner_changed boolean:=false; next_step text; invalidated_submission uuid;
BEGIN
 IF p_key IS NULL THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
 -- Serialize a workspace receipt key even when two requests name different tasks.
 PERFORM pg_advisory_xact_lock(hashtextextended(common.current_workspace_id()::text||':'||p_key::text,0));
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF t.execution_mode<>'execution_v3' THEN RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001'; END IF;
 IF NOT workflow.task_execution_authorized_v3(p_task,p_action) THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task FOR UPDATE;
 IF NOT FOUND OR s->>'protocol_version' IS DISTINCT FROM '3' THEN RAISE EXCEPTION 'TASK_EXECUTION_NOT_INITIALIZED' USING ERRCODE='40001'; END IF;
 SELECT * INTO prior FROM workflow.task_execution_request WHERE workspace_id=t.workspace_id AND key=p_key;
 IF FOUND THEN
  IF prior.task_id<>p_task OR prior.actor_user_ref_id<>a OR prior.action<>p_action OR prior.body IS DISTINCT FROM p_body
  THEN RAISE EXCEPTION 'TASK_EXECUTION_IDEMPOTENCY_CONFLICT' USING ERRCODE='40001'; END IF;
  RETURN prior.response;
 END IF;
 allowed:=CASE p_action
  WHEN 'append_feedback' THEN ARRAY['text','next_step'] WHEN 'defer' THEN ARRAY['reason','due_at']
  WHEN 'request_adjustment' THEN ARRAY['reason'] WHEN 'pause' THEN ARRAY['reason'] WHEN 'resume' THEN ARRAY['reason']
  WHEN 'submit_completion' THEN ARRAY['note'] WHEN 'review_completion' THEN ARRAY['submission_id','decision','note']
  WHEN 'revise' THEN ARRAY['description','due_at','executor_user_id','reason']
  WHEN 'continue_original' THEN ARRAY['reason'] WHEN 'close' THEN ARRAY['reason'] END;
 IF allowed IS NULL OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' OR p_body-(allowed||versions)<>'{}'::jsonb
  OR EXISTS(SELECT 1 FROM unnest(versions) k WHERE jsonb_typeof(p_body->k) IS DISTINCT FROM 'number' OR (p_body->>k)!~'^[1-9][0-9]*$')
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
 IF (p_body->>'version_no')::int<>t.version_no OR (p_body->>'requirements_version')::int<>(s->>'requirements_version')::int
  OR (p_body->>'responsibility_version')::int<>(s->>'responsibility_version')::int
 THEN RAISE EXCEPTION 'TASK_EXECUTION_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 IF NOT COALESCE((workflow.task_execution_capabilities_v3(p_task)->>p_action)::boolean,false)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_ACTION_UNAVAILABLE' USING ERRCODE='40001'; END IF;
 IF EXISTS(SELECT 1 FROM unnest(allowed) k WHERE p_body ? k AND jsonb_typeof(p_body->k) IS DISTINCT FROM 'string')
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
 note:=btrim(COALESCE(p_body->>'reason',p_body->>'note',p_body->>'text',''));
 IF (p_action NOT IN ('review_completion','revise','continue_original') AND length(note)=0)
  OR length(note)>(CASE WHEN p_action IN ('append_feedback','submit_completion','review_completion') THEN 4000 ELSE 1000 END)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_REASON_REQUIRED' USING ERRCODE='22023'; END IF;
 SELECT count(*),min(assignee_user_ref_id::text)::uuid INTO owner_count,o FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 IF owner_count<>1 THEN RAISE EXCEPTION 'TASK_EXECUTION_EXECUTOR_INVALID' USING ERRCODE='40001'; END IF;
 before_s:=s; nt:=t;
 IF p_action='append_feedback' THEN
  next_step:=CASE WHEN p_body ? 'next_step' THEN btrim(p_body->>'next_step') ELSE s->>'next_step' END;
  IF length(COALESCE(next_step,''))>2000 OR (t.status='in_progress' AND length(COALESCE(next_step,''))=0)
  THEN RAISE EXCEPTION 'TASK_EXECUTION_NEXT_STEP_REQUIRED' USING ERRCODE='22023'; END IF;
  s:=s||jsonb_build_object('latest_feedback',jsonb_build_object('text',note,'actor_user_id',a,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'occurred_at',now_at,'event_id',event_id),
   'next_step',next_step,'last_execution_feedback_at',now_at);
  recipients:=ARRAY[t.creator_user_ref_id];
 ELSIF p_action='defer' THEN
  IF p_body->>'due_at' IS NULL OR (p_body->>'due_at')!~'(Z|[+-][0-9]{2}:[0-9]{2})$' THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
  nt.due_at:=(p_body->>'due_at')::timestamptz;
  IF nt.due_at<=now_at OR nt.due_at<=t.due_at THEN RAISE EXCEPTION 'TASK_DEFER_DATE_INVALID' USING ERRCODE='22023'; END IF;
  nt.status:='deferred';
  s:=s||jsonb_build_object('defer',jsonb_build_object('reason',note,'previous_due_at',t.due_at,'due_at',nt.due_at,'actor_user_id',a,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'occurred_at',now_at,'event_id',event_id),
   'requirements_version',(s->>'requirements_version')::int+1,'requirements',(s->'requirements')||jsonb_build_object('due_at',nt.due_at));
  recipients:=ARRAY[t.creator_user_ref_id];
 ELSIF p_action IN ('request_adjustment','pause') THEN
  nt.status:='needs_adjustment';
  s:=s||jsonb_build_object('processing_reason',CASE WHEN p_action='pause' THEN 'paused' ELSE 'adjustment_request' END,
   'processing_detail',jsonb_build_object('reason',note,'actor_user_id',a,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'occurred_at',now_at,'event_id',event_id));
  recipients:=ARRAY[t.creator_user_ref_id,o];
 ELSIF p_action IN ('resume','continue_original') THEN
  nt.status:='in_progress';s:=s||jsonb_build_object('processing_reason',NULL,'processing_detail',NULL);
  recipients:=ARRAY[t.creator_user_ref_id,o];
 ELSIF p_action='submit_completion' THEN
  submission_id:=gen_random_uuid();
  snap:=jsonb_build_object('protocol_version',3,'note',note,'submitted_at',now_at,'executor_user_id',o,
   'creator_user_ref_id',t.creator_user_ref_id,'reviewer_user_id',t.creator_user_ref_id,'requirements',s->'requirements',
   'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version',
   'round',(s->>'submission_round')::int+1,'self_managed',o=t.creator_user_ref_id);
  s:=s||jsonb_build_object('submission_round',(s->>'submission_round')::int+1);
  nt.completion_note:=note;nt.completion_submitted_at:=now_at;nt.completion_review_note:=NULL;
  IF o=t.creator_user_ref_id THEN
   nt.status:='completed';nt.completed_at:=now_at;nt.verification_status:='not_required';
   s:=s||jsonb_build_object('current_submission',NULL,'completion_result',jsonb_build_object('action','submit_completion','note',note,'actor_user_id',a,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'occurred_at',now_at,'event_id',event_id,'submission_id',submission_id));
  ELSE
   nt.status:='needs_adjustment';nt.verification_status:='pending';
   s:=s||jsonb_build_object('processing_reason','awaiting_review','processing_detail',jsonb_build_object('note',note,'actor_user_id',a,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'occurred_at',now_at,'event_id',event_id),
    'current_submission',submission_id);
   recipients:=ARRAY[t.creator_user_ref_id];
  END IF;
 ELSIF p_action='review_completion' THEN
  IF p_body->>'submission_id' IS NULL OR p_body->>'decision' IS NULL OR p_body->>'decision' NOT IN ('approve','reject')
   THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
  submission_id:=(p_body->>'submission_id')::uuid;decision:=p_body->>'decision';
  IF submission_id IS DISTINCT FROM (s->>'current_submission')::uuid THEN RAISE EXCEPTION 'TASK_SUBMISSION_STALE' USING ERRCODE='40001'; END IF;
  SELECT snapshot INTO snap FROM workflow.task_execution_submission WHERE id=submission_id AND task_id=p_task AND workspace_id=t.workspace_id;
  IF snap->>'protocol_version' IS DISTINCT FROM '3' AND s->'adopted_submission'->>'submission_id'=submission_id::text THEN
   snap:=snap||s->'adopted_submission';
  END IF;
  IF snap IS NULL OR (snap->>'requirements_version')::int IS DISTINCT FROM (s->>'requirements_version')::int
   OR (snap->>'responsibility_version')::int IS DISTINCT FROM (s->>'responsibility_version')::int
   OR (snap->>'executor_user_id')::uuid IS DISTINCT FROM o OR (snap->>'reviewer_user_id')::uuid IS DISTINCT FROM a
  THEN RAISE EXCEPTION 'TASK_SUBMISSION_STALE' USING ERRCODE='40001'; END IF;
  IF decision='reject' AND length(note)=0 THEN RAISE EXCEPTION 'TASK_EXECUTION_REASON_REQUIRED' USING ERRCODE='22023'; END IF;
  nt.status:=CASE WHEN decision='approve' THEN 'completed' ELSE 'in_progress' END;
  nt.verification_status:=CASE WHEN decision='approve' THEN 'passed' ELSE 'rejected' END;
  nt.completion_review_note:=note;
  IF decision='approve' THEN
   nt.completed_at:=now_at;
   s:=s||jsonb_build_object('completion_result',jsonb_build_object('action','review_completion','note',snap->>'note','review_note',note,'actor_user_id',a,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'occurred_at',now_at,'event_id',event_id,'submission_id',submission_id));
  END IF;
  s:=s||jsonb_build_object('last_review',jsonb_build_object('submission_id',submission_id,'decision',decision,'note',note,'actor_user_id',a,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'occurred_at',now_at,'event_id',event_id),
   'current_submission',NULL,'processing_reason',NULL,'processing_detail',NULL);
  recipients:=ARRAY[o];snap:=NULL;
 ELSIF p_action='revise' THEN
  IF NOT(p_body ?| ARRAY['description','due_at','executor_user_id']) THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
  IF p_body ? 'description' THEN
   nt.description:=btrim(p_body->>'description');
   IF length(nt.description) NOT BETWEEN 5 AND 500 THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_INVALID' USING ERRCODE='22023'; END IF;
   IF nt.description IS DISTINCT FROM t.description THEN
    nt.title:=regexp_replace(nt.description,'\s+',' ','g');
    IF length(nt.title)>42 THEN nt.title:=left(nt.title,42)||'…'; END IF;
   END IF;
  END IF;
  IF p_body ? 'due_at' THEN
   IF (p_body->>'due_at')!~'(Z|[+-][0-9]{2}:[0-9]{2})$' THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
   nt.due_at:=(p_body->>'due_at')::timestamptz;
   IF nt.due_at IS DISTINCT FROM t.due_at AND nt.due_at<=now_at THEN RAISE EXCEPTION 'TASK_DUE_AT_MUST_BE_FUTURE' USING ERRCODE='22023'; END IF;
  END IF;
  target:=CASE WHEN p_body ? 'executor_user_id' THEN (p_body->>'executor_user_id')::uuid ELSE o END;
  owner_changed:=target IS DISTINCT FROM o;
  req_changed:=(nt.description,nt.due_at) IS DISTINCT FROM (t.description,t.due_at);
  changed:=owner_changed OR req_changed;
  IF owner_changed THEN
   IF NOT security.authorization_task('task.assign',p_task) THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
   person:=workflow.task_execution_person(p_task,target);
   IF person IS NULL OR NOT workflow.task_execution_user_allows(p_task,target,'task.read',target,true)
    OR NOT workflow.task_execution_user_allows(p_task,target,'task.complete',target,true)
   THEN RAISE EXCEPTION 'TASK_EXECUTION_EXECUTOR_INVALID' USING ERRCODE='22023'; END IF;
   -- Preserve old responsibility in the event snapshot; only the current owner row changes.
   DELETE FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
   INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility)
    VALUES(p_task,t.workspace_id,target,(person->>'team_id')::uuid,person->>'role_code','owner');
   s:=s||jsonb_build_object('responsibility_version',(s->>'responsibility_version')::int+1);
  END IF;
  IF changed THEN
   invalidated_submission:=(s->>'current_submission')::uuid;
   nt.status:='in_progress';nt.completion_note:=NULL;nt.completion_submitted_at:=NULL;nt.completion_review_note:=NULL;nt.verification_status:='not_required';
   s:=s||jsonb_build_object('current_submission',NULL,'processing_reason',NULL,'processing_detail',NULL,
    'requirements_version',(s->>'requirements_version')::int+CASE WHEN req_changed THEN 1 ELSE 0 END,
    'requirements',jsonb_build_object('title',nt.title,'description',nt.description,'due_at',nt.due_at));
   recipients:=ARRAY[target];
  END IF;
 ELSIF p_action='close' THEN
  invalidated_submission:=(s->>'current_submission')::uuid;
  nt.status:='completed';nt.completed_at:=now_at;nt.completion_note:=note;nt.verification_status:='not_required';nt.completion_review_note:=NULL;
  s:=s||jsonb_build_object('current_submission',NULL,'processing_reason',NULL,'processing_detail',NULL,
   'completion_result',jsonb_build_object('action','close','note',note,'actor_user_id',a,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'occurred_at',now_at,'event_id',event_id));
  recipients:=ARRAY[o];
 END IF;
 IF changed THEN
  UPDATE workflow.task SET status=nt.status,title=nt.title,description=nt.description,due_at=nt.due_at,
   completion_note=nt.completion_note,completion_submitted_at=nt.completion_submitted_at,
   completion_review_note=nt.completion_review_note,verification_status=nt.verification_status,completed_at=nt.completed_at
   WHERE id=p_task RETURNING version_no INTO v;
  UPDATE workflow.task_execution SET state=s,updated_at=now_at WHERE task_id=p_task;
  IF snap IS NOT NULL THEN
   INSERT INTO workflow.task_execution_submission(id,task_id,workspace_id,actor_user_ref_id,snapshot)
   VALUES(submission_id,p_task,t.workspace_id,a,snap||jsonb_build_object('version_no',v));
  END IF;
  INSERT INTO workflow.task_execution_event(id,task_id,workspace_id,actor_user_ref_id,kind,body)
   VALUES(event_id,p_task,t.workspace_id,a,p_action,jsonb_build_object('protocol_version',3,'command',p_body,
    'before',before_s||jsonb_build_object('description',t.description,'due_at',t.due_at,'executor_user_id',o,'executor_name',(SELECT display_name FROM platform.user_ref WHERE id=o)),
    'after',s||jsonb_build_object('description',nt.description,'due_at',nt.due_at,'executor_user_id',COALESCE(target,o),'executor_name',(SELECT display_name FROM platform.user_ref WHERE id=COALESCE(target,o))),
    'from_status',t.status,'to_status',nt.status,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a),'version_no',v,
    'before_executor_user_id',o,'after_executor_user_id',COALESCE(target,o),'invalidated_submission_id',invalidated_submission,
    'before_due_at',t.due_at,'after_due_at',nt.due_at,'before_description',t.description,'after_description',nt.description));
  INSERT INTO workflow.task_event(id,workspace_id,task_id,event_type,from_status,to_status,actor_user_ref_id,note,payload)
   VALUES(event_id,t.workspace_id,p_task,p_action,t.status,nt.status,a,note,jsonb_build_object('execution_protocol',3,
    'requirements_version',s->'requirements_version','responsibility_version',s->'responsibility_version',
    'submission_id',submission_id,'decision',decision,'completion_result',s->'completion_result','version_no',v));
  FOREACH recipient IN ARRAY recipients LOOP
   IF recipient IS NOT NULL AND recipient<>a AND workflow.task_execution_person(p_task,recipient) IS NOT NULL
    AND workflow.task_execution_user_allows(p_task,recipient,'task.read',recipient,true) THEN
    INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,channel_code,template_code,title,body,object_type,object_id,status,dedupe_key,payload)
     VALUES(t.workspace_id,recipient,'in_app','task_execution_changed','任务有新的进展',nt.title,'task',p_task,'pending',
      'task_execution:'||event_id::text||':'||recipient::text,
      jsonb_build_object('task_id',p_task,'event_id',event_id,'action',p_action,'event_version',v,'protocol_version',3))
     ON CONFLICT(workspace_id,recipient_user_ref_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING;
   END IF;
  END LOOP;
 END IF;
 result:=jsonb_build_object('changed',changed,'event_id',CASE WHEN changed THEN event_id ELSE NULL END,
  'version_no',CASE WHEN changed THEN v ELSE t.version_no END,'requirements_version',s->'requirements_version',
  'responsibility_version',s->'responsibility_version','execution',workflow.task_execution_read(p_task));
 INSERT INTO workflow.task_execution_request(task_id,workspace_id,actor_user_ref_id,key,action,body,response)
 VALUES(p_task,t.workspace_id,a,p_key,p_action,p_body,result);
 RETURN result;
END $$;

-- Read-only adoption eligibility is shared with both clients, never reimplemented
-- as a looser client-side status mapping. A historical review must keep its actor.
CREATE FUNCTION workflow.task_execution_adoption_v3(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; o uuid; n int; reason text; sid uuid;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND OR NOT security.authorization_task('task.read',p_task) THEN RETURN NULL; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 s:=COALESCE(s,workflow.task_execution_initial(p_task));
 SELECT count(*),min(assignee_user_ref_id::text)::uuid INTO n,o FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 reason:=CASE
  WHEN t.execution_mode='execution_v3' OR t.status IN ('completed','cancelled') THEN 'TASK_EXECUTION_ACTION_UNAVAILABLE'
  WHEN NOT workflow.task_execution_authorized_v3(p_task,'adopt_v3') THEN 'TASK_EXECUTION_FORBIDDEN'
  WHEN NOT security.workspace_module_enabled(t.workspace_id,'task') THEN 'TASK_EXECUTION_ROLLOUT_DISABLED'
  WHEN n<>1 OR t.target_position IS NOT NULL OR EXISTS(SELECT 1 FROM workflow.task_candidate WHERE task_id=p_task AND decision='pending')
   OR workflow.task_execution_person(p_task,o) IS NULL
   OR NOT workflow.task_execution_user_allows(p_task,o,'task.read',o,true)
   OR NOT workflow.task_execution_user_allows(p_task,o,'task.complete',o,true) THEN 'TASK_EXECUTION_EXECUTOR_INVALID'
  WHEN s->>'pending_requirements' IS NOT NULL OR s->'execution_handover'->>'status' IN ('pending','rejected')
   OR s->'reviewer_handover'->>'status' IN ('pending','rejected') THEN 'TASK_EXECUTION_PENDING_FLOW'
  WHEN t.status='pending_review' AND (o=t.creator_user_ref_id OR COALESCE((s->>'current_reviewer')::uuid,t.creator_user_ref_id)<>t.creator_user_ref_id)
   THEN 'TASK_LEGACY_REVIEW_REQUIRED'
  WHEN t.status='pending_review' AND s->>'current_submission' IS NULL
   AND (t.completion_submitted_at IS NULL OR length(btrim(COALESCE(t.completion_note,'')))=0) THEN 'TASK_SUBMISSION_MISSING'
  WHEN t.status='pending_review' AND s->>'current_submission' IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM workflow.task_execution_submission WHERE id=(s->>'current_submission')::uuid
    AND task_id=p_task AND workspace_id=t.workspace_id AND actor_user_ref_id=o AND length(btrim(COALESCE(snapshot->>'note','')))>0)
   THEN 'TASK_SUBMISSION_MISSING'
  ELSE NULL END;
 RETURN jsonb_build_object('available',reason IS NULL,'reason',reason,'version_no',t.version_no,
  'requirements_version',COALESCE((s->>'requirements_version')::int,1),'responsibility_version',COALESCE((s->>'responsibility_version')::int,1));
END $$;

CREATE FUNCTION workflow.task_execution_adopt_v3(p_task uuid,p_body jsonb,p_key uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; old_s jsonb; eligibility jsonb; a uuid:=common.current_user_ref_id(); o uuid;
 prior workflow.task_execution_request; result jsonb; e uuid:=gen_random_uuid(); sid uuid; v int; phase text;
 snap jsonb; req_version int; responsibility_version int; vars text[]:=ARRAY['version_no','requirements_version','responsibility_version'];
BEGIN
 IF p_key IS NULL THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(common.current_workspace_id()::text||':'||p_key::text,0));
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND OR NOT workflow.task_execution_authorized_v3(p_task,'adopt_v3') THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM workflow.task_execution_request WHERE workspace_id=t.workspace_id AND key=p_key;
 IF FOUND THEN
  IF prior.task_id<>p_task OR prior.actor_user_ref_id<>a OR prior.action<>'adopt_v3' OR prior.body IS DISTINCT FROM p_body
  THEN RAISE EXCEPTION 'TASK_EXECUTION_IDEMPOTENCY_CONFLICT' USING ERRCODE='40001'; END IF;
  RETURN prior.response;
 END IF;
 IF jsonb_typeof(p_body) IS DISTINCT FROM 'object' OR p_body-(vars||ARRAY['note'])<>'{}'::jsonb
  OR EXISTS(SELECT 1 FROM unnest(vars) k WHERE jsonb_typeof(p_body->k) IS DISTINCT FROM 'number' OR (p_body->>k)!~'^[1-9][0-9]*$')
  OR (p_body ? 'note' AND (jsonb_typeof(p_body->'note') IS DISTINCT FROM 'string' OR length(p_body->>'note')>1000))
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INVALID_COMMAND' USING ERRCODE='22023'; END IF;
 eligibility:=workflow.task_execution_adoption_v3(p_task);
 IF NOT COALESCE((eligibility->>'available')::boolean,false) THEN RAISE EXCEPTION '%',COALESCE(eligibility->>'reason','TASK_EXECUTION_ACTION_UNAVAILABLE') USING ERRCODE='40001'; END IF;
 IF EXISTS(SELECT 1 FROM unnest(vars) k WHERE (p_body->>k)::int<>(eligibility->>k)::int)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 SELECT state INTO old_s FROM workflow.task_execution WHERE task_id=p_task FOR UPDATE;
 old_s:=COALESCE(old_s,workflow.task_execution_initial(p_task));
 SELECT assignee_user_ref_id INTO o FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 req_version:=(eligibility->>'requirements_version')::int;responsibility_version:=(eligibility->>'responsibility_version')::int;
 phase:=CASE WHEN t.status='deferred' THEN 'deferred' WHEN t.status='pending_review' OR old_s->>'pause' IS NOT NULL THEN 'needs_adjustment' ELSE 'in_progress' END;
 s:=jsonb_build_object('protocol_version',3,'requirements_version',req_version,'responsibility_version',responsibility_version,
  'current_reviewer',t.creator_user_ref_id,'requirements',jsonb_build_object('title',t.title,'description',t.description,'due_at',t.due_at),
  'processing_reason',CASE WHEN t.status='pending_review' THEN 'awaiting_review' WHEN old_s->>'pause' IS NOT NULL THEN 'paused' END,
  'processing_detail',CASE WHEN t.status='pending_review' THEN jsonb_build_object('note',t.completion_note,'occurred_at',t.completion_submitted_at,'actor_user_id',o,'historical',true)
    WHEN old_s->>'pause' IS NOT NULL THEN old_s->'pause' END,
  'current_submission',NULL,'submission_round',0,'latest_feedback',NULL,'next_step',NULL,'completion_result',NULL,
  'defer',CASE WHEN t.status='deferred' THEN jsonb_build_object('historical',true,'due_at',t.due_at,'reason',NULL) END,
  'first_started_at',old_s->'first_started_at','adopted_from',t.execution_mode);
 IF t.status='pending_review' THEN
  sid:=(old_s->>'current_submission')::uuid;
  IF sid IS NULL THEN
   sid:=gen_random_uuid();
   snap:=jsonb_build_object('legacy',true,'note',t.completion_note,'submitted_at',t.completion_submitted_at,
    'executor_user_id',o,'reviewer_user_id',t.creator_user_ref_id,'requirements_version',req_version,'responsibility_version',responsibility_version);
   INSERT INTO workflow.task_execution_submission(id,task_id,workspace_id,actor_user_ref_id,snapshot)
   VALUES(sid,p_task,t.workspace_id,o,snap);
  ELSE
   SELECT snapshot INTO snap FROM workflow.task_execution_submission WHERE id=sid AND task_id=p_task AND workspace_id=t.workspace_id;
  END IF;
  s:=s||jsonb_build_object('current_submission',sid,'submission_round',1,'adopted_submission',jsonb_build_object('submission_id',sid,
   'executor_user_id',o,'reviewer_user_id',t.creator_user_ref_id,'requirements_version',req_version,'responsibility_version',responsibility_version),
   'processing_detail',jsonb_build_object('note',snap->>'note','occurred_at',COALESCE(snap->'submitted_at',to_jsonb(t.completion_submitted_at)),'actor_user_id',o,'historical',true));
 END IF;
 INSERT INTO workflow.task_execution(task_id,workspace_id,state) VALUES(p_task,t.workspace_id,s)
 ON CONFLICT(task_id) DO UPDATE SET state=EXCLUDED.state,updated_at=clock_timestamp();
 UPDATE workflow.task SET execution_mode='execution_v3',status=phase WHERE id=p_task RETURNING version_no INTO v;
 INSERT INTO workflow.task_execution_event(id,task_id,workspace_id,actor_user_ref_id,kind,body)
 VALUES(e,p_task,t.workspace_id,a,'adopt_v3',jsonb_build_object('command',p_body,'before',old_s,'after',s,'from_status',t.status,
  'to_status',phase,'from_mode',t.execution_mode,'version_no',v,'actor_name',(SELECT display_name FROM platform.user_ref WHERE id=a)));
 INSERT INTO workflow.task_event(id,workspace_id,task_id,event_type,from_status,to_status,actor_user_ref_id,note,payload)
 VALUES(e,t.workspace_id,p_task,'adopt_v3',t.status,phase,a,COALESCE(p_body->>'note',''),jsonb_build_object('execution_protocol',3,'from_mode',t.execution_mode,'version_no',v));
 result:=jsonb_build_object('changed',true,'event_id',e,'version_no',v,'requirements_version',req_version,
  'responsibility_version',responsibility_version,'execution',workflow.task_execution_read(p_task));
 INSERT INTO workflow.task_execution_request(task_id,workspace_id,actor_user_ref_id,key,action,body,response)
 VALUES(p_task,t.workspace_id,a,p_key,'adopt_v3',p_body,result);
 RETURN result;
END $$;


CREATE OR REPLACE FUNCTION security.task_actor_recipient(p_task uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=p_task
  AND a.workspace_id=common.current_workspace_id() AND a.assignee_user_ref_id=common.current_user_ref_id()
  AND security.task_recipient_eligible(p_task,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=p_task
  AND c.workspace_id=common.current_workspace_id() AND c.user_ref_id=common.current_user_ref_id()
  AND security.task_recipient_eligible(p_task,c.user_ref_id,c.role_code,c.team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_execution x JOIN workflow.task t ON t.id=x.task_id AND t.execution_mode IN ('execution_v1','execution_v2','execution_v3') WHERE x.task_id=p_task AND x.workspace_id=common.current_workspace_id()
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
 FROM workflow.task t LEFT JOIN workflow.task_execution x ON x.task_id=t.id AND t.execution_mode IN ('execution_v1','execution_v2','execution_v3')
 WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL),false);
$$;


CREATE OR REPLACE FUNCTION workflow.task_execution_capabilities(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; a uuid:=common.current_user_ref_id(); owner_id uuid; owner_ok boolean; review_ok boolean;
 coordinate_ok boolean; feedback_ok boolean; executing boolean; live boolean; handover boolean; self_task boolean; owner_count int; v2 boolean;
BEGIN
 IF EXISTS(SELECT 1 FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND execution_mode='execution_v3') THEN RETURN workflow.task_execution_capabilities_v3(p_task); END IF;
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
 RETURN jsonb_build_object('adopt_v3',COALESCE((workflow.task_execution_adoption_v3(p_task)->>'available')::boolean,false),'save_checklist',COALESCE(v2 AND owner_ok AND t.status IN ('pending_execution','in_progress') AND NOT handover,false),
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
 IF EXISTS(SELECT 1 FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND execution_mode='execution_v3') THEN RETURN workflow.task_execution_summary_v3(p_task); END IF;
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.authorization_task('task.read',p_task) THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF t.execution_mode='legacy' THEN RETURN NULL; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 s:=COALESCE(s,workflow.task_execution_initial(p_task));
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 SELECT count(*) INTO owner_count FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner';
 RETURN s || jsonb_build_object('supported_protocol_version',3,'adopt_versions',(workflow.task_execution_adoption_v3(p_task)-ARRAY['available','reason']),'adoption',workflow.task_execution_adoption_v3(p_task),'execution_mode',t.execution_mode,'task_id',p_task,'status',t.status,'version_no',t.version_no,'creator_user_ref_id',t.creator_user_ref_id,
 'checklist_revision',COALESCE((s->>'checklist_revision')::int,1),'self_managed',owner_count=1 AND owner_id=t.creator_user_ref_id,
 'review_required',NOT (owner_count=1 AND owner_id=t.creator_user_ref_id),'requirements_revalidation',EXISTS(SELECT 1 FROM jsonb_array_elements(s->'items') i WHERE COALESCE((i->>'requires_revalidation')::boolean,false)),
 'requirements_incomplete',length(btrim(COALESCE(s->'requirements'->>'deliverable','')))=0 OR length(btrim(COALESCE(s->'requirements'->>'acceptance_criteria','')))=0,
 'executor',workflow.task_execution_person(p_task,owner_id),'executor_user_id',owner_id,
 'reviewer',workflow.task_execution_person(p_task,(s->>'current_reviewer')::uuid),
 'coordination_required',CASE WHEN t.execution_mode='execution_v2' THEN workflow.task_execution_coordination_status(p_task) ELSE '{}'::jsonb END,
 'progress',workflow.task_execution_progress(s),'checklist',s->'items','capabilities',workflow.task_execution_capabilities(p_task));
END $$;


-- V3 reassign uses the same read/complete target eligibility as the write.
-- Its original creator may become executor; no obsolete accept step is added.
CREATE OR REPLACE FUNCTION workflow.task_execution_candidate(p_task uuid,p_user uuid,p_purpose text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s jsonb; owner_id uuid; creator_id uuid; mode text; person jsonb;
BEGIN
 SELECT creator_user_ref_id,execution_mode INTO creator_id,mode FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id();
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 person:=workflow.task_execution_person(p_task,p_user);
 IF mode='execution_v3' THEN
  IF p_purpose IS DISTINCT FROM 'executor' OR person IS NULL OR p_user=owner_id
   OR NOT COALESCE((workflow.task_execution_capabilities_v3(p_task)->>'revise')::boolean,false)
   OR NOT security.authorization_task('task.assign',p_task)
   OR NOT workflow.task_execution_user_allows(p_task,p_user,'task.read',p_user,true)
   OR NOT workflow.task_execution_user_allows(p_task,p_user,'task.complete',p_user,true)
  THEN RETURN NULL; END IF;
  RETURN person;
 END IF;
 IF p_purpose NOT IN ('executor','reviewer') OR person IS NULL OR p_user=owner_id OR (p_user=(s->>'current_reviewer')::uuid AND NOT (mode='execution_v2' AND p_purpose='executor' AND p_user=creator_id))
  OR NOT workflow.task_execution_user_allows(p_task,p_user,'task.read',p_user,true)
 THEN RETURN NULL; END IF;
 IF p_purpose='executor' AND (NOT workflow.task_execution_user_allows(p_task,p_user,'task.accept',p_user,true)
  OR NOT workflow.task_execution_user_allows(p_task,p_user,'task.complete',p_user,true)) THEN RETURN NULL; END IF;
 IF p_purpose='reviewer' AND NOT workflow.task_execution_user_allows(p_task,p_user,'task.review',p_user,true) THEN RETURN NULL; END IF;
 RETURN person;
END $$;

CREATE OR REPLACE FUNCTION workflow.task_execution_candidates(p_task uuid,p_purpose text,p_query text,p_limit integer,p_offset integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rows jsonb; total_count integer; cap jsonb;
BEGIN
 IF p_purpose IS NULL OR p_purpose NOT IN ('executor','reviewer') OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
  OR p_offset IS NULL OR p_offset<0 OR length(COALESCE(p_query,''))>100
 THEN RAISE EXCEPTION 'TASK_CANDIDATES_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 cap:=workflow.task_execution_capabilities(p_task);
 IF EXISTS(SELECT 1 FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND execution_mode='execution_v3') THEN
  IF p_purpose<>'executor' OR NOT COALESCE((cap->>'revise')::boolean,false) OR NOT security.authorization_task('task.assign',p_task)
  THEN RAISE EXCEPTION 'TASK_CANDIDATES_FORBIDDEN' USING ERRCODE='42501'; END IF;
 ELSIF NOT COALESCE((cap->>('initiate_'||p_purpose||'_handover'))::boolean,false)
 THEN RAISE EXCEPTION 'TASK_CANDIDATES_FORBIDDEN' USING ERRCODE='42501'; END IF;
 WITH candidates AS MATERIALIZED (
  SELECT u.id,u.display_name,workflow.task_execution_candidate(p_task,u.id,p_purpose) person
  FROM platform.user_ref u WHERE u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL
   AND (COALESCE(p_query,'')='' OR strpos(lower(u.display_name),lower(p_query))>0 OR strpos(lower(COALESCE(u.account_code,'')),lower(p_query))>0)
 ), eligible AS MATERIALIZED (SELECT * FROM candidates WHERE person IS NOT NULL),
 page AS (SELECT * FROM eligible ORDER BY display_name,id LIMIT p_limit OFFSET p_offset)
 SELECT (SELECT count(*)::int FROM eligible),COALESCE((SELECT jsonb_agg(jsonb_build_object('user_id',p.id,'display_name',p.display_name,
   'teams',COALESCE((SELECT jsonb_agg(jsonb_build_object('team_id',t.id,'name',t.name) ORDER BY t.name,t.id)
    FROM platform.team t WHERE t.workspace_id=common.current_workspace_id() AND t.status='active' AND t.deleted_at IS NULL
    AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to AND (t.id=(p.person->>'team_id')::uuid OR EXISTS(
      SELECT 1 FROM platform.team_membership m WHERE m.team_id=t.id AND m.workspace_id=t.workspace_id AND m.user_ref_id=p.id
      AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to))),'[]'::jsonb)) ORDER BY p.display_name,p.id) FROM page p),'[]'::jsonb)
 INTO total_count,rows;
 RETURN jsonb_build_object('items',rows,'total',total_count,'limit',p_limit,'offset',p_offset);
END $$;

CREATE OR REPLACE FUNCTION workflow.guard_task_execution_legacy_write() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE command_owner name; guarded boolean;
BEGIN
 SELECT pg_get_userbyid(proowner) INTO command_owner FROM pg_proc WHERE oid='workflow.task_execution_command(uuid,text,jsonb,uuid)'::regprocedure;
 IF current_user=command_owner OR EXISTS(SELECT 1 FROM pg_proc WHERE
  oid IN ('workflow.task_execution_initialize_v3(uuid,jsonb)'::regprocedure,
   'workflow.task_execution_command_v3(uuid,text,jsonb,uuid)'::regprocedure,
   'workflow.task_execution_adopt_v3(uuid,jsonb,uuid)'::regprocedure)
  AND pg_get_userbyid(proowner)=current_user) THEN RETURN COALESCE(NEW,OLD); END IF;
 IF TG_TABLE_NAME='task' THEN
  IF TG_OP='INSERT' THEN
   IF NEW.execution_mode<>'legacy' OR NEW.execution_origin_xid<>pg_current_xact_id() THEN RAISE EXCEPTION 'TASK_EXECUTION_MODE_IMMUTABLE' USING ERRCODE='40001'; END IF;
   RETURN NEW;
  END IF;
  IF NEW.execution_mode IS DISTINCT FROM OLD.execution_mode OR NEW.execution_origin_xid IS DISTINCT FROM OLD.execution_origin_xid
  THEN RAISE EXCEPTION 'TASK_EXECUTION_MODE_IMMUTABLE' USING ERRCODE='40001'; END IF;
  IF OLD.execution_mode='legacy' THEN RETURN NEW; END IF;
  IF OLD.execution_mode='execution_v3' AND (to_jsonb(NEW)-ARRAY['updated_at','version_no']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['updated_at','version_no']) THEN RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001'; END IF;
  IF OLD.execution_mode='execution_v2' AND NEW.status='cancelled' AND NEW.status IS DISTINCT FROM OLD.status THEN RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001'; END IF;
 ELSE
  -- Inspect both old and new parents when an assignee is moved between tasks.
  guarded:=false;
  IF TG_OP<>'DELETE' THEN
   SELECT execution_mode IN ('execution_v1','execution_v2','execution_v3') INTO guarded FROM workflow.task WHERE id=NEW.task_id;
  END IF;
  IF TG_OP<>'INSERT' THEN
   guarded:=COALESCE(guarded,false) OR EXISTS(SELECT 1 FROM workflow.task WHERE id=OLD.task_id AND execution_mode IN ('execution_v1','execution_v2','execution_v3'));
  END IF;
  IF NOT COALESCE(guarded,false) THEN RETURN COALESCE(NEW,OLD); END IF;
  IF (TG_OP<>'DELETE' AND EXISTS(SELECT 1 FROM workflow.task WHERE id=NEW.task_id AND execution_mode='execution_v3')) OR (TG_OP<>'INSERT' AND EXISTS(SELECT 1 FROM workflow.task WHERE id=OLD.task_id AND execution_mode='execution_v3')) THEN RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001'; END IF;
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
 IF (SELECT execution_mode FROM workflow.task WHERE id=NEW.id)='execution_v3' THEN
  IF NOT EXISTS(SELECT 1 FROM workflow.task_execution WHERE task_id=NEW.id AND state->>'protocol_version'='3') THEN RAISE EXCEPTION 'TASK_EXECUTION_NOT_INITIALIZED' USING ERRCODE='23514'; END IF;
  RETURN NEW;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM workflow.task_execution WHERE task_id=NEW.id AND (jsonb_array_length(state->'items')>=1 OR (state->>'protocol_version'='2' AND jsonb_typeof(state->'items')='array')))
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INITIAL_CHECKLIST_REQUIRED' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;


CREATE OR REPLACE FUNCTION security.save_task_alignment(p_task uuid,p_body jsonb,p_expected_version integer DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; is_new boolean; codes text[]; ids uuid[]; v_metric_code text; kr uuid; contribution jsonb; saved jsonb; v2 boolean; target_ids uuid[]; reason text;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF t.id IS NULL THEN RAISE no_data_found; END IF;
 is_new:=security.authorization_task_created(t.id) AND NOT EXISTS(SELECT 1 FROM workflow.task_alignment WHERE task_id=t.id);
 IF NOT is_new AND NOT (security.authorization_task('task.read',t.id) AND security.fde_can_coordinate_task(t.id))
 THEN RAISE insufficient_privilege; END IF;
 v2:=COALESCE(p_body->>'contract_version'='2' AND jsonb_typeof(p_body->'contract_version')='number',false);
 IF EXISTS(SELECT 1 FROM workflow.task_alignment WHERE task_id=t.id AND contract_version=2) AND NOT v2
 THEN RAISE EXCEPTION 'alignment_contract_upgrade_required' USING ERRCODE='P0001'; END IF;
 IF t.status IN ('completed','cancelled','pending_review') OR (t.execution_mode='execution_v3' AND EXISTS(SELECT 1 FROM workflow.task_execution WHERE task_id=t.id AND state->>'processing_reason'='awaiting_review')) THEN RAISE EXCEPTION '任务当前不可修改对齐关系' USING ERRCODE='P0001'; END IF;
 IF NOT is_new AND (p_expected_version IS NULL OR p_expected_version<>t.version_no)
 THEN RAISE EXCEPTION '任务已更新，请刷新后提交' USING ERRCODE='P0001'; END IF;
 IF is_new AND p_expected_version IS NOT NULL AND p_expected_version<>t.version_no
 THEN RAISE EXCEPTION '任务版本不一致' USING ERRCODE='P0001'; END IF;
 IF p_body IS NULL OR jsonb_typeof(p_body)<>'object'
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) k WHERE
   (v2 AND k NOT IN ('contract_version','target_ids','key_result_ids','other_reason','expected_contribution'))
   OR (NOT v2 AND k NOT IN ('metric_codes','key_result_ids','expected_contribution')))
  OR jsonb_typeof(p_body->'key_result_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'key_result_ids')>20
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'key_result_ids') x WHERE jsonb_typeof(x)<>'string')
 THEN RAISE EXCEPTION '任务关联格式无效' USING ERRCODE='22023'; END IF;
 SELECT COALESCE(array_agg(DISTINCT value::uuid ORDER BY value::uuid),'{}'::uuid[]) INTO ids FROM jsonb_array_elements_text(p_body->'key_result_ids');
 codes:='{}'; target_ids:='{}';
 IF v2 THEN
  IF jsonb_typeof(p_body->'target_ids') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'target_ids')>20
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'target_ids') x WHERE jsonb_typeof(x)<>'string')
   OR (p_body->'other_reason' IS NOT NULL AND p_body->'other_reason'<>'null'::jsonb AND jsonb_typeof(p_body->'other_reason')<>'string')
  THEN RAISE EXCEPTION '目标或其他原因格式无效' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(array_agg(DISTINCT value::uuid ORDER BY value::uuid),'{}'::uuid[]) INTO target_ids FROM jsonb_array_elements_text(p_body->'target_ids');
  reason:=NULLIF(btrim(p_body->>'other_reason',U&'\0009\000A\000B\000C\000D\0020\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000\FEFF'),'');
  IF ((cardinality(target_ids)+cardinality(ids)>0)=(reason IS NOT NULL)) OR char_length(reason)>2000
   OR (reason IS NOT NULL AND p_body->'expected_contribution' IS NOT NULL AND p_body->'expected_contribution'<>'null'::jsonb)
  THEN RAISE EXCEPTION '请选择具体目标或KR，或填写其他必要性原因' USING ERRCODE='22023'; END IF;
 ELSE
  IF jsonb_typeof(p_body->'metric_codes') IS DISTINCT FROM 'array' OR jsonb_array_length(p_body->'metric_codes')>20
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'metric_codes') x WHERE jsonb_typeof(x)<>'string')
  THEN RAISE EXCEPTION '任务指标格式无效' USING ERRCODE='22023'; END IF;
  SELECT COALESCE(array_agg(DISTINCT value ORDER BY value),'{}'::text[]) INTO codes FROM jsonb_array_elements_text(p_body->'metric_codes');
  IF cardinality(codes)+cardinality(ids)=0 THEN RAISE EXCEPTION '任务至少选择一个指标或类别' USING ERRCODE='22023'; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM workflow.task_target_alignment l JOIN crm.sales_target s ON s.id=l.target_id AND s.workspace_id=l.workspace_id
  WHERE l.task_id=t.id AND NOT security.target_scope_read(s.scope_type,s.user_ref_id,s.team_id,s.department_code))
 THEN RAISE insufficient_privilege; END IF;
 -- Deterministic shared locks preserve the selected object identity until commit.
 PERFORM s.id FROM crm.sales_target s WHERE s.id=ANY(target_ids) AND s.workspace_id=t.workspace_id ORDER BY s.id FOR SHARE;
 IF (SELECT count(*) FROM crm.sales_target s JOIN crm.review_metric m ON m.workspace_id=s.workspace_id AND m.code=s.kind
   WHERE s.id=ANY(target_ids) AND s.workspace_id=t.workspace_id
    AND security.target_scope_read(s.scope_type,s.user_ref_id,s.team_id,s.department_code)
    AND m.active AND m.goal_selectable)<>cardinality(target_ids)
 THEN RAISE insufficient_privilege; END IF;
 -- An editor cannot unknowingly drop a hidden link while replacing the visible set.
 IF EXISTS(SELECT 1 FROM workflow.task_key_result_alignment k WHERE k.task_id=t.id AND NOT security.review_key_result_readable(k.key_result_id))
 THEN RAISE insufficient_privilege; END IF;
 FOREACH kr IN ARRAY ids LOOP
  PERFORM 1 FROM crm.review_key_result k JOIN crm.review_objective o ON o.id=k.objective_id AND o.workspace_id=k.workspace_id
   WHERE k.id=kr AND k.workspace_id=t.workspace_id FOR SHARE OF k,o;
  IF NOT FOUND OR NOT security.review_key_result_readable(kr) THEN RAISE insufficient_privilege; END IF;
 END LOOP;
 FOR v_metric_code IN SELECT code FROM (SELECT unnest(codes) code UNION SELECT k.metric_code FROM crm.review_key_result k WHERE k.id=ANY(ids) AND k.workspace_id=t.workspace_id UNION SELECT s.kind FROM crm.sales_target s WHERE s.id=ANY(target_ids) AND s.workspace_id=t.workspace_id) selected ORDER BY code LOOP
  PERFORM 1 FROM crm.review_metric m WHERE m.workspace_id=t.workspace_id AND m.code=v_metric_code AND m.active FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION '任务指标不存在或已停用' USING ERRCODE='22023'; END IF;
 END LOOP;
 INSERT INTO workflow.task_alignment(task_id,workspace_id,updated_by_user_ref_id)
 VALUES(t.id,t.workspace_id,common.current_user_ref_id()) ON CONFLICT(task_id) DO NOTHING;
 DELETE FROM workflow.task_target_alignment WHERE task_id=t.id;
 INSERT INTO workflow.task_target_alignment(task_id,workspace_id,target_id) SELECT t.id,t.workspace_id,unnest(target_ids);
 DELETE FROM workflow.task_metric_alignment WHERE task_id=t.id;
 DELETE FROM workflow.task_key_result_alignment WHERE task_id=t.id;
 INSERT INTO workflow.task_key_result_alignment(task_id,workspace_id,key_result_id) SELECT t.id,t.workspace_id,unnest(ids);
 INSERT INTO workflow.task_metric_alignment(task_id,workspace_id,metric_code,direct)
 SELECT t.id,t.workspace_id,c,c=ANY(codes) FROM (
  SELECT unnest(codes) c UNION SELECT k.metric_code FROM crm.review_key_result k WHERE k.id=ANY(ids) AND k.workspace_id=t.workspace_id
  UNION SELECT s.kind FROM crm.sales_target s WHERE s.id=ANY(target_ids) AND s.workspace_id=t.workspace_id
 ) all_codes;
 contribution:=security.validate_task_contribution(t.id,p_body->'expected_contribution',false);
 -- A previously reported claim cannot be reassigned to another metric by editing links.
 IF EXISTS(SELECT 1 FROM workflow.task_alignment a WHERE a.task_id=t.id AND a.actual_contribution IS NOT NULL
   AND NOT EXISTS(SELECT 1 FROM workflow.task_metric_alignment m WHERE m.task_id=t.id AND m.metric_code=a.actual_contribution->>'metric_code'))
 THEN RAISE EXCEPTION '已报告贡献所属指标须保留' USING ERRCODE='P0001'; END IF;
 UPDATE workflow.task_alignment SET contract_version=CASE WHEN v2 THEN 2 ELSE 1 END,other_reason=reason,expected_contribution=contribution,updated_by_user_ref_id=common.current_user_ref_id(),updated_at=clock_timestamp() WHERE task_id=t.id;
 IF NOT is_new THEN UPDATE workflow.task SET version_no=version_no+1 WHERE id=t.id RETURNING * INTO t; END IF;
 saved:=security.task_alignment_read(t.id);
 INSERT INTO workflow.task_alignment_history(task_id,workspace_id,task_version,event_kind,snapshot,actor_user_ref_id)
 VALUES(t.id,t.workspace_id,t.version_no,'alignment',saved,common.current_user_ref_id());
 RETURN saved;
END $$;

-- Preserve the exact V1/V2 implementation but close its adoption entrypoint to
-- V3 before it can change mode. The archived function is owner-only.
ALTER FUNCTION workflow.task_execution_command(uuid,text,jsonb,uuid) RENAME TO task_execution_command_before_v3;
CREATE FUNCTION workflow.task_execution_command(p_task uuid,p_action text,p_body jsonb,p_key uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE current_mode text;
BEGIN
 -- Serialize protocol admission with explicit adoption; never check mode before
 -- waiting for the lock and then invoke a legacy command on newly adopted V3.
 SELECT execution_mode INTO current_mode FROM workflow.task WHERE id=p_task
  AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF current_mode='execution_v3'
 THEN RAISE EXCEPTION 'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED' USING ERRCODE='40001'; END IF;
 RETURN workflow.task_execution_command_before_v3(p_task,p_action,p_body,p_key);
END $$;
DO $private_old_command$
DECLARE grantee_name name; old_owner name; permission record;
BEGIN
 -- Rename keeps the deployed OID/owner/ACL on the archived implementation.
 -- Transfer precisely that public entrypoint ownership and grants to the new
 -- wrapper before restricting the archive. No installation matrix is replayed.
 SELECT pg_get_userbyid(proowner) INTO old_owner FROM pg_proc
 WHERE oid='workflow.task_execution_command_before_v3(uuid,text,jsonb,uuid)'::regprocedure;
 FOR grantee_name IN SELECT DISTINCT r.rolname FROM pg_proc p
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 JOIN pg_roles r ON r.oid=a.grantee
 WHERE p.oid='workflow.task_execution_command(uuid,text,jsonb,uuid)'::regprocedure AND a.grantee<>p.proowner LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION workflow.task_execution_command(uuid,text,jsonb,uuid) FROM %I',grantee_name);
 END LOOP;
 REVOKE ALL ON FUNCTION workflow.task_execution_command(uuid,text,jsonb,uuid) FROM PUBLIC;
 EXECUTE format('ALTER FUNCTION workflow.task_execution_command(uuid,text,jsonb,uuid) OWNER TO %I',old_owner);
 FOR permission IN SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) END grantee,
  a.is_grantable FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 WHERE p.oid='workflow.task_execution_command_before_v3(uuid,text,jsonb,uuid)'::regprocedure AND a.grantee<>p.proowner LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION workflow.task_execution_command(uuid,text,jsonb,uuid) TO %s%s',
   permission.grantee,CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
 END LOOP;
 FOR grantee_name IN SELECT r.rolname FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl
  JOIN pg_roles r ON r.oid=acl.grantee WHERE p.oid='workflow.task_execution_command_before_v3(uuid,text,jsonb,uuid)'::regprocedure
  AND acl.grantee<>p.proowner LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION workflow.task_execution_command_before_v3(uuid,text,jsonb,uuid) FROM %I',grantee_name);
 END LOOP;
END $private_old_command$;
REVOKE ALL ON FUNCTION workflow.task_execution_command_before_v3(uuid,text,jsonb,uuid) FROM PUBLIC,salegent_feishu_worker;

-- Existing function ACLs above are preserved. New entrypoints are separately
-- admitted by the installer; helpers never receive runtime table-write grants.
REVOKE ALL ON FUNCTION workflow.task_execution_authorized_v3(uuid,text),
 workflow.task_execution_capabilities_v3(uuid),workflow.task_execution_summary_v3(uuid),
 workflow.task_execution_initialize_v3(uuid,jsonb),workflow.task_execution_command_v3(uuid,text,jsonb,uuid),
 workflow.task_execution_adoption_v3(uuid),workflow.task_execution_adopt_v3(uuid,jsonb,uuid)
 FROM PUBLIC,salegent_feishu_worker;
-- Add only protocol/state scalars to the established restricted projection.
-- The original V435 checksum stays unchanged for already upgraded local DBs.
CREATE OR REPLACE FUNCTION security.demo_readonly_read(p_session uuid,p_workspace uuid,p_identity uuid,p_permission_version integer,
 p_resource text,p_id uuid,p_query text,p_offset integer,p_limit integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET statement_timeout='5s' AS $$
DECLARE session_data jsonb; result jsonb;
BEGIN
 IF current_setting('transaction_read_only')<>'on' THEN RAISE EXCEPTION 'DEMO_READ_ONLY_REQUIRED' USING ERRCODE='42501'; END IF;
 session_data:=security.demo_readonly_session(p_session,p_workspace,p_identity,p_permission_version);
 IF session_data IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_ref_id','',true),set_config('app.role_code','',true),set_config('app.authorization_context','',true),
 set_config('app.workspace_id',p_workspace::text,true),set_config('app.demo_readonly_context',session_data::text,true);
 IF p_resource NOT IN ('overview','customers','opportunities','follow-ups','tasks') OR p_resource IS NULL
 OR p_offset IS NULL OR p_offset<0 OR p_offset>10000 OR p_limit IS NULL OR p_limit<1 OR p_limit>50
 OR length(COALESCE(p_query,''))>100 THEN RAISE EXCEPTION 'DEMO_QUERY_INVALID' USING ERRCODE='22023'; END IF;
 IF p_resource='overview' THEN
  SELECT jsonb_build_object('customer_count',(SELECT count(*) FROM crm.customer WHERE workspace_id=p_workspace AND deleted_at IS NULL),
   'opportunity_count',(SELECT count(*) FROM crm.opportunity WHERE workspace_id=p_workspace AND deleted_at IS NULL),
   'follow_up_count',(SELECT count(*) FROM activity.visit WHERE workspace_id=p_workspace AND deleted_at IS NULL AND status IN ('confirmed','archived')),
   'task_count',(SELECT count(*) FROM workflow.task WHERE workspace_id=p_workspace AND deleted_at IS NULL),
   'data_version',session_data->>'data_version') INTO result;
  RETURN result;
 END IF;
 -- Explicit DTOs are the only output. No attributes, staff, contact, raw text,
 -- model output, upload or media fields are selected.
 IF p_resource='customers' THEN
  WITH visible AS (
   SELECT c.id,c.name,c.updated_at,jsonb_build_object('id',c.id,'name',c.name,'industry',c.industry_code,
    'level',c.level_code,'stage',c.lifecycle_status,'description',x.display_text,'updated_at',c.updated_at) item
   FROM crm.customer c LEFT JOIN security.demo_readonly_text x ON x.workspace_id=c.workspace_id AND x.resource='customers'
    AND x.resource_id=c.id AND x.source_updated_at=c.updated_at
   WHERE c.workspace_id=p_workspace AND c.deleted_at IS NULL AND (p_id IS NULL OR c.id=p_id)
    AND (COALESCE(p_query,'')='' OR strpos(lower(c.name),lower(p_query))>0)
  ), page AS (SELECT item FROM visible ORDER BY updated_at DESC,id LIMIT p_limit OFFSET p_offset)
  SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM visible)) INTO result;
 ELSIF p_resource='opportunities' THEN
  WITH visible AS (
   SELECT o.id,o.name,o.updated_at,jsonb_build_object('id',o.id,'name',o.name,'customer',CASE WHEN c.id IS NOT NULL THEN jsonb_build_object('id',c.id,'name',c.name) END,
    'product',o.product_line,'stage',CASE WHEN o.status IN ('won','lost') THEN o.status
     WHEN o.probability=10 THEN 'identified' WHEN o.probability=30 THEN 'qualified'
     WHEN o.probability=50 THEN 'solution' WHEN o.probability=70 THEN 'proposal'
     WHEN o.probability=90 THEN 'negotiation' ELSE o.stage_code END,'amount',o.amount,'expected_close_date',o.expected_close_date,'updated_at',o.updated_at) item
   FROM crm.opportunity o LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
   WHERE o.workspace_id=p_workspace AND o.deleted_at IS NULL AND (p_id IS NULL OR o.id=p_id)
    AND (COALESCE(p_query,'')='' OR strpos(lower(o.name),lower(p_query))>0)
  ), page AS (SELECT item FROM visible ORDER BY updated_at DESC,id LIMIT p_limit OFFSET p_offset)
  SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM visible)) INTO result;
 ELSIF p_resource='follow-ups' THEN
  WITH visible AS (
   SELECT v.id,v.updated_at,jsonb_build_object('id',v.id,
    'customer',CASE WHEN c.id IS NOT NULL THEN jsonb_build_object('id',c.id,'name',c.name) END,
    'opportunity',CASE WHEN o.id IS NOT NULL THEN jsonb_build_object('id',o.id,'name',o.name) END,
    'follow_up_date',(v.interaction_at AT TIME ZONE 'Asia/Shanghai')::date,'type',v.interaction_mode_code,'display_text',x.display_text,'updated_at',v.updated_at) item
   FROM activity.visit v LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id AND c.deleted_at IS NULL
   LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id AND o.deleted_at IS NULL
   LEFT JOIN security.demo_readonly_text x ON x.workspace_id=v.workspace_id AND x.resource='follow-ups'
    AND x.resource_id=v.id AND x.source_updated_at=v.updated_at
   WHERE v.workspace_id=p_workspace AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND (p_id IS NULL OR v.id=p_id)
    AND (COALESCE(p_query,'')='' OR strpos(lower(COALESCE(c.name,'')),lower(p_query))>0 OR strpos(lower(COALESCE(x.display_text,'')),lower(p_query))>0)
  ), page AS (SELECT item FROM visible ORDER BY updated_at DESC,id LIMIT p_limit OFFSET p_offset)
  SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM visible)) INTO result;
 ELSE
  WITH visible AS (
   SELECT t.id,t.updated_at,jsonb_build_object('id',t.id,'title',t.title,'status',t.status,'due_at',t.due_at,
    'execution_mode',t.execution_mode,'processing_reason',CASE WHEN t.execution_mode='execution_v3' AND e.state->>'processing_reason' IN ('adjustment_request','paused','awaiting_review') THEN e.state->>'processing_reason' END,
    'progress_percent',CASE WHEN t.execution_mode<>'execution_v3' AND e.task_id IS NOT NULL THEN workflow.task_execution_progress(e.state)->'percent' END,
    'description',x.display_text,'customer',CASE WHEN c.id IS NOT NULL THEN jsonb_build_object('id',c.id,'name',c.name) END,
    'opportunity',CASE WHEN o.id IS NOT NULL THEN jsonb_build_object('id',o.id,'name',o.name) END,'updated_at',t.updated_at) item
   FROM workflow.task t LEFT JOIN crm.customer c ON c.id=t.customer_id AND c.workspace_id=t.workspace_id AND c.deleted_at IS NULL
   LEFT JOIN crm.opportunity o ON o.id=t.opportunity_id AND o.workspace_id=t.workspace_id AND o.deleted_at IS NULL
   LEFT JOIN workflow.task_execution e ON e.task_id=t.id AND e.workspace_id=t.workspace_id
   LEFT JOIN security.demo_readonly_text x ON x.workspace_id=t.workspace_id AND x.resource='tasks'
    AND x.resource_id=t.id AND x.source_updated_at=t.updated_at
   WHERE t.workspace_id=p_workspace AND t.deleted_at IS NULL AND (p_id IS NULL OR t.id=p_id)
    AND (COALESCE(p_query,'')='' OR strpos(lower(t.title),lower(p_query))>0)
  ), page AS (SELECT item FROM visible ORDER BY updated_at DESC,id LIMIT p_limit OFFSET p_offset)
  SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM visible)) INTO result;
 END IF;
 IF p_id IS NOT NULL THEN RETURN result->'items'->0; END IF;
 RETURN result||jsonb_build_object('offset',p_offset,'limit',p_limit,'data_version',session_data->>'data_version');
END $$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V436','Four-state lightweight tasks with immutable completion rounds creator review and explicit adoption');
COMMIT;
