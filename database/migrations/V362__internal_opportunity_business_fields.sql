-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V214__opportunity_business_fields.sql.
-- Original SHA-256: 1eb027f46afdff2779661522439d4d10ddd5747e39751b99c77eab8fdf8fe5e5. See shenma_whole_121_manifest.json.
BEGIN;
SET LOCAL check_function_bodies=on;

-- Formal current facts are separate from immutable import provenance. No data
-- is initialized from source_fields, and no business read scope is enlarged.
CREATE FUNCTION crm.valid_opportunity_sources(p_values text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT p_values IS NOT NULL AND cardinality(p_values)<=7
 AND p_values <@ ARRAY['company_lead','partner_existing','marketing_event','customer_referral',
                       'partner_stock','digital_marketing','sales_outbound']::text[]
 AND array_position(p_values,NULL) IS NULL
 AND cardinality(p_values)=(SELECT count(DISTINCT source_value) FROM unnest(p_values) source_value)
$$;

ALTER TABLE crm.opportunity
 ADD COLUMN customer_source text[] NOT NULL DEFAULT '{}' CHECK(crm.valid_opportunity_sources(customer_source)),
 ADD COLUMN priority text CHECK(priority IS NULL OR priority='p0'),
 ADD COLUMN is_key boolean,
 ADD COLUMN trial_status text CHECK(trial_status IN ('preparing','requested','enabled')),
 ADD COLUMN summary text CHECK(length(summary)<=500),
 ADD COLUMN note text CHECK(length(note)<=5000),
 ADD COLUMN ppl_status text CHECK(ppl_status IN ('created','not_created')),
 ADD COLUMN external_system_status text CHECK(external_system_status IN ('commit','high','medium','low')),
 ADD COLUMN primary_fde_id uuid,
 ADD COLUMN fde_support_stage text CHECK(fde_support_stage IN ('trial_support','demo_build','project_delivery')),
 ADD COLUMN fde_progress text CHECK(length(fde_progress)<=5000),
 ADD CONSTRAINT opportunity_primary_fde_workspace FOREIGN KEY(primary_fde_id,workspace_id)
 REFERENCES platform.user_ref(id,workspace_id);

CREATE TABLE crm.opportunity_project_participant (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 opportunity_id uuid NOT NULL,
 user_ref_id uuid NOT NULL,
 participant_role text NOT NULL CHECK(participant_role IN ('sales','fde','other')),
 created_by_user_ref_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(opportunity_id,user_ref_id),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(created_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
COMMENT ON TABLE crm.opportunity_project_participant IS '正式项目参与职责；不参与商机授权，不代表本次拜访出席';
CREATE INDEX opportunity_project_participant_user ON crm.opportunity_project_participant(workspace_id,user_ref_id);
ALTER TABLE crm.opportunity_project_participant ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_project_participant FORCE ROW LEVEL SECURITY;
CREATE POLICY active_tenant_guard ON crm.opportunity_project_participant AS RESTRICTIVE FOR ALL
 USING(workspace_id=common.current_workspace_id() AND security.workspace_is_active(common.current_workspace_id()))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.workspace_is_active(common.current_workspace_id()));
CREATE POLICY project_participant_read ON crm.opportunity_project_participant FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.authorization_opportunity('opportunity.read',opportunity_id));
CREATE POLICY project_participant_insert ON crm.opportunity_project_participant FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND security.authorization_opportunity_mutation(opportunity_id));
CREATE POLICY project_participant_update ON crm.opportunity_project_participant FOR UPDATE
 USING(workspace_id=common.current_workspace_id() AND security.authorization_opportunity('opportunity.update',opportunity_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.authorization_opportunity('opportunity.update',opportunity_id));
CREATE POLICY project_participant_delete ON crm.opportunity_project_participant FOR DELETE USING(
 workspace_id=common.current_workspace_id() AND security.authorization_opportunity('opportunity.update',opportunity_id));

CREATE FUNCTION crm.guard_project_participant() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.user_ref_id,NEW.created_at,NEW.created_by_user_ref_id)
   IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.opportunity_id,OLD.user_ref_id,OLD.created_at,OLD.created_by_user_ref_id)
  THEN RAISE EXCEPTION '项目参与关系身份不可修改' USING ERRCODE='23514'; END IF;
  IF NEW.participant_role=OLD.participant_role THEN RETURN NEW; END IF;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=NEW.user_ref_id AND u.workspace_id=NEW.workspace_id
  AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL)
  OR (NEW.participant_role='fde' AND NOT security.fde_user_is_active(NEW.user_ref_id))
 THEN RAISE EXCEPTION '项目参与者不存在、已停用或职责不适用' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER project_participant_guard BEFORE INSERT OR UPDATE ON crm.opportunity_project_participant
 FOR EACH ROW EXECUTE FUNCTION crm.guard_project_participant();
