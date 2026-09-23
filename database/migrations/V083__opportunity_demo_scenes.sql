BEGIN;
-- Demo is a recorded scenario deliverable, never a deployment/runtime object.
CREATE TABLE crm.opportunity_demo_scenes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 opportunity_id uuid NOT NULL,
 name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 200),
 description text NOT NULL DEFAULT '' CHECK (length(description)<=5000),
 created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 deleted_at timestamptz,
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(created_by,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
COMMENT ON TABLE crm.opportunity_demo_scenes IS '商机场景成果登记；不含运行部署状态；按创建人和创建日期统计本人登记数量';
CREATE INDEX demo_scenes_opportunity ON crm.opportunity_demo_scenes(workspace_id,opportunity_id,created_at DESC,id DESC) WHERE deleted_at IS NULL;
CREATE INDEX demo_scenes_creator ON crm.opportunity_demo_scenes(workspace_id,created_by,created_at) WHERE deleted_at IS NULL;
CREATE FUNCTION security.can_write_demo_scene(p_opportunity uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.has_opportunity_access(p_opportunity)
 OR (security.is_fde_actor() AND security.fde_opportunity_in_scope(p_opportunity));
$$;
ALTER TABLE crm.opportunity_demo_scenes ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_demo_scenes FORCE ROW LEVEL SECURITY;
CREATE POLICY demo_scene_read ON crm.opportunity_demo_scenes FOR SELECT USING (
 workspace_id=common.current_workspace_id() AND security.has_opportunity_read_access(opportunity_id));
CREATE POLICY demo_scene_insert ON crm.opportunity_demo_scenes FOR INSERT WITH CHECK (
 workspace_id=common.current_workspace_id() AND created_by=common.current_user_ref_id()
 AND security.can_write_demo_scene(opportunity_id));
CREATE POLICY demo_scene_update ON crm.opportunity_demo_scenes FOR UPDATE USING (
 workspace_id=common.current_workspace_id() AND security.can_write_demo_scene(opportunity_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.can_write_demo_scene(opportunity_id));
CREATE FUNCTION crm.protect_demo_scene() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.created_by,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.opportunity_id,OLD.created_by,OLD.created_at)
 THEN RAISE EXCEPTION 'Demo所属商机与创建来源不可修改' USING ERRCODE='22023'; END IF;
 IF OLD.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Demo已删除' USING ERRCODE='22023'; END IF;
 NEW.updated_at=clock_timestamp(); NEW.version_no=OLD.version_no+1; RETURN NEW;
END $$;
CREATE TRIGGER demo_scene_version BEFORE UPDATE ON crm.opportunity_demo_scenes FOR EACH ROW EXECUTE FUNCTION crm.protect_demo_scene();
CREATE TRIGGER business_audit AFTER INSERT OR UPDATE ON crm.opportunity_demo_scenes FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
-- A minimal authorized history projection is available to project collaborators;
-- this does not grant general access to the operations audit log.
CREATE FUNCTION security.demo_scene_history(p_scene uuid,p_limit integer DEFAULT 50,p_offset integer DEFAULT 0)
 RETURNS TABLE(id bigint,occurred_at timestamptz,actor_name text,action_code text,before_snapshot jsonb,after_snapshot jsonb,total bigint)
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM crm.opportunity_demo_scenes d WHERE d.id=p_scene
 AND d.workspace_id=common.current_workspace_id() AND security.has_opportunity_read_access(d.opportunity_id))
 THEN RAISE insufficient_privilege; END IF;
 RETURN QUERY SELECT a.id,a.occurred_at,COALESCE(a.actor_name_snapshot,u.display_name,'系统'),a.action_code,
 a.before_snapshot,a.after_snapshot,count(*) OVER() FROM ops.audit_log a
 LEFT JOIN platform.user_ref u ON u.id=a.actor_user_ref_id AND u.workspace_id=a.workspace_id
 WHERE a.workspace_id=common.current_workspace_id() AND a.object_type='opportunity_demo_scenes' AND a.object_id=p_scene
 ORDER BY a.occurred_at DESC,a.id DESC LIMIT LEAST(GREATEST(p_limit,1),100) OFFSET GREATEST(p_offset,0);
END $$;
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='crm' AND table_name='opportunity' AND privilege_type='INSERT' AND grantee<>'PUBLIC'
 LOOP EXECUTE format('GRANT SELECT,INSERT,UPDATE ON crm.opportunity_demo_scenes TO %I',r.grantee); END LOOP;
END $$;
COMMIT;
