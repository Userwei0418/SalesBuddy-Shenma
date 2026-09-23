BEGIN;
ALTER TABLE workflow.task ADD COLUMN target_position text CHECK(target_position IN ('self','supervisor','manager','operations'));
COMMENT ON COLUMN workflow.task.target_position IS '空=原有指定接收人；岗位任务保存发布时的候选名单，只有一人领取完成';
ALTER TABLE workflow.task ADD CONSTRAINT task_workspace_key UNIQUE(id,workspace_id);
CREATE TABLE workflow.task_candidate (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 task_id uuid NOT NULL,
 user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 role_code text NOT NULL CHECK(role_code IN ('sales','supervisor','manager','operations','administrator')),
 team_id uuid REFERENCES platform.team(id),
 decision text NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','declined','claimed')),
 decision_note text,
 assigned_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 decided_at timestamptz,
 UNIQUE(task_id,user_ref_id),
 FOREIGN KEY(task_id,workspace_id) REFERENCES workflow.task(id,workspace_id),
 CHECK((decision='pending')=(decided_at IS NULL))
);
CREATE UNIQUE INDEX task_candidate_one_winner ON workflow.task_candidate(task_id) WHERE decision='claimed';
CREATE INDEX task_candidate_inbox ON workflow.task_candidate(workspace_id,user_ref_id,assigned_at DESC);
COMMENT ON TABLE workflow.task_candidate IS '岗位待办接收快照；拒绝只代表本人不领取，全部拒绝才取消，领取人与正式负责人同一事务写入';
ALTER TABLE workflow.task_candidate ENABLE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON workflow.task_candidate
 USING(workspace_id=common.current_workspace_id()) WITH CHECK(workspace_id=common.current_workspace_id());
ALTER TABLE workflow.task_assignee DROP CONSTRAINT task_assignee_assignee_role_check;
ALTER TABLE workflow.task_assignee ADD CONSTRAINT task_assignee_assignee_role_check
 CHECK(assignee_role IN ('sales','supervisor','manager','operations','administrator'));
CREATE POLICY task_candidate_read ON workflow.task FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND EXISTS(SELECT 1 FROM workflow.task_candidate c
   WHERE c.task_id=task.id AND c.user_ref_id=common.current_user_ref_id()));
CREATE POLICY task_candidate_update ON workflow.task FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND target_position IS NOT NULL AND EXISTS(
   SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=task.id AND c.user_ref_id=common.current_user_ref_id()
     AND c.decision='pending')) WITH CHECK(workspace_id=common.current_workspace_id());
CREATE POLICY task_operations_insert ON workflow.task FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND creator_user_ref_id=common.current_user_ref_id()
 AND (security.has_active_role('operations') OR security.has_active_role('administrator')));
CREATE TRIGGER business_audit AFTER INSERT OR UPDATE OR DELETE ON workflow.task_candidate
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
CREATE VIEW workflow.v_task_action_recipient WITH(security_invoker=true) AS
 SELECT a.task_id,a.workspace_id,a.assignee_user_ref_id,a.assignee_team_id,a.assignee_role,'owner'::text AS responsibility
 FROM workflow.task_assignee a WHERE a.responsibility='owner'
 UNION ALL
 SELECT c.task_id,c.workspace_id,c.user_ref_id,c.team_id,c.role_code,'candidate'::text
 FROM workflow.task_candidate c JOIN workflow.task t ON t.id=c.task_id
 JOIN platform.user_ref u ON u.id=c.user_ref_id AND u.workspace_id=c.workspace_id
 WHERE t.status='pending_confirm' AND c.decision='pending' AND u.status='active' AND u.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=u.id AND r.workspace_id=u.workspace_id
     AND r.role_code=c.role_code AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to)
   AND (t.target_position<>'supervisor' OR EXISTS(SELECT 1 FROM platform.team_membership tm
     WHERE tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id AND tm.team_id=c.team_id
       AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to));
CREATE OR REPLACE FUNCTION workflow.enqueue_task_notification(p_workspace_id uuid,p_recipient_user_ref_id uuid,
 p_template_code text,p_title text,p_body text,p_task_id uuid,p_dedupe_key text,p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task;
BEGIN
 IF p_workspace_id IS DISTINCT FROM common.current_workspace_id() THEN
  RAISE EXCEPTION 'WORKSPACE_SCOPE_VIOLATION';
 END IF;
 SELECT * INTO t FROM workflow.task WHERE id=p_task_id AND workspace_id=p_workspace_id AND deleted_at IS NULL;
 IF t.id IS NULL OR NOT(t.creator_user_ref_id=common.current_user_ref_id()
   OR EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.assignee_user_ref_id=common.current_user_ref_id())
   OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=t.id AND c.user_ref_id=common.current_user_ref_id())) THEN
  RAISE EXCEPTION 'TASK_NOTIFICATION_FORBIDDEN';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=p_recipient_user_ref_id AND u.workspace_id=p_workspace_id
   AND u.status='active' AND u.deleted_at IS NULL) THEN RETURN false; END IF;
 IF NOT(t.creator_user_ref_id=p_recipient_user_ref_id
   OR EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.assignee_user_ref_id=p_recipient_user_ref_id)
   OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=t.id AND c.user_ref_id=p_recipient_user_ref_id
     AND EXISTS(SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=c.user_ref_id AND r.workspace_id=c.workspace_id
       AND r.role_code=c.role_code AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to))) THEN
  RETURN false;
 END IF;
 INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,channel_code,template_code,title,body,
   object_type,object_id,status,dedupe_key,payload)
 VALUES(p_workspace_id,p_recipient_user_ref_id,'in_app',p_template_code,p_title,p_body,'task',p_task_id,
   'pending',p_dedupe_key,COALESCE(p_payload,'{}'::jsonb))
 ON CONFLICT(workspace_id,recipient_user_ref_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING;
 RETURN true;
END $$;

DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='workflow' AND table_name='task' AND privilege_type='INSERT' AND grantee<>'PUBLIC'
 LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON workflow.task_candidate TO %I',r.grantee);
  EXECUTE format('GRANT SELECT ON workflow.v_task_action_recipient TO %I',r.grantee);
 END LOOP;
END $$;
COMMIT;