CREATE TRIGGER business_audit AFTER INSERT OR UPDATE OR DELETE ON crm.opportunity_project_participant
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();

CREATE FUNCTION crm.guard_primary_fde_change() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' AND NEW.primary_fde_id IS NOT DISTINCT FROM OLD.primary_fde_id THEN RETURN NEW; END IF;
 IF TG_OP='INSERT' AND NEW.primary_fde_id IS NULL THEN RETURN NEW; END IF;
 IF NOT security.authorization_allows('opportunity.fde_members',NEW.workspace_id,NEW.owner_user_ref_id,
  ARRAY[NEW.owner_team_id],NEW.owner_user_ref_id=common.current_user_ref_id())
  AND NOT (TG_OP='UPDATE' AND security.can_manage_fde_members(NEW.id))
 THEN RAISE EXCEPTION '无权调整主责FDE' USING ERRCODE='42501'; END IF;
 IF NEW.primary_fde_id IS NOT NULL AND NOT security.fde_user_is_active(NEW.primary_fde_id)
 THEN RAISE EXCEPTION '新主责须为当前有效FDE' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER opportunity_primary_fde_guard BEFORE INSERT OR UPDATE OF primary_fde_id ON crm.opportunity
 FOR EACH ROW EXECUTE FUNCTION crm.guard_primary_fde_change();

-- Core, roster and primary are written in one transaction; check their final
-- state after all constituent writes, including the legacy roster endpoint.
CREATE FUNCTION crm.check_primary_fde_roster() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_opportunity uuid;
BEGIN
 IF TG_TABLE_NAME='opportunity' THEN
  IF TG_OP='UPDATE' AND NEW.primary_fde_id IS NOT DISTINCT FROM OLD.primary_fde_id THEN RETURN NULL; END IF;
  v_opportunity:=NEW.id;
 ELSE
  v_opportunity:=CASE WHEN TG_OP='DELETE' THEN OLD.opportunity_id ELSE NEW.opportunity_id END;
 END IF;
 IF EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=v_opportunity AND o.primary_fde_id IS NOT NULL
  AND NOT EXISTS(SELECT 1 FROM crm.opportunity_participant p WHERE p.workspace_id=o.workspace_id
   AND p.opportunity_id=o.id AND p.user_ref_id=o.primary_fde_id AND p.participant_role='fde'
   AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to))
 THEN RAISE EXCEPTION '主责FDE须保留在长期FDE名单中，请一并更换或清空主责' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION crm.check_primary_fde_roster() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER primary_fde_roster_opportunity AFTER INSERT OR UPDATE ON crm.opportunity
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION crm.check_primary_fde_roster();
CREATE CONSTRAINT TRIGGER primary_fde_roster_participant AFTER INSERT OR UPDATE OR DELETE ON crm.opportunity_participant
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION crm.check_primary_fde_roster();

REVOKE ALL ON crm.opportunity_project_participant FROM PUBLIC,salegent_feishu_worker;
DO $$ DECLARE v_role record; BEGIN
 FOR v_role IN SELECT DISTINCT g.grantee FROM information_schema.role_table_grants g
 JOIN pg_roles r ON r.rolname=g.grantee
 WHERE g.table_schema='crm' AND g.table_name='opportunity' AND g.privilege_type='INSERT'
 AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member') LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON crm.opportunity_project_participant TO %I',v_role.grantee);
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V362','Formal opportunity business fields and project participants');
COMMIT;
