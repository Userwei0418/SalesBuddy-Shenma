BEGIN;
-- A/B/C are legacy customer tiers, not the separate opportunity amount grades.
UPDATE crm.customer SET level_code=CASE upper(trim(level_code))
 WHEN 'A' THEN 'Tier-1' WHEN 'B' THEN 'Tier-2' WHEN 'C' THEN 'Tier-3' END
 WHERE upper(trim(level_code)) IN ('A','B','C');
COMMENT ON COLUMN crm.customer.level_code IS '当前客户等级 Tier-1 / Tier-2 / Tier-3；历史未知值保留，不推算等级';

CREATE FUNCTION security.profile_scope_allowed(p_scope text,p_user uuid,p_team uuid,p_write boolean DEFAULT false)
 RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE
 WHEN p_scope='department' AND p_user IS NULL AND p_team IS NULL THEN
  common.current_role_code()='manager' AND security.has_active_role('manager')
 WHEN p_scope='team' AND p_user IS NULL THEN EXISTS(
  SELECT 1 FROM platform.team t WHERE t.id=p_team AND t.workspace_id=common.current_workspace_id()
   AND t.deleted_at IS NULL AND t.status='active' AND (security.supervises_team(t.id)
    OR (NOT p_write AND common.current_role_code()='manager' AND security.has_active_role('manager'))))
 WHEN p_scope='person' AND p_team IS NULL THEN EXISTS(
  SELECT 1 FROM platform.user_ref u WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id()
   AND u.deleted_at IS NULL AND u.status='active' AND (
    (p_user=common.current_user_ref_id() AND security.has_active_role(common.current_role_code())
      AND (NOT p_write OR common.current_role_code()='sales')) OR
    (NOT p_write AND ((common.current_role_code()='manager' AND security.has_active_role('manager'))
     OR EXISTS(SELECT 1 FROM platform.team_membership tm WHERE tm.user_ref_id=u.id
      AND tm.workspace_id=u.workspace_id AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
      AND security.supervises_team(tm.team_id))))))
 ELSE false END;
$$;
CREATE TABLE crm.sales_target (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 target_year integer NOT NULL CHECK(target_year BETWEEN 2000 AND 2100),
 scope_type text NOT NULL CHECK(scope_type IN ('person','team','department')),
 user_ref_id uuid, team_id uuid REFERENCES platform.team(id),
 kind text NOT NULL CHECK(kind IN ('collection','recognized')),
 amount numeric(18,2) NOT NULL CHECK(amount>0),
 updated_by_user_ref_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 version_no integer NOT NULL DEFAULT 1,
 CHECK((scope_type='person' AND user_ref_id IS NOT NULL AND team_id IS NULL)
  OR (scope_type='team' AND team_id IS NOT NULL AND user_ref_id IS NULL)
  OR (scope_type='department' AND user_ref_id IS NULL AND team_id IS NULL)),
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(updated_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 UNIQUE NULLS NOT DISTINCT(workspace_id,target_year,scope_type,user_ref_id,team_id,kind)
);
COMMENT ON TABLE crm.sales_target IS '年度销售目标，单位元；个人/团队/部门独立设定，不自动相加或沿用上一年；实绩另查 customer_actual';
ALTER TABLE crm.sales_target ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.sales_target FORCE ROW LEVEL SECURITY;
CREATE POLICY sales_target_read ON crm.sales_target FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.profile_scope_allowed(scope_type,user_ref_id,team_id));
CREATE POLICY sales_target_insert ON crm.sales_target FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND updated_by_user_ref_id=common.current_user_ref_id()
 AND security.profile_scope_allowed(scope_type,user_ref_id,team_id,true));
CREATE POLICY sales_target_update ON crm.sales_target FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.profile_scope_allowed(scope_type,user_ref_id,team_id,true))
 WITH CHECK(workspace_id=common.current_workspace_id() AND updated_by_user_ref_id=common.current_user_ref_id()
 AND security.profile_scope_allowed(scope_type,user_ref_id,team_id,true));
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='crm' AND table_name='customer' AND privilege_type='INSERT' AND grantee<>'PUBLIC'
 LOOP EXECUTE format('GRANT SELECT,INSERT,UPDATE ON crm.sales_target TO %I',r.grantee); END LOOP;
END $$;
COMMIT;
