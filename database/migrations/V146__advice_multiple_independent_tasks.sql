-- One suggestion can produce several independently owned tasks in one decision transaction.
-- Keep the legacy suggestion.task_id as the first task for existing clients.
-- No task, notification, grant or historical suggestion is rewritten.
CREATE INDEX IF NOT EXISTS task_by_suggestion ON workflow.task(source_suggestion_id)
  WHERE source_suggestion_id IS NOT NULL;
DROP INDEX IF EXISTS workflow.task_one_per_suggestion;
COMMENT ON COLUMN workflow.task.source_suggestion_id IS
  '人工采纳建议来源；一次决定可为多名负责人各建一条独立任务，与建议决定同事务提交';

-- Existing audit functions keep their signatures, ownership, ACLs and scope checks.
-- Add all task references while retaining the first-task fields for older readers.
DO $migration$
DECLARE
  function_name text;
  definition text;
  old_fragment text;
  new_fragment text;
BEGIN
  FOREACH function_name IN ARRAY ARRAY[
    'security.agent_operation_rows_v066(timestamp with time zone,timestamp with time zone)',
    'security.business_activity_rows(timestamp with time zone,timestamp with time zone)'
  ] LOOP
    definition := pg_get_functiondef(function_name::regprocedure);
    old_fragment := '''task_id'',s.task_id,';
    new_fragment := '''task_id'',s.task_id,''task_ids'',(SELECT COALESCE(jsonb_agg(st.id ORDER BY st.created_at,st.id),''[]''::jsonb) FROM workflow.task st WHERE st.source_suggestion_id=s.id AND st.workspace_id=s.workspace_id),';
    IF position(new_fragment IN definition)=0 THEN
      IF position(old_fragment IN definition)=0 THEN
        RAISE EXCEPTION 'Unexpected suggestion audit definition: %', function_name;
      END IF;
      definition := replace(definition,old_fragment,new_fragment);
    END IF;
    IF function_name LIKE 'security.agent_operation_rows%' THEN
      old_fragment := 's.task_id=e.task_id';
      new_fragment := 'EXISTS(SELECT 1 FROM workflow.task st WHERE st.id=e.task_id AND st.source_suggestion_id=s.id AND st.workspace_id=s.workspace_id)';
      IF position(new_fragment IN definition)=0 THEN
        IF position(old_fragment IN definition)=0 THEN
          RAISE EXCEPTION 'Unexpected created-task audit definition';
        END IF;
        definition := replace(definition,old_fragment,new_fragment);
      END IF;
    END IF;
    EXECUTE definition;
  END LOOP;
END
$migration$;
