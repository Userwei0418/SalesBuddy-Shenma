BEGIN;
SET LOCAL check_function_bodies=on;

ALTER TABLE workflow.task ADD COLUMN association_kind text;
UPDATE workflow.task SET association_kind=CASE
 WHEN customer_id IS NULL AND opportunity_id IS NULL THEN 'daily'
 WHEN customer_id IS NOT NULL AND opportunity_id IS NOT NULL THEN 'customer'
 ELSE 'legacy_customer' END;
ALTER TABLE workflow.task ALTER COLUMN association_kind SET NOT NULL;
ALTER TABLE workflow.task ADD CONSTRAINT task_association_kind_check
 CHECK(association_kind IN ('daily','customer','legacy_customer'));
COMMENT ON COLUMN workflow.task.association_kind IS
 '关联分类：daily无客户商机；customer两者必填；legacy_customer仅保留历史不完整关联，不改变task_type/source_code';

CREATE FUNCTION workflow.enforce_task_association() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' AND NEW.customer_id IS NOT DISTINCT FROM OLD.customer_id
 AND NEW.opportunity_id IS NOT DISTINCT FROM OLD.opportunity_id
 AND NEW.association_kind IS NOT DISTINCT FROM OLD.association_kind THEN RETURN NEW; END IF;
 IF NEW.association_kind IS NULL THEN
  NEW.association_kind:=CASE WHEN NEW.customer_id IS NULL AND NEW.opportunity_id IS NULL THEN 'daily' ELSE 'customer' END;
 END IF;
 IF NEW.association_kind='daily' AND NEW.customer_id IS NULL AND NEW.opportunity_id IS NULL THEN RETURN NEW; END IF;
 IF NEW.association_kind<>'customer' OR NEW.customer_id IS NULL OR NEW.opportunity_id IS NULL THEN
  RAISE EXCEPTION '客户任务必须同时选择客户和商机；日常任务不能关联客户或商机' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=NEW.opportunity_id
 AND o.customer_id=NEW.customer_id AND o.workspace_id=NEW.workspace_id AND o.deleted_at IS NULL) THEN
  RAISE EXCEPTION '商机不存在、无权关联或不属于所选客户' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER task_association_guard BEFORE INSERT OR UPDATE ON workflow.task
 FOR EACH ROW EXECUTE FUNCTION workflow.enforce_task_association();

-- Task qualification follows active account/role/team, independently of CRM participation.
CREATE FUNCTION security.task_recipient_eligible(p_task uuid,p_user uuid,p_role text,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM workflow.task t JOIN platform.user_ref u ON u.id=p_user AND u.workspace_id=t.workspace_id
 WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
 AND u.status='active' AND u.deleted_at IS NULL
 AND p_role IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead')
 AND EXISTS(SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=u.id AND r.workspace_id=u.workspace_id
 AND r.role_code=p_role AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to)
 AND (p_role NOT IN ('fde','fde_lead') OR security.fde_user_is_active(p_user,p_role,p_team)));
$$;
CREATE OR REPLACE FUNCTION security.fde_task_eligible(p_task uuid,p_user uuid,p_role text,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p_role IN ('fde','fde_lead') AND security.task_recipient_eligible(p_task,p_user,p_role,p_team);
$$;
CREATE FUNCTION security.task_actor_recipient(p_task uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=p_task
 AND a.workspace_id=common.current_workspace_id() AND a.assignee_user_ref_id=common.current_user_ref_id()
 AND a.assignee_role=common.current_role_code()
 AND security.task_recipient_eligible(p_task,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=p_task
 AND c.workspace_id=common.current_workspace_id() AND c.user_ref_id=common.current_user_ref_id()
 AND c.role_code=common.current_role_code()
 AND security.task_recipient_eligible(p_task,c.user_ref_id,c.role_code,c.team_id));
$$;
DROP POLICY task_opportunity_boundary ON workflow.task;
CREATE POLICY task_opportunity_boundary ON workflow.task AS RESTRICTIVE FOR SELECT USING(
 opportunity_id IS NULL OR security.has_opportunity_read_access(opportunity_id)
 OR creator_user_ref_id=common.current_user_ref_id() OR security.task_actor_recipient(id)
 OR (security.is_fde_actor() AND security.fde_can_coordinate_task(id)));
DROP POLICY task_opportunity_boundary_update ON workflow.task;
CREATE POLICY task_opportunity_boundary_update ON workflow.task AS RESTRICTIVE FOR UPDATE USING(
 opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id)
 OR security.task_actor_recipient(id) OR security.fde_can_coordinate_task(id)) WITH CHECK(
 opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id)
 OR security.task_actor_recipient(id) OR security.fde_can_coordinate_task(id));

CREATE OR REPLACE VIEW workflow.v_task_action_recipient WITH(security_invoker=true) AS
 SELECT a.task_id,a.workspace_id,a.assignee_user_ref_id,a.assignee_team_id,a.assignee_role,'owner'::text AS responsibility
 FROM workflow.task_assignee a WHERE a.responsibility='owner'
 AND security.task_recipient_eligible(a.task_id,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id)
 UNION ALL
 SELECT c.task_id,c.workspace_id,c.user_ref_id,c.team_id,c.role_code,'candidate'::text
 FROM workflow.task_candidate c JOIN workflow.task t ON t.id=c.task_id
 WHERE t.status='pending_confirm' AND c.decision='pending'
 AND security.task_recipient_eligible(c.task_id,c.user_ref_id,c.role_code,c.team_id)
 AND (t.target_position<>'supervisor' OR EXISTS(SELECT 1 FROM platform.team_membership tm
 WHERE tm.user_ref_id=c.user_ref_id AND tm.workspace_id=c.workspace_id AND tm.team_id=c.team_id
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to));

-- Minimal task context does not make either underlying CRM row readable.
CREATE FUNCTION security.task_link_context(p_task uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('customer_name',c.name,'opportunity_name',o.name)
 FROM workflow.task t LEFT JOIN crm.customer c ON c.id=t.customer_id AND c.workspace_id=t.workspace_id
 LEFT JOIN crm.opportunity o ON o.id=t.opportunity_id AND o.workspace_id=t.workspace_id
 WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
 AND (t.creator_user_ref_id=common.current_user_ref_id() OR security.task_actor_recipient(t.id)
 OR security.fde_can_coordinate_task(t.id));
$$;
COMMENT ON FUNCTION security.task_link_context(uuid) IS '仅任务参与人/协调人可读取关联名称，不授予客户、商机详情权限';

-- FDE may act on their own visible opportunity/visit suggestions only.
DROP POLICY fde_suggestion_decision_guard ON insight.business_suggestion;
CREATE POLICY fde_suggestion_decision_guard ON insight.business_suggestion AS RESTRICTIVE FOR UPDATE USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR EXISTS(
 SELECT 1 FROM insight.business_advice a WHERE a.id=business_suggestion.advice_id
 AND a.actor_user_ref_id=common.current_user_ref_id() AND a.actor_role_code=common.current_role_code()
 AND a.subject_kind IN ('opportunity','visit'))) WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR EXISTS(
 SELECT 1 FROM insight.business_advice a WHERE a.id=business_suggestion.advice_id
 AND a.actor_user_ref_id=common.current_user_ref_id() AND a.actor_role_code=common.current_role_code()
 AND a.subject_kind IN ('opportunity','visit')));

INSERT INTO ops.schema_migration(version,description)
VALUES('V086','客户任务与日常任务关联边界；跨部门任务接收处理及最小上下文；FDE协作建议确认');
COMMIT;
