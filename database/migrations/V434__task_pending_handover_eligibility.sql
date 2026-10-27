-- Preserve V433 history: extend only the read-only coordination projection.
-- CREATE OR REPLACE retains the existing owner-only function ACL.
BEGIN;

CREATE OR REPLACE FUNCTION workflow.task_execution_coordination_status(p_task uuid) RETURNS jsonb
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
 -- A pending handover target must still be able to read, accept AND execute.
 -- The owner check above only tests task.accept before acceptance, so it cannot
 -- detect task.read/task.complete being revoked while the handover is pending.
 -- Reuse the existing UI/notification reason; never change acceptance or owner.
 IF s->'execution_handover'->>'status'='pending' THEN
  person_id:=(s->'execution_handover'->>'target_user_id')::uuid;
  IF owner_count<>1 OR person_id IS DISTINCT FROM owner_id
   OR workflow.task_execution_person(p_task,person_id) IS NULL
   OR NOT workflow.task_execution_user_allows(p_task,person_id,'task.read',person_id,true)
   OR NOT workflow.task_execution_user_allows(p_task,person_id,'task.accept',person_id,true)
   OR NOT workflow.task_execution_user_allows(p_task,person_id,'task.complete',person_id,true)
  THEN result:=result||'{"executor_unavailable":true}'::jsonb; END IF;
 END IF;
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

INSERT INTO ops.schema_migration(version,description) VALUES('V434','Detect pending executor handover read and execution permission loss');
COMMIT;
