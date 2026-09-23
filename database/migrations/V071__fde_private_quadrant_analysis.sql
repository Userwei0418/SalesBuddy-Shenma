BEGIN;
SET LOCAL check_function_bodies=on;

-- A score is a projection of facts the actor may currently read. It is not a
-- commercial customer mutation. V069's generic ALL-policy split correctly kept
-- commercial writes narrow, but also removed FDE's private analysis write path.
-- Add only INSERT/UPDATE on the actor's own score; leave every crm policy intact.
CREATE POLICY fde_quadrant_insert ON insight.quadrant_score FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor()
 AND subject_user_ref_id=common.current_user_ref_id()
 AND security.has_customer_access(customer_id));
CREATE POLICY fde_quadrant_update ON insight.quadrant_score FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor()
 AND subject_user_ref_id=common.current_user_ref_id()
 AND security.has_customer_access(customer_id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor()
 AND subject_user_ref_id=common.current_user_ref_id()
 AND security.has_customer_access(customer_id));

-- Restrictive guards also constrain any inherited permissive path. Check both
-- old and new UPDATE rows so another person's score cannot be reassigned to self.
CREATE POLICY fde_quadrant_insert_boundary ON insight.quadrant_score
 AS RESTRICTIVE FOR INSERT WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR (
 workspace_id=common.current_workspace_id() AND security.is_fde_actor()
 AND subject_user_ref_id=common.current_user_ref_id()
 AND security.has_customer_access(customer_id)));
CREATE POLICY fde_quadrant_update_boundary ON insight.quadrant_score
 AS RESTRICTIVE FOR UPDATE USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR (
 workspace_id=common.current_workspace_id() AND security.is_fde_actor()
 AND subject_user_ref_id=common.current_user_ref_id()
 AND security.has_customer_access(customer_id))) WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR (
 workspace_id=common.current_workspace_id() AND security.is_fde_actor()
 AND subject_user_ref_id=common.current_user_ref_id()
 AND security.has_customer_access(customer_id)));
CREATE POLICY fde_quadrant_delete_boundary ON insight.quadrant_score
 AS RESTRICTIVE FOR DELETE USING(common.current_role_code() NOT IN ('fde','fde_lead'));

INSERT INTO ops.schema_migration(version,description)
 VALUES('V071','FDE本人作战地图分析独立写权限，保留客户商业权限与评分主体隔离');
COMMIT;
