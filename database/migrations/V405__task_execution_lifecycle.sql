BEGIN;

INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('task.feedback','任务过程反馈','待办事项',ARRAY['self','assigned','teams','workspace'],false),
 ('task.reviewer_handover','交接任务验收责任','待办事项',ARRAY['self','assigned','teams','workspace'],true);
INSERT INTO control.permission_module(permission_code,module_code) VALUES
 ('task.feedback','task'),('task.reviewer_handover','task');
-- Future tenants only. Never backfill previously published tenant role grants.
UPDATE config.permission_role_default SET permissions=permissions||
 '[{"permission":"task.feedback","scope":"inherit","team_ids":[]}]'::jsonb
 WHERE permissions @> '[{"permission":"task.review"}]'::jsonb;
UPDATE config.permission_role_default SET permissions=permissions||
 '[{"permission":"task.reviewer_handover","scope":"inherit","team_ids":[]}]'::jsonb
 WHERE permissions @> '[{"permission":"task.coordinate"}]'::jsonb;

-- All mutable projections are private to explicitly authorized commands. Runtime
-- roles receive SELECT/EXECUTE, never INSERT/UPDATE/DELETE on these tables.
CREATE TABLE workflow.task_execution (
 task_id uuid PRIMARY KEY REFERENCES workflow.task(id), workspace_id uuid NOT NULL,
 state jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task(id,workspace_id)
);
CREATE TABLE workflow.task_execution_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), task_id uuid NOT NULL,
 workspace_id uuid NOT NULL, actor_user_ref_id uuid NOT NULL, kind text NOT NULL,
 body jsonb NOT NULL, occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task(id,workspace_id)
);
CREATE INDEX task_execution_events_order ON workflow.task_execution_event(task_id,occurred_at,id);
CREATE TABLE workflow.task_execution_submission (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),task_id uuid NOT NULL,workspace_id uuid NOT NULL,
 actor_user_ref_id uuid NOT NULL,snapshot jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task(id,workspace_id)
);
CREATE TABLE workflow.task_execution_request (
 task_id uuid NOT NULL,workspace_id uuid NOT NULL,actor_user_ref_id uuid NOT NULL,key uuid NOT NULL,
 action text NOT NULL,body jsonb NOT NULL,response jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,key),FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task(id,workspace_id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['task_execution','task_execution_event','task_execution_submission','task_execution_request'] LOOP
  EXECUTE format('ALTER TABLE workflow.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY execution_read ON workflow.%I FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.authorization_task(''task.read'',task_id))',t);
  EXECUTE format('REVOKE ALL ON workflow.%I FROM PUBLIC,salegent_feishu_worker',t);
 END LOOP;
END $$;

CREATE FUNCTION workflow.task_execution_initial(p_task uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('protocol_version',1,'requirements_version',1,'responsibility_version',1,
 'current_reviewer',t.creator_user_ref_id,'executor_transferred',false,
 'requirements',jsonb_build_object('title',t.title,'description',t.description,
  'deliverable','','acceptance_criteria','','due_at',t.due_at,'priority_code',t.priority_code,'checklist','[]'::jsonb),
 'items','[]'::jsonb,'pause',NULL,'execution_handover',NULL,'reviewer_handover',NULL,
 'pending_requirements',NULL,'current_submission',NULL,'first_started_at',NULL,'last_execution_feedback_at',NULL)
 FROM workflow.task t WHERE t.id=p_task;
$$;
-- Legacy rows retain unknown progress; migration never manufactures completion.
INSERT INTO workflow.task_execution(task_id,workspace_id,state)
 SELECT id,workspace_id,workflow.task_execution_initial(id) FROM workflow.task;

-- Evaluate another participant's live grants without impersonating it in GUCs.
CREATE FUNCTION workflow.task_execution_user_allows(p_task uuid,p_user uuid,p_permission text,p_owner uuid,p_assigned boolean)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH task AS (SELECT * FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL),
 grants AS MATERIALIZED (SELECT g.* FROM task t CROSS JOIN LATERAL security.authorization_grants_for(t.workspace_id,p_user) g),
 teams AS (SELECT creator_team_id id FROM task UNION SELECT a.assignee_team_id FROM workflow.task_assignee a JOIN task t ON a.task_id=t.id)
 SELECT EXISTS(SELECT 1 FROM task) AND NOT EXISTS(SELECT 1 FROM grants WHERE permission_code=p_permission AND effect='deny')
 AND EXISTS(SELECT 1 FROM grants WHERE permission_code=p_permission AND effect='allow' AND
 (scope_code='workspace' OR (scope_code='self' AND p_owner=p_user) OR (scope_code='assigned' AND p_assigned)
  OR (scope_code='teams' AND team_ids&&ARRAY(SELECT id FROM teams))));
$$;
CREATE FUNCTION workflow.task_execution_person(p_task uuid,p_user uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('user_id',u.id,'name',u.display_name,'role_code',r.role_code,'team_id',r.team_id)
 FROM workflow.task t JOIN platform.user_ref u ON u.id=p_user AND u.workspace_id=t.workspace_id
 JOIN platform.role_binding r ON r.user_ref_id=u.id AND r.workspace_id=u.workspace_id
 WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
 AND u.status='active' AND u.deleted_at IS NULL AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
 AND r.role_code IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead')
 AND (r.role_code NOT IN ('fde','fde_lead') OR security.fde_user_is_active(u.id,r.role_code,r.team_id))
 ORDER BY r.valid_from DESC,r.role_code LIMIT 1;
$$;

-- Current review responsibility and a pending candidate are task participants;
-- a rejected/superseded review candidate obtains no continuing assignment.
CREATE OR REPLACE FUNCTION security.task_actor_recipient(p_task uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=p_task
  AND a.workspace_id=common.current_workspace_id() AND a.assignee_user_ref_id=common.current_user_ref_id()
  AND security.task_recipient_eligible(p_task,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=p_task
  AND c.workspace_id=common.current_workspace_id() AND c.user_ref_id=common.current_user_ref_id()
  AND security.task_recipient_eligible(p_task,c.user_ref_id,c.role_code,c.team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_execution x WHERE x.task_id=p_task AND x.workspace_id=common.current_workspace_id()
  AND (common.current_user_ref_id()=(x.state->>'current_reviewer')::uuid
   OR (x.state->'reviewer_handover'->>'status'='pending' AND common.current_user_ref_id()=(x.state->'reviewer_handover'->>'target_user_id')::uuid))
  AND workflow.task_execution_person(p_task,common.current_user_ref_id()) IS NOT NULL);
$$;

-- The review scope follows current review responsibility; the immutable creator
-- continues to describe provenance. All other existing task grants stay intact.
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
 FROM workflow.task t LEFT JOIN workflow.task_execution x ON x.task_id=t.id
 WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL),false);
$$;

CREATE FUNCTION workflow.task_execution_progress(p_state jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 WITH counts AS (SELECT count(*)::int total,
 count(*) FILTER(WHERE value->>'state'='completed' AND NOT COALESCE((value->>'requires_revalidation')::boolean,false))::int completed,
 COALESCE(bool_or(COALESCE((value->>'requires_revalidation')::boolean,false)),false) revalidate
 FROM jsonb_array_elements(p_state->'items'))
 SELECT jsonb_build_object('requirements_version',(p_state->>'requirements_version')::int,'completed',completed,'total',total,
 'percent',CASE WHEN total=0 OR revalidate THEN NULL ELSE completed*100/total END,
 'basis',CASE WHEN total=0 THEN 'missing_checklist' WHEN revalidate THEN 'requires_revalidation' ELSE 'checklist' END) FROM counts;
$$;

CREATE FUNCTION workflow.task_execution_capabilities(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; a uuid:=common.current_user_ref_id(); owner_id uuid; owner_ok boolean; review_ok boolean;
 coordinate_ok boolean; feedback_ok boolean; executing boolean; live boolean; handover boolean;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND OR NOT security.authorization_task('task.read',p_task) THEN RETURN '{}'::jsonb; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 owner_ok:=a=owner_id AND workflow.task_execution_person(p_task,a) IS NOT NULL AND security.authorization_task('task.complete',p_task);
 review_ok:=a=(s->>'current_reviewer')::uuid AND a IS DISTINCT FROM owner_id AND security.authorization_task('task.review',p_task);
 coordinate_ok:=security.authorization_task('task.coordinate',p_task);
 feedback_ok:=security.authorization_task('task.feedback',p_task) AND (a=owner_id OR a=(s->>'current_reviewer')::uuid OR coordinate_ok
  OR EXISTS(SELECT 1 FROM workflow.task_assignee WHERE task_id=p_task AND assignee_user_ref_id=a));
 live:=t.status NOT IN ('completed','cancelled');
 handover:=COALESCE(s->'execution_handover'->>'status' IN ('pending','rejected'),false);
 executing:=live AND t.status IN ('pending_execution','in_progress') AND NOT handover AND s->>'pause' IS NULL;
 RETURN jsonb_build_object('set_item_progress',COALESCE(owner_ok AND executing,false),
 'append_feedback',COALESCE(feedback_ok AND live,false),
 'pause',COALESCE((owner_ok OR coordinate_ok) AND t.status IN ('pending_execution','in_progress') AND s->>'pause' IS NULL,false),
 'recheck_pause',COALESCE((owner_ok OR coordinate_ok) AND live AND s->>'pause' IS NOT NULL,false),
 'resume',COALESCE((owner_ok OR coordinate_ok) AND live AND NOT handover AND s->>'pause' IS NOT NULL
  AND workflow.task_execution_person(p_task,owner_id) IS NOT NULL
  AND workflow.task_execution_user_allows(p_task,owner_id,'task.complete',owner_id,true),false),
 'initiate_executor_handover',COALESCE(coordinate_ok AND live AND t.status<>'pending_review',false),
 'initiate_reviewer_handover',COALESCE(security.authorization_task('task.reviewer_handover',p_task) AND live,false),
 'respond_executor_handover',COALESCE(a=(s->'execution_handover'->>'target_user_id')::uuid AND live,false),
 'respond_reviewer_handover',COALESCE(a=(s->'reviewer_handover'->>'target_user_id')::uuid AND live,false),
 'propose_requirements',COALESCE((owner_ok OR review_ok OR coordinate_ok OR (a=(s->>'current_reviewer')::uuid AND security.authorization_task('task.review',p_task)))
  AND live AND t.status<>'pending_review' AND NOT handover AND s->'reviewer_handover'->>'status' IS DISTINCT FROM 'pending' AND s->>'pending_requirements' IS NULL,false),
 'respond_requirements',COALESCE(live AND t.status<>'pending_review' AND (owner_ok OR review_ok OR (a=(s->>'current_reviewer')::uuid AND security.authorization_task('task.review',p_task))),false),
 'withdraw_requirements',COALESCE(live AND (a=(s->'pending_requirements'->>'proposer_id')::uuid OR coordinate_ok),false),
 'submit_completion',COALESCE(owner_ok AND executing AND s->>'pending_requirements' IS NULL AND (workflow.task_execution_progress(s)->>'percent')::int=100,false),
 'review_completion',COALESCE(review_ok AND t.status='pending_review',false),
 'withdraw_completion',COALESCE(owner_ok AND t.status='pending_review',false),
 'accept_task',COALESCE(live AND t.status='pending_confirm' AND NOT handover AND security.task_actor_recipient(p_task) AND security.authorization_task('task.accept',p_task),false),
 'decline_task',COALESCE(live AND t.status='pending_confirm' AND NOT handover AND security.task_actor_recipient(p_task) AND security.authorization_task('task.decline',p_task),false));
END $$;

-- One shared final-target predicate serves both candidate discovery and writes.
CREATE FUNCTION workflow.task_execution_candidate(p_task uuid,p_user uuid,p_purpose text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s jsonb; owner_id uuid; person jsonb;
BEGIN
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 person:=workflow.task_execution_person(p_task,p_user);
 IF p_purpose NOT IN ('executor','reviewer') OR person IS NULL OR p_user=owner_id OR p_user=(s->>'current_reviewer')::uuid
  OR NOT workflow.task_execution_user_allows(p_task,p_user,'task.read',p_user,true)
 THEN RETURN NULL; END IF;
 IF p_purpose='executor' AND (NOT workflow.task_execution_user_allows(p_task,p_user,'task.accept',p_user,true)
  OR NOT workflow.task_execution_user_allows(p_task,p_user,'task.complete',p_user,true)) THEN RETURN NULL; END IF;
 IF p_purpose='reviewer' AND NOT workflow.task_execution_user_allows(p_task,p_user,'task.review',p_user,true) THEN RETURN NULL; END IF;
 RETURN person;
END $$;

CREATE FUNCTION workflow.task_execution_candidates(p_task uuid,p_purpose text,p_query text,p_limit integer,p_offset integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rows jsonb; total_count integer; cap jsonb;
BEGIN
 IF p_purpose IS NULL OR p_purpose NOT IN ('executor','reviewer') OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
  OR p_offset IS NULL OR p_offset<0 OR length(COALESCE(p_query,''))>100
 THEN RAISE EXCEPTION 'TASK_CANDIDATES_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 cap:=workflow.task_execution_capabilities(p_task);
 IF NOT COALESCE((cap->>('initiate_'||p_purpose||'_handover'))::boolean,false)
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

CREATE FUNCTION workflow.task_execution_summary(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb; owner_id uuid;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_NOT_FOUND' USING ERRCODE='P0002'; END IF;
 IF NOT security.authorization_task('task.read',p_task) THEN RAISE EXCEPTION 'TASK_EXECUTION_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task;
 s:=COALESCE(s,workflow.task_execution_initial(p_task));
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 RETURN s || jsonb_build_object('task_id',p_task,'status',t.status,'version_no',t.version_no,'creator_user_ref_id',t.creator_user_ref_id,
 'executor',workflow.task_execution_person(p_task,owner_id),'executor_user_id',owner_id,
 'reviewer',workflow.task_execution_person(p_task,(s->>'current_reviewer')::uuid),
 'progress',workflow.task_execution_progress(s),'checklist',s->'items','capabilities',workflow.task_execution_capabilities(p_task));
END $$;

-- Detail shares the exact core projection and object authority with list reads.
CREATE FUNCTION workflow.task_execution_read(p_task uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE summary jsonb;
BEGIN
 summary:=workflow.task_execution_summary(p_task);
 RETURN summary || jsonb_build_object('events',COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.occurred_at,e.id) FROM
  (SELECT id,actor_user_ref_id,kind,body,body AS payload,occurred_at FROM workflow.task_execution_event WHERE task_id=p_task ORDER BY occurred_at DESC,id DESC LIMIT 200) e),'[]'::jsonb),
 'submissions',COALESCE((SELECT jsonb_agg(to_jsonb(q) ORDER BY q.created_at,q.id) FROM
  (SELECT id,actor_user_ref_id,snapshot,created_at FROM workflow.task_execution_submission WHERE task_id=p_task ORDER BY created_at DESC,id DESC LIMIT 50) q),'[]'::jsonb));
END $$;

CREATE FUNCTION workflow.task_execution_validate_requirements(p jsonb) RETURNS void
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE i jsonb;
BEGIN
 IF jsonb_typeof(p)<>'object' OR p->>'title' IS NULL OR length(btrim(p->>'title')) NOT BETWEEN 1 AND 500
 OR p->>'priority_code' NOT IN ('normal','medium','high','urgent') OR p->>'due_at' IS NULL
 OR jsonb_typeof(p->'checklist') IS DISTINCT FROM 'array' OR jsonb_array_length(p->'checklist') NOT BETWEEN 1 AND 100
 OR (SELECT count(*)<>count(DISTINCT value->>'item_id') FROM jsonb_array_elements(p->'checklist'))
 OR p - ARRAY['title','description','deliverable','acceptance_criteria','due_at','priority_code','checklist'] <> '{}'::jsonb
 THEN RAISE EXCEPTION 'TASK_REQUIREMENTS_INVALID' USING ERRCODE='22023'; END IF;
 PERFORM (p->>'due_at')::timestamptz;
 FOR i IN SELECT value FROM jsonb_array_elements(p->'checklist') LOOP
  IF i->>'item_id' IS NULL OR jsonb_typeof(i->'definition_version') IS DISTINCT FROM 'number'
   OR (i->>'definition_version')::int<1 OR length(btrim(COALESCE(i->>'title',''))) NOT BETWEEN 1 AND 500
   OR length(btrim(COALESCE(i->>'acceptance_criteria',''))) NOT BETWEEN 1 AND 2000
   OR i - ARRAY['item_id','definition_version','title','acceptance_criteria']<>'{}'::jsonb
  THEN RAISE EXCEPTION 'TASK_CHECKLIST_INVALID' USING ERRCODE='22023'; END IF;
  PERFORM (i->>'item_id')::uuid;
 END LOOP;
END $$;

CREATE FUNCTION workflow.task_execution_initialize(p_task uuid,p_checklist jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; s jsonb;
BEGIN
 SELECT * INTO t FROM workflow.task WHERE id=p_task AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND OR NOT security.authorization_task_created(p_task) THEN RAISE EXCEPTION 'TASK_INITIALIZE_FORBIDDEN' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM workflow.task_execution WHERE task_id=p_task) THEN RAISE EXCEPTION 'TASK_ALREADY_INITIALIZED' USING ERRCODE='40001'; END IF;
 s:=workflow.task_execution_initial(p_task);
 s:=jsonb_set(s,'{requirements,checklist}',p_checklist);
 PERFORM workflow.task_execution_validate_requirements(s->'requirements');
 s:=jsonb_set(s,'{items}',(SELECT jsonb_agg(value||jsonb_build_object('state','not_started','note','','requires_revalidation',false)) FROM jsonb_array_elements(p_checklist)));
 INSERT INTO workflow.task_execution(task_id,workspace_id,state) VALUES(p_task,t.workspace_id,s);
 RETURN workflow.task_execution_read(p_task);
END $$;

CREATE FUNCTION workflow.task_execution_command(p_task uuid,p_action text,p_body jsonb,p_key uuid) RETURNS jsonb
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
 SELECT state INTO s FROM workflow.task_execution WHERE task_id=p_task FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TASK_EXECUTION_NOT_INITIALIZED' USING ERRCODE='40001'; END IF;
 old_s:=s; next_task:=t; next_status:=t.status; reviewer_id:=(s->>'current_reviewer')::uuid;
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 self_task:=a=owner_id AND owner_id=t.creator_user_ref_id AND NOT (s->>'executor_transferred')::boolean;
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

-- Private worker helper: workspace comes from the task, never a caller-supplied
-- tenant or identity. Function-local setting is restored automatically on return.
CREATE FUNCTION workflow.task_execution_pause_recheck_recipients(p_task uuid) RETURNS SETOF uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET app.workspace_id='' AS $$
DECLARE ws uuid; s jsonb; owner_id uuid; creator_id uuid; target uuid; assigned boolean;
BEGIN
 SELECT t.workspace_id,x.state,t.creator_user_ref_id INTO ws,s,creator_id FROM workflow.task t JOIN workflow.task_execution x ON x.task_id=t.id
 WHERE t.id=p_task AND t.deleted_at IS NULL AND t.status NOT IN ('completed','cancelled');
 IF ws IS NULL OR s->>'pause' IS NULL THEN RETURN; END IF;
 PERFORM set_config('app.workspace_id',ws::text,true);
 SELECT assignee_user_ref_id INTO owner_id FROM workflow.task_assignee WHERE task_id=p_task AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1;
 FOR target IN SELECT DISTINCT id FROM (VALUES(owner_id),((s->>'current_reviewer')::uuid),((s->'pause'->>'actor_user_id')::uuid)) r(id)
  WHERE id IS NOT NULL LOOP
  assigned:=target=owner_id OR target=(s->>'current_reviewer')::uuid
   OR EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=p_task AND a.assignee_user_ref_id=target
    AND security.task_recipient_eligible(p_task,target,a.assignee_role,a.assignee_team_id))
   OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=p_task AND c.user_ref_id=target
    AND security.task_recipient_eligible(p_task,target,c.role_code,c.team_id));
  IF workflow.task_execution_person(p_task,target) IS NOT NULL
   AND workflow.task_execution_user_allows(p_task,target,'task.read',CASE WHEN assigned THEN target ELSE creator_id END,assigned)
   AND ((target=owner_id AND workflow.task_execution_user_allows(p_task,target,'task.complete',owner_id,true))
    OR (target=(s->>'current_reviewer')::uuid AND workflow.task_execution_user_allows(p_task,target,'task.review',target,true))
    OR workflow.task_execution_user_allows(p_task,target,'task.coordinate',creator_id,assigned))
  THEN RETURN NEXT target; END IF;
 END LOOP;
END $$;

-- Worker-only EXECUTE. No user/tenant/time parameter and no state transition.
-- A task lock serializes reminder creation against explicit resume/recheck.
CREATE FUNCTION workflow.task_execution_enqueue_pause_rechecks(p_limit integer) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row record; s jsonb; target uuid; inserted integer; total integer:=0; stamp timestamptz:=clock_timestamp();
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 1000
 THEN RAISE EXCEPTION 'TASK_PAUSE_RECHECK_LIMIT_INVALID' USING ERRCODE='22023'; END IF;
 -- Preserve read/cancelled history; queued reminders for obsolete rounds must
 -- not be delivered after a resume, a new recheck round, or permission loss.
 UPDATE workflow.notification n SET status='cancelled'
 WHERE n.template_code='task_pause_recheck' AND n.status NOT IN ('cancelled','read') AND NOT EXISTS(
  SELECT 1 FROM workflow.task t JOIN workflow.task_execution x ON x.task_id=t.id
  WHERE t.id=n.object_id AND t.workspace_id=n.workspace_id AND t.deleted_at IS NULL AND t.status NOT IN ('completed','cancelled')
   AND x.state->'pause'->>'id'=n.payload->>'pause_id'
   AND x.state->'pause'->>'recheck_version'=n.payload->>'recheck_version'
   AND n.recipient_user_ref_id IN (SELECT workflow.task_execution_pause_recheck_recipients(t.id)));
 FOR row IN
  SELECT t.id,t.workspace_id,t.title FROM workflow.task t JOIN workflow.task_execution x ON x.task_id=t.id
  WHERE t.deleted_at IS NULL AND t.status NOT IN ('completed','cancelled') AND x.state->>'pause' IS NOT NULL
   AND (x.state->'pause'->>'recheck_at')::timestamptz<=stamp
   AND EXISTS(SELECT 1 FROM workflow.task_execution_pause_recheck_recipients(t.id) r(recipient) WHERE NOT EXISTS(
    SELECT 1 FROM workflow.notification n WHERE n.workspace_id=t.workspace_id AND n.recipient_user_ref_id=r.recipient
     AND n.dedupe_key='task_pause_recheck:'||(x.state->'pause'->>'id')||':'||(x.state->'pause'->>'recheck_version')||':'||r.recipient::text))
  ORDER BY (x.state->'pause'->>'recheck_at')::timestamptz,t.id LIMIT p_limit FOR UPDATE OF t SKIP LOCKED
 LOOP
  -- A command may have committed while the initial scan was waiting for a row.
  SELECT x.state INTO s FROM workflow.task_execution x JOIN workflow.task t ON t.id=x.task_id
   WHERE x.task_id=row.id AND t.deleted_at IS NULL AND t.status NOT IN ('completed','cancelled');
  IF s->>'pause' IS NULL OR (s->'pause'->>'recheck_at')::timestamptz>stamp THEN CONTINUE; END IF;
  FOR target IN SELECT workflow.task_execution_pause_recheck_recipients(row.id) LOOP
   INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,channel_code,template_code,title,body,
    object_type,object_id,status,dedupe_key,scheduled_at,payload)
   VALUES(row.workspace_id,target,'in_app','task_pause_recheck','任务暂停待复查',row.title,'task',row.id,'pending',
    'task_pause_recheck:'||(s->'pause'->>'id')||':'||(s->'pause'->>'recheck_version')||':'||target::text,stamp,
    jsonb_build_object('task_id',row.id,'pause_id',s->'pause'->>'id','recheck_version',(s->'pause'->>'recheck_version')::int,
     'recheck_at',s->'pause'->>'recheck_at','reason',s->'pause'->>'reason','action','recheck_pause','monitored_at',stamp))
   ON CONFLICT(workspace_id,recipient_user_ref_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING;
   GET DIAGNOSTICS inserted=ROW_COUNT;
   total:=total+inserted;
  END LOOP;
 END LOOP;
 RETURN total;
END $$;

-- These guards have no app.* escape switch. Only the owner of the reviewed
-- SECURITY DEFINER command can write protected fields. Runtime role membership
-- or arbitrary set_config calls cannot manufacture that database identity.
CREATE FUNCTION workflow.guard_task_execution_legacy_write() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE command_owner name;
BEGIN
 SELECT pg_get_userbyid(proowner) INTO command_owner FROM pg_proc WHERE oid='workflow.task_execution_command(uuid,text,jsonb,uuid)'::regprocedure;
 IF current_user=command_owner THEN RETURN COALESCE(NEW,OLD); END IF;
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
CREATE TRIGGER a_task_execution_write_guard BEFORE UPDATE ON workflow.task FOR EACH ROW EXECUTE FUNCTION workflow.guard_task_execution_legacy_write();
CREATE TRIGGER a_task_execution_owner_guard BEFORE INSERT OR UPDATE OR DELETE ON workflow.task_assignee FOR EACH ROW EXECUTE FUNCTION workflow.guard_task_execution_legacy_write();
CREATE TRIGGER a_task_execution_event_guard BEFORE INSERT ON workflow.task_event FOR EACH ROW EXECUTE FUNCTION workflow.guard_task_execution_legacy_write();

CREATE FUNCTION workflow.require_task_execution_initialization() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN RETURN NEW; END IF;
 IF NOT EXISTS(SELECT 1 FROM workflow.task_execution WHERE task_id=NEW.id AND jsonb_array_length(state->'items')>=1)
 THEN RAISE EXCEPTION 'TASK_EXECUTION_INITIAL_CHECKLIST_REQUIRED' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER task_execution_initialization AFTER INSERT ON workflow.task DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION workflow.require_task_execution_initialization();

-- Existing authorized cancellation remains compatible and closes parallel
-- processes without manufacturing a resume or discarding their event history.
CREATE FUNCTION workflow.close_task_execution_on_cancel() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s jsonb;
BEGIN
 IF NEW.status='cancelled' AND OLD.status<>'cancelled' THEN
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
CREATE TRIGGER task_execution_cancel AFTER UPDATE OF status ON workflow.task FOR EACH ROW EXECUTE FUNCTION workflow.close_task_execution_on_cancel();

-- Default ACL remains closed. The Backend runtime inventory must explicitly
-- grant only read/summary/command/initialize/candidates to the API role;
-- enqueue_pause_rechecks is a separate worker-only entrypoint.
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='workflow' AND (p.proname LIKE 'task_execution_%' OR p.proname IN ('guard_task_execution_legacy_write','require_task_execution_initialization','close_task_execution_on_cancel')) LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,salegent_feishu_worker',f.signature);
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V405','Versioned task execution checklist feedback pause handover requirements and immutable submissions');
COMMIT;
