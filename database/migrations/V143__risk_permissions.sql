BEGIN;
-- Validate the row itself, also for INSERT ... RETURNING. The assigned
-- relation is an actual opportunity participation, never another grant's scope.
CREATE FUNCTION security.authorization_risk_scope(p_permission text,p_workspace uuid,p_owner uuid,
 p_team uuid,p_customer uuid,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_allows(p_permission,p_workspace,p_owner,
 ARRAY[p_team] || ARRAY(SELECT DISTINCT tm.team_id FROM crm.opportunity_participant p
 JOIN platform.team_membership tm ON tm.user_ref_id=p.user_ref_id AND tm.workspace_id=p.workspace_id
 JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
 WHERE p.workspace_id=p_workspace AND p.opportunity_id=p_opportunity
 AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 AND security.fde_user_is_active(p.user_ref_id)
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 AND t.status='active' AND t.deleted_at IS NULL),
 p_owner=common.current_user_ref_id() OR EXISTS(SELECT 1 FROM crm.opportunity_participant p
 WHERE p.workspace_id=p_workspace AND p.opportunity_id=p_opportunity
 AND p.user_ref_id=common.current_user_ref_id()
 AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 AND security.fde_user_is_active(p.user_ref_id)))
 OR ((p_owner IS NULL OR (p_opportunity IS NULL AND p_permission NOT IN ('risk.resolve','risk.auto_review')))
 AND p_workspace=common.current_workspace_id() AND p_customer IS NOT NULL
 AND security.authorization_customer(p_permission,p_customer));
$$;
CREATE FUNCTION security.authorization_risk(p_permission text,p_risk uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM insight.risk r WHERE r.id=p_risk
 AND r.workspace_id=common.current_workspace_id() AND r.deleted_at IS NULL
 AND security.authorization_risk_scope(p_permission,r.workspace_id,r.owner_user_ref_id,
 r.owner_team_id,r.customer_id,r.opportunity_id));
$$;
CREATE FUNCTION security.authorization_risk_generation(p_customer uuid,p_owner uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p_owner=common.current_user_ref_id() AND (
  security.authorization_customer('risk.auto_review',p_customer)
  OR security.authorization_customer('agent.personal_risks',p_customer));
$$;
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT * FROM pg_policies WHERE schemaname='insight' AND tablename IN ('risk','risk_event') LOOP
 EXECUTE format('DROP POLICY %I ON insight.%I',p.policyname,p.tablename); END LOOP;
END $$;
CREATE POLICY permission_read ON insight.risk FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND deleted_at IS NULL
 AND security.authorization_risk_scope(security.authorization_read_feature('risk'),workspace_id,
 owner_user_ref_id,owner_team_id,customer_id,opportunity_id));
CREATE POLICY permission_insert ON insight.risk FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.authorization_risk_generation(customer_id,owner_user_ref_id));
CREATE POLICY permission_update ON insight.risk FOR UPDATE USING(
 security.authorization_risk('risk.resolve',id) OR security.authorization_risk_generation(customer_id,owner_user_ref_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND (
 security.authorization_risk('risk.resolve',id) OR security.authorization_risk_generation(customer_id,owner_user_ref_id)));
CREATE FUNCTION security.guard_risk_permission() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_user AND rolsuper) THEN RETURN NEW; END IF;
 IF (NEW.workspace_id,NEW.customer_id,NEW.opportunity_id,NEW.owner_user_ref_id,NEW.owner_team_id,NEW.source_visit_id)
 IS DISTINCT FROM (OLD.workspace_id,OLD.customer_id,OLD.opportunity_id,OLD.owner_user_ref_id,OLD.owner_team_id,OLD.source_visit_id)
 OR NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN RAISE insufficient_privilege; END IF;
 IF (NEW.status,NEW.resolved_at,NEW.resolution_note) IS DISTINCT FROM (OLD.status,OLD.resolved_at,OLD.resolution_note)
 AND NOT security.authorization_risk('risk.resolve',OLD.id) THEN RAISE insufficient_privilege; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','resolved_at','resolution_note','version_no','updated_at'])
 IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','resolved_at','resolution_note','version_no','updated_at'])
 AND NOT security.authorization_risk_generation(OLD.customer_id,OLD.owner_user_ref_id)
 THEN RAISE insufficient_privilege; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER permission_risk_update BEFORE UPDATE ON insight.risk
 FOR EACH ROW EXECUTE FUNCTION security.guard_risk_permission();
