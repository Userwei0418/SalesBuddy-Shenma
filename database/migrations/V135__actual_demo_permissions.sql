BEGIN;
-- Actuals keep their immutable ledger trigger. Only creation and voiding are
-- authorized; no permission enables editing amounts or deleting ledger rows.
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT * FROM pg_policies WHERE schemaname='crm' AND tablename IN ('customer_actual','opportunity_demo_scenes') LOOP
  EXECUTE format('DROP POLICY %I ON %I.%I',p.policyname,p.schemaname,p.tablename);
 END LOOP;
END $$;
CREATE POLICY permission_read ON crm.customer_actual FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND CASE WHEN opportunity_id IS NULL
 THEN security.authorization_customer(security.authorization_read_feature('actual'),customer_id)
 ELSE security.authorization_opportunity(security.authorization_read_feature('actual'),opportunity_id) END);
CREATE POLICY permission_create ON crm.customer_actual FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND confirmed_by_user_ref_id=common.current_user_ref_id()
 AND security.authorization_customer('actual.create',customer_id)
 AND (opportunity_id IS NULL OR security.authorization_opportunity('actual.create',opportunity_id))
 AND (opportunity_id IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=opportunity_id
  AND o.workspace_id=customer_actual.workspace_id AND o.customer_id=customer_actual.customer_id)));
CREATE POLICY permission_void ON crm.customer_actual FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.authorization_customer('actual.void',customer_id)
 AND (opportunity_id IS NULL OR security.authorization_opportunity('actual.void',opportunity_id))) WITH CHECK(
 workspace_id=common.current_workspace_id() AND voided_by_user_ref_id=common.current_user_ref_id()
 AND security.authorization_customer('actual.void',customer_id)
 AND (opportunity_id IS NULL OR security.authorization_opportunity('actual.void',opportunity_id)));

CREATE OR REPLACE FUNCTION security.can_write_demo_scene(p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity('demo_scene.update',p_opportunity);
$$;
CREATE POLICY permission_read ON crm.opportunity_demo_scenes FOR SELECT USING(
 workspace_id=common.current_workspace_id()
 AND security.authorization_opportunity(security.authorization_read_feature('demo_scene'),opportunity_id));
CREATE POLICY permission_create ON crm.opportunity_demo_scenes FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND created_by=common.current_user_ref_id()
 AND deleted_at IS NULL AND security.authorization_opportunity('demo_scene.create',opportunity_id));
CREATE POLICY permission_update ON crm.opportunity_demo_scenes FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND (
 security.authorization_opportunity('demo_scene.update',opportunity_id)
 OR security.authorization_opportunity('demo_scene.delete',opportunity_id))) WITH CHECK(
 workspace_id=common.current_workspace_id() AND (
 security.authorization_opportunity('demo_scene.update',opportunity_id)
 OR security.authorization_opportunity('demo_scene.delete',opportunity_id)));
CREATE FUNCTION crm.guard_demo_permission_transition() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN RETURN NEW; END IF;
 IF (NEW.workspace_id,NEW.opportunity_id,NEW.created_by,NEW.created_at)
 IS DISTINCT FROM (OLD.workspace_id,OLD.opportunity_id,OLD.created_by,OLD.created_at) THEN
  RAISE EXCEPTION 'Demo ownership is immutable' USING ERRCODE='42501';
 END IF;
 IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
  IF OLD.deleted_at IS NOT NULL OR NEW.deleted_at IS NULL OR
   NOT security.authorization_opportunity('demo_scene.delete',OLD.opportunity_id) THEN RAISE insufficient_privilege; END IF;
 END IF;
 IF (NEW.name,NEW.description) IS DISTINCT FROM (OLD.name,OLD.description) AND
  NOT security.authorization_opportunity('demo_scene.update',OLD.opportunity_id) THEN RAISE insufficient_privilege; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_demo_permission BEFORE UPDATE ON crm.opportunity_demo_scenes
 FOR EACH ROW EXECUTE FUNCTION crm.guard_demo_permission_transition();
CREATE OR REPLACE FUNCTION security.demo_scene_history(p_scene uuid, p_limit integer DEFAULT 50, p_offset integer DEFAULT 0)
 RETURNS TABLE(id bigint, occurred_at timestamp with time zone, actor_name text, action_code text, before_snapshot jsonb, after_snapshot jsonb, total bigint)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM crm.opportunity_demo_scenes d WHERE d.id=p_scene
 AND d.workspace_id=common.current_workspace_id() AND security.authorization_opportunity('demo_scene.read',d.opportunity_id))
 THEN RAISE insufficient_privilege; END IF;
 RETURN QUERY SELECT a.id,a.occurred_at,COALESCE(a.actor_name_snapshot,u.display_name,'系统'),a.action_code,
 a.before_snapshot,a.after_snapshot,count(*) OVER() FROM ops.audit_log a
 LEFT JOIN platform.user_ref u ON u.id=a.actor_user_ref_id AND u.workspace_id=a.workspace_id
 WHERE a.workspace_id=common.current_workspace_id() AND a.object_type='opportunity_demo_scenes' AND a.object_id=p_scene
 ORDER BY a.occurred_at DESC,a.id DESC LIMIT LEAST(GREATEST(p_limit,1),100) OFFSET GREATEST(p_offset,0);
END $function$;

INSERT INTO ops.schema_migration(version,description) VALUES('V135','Separate actual ledger and demo scene action permissions');
COMMIT;
