BEGIN;

CREATE OR REPLACE FUNCTION security.task_actor_recipient(p_task uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=p_task
 AND a.workspace_id=common.current_workspace_id() AND a.assignee_user_ref_id=common.current_user_ref_id()

 AND security.task_recipient_eligible(p_task,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=p_task
 AND c.workspace_id=common.current_workspace_id() AND c.user_ref_id=common.current_user_ref_id()

 AND security.task_recipient_eligible(p_task,c.user_ref_id,c.role_code,c.team_id));
$function$
;

CREATE OR REPLACE FUNCTION security.fde_can_act_on_task(p_task uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT security.is_fde_actor() AND (
 EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=p_task AND a.workspace_id=common.current_workspace_id()
 AND a.assignee_user_ref_id=common.current_user_ref_id() AND a.responsibility='owner'
 AND security.fde_task_eligible(p_task,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=p_task AND c.workspace_id=common.current_workspace_id()
 AND c.user_ref_id=common.current_user_ref_id() AND c.decision='pending'
 AND security.fde_task_eligible(p_task,c.user_ref_id,c.role_code,c.team_id)));
 $function$
;

CREATE OR REPLACE FUNCTION security.fde_can_record_task_response(p_task uuid)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT security.is_fde_actor() AND EXISTS(SELECT 1 FROM workflow.task_candidate c
 WHERE c.task_id=p_task AND c.workspace_id=common.current_workspace_id()
 AND c.user_ref_id=common.current_user_ref_id()
 AND c.decision IN ('pending','claimed','declined')
 AND security.fde_task_eligible(c.task_id,c.user_ref_id,c.role_code,c.team_id));
 $function$
;


CREATE FUNCTION security.authorization_task(p_permission text,p_task uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE((SELECT security.authorization_allows(p_permission,t.workspace_id,
  CASE WHEN p_permission NOT IN ('task.coordinate','task.cancel','task.review') AND security.task_actor_recipient(t.id) THEN common.current_user_ref_id() ELSE t.creator_user_ref_id END,
  ARRAY[t.creator_team_id] || ARRAY(SELECT a.assignee_team_id FROM workflow.task_assignee a
    WHERE a.task_id=t.id AND a.workspace_id=t.workspace_id) || ARRAY(
    SELECT c.team_id FROM workflow.task_candidate c WHERE c.task_id=t.id AND c.workspace_id=t.workspace_id),
  security.task_actor_recipient(t.id)) OR
  (p_permission NOT LIKE 'task.%' OR p_permission='task.read')
  AND t.opportunity_id IS NOT NULL AND security.authorization_opportunity(p_permission,t.opportunity_id)
 FROM workflow.task t WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL),false);
$$;
CREATE FUNCTION security.authorization_task_created(p_task uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM workflow.task t WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id()
 AND t.creator_user_ref_id=common.current_user_ref_id() AND t.created_at>=transaction_timestamp()
 AND pg_xact_status(t.xmin::text::xid8)='in progress'
 AND security.authorization_task('task.create_'||t.association_kind,t.id));
$$;
CREATE FUNCTION security.authorization_task_mutation(p_task uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_task_created(p_task) OR security.authorization_task('task.coordinate',p_task)
 OR EXISTS(SELECT 1 FROM workflow.task t WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id()
 AND ((t.creator_user_ref_id=common.current_user_ref_id() AND (
  security.authorization_task('task.cancel',p_task) OR security.authorization_task('task.review',p_task)))
 OR (security.task_actor_recipient(p_task) AND (
  security.authorization_task('task.accept',p_task) OR security.authorization_task('task.decline',p_task)
  OR security.authorization_task('task.complete',p_task)))));
$$;
CREATE OR REPLACE FUNCTION security.fde_can_coordinate_task(p_task uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_task('task.coordinate',p_task) OR EXISTS(
  SELECT 1 FROM workflow.task t WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id()
  AND t.creator_user_ref_id=common.current_user_ref_id() AND (
   security.authorization_task_created(t.id) OR security.authorization_task('task.cancel',t.id)));
$$;
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT tablename,policyname FROM pg_policies WHERE schemaname='workflow'
 AND tablename IN ('task','task_candidate','task_assignee','task_event') LOOP
  EXECUTE format('DROP POLICY %I ON workflow.%I',p.policyname,p.tablename);
 END LOOP;
END $$;
CREATE POLICY permission_read ON workflow.task FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND (
 security.authorization_task('task.read',id) OR
 security.authorization_allows('task.read',workspace_id,creator_user_ref_id,ARRAY[creator_team_id],false)));
CREATE POLICY permission_insert ON workflow.task FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND creator_user_ref_id=common.current_user_ref_id()
 AND security.authorization_allows('task.create_'||association_kind,workspace_id,creator_user_ref_id,ARRAY[creator_team_id],true)
 AND (opportunity_id IS NULL OR security.authorization_opportunity('task.create_customer',opportunity_id)));
CREATE POLICY permission_update ON workflow.task FOR UPDATE
 USING(workspace_id=common.current_workspace_id() AND security.authorization_task_mutation(id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.authorization_task_mutation(id));
-- Children carry the same task boundary; being an unrelated company member is
-- insufficient. Keep audit events append-only and omit physical task deletion.
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['task_candidate','task_assignee','task_event'] LOOP
  EXECUTE format('CREATE POLICY permission_read ON workflow.%I FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.authorization_task(''task.read'',task_id))',t);
  EXECUTE format('CREATE POLICY permission_insert ON workflow.%I FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id() AND security.authorization_task_mutation(task_id))',t);
  IF t<>'task_event' THEN
   EXECUTE format('CREATE POLICY permission_update ON workflow.%I FOR UPDATE USING(workspace_id=common.current_workspace_id() AND security.authorization_task_mutation(task_id)) WITH CHECK(workspace_id=common.current_workspace_id() AND security.authorization_task_mutation(task_id))',t);
   EXECUTE format('CREATE POLICY permission_delete ON workflow.%I FOR DELETE USING(workspace_id=common.current_workspace_id() AND security.fde_can_coordinate_task(task_id))',t);
  END IF;
 END LOOP;
END $$;
CREATE FUNCTION workflow.enforce_permission_task_transition() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE action text;
BEGIN
 IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN RETURN NEW; END IF;
 IF NEW.workspace_id IS DISTINCT FROM OLD.workspace_id OR NEW.creator_user_ref_id IS DISTINCT FROM OLD.creator_user_ref_id
 OR NEW.creator_team_id IS DISTINCT FROM OLD.creator_team_id OR NEW.customer_id IS DISTINCT FROM OLD.customer_id
 OR NEW.opportunity_id IS DISTINCT FROM OLD.opportunity_id OR NEW.association_kind IS DISTINCT FROM OLD.association_kind THEN
  RAISE EXCEPTION '任务归属不可通过状态操作修改' USING ERRCODE='42501';
 END IF;
 action:=CASE WHEN NEW.status='cancelled' THEN
  CASE WHEN OLD.creator_user_ref_id=common.current_user_ref_id() THEN 'task.cancel'
       WHEN security.task_actor_recipient(OLD.id) THEN 'task.decline' ELSE 'task.coordinate' END
 WHEN NEW.status='pending_execution' AND OLD.status='pending_confirm' THEN 'task.accept'
 WHEN NEW.status='pending_review' THEN 'task.complete'
 WHEN OLD.status='pending_review' AND NEW.status IN ('completed','in_progress') THEN 'task.review'
 WHEN NEW.status='completed' THEN 'task.complete'
 WHEN NEW.status='pending_confirm' AND NEW.target_position IS NULL THEN 'task.coordinate'
 WHEN NEW.status='pending_confirm' AND OLD.status='pending_confirm' THEN 'task.decline'
 ELSE NULL END;
 IF action IS NULL OR NOT security.authorization_task(action,OLD.id) THEN
  RAISE EXCEPTION '当前账号没有此待办操作的授权' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER permission_task_transition BEFORE UPDATE ON workflow.task
 FOR EACH ROW EXECUTE FUNCTION workflow.enforce_permission_task_transition();
REVOKE ALL ON FUNCTION workflow.enforce_permission_task_transition() FROM PUBLIC;


ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v129;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v129();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_task(text,uuid),security.authorization_task_created(uuid),security.authorization_task_mutation(uuid) TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v129(),security.authorization_task(text,uuid),security.authorization_task_created(uuid),security.authorization_task_mutation(uuid) FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V130','Union task permissions with independent workflow eligibility');
COMMIT;
