-- A recorded follow-up can be materialized after its real deadline. Preserve
-- that business date instead of silently moving it to a future day. Management
-- tasks retain their original due-after-creation invariant; TaskCreate and
-- TaskService also continue rejecting a past deadline for manual creation.
--
-- Provenance is structural (visit_follow_up + source visit), independent of the
-- caller/provider label. Source eligibility, archive state, recorder authority
-- and unchanged facts are rechecked under RLS with row locks before persistence.
-- No historical dates are backfilled or rewritten. Existing valid future tasks
-- satisfy the new check unchanged. Missing/cross-workspace sources stop an
-- upgrade at FK validation rather than being reassigned or repaired implicitly.
ALTER TABLE workflow.task DROP CONSTRAINT task_check;
ALTER TABLE workflow.task ADD CONSTRAINT task_check CHECK (
    due_at > created_at
    OR (task_type = 'visit_follow_up' AND source_visit_id IS NOT NULL)
);

ALTER TABLE workflow.task DROP CONSTRAINT task_source_visit_id_fkey;
ALTER TABLE workflow.task ADD CONSTRAINT task_source_visit_workspace_fkey
    FOREIGN KEY (source_visit_id, workspace_id) REFERENCES activity.visit (id, workspace_id);

COMMENT ON CONSTRAINT task_check ON workflow.task IS
    '管理任务必须晚于创建时间；有明确拜访来源的跟进任务保留真实截止时间，可已逾期。';
COMMENT ON CONSTRAINT task_source_visit_workspace_fkey ON workflow.task IS
    '跟进任务来源必须属于同一工作空间；来源归档状态及记录人权限由写入前事实复核保证。';
