-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V215__opportunity_workspace_views.sql.
-- Original SHA-256: 6c3e565af7516525837568523a52864c4cb020ed28238ac203ed0879e4b2e64a. See shenma_whole_121_manifest.json.
BEGIN;

-- A view stores presentation settings only. Its visibility does not participate
-- in any crm.opportunity/customer/visit authorization function or policy.
CREATE TABLE IF NOT EXISTS crm.opportunity_saved_view (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 owner_user_ref_id uuid NOT NULL,
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 40),
 visibility text NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','team','company')),
 team_id uuid,
 config jsonb NOT NULL CHECK(jsonb_typeof(config)='object' AND octet_length(config::text)<=32768),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 deleted_at timestamptz,
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 CHECK((visibility='team')=(team_id IS NOT NULL)),
 FOREIGN KEY(owner_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(team_id,workspace_id) REFERENCES platform.team(id,workspace_id)
);
CREATE INDEX IF NOT EXISTS opportunity_saved_view_owner ON crm.opportunity_saved_view(workspace_id,owner_user_ref_id,updated_at DESC,id DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS opportunity_saved_view_team ON crm.opportunity_saved_view(workspace_id,team_id,updated_at DESC,id DESC) WHERE deleted_at IS NULL AND visibility='team';

CREATE OR REPLACE FUNCTION security.opportunity_view_team_allowed(p_team uuid,p_publish boolean DEFAULT false) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(
  SELECT 1 FROM platform.team t
  JOIN platform.team_membership m ON m.team_id=t.id AND m.workspace_id=t.workspace_id
  JOIN platform.user_ref u ON u.id=m.user_ref_id AND u.workspace_id=m.workspace_id
  JOIN platform.role_binding b ON b.workspace_id=m.workspace_id AND b.user_ref_id=m.user_ref_id
    AND b.role_code=m.membership_role AND (b.team_id IS NULL OR b.team_id=m.team_id)
  WHERE t.id=p_team AND t.workspace_id=common.current_workspace_id()
    AND m.user_ref_id=common.current_user_ref_id()
    AND (NOT p_publish OR m.membership_role IN ('supervisor','fde_lead'))
    AND t.status='active' AND t.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL
    AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
    AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
    AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
 ) AND security.authorization_allows('opportunity.read',common.current_workspace_id(),NULL,ARRAY[p_team],false)
   AND (NOT p_publish OR security.authorization_allows('opportunity.update',common.current_workspace_id(),NULL,ARRAY[p_team],false))
$$;
REVOKE ALL ON FUNCTION security.opportunity_view_team_allowed(uuid,boolean) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION security.opportunity_view_team_options()
 RETURNS TABLE(id uuid,name text,can_publish boolean)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT t.id,t.name,security.opportunity_view_team_allowed(t.id,true)
 FROM platform.team t WHERE t.workspace_id=common.current_workspace_id()
   AND security.opportunity_view_team_allowed(t.id,false)
 ORDER BY t.name,t.id
$$;
REVOKE ALL ON FUNCTION security.opportunity_view_team_options() FROM PUBLIC,salegent_feishu_worker;


-- The current baseline deliberately omits V205. Existing V205 data is retained,
-- including original v1 JSON; conversion is read-only until a user saves v2.
ALTER TABLE crm.opportunity_saved_view DROP CONSTRAINT IF EXISTS opportunity_saved_view_visibility_check;
ALTER TABLE crm.opportunity_saved_view ADD CONSTRAINT opportunity_saved_view_visibility_check
 CHECK(visibility IN ('private','team','company'));
CREATE UNIQUE INDEX IF NOT EXISTS opportunity_saved_view_workspace_identity ON crm.opportunity_saved_view(id,workspace_id);
CREATE OR REPLACE FUNCTION security.opportunity_view_company_allowed() RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.company_administrator_identity(common.current_workspace_id(),common.current_user_ref_id())
 AND security.authorization_allows('opportunity.read',common.current_workspace_id(),NULL,ARRAY[]::uuid[],false)
 AND security.authorization_allows('opportunity.update',common.current_workspace_id(),NULL,ARRAY[]::uuid[],false)
$$;
REVOKE ALL ON FUNCTION security.opportunity_view_company_allowed() FROM PUBLIC,salegent_feishu_worker;

ALTER TABLE crm.opportunity_saved_view ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_saved_view FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS active_tenant_guard ON crm.opportunity_saved_view;
CREATE POLICY active_tenant_guard ON crm.opportunity_saved_view AS RESTRICTIVE FOR ALL TO PUBLIC
 USING ((SELECT security.workspace_is_active(common.current_workspace_id())))
 WITH CHECK ((SELECT security.workspace_is_active(common.current_workspace_id())));
DROP POLICY IF EXISTS opportunity_view_read ON crm.opportunity_saved_view;
CREATE POLICY opportunity_view_read ON crm.opportunity_saved_view FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.authorization_has('opportunity.read')
 AND (owner_user_ref_id=common.current_user_ref_id()
      OR (deleted_at IS NULL AND (visibility='company' OR (visibility='team' AND security.opportunity_view_team_allowed(team_id,false))))));
DROP POLICY IF EXISTS opportunity_view_insert ON crm.opportunity_saved_view;
CREATE POLICY opportunity_view_insert ON crm.opportunity_saved_view FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id()
 AND deleted_at IS NULL AND security.authorization_has('opportunity.read')
 AND (visibility='private' OR (visibility='team' AND security.opportunity_view_team_allowed(team_id,true))
 OR (visibility='company' AND security.opportunity_view_company_allowed())));
