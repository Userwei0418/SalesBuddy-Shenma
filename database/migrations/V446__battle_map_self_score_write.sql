BEGIN;

-- A private analysis result is not a commercial customer edit. V069 split the
-- original ALL policy and made generic score writes require customer.update.
-- Keep that commercial policy, all subject/FDE boundaries and runtime ACLs;
-- give the existing score-writing Worker a separate path for readable facts.
-- API identities still have SELECT only. The handler retains its job-lease
-- fence, authorization-version recheck and atomic close-current/insert pair.
CREATE POLICY self_quadrant_insert ON insight.quadrant_score
 FOR INSERT WITH CHECK (
  workspace_id=common.current_workspace_id()
  AND subject_user_ref_id=common.current_user_ref_id()
  AND security.has_customer_access(customer_id));

CREATE POLICY self_quadrant_update ON insight.quadrant_score
 FOR UPDATE USING (
  workspace_id=common.current_workspace_id()
  AND subject_user_ref_id=common.current_user_ref_id()
  AND security.has_customer_access(customer_id))
 WITH CHECK (
  workspace_id=common.current_workspace_id()
  AND subject_user_ref_id=common.current_user_ref_id()
  AND security.has_customer_access(customer_id));

INSERT INTO ops.schema_migration(version,description)
 VALUES('V446','本人作战地图评分按客户读取权限保存，保持商业写权限和评分主体隔离');
COMMIT;
