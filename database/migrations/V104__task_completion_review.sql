BEGIN;
ALTER TABLE workflow.task DROP CONSTRAINT task_status_check;
ALTER TABLE workflow.task ADD CONSTRAINT task_status_check CHECK(status IN
 ('pending_confirm','pending_execution','in_progress','pending_review','completed','deferred','cancelled'));
ALTER TABLE workflow.task ADD COLUMN completion_submitted_at timestamptz;
ALTER TABLE workflow.task ADD COLUMN completion_review_note text;
COMMENT ON COLUMN workflow.task.completion_submitted_at IS '最近一次提交完成时间；多轮说明保存在 task_event';
COMMENT ON COLUMN workflow.task.completion_review_note IS '最近一轮验收意见；完成说明与驳回原因分别保留';
-- Only the original creator in the current tenant can exercise this policy.
CREATE POLICY task_operations_creator_update ON workflow.task FOR UPDATE
USING(workspace_id=common.current_workspace_id() AND creator_user_ref_id=common.current_user_ref_id()
 AND (security.has_active_role('operations') OR security.has_active_role('administrator')))
WITH CHECK(workspace_id=common.current_workspace_id() AND creator_user_ref_id=common.current_user_ref_id()
 AND (security.has_active_role('operations') OR security.has_active_role('administrator')));
INSERT INTO ops.schema_migration(version,description)
VALUES('V104','任务完成由发起人验收；自建自领任务直接完成；保留逐轮事件');
COMMIT;