DROP POLICY IF EXISTS opportunity_view_update ON crm.opportunity_saved_view;
CREATE POLICY opportunity_view_update ON crm.opportunity_saved_view FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id()
 AND deleted_at IS NULL AND security.authorization_has('opportunity.read'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND owner_user_ref_id=common.current_user_ref_id()
 AND security.authorization_has('opportunity.read')
 AND (deleted_at IS NOT NULL OR visibility='private' OR (visibility='team' AND security.opportunity_view_team_allowed(team_id,true))
 OR (visibility='company' AND security.opportunity_view_company_allowed())));

CREATE OR REPLACE FUNCTION crm.protect_opportunity_saved_view() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (NEW.id,NEW.workspace_id,NEW.owner_user_ref_id,NEW.created_at)
  IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.owner_user_ref_id,OLD.created_at)
 THEN RAISE EXCEPTION '视图所属公司和创建人不可修改' USING ERRCODE='22023'; END IF;
 IF OLD.deleted_at IS NOT NULL THEN RAISE EXCEPTION '视图已删除' USING ERRCODE='22023'; END IF;
 NEW.updated_at=clock_timestamp(); NEW.version_no=OLD.version_no+1; RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS opportunity_saved_view_version ON crm.opportunity_saved_view;
CREATE TRIGGER opportunity_saved_view_version BEFORE UPDATE ON crm.opportunity_saved_view
 FOR EACH ROW EXECUTE FUNCTION crm.protect_opportunity_saved_view();
DROP TRIGGER IF EXISTS opportunity_saved_view_audit ON crm.opportunity_saved_view;
CREATE TRIGGER opportunity_saved_view_audit AFTER INSERT OR UPDATE ON crm.opportunity_saved_view
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
REVOKE ALL ON crm.opportunity_saved_view FROM PUBLIC,salegent_feishu_worker;


CREATE TABLE crm.opportunity_view_preference (
 id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 user_ref_id uuid NOT NULL,
 default_view_id uuid,
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,user_ref_id),
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(default_view_id,workspace_id) REFERENCES crm.opportunity_saved_view(id,workspace_id)
);
ALTER TABLE crm.opportunity_view_preference ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_view_preference FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS active_tenant_guard ON crm.opportunity_view_preference;
CREATE POLICY active_tenant_guard ON crm.opportunity_view_preference AS RESTRICTIVE FOR ALL TO PUBLIC
 USING ((SELECT security.workspace_is_active(common.current_workspace_id())))
 WITH CHECK ((SELECT security.workspace_is_active(common.current_workspace_id())));
CREATE POLICY opportunity_view_preference_read ON crm.opportunity_view_preference FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND user_ref_id=common.current_user_ref_id()
 AND security.authorization_has('opportunity.read'));
CREATE POLICY opportunity_view_preference_insert ON crm.opportunity_view_preference FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND user_ref_id=common.current_user_ref_id()
 AND security.authorization_has('opportunity.read') AND (default_view_id IS NULL OR EXISTS(
 SELECT 1 FROM crm.opportunity_saved_view v WHERE v.id=opportunity_view_preference.default_view_id AND v.workspace_id=opportunity_view_preference.workspace_id AND v.deleted_at IS NULL)));
CREATE POLICY opportunity_view_preference_update ON crm.opportunity_view_preference FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND user_ref_id=common.current_user_ref_id()
 AND security.authorization_has('opportunity.read')) WITH CHECK(
 workspace_id=common.current_workspace_id() AND user_ref_id=common.current_user_ref_id()
 AND security.authorization_has('opportunity.read') AND (default_view_id IS NULL OR EXISTS(
 SELECT 1 FROM crm.opportunity_saved_view v WHERE v.id=opportunity_view_preference.default_view_id AND v.workspace_id=opportunity_view_preference.workspace_id AND v.deleted_at IS NULL)));
CREATE FUNCTION crm.protect_opportunity_view_preference() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (NEW.id,NEW.workspace_id,NEW.user_ref_id,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.user_ref_id,OLD.created_at)
 THEN RAISE EXCEPTION '默认方案所属公司和用户不可修改' USING ERRCODE='22023'; END IF;
 NEW.version_no=OLD.version_no+1; NEW.updated_at=clock_timestamp(); RETURN NEW;
END $$;
CREATE TRIGGER opportunity_view_preference_version BEFORE UPDATE ON crm.opportunity_view_preference
 FOR EACH ROW EXECUTE FUNCTION crm.protect_opportunity_view_preference();
CREATE TRIGGER opportunity_view_preference_audit AFTER INSERT OR UPDATE ON crm.opportunity_view_preference
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
REVOKE ALL ON crm.opportunity_view_preference FROM PUBLIC,salegent_feishu_worker;

-- Grant only to existing CRM business runtime roles; no Feishu-worker access, DELETE,
-- business-table policy change or new opportunity editing capability is added.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT g.grantee FROM information_schema.role_table_grants g
 JOIN pg_roles p ON p.rolname=g.grantee
 WHERE g.table_schema='crm' AND g.table_name='opportunity' AND g.privilege_type='INSERT'
  AND NOT pg_has_role(p.oid,'salegent_feishu_worker','member')
 LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON crm.opportunity_saved_view,crm.opportunity_view_preference TO %I',r.grantee);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.opportunity_view_company_allowed() TO %I',r.grantee);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.opportunity_view_team_allowed(uuid,boolean) TO %I',r.grantee);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.opportunity_view_team_options() TO %I',r.grantee);
 END LOOP;
END $$;
COMMENT ON TABLE crm.opportunity_saved_view IS '个人或授权团队的商机列/筛选视图；仅配置，不授予业务数据读取权限';
INSERT INTO ops.schema_migration(version,description) VALUES('V363','Workspace v2 saved views, company publication and per-user defaults');
COMMIT;