CREATE POLICY permission_read ON insight.risk_event FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.authorization_risk(security.authorization_read_feature('risk'),risk_id));
CREATE POLICY permission_insert ON insight.risk_event FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
 AND EXISTS(SELECT 1 FROM insight.risk r WHERE r.id=risk_id AND r.workspace_id=common.current_workspace_id()
 AND (CASE WHEN event_type='resolved' THEN security.authorization_risk('risk.resolve',r.id)
 ELSE security.authorization_risk_generation(r.customer_id,r.owner_user_ref_id) END)));

-- The recorded company owner remains the execution identity. Extra grants can
-- enable that owner's action without inventing a sales appointment.
ALTER TABLE insight.customer_risk_assessment DROP CONSTRAINT customer_risk_assessment_actor_role_code_check;
ALTER TABLE insight.customer_risk_assessment ADD CONSTRAINT customer_risk_assessment_actor_role_code_check
 CHECK(actor_role_code IN ('sales','supervisor','manager','fde','fde_lead','operations','administrator'));
CREATE OR REPLACE FUNCTION security.customer_risk_assessment_owner(p_customer uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT own.owner_user_ref_id FROM crm.customer_ownership own
 JOIN crm.customer c ON c.id=own.customer_id AND c.workspace_id=own.workspace_id
 WHERE c.id=p_customer AND c.workspace_id=common.current_workspace_id()
 AND c.deleted_at IS NULL AND own.state='claimed' AND security.has_customer_access(c.id)
 AND security.authorization_user_allows('risk.auto_review',c.workspace_id,own.owner_user_ref_id,
 own.owner_user_ref_id,ARRAY[c.owner_team_id],true);
$$;
ALTER POLICY customer_risk_assessment_execute ON insight.customer_risk_assessment
 USING(workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
 AND actor_role_code=common.current_role_code() AND security.has_active_role(actor_role_code)
 AND actor_user_ref_id=security.customer_risk_assessment_owner(customer_id)
 AND security.authorization_customer('risk.auto_review',customer_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
 AND actor_role_code=common.current_role_code() AND security.has_active_role(actor_role_code)
 AND actor_user_ref_id=security.customer_risk_assessment_owner(customer_id)
 AND security.authorization_customer('risk.auto_review',customer_id));
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v142;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record; f record;
BEGIN
 total:=security.reconcile_runtime_grants_v142();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_risk_scope(text,uuid,uuid,uuid,uuid,uuid),security.authorization_risk(text,uuid),security.authorization_risk_generation(uuid,uuid) TO %I',r.rolname);
  -- Broad bootstrap grants must not expose internal grant calculators or the
  -- owner-only migration/privilege-repair chain to application connections.
  FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p
   JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='security'
   AND (p.proname LIKE 'reconcile_runtime_grants%' OR p.proname IN (
    'authorization_grants_for','authorization_user_allows','authorization_legacy_fde_visit',
    'initialize_permission_roles','initialize_workspace_permissions','validate_authorization_grants',
    'authorization_password_write','unlock_account_after_password','require_permission_administrator',
    'bump_authorization_scope_revision'))
   AND p.proowner<>(SELECT oid FROM pg_roles WHERE rolname=r.rolname)
  LOOP EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',f.signature,r.rolname); END LOOP;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v142(),
 security.authorization_risk_scope(text,uuid,uuid,uuid,uuid,uuid),security.authorization_risk(text,uuid),security.authorization_risk_generation(uuid,uuid),security.guard_risk_permission()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V143','Separate scoped risk resolution from authorized risk generation');
COMMIT;
