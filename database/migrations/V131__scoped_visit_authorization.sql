BEGIN;


CREATE FUNCTION security.authorization_visit_target(p_permission text,p_customer uuid,p_opportunity uuid,
 p_recorder uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.customer_reference(p_customer) IS NOT NULL
 AND (p_opportunity IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity o
  WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id() AND o.customer_id=p_customer
  AND o.deleted_at IS NULL AND security.has_opportunity_read_access(o.id)))
 AND security.authorization_allows(p_permission,common.current_workspace_id(),p_recorder,ARRAY[p_team],
  p_opportunity IS NOT NULL AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=p_opportunity
   AND o.workspace_id=common.current_workspace_id() AND (o.owner_user_ref_id=common.current_user_ref_id()
    OR EXISTS(SELECT 1 FROM crm.opportunity_participant p WHERE p.opportunity_id=o.id
     AND p.workspace_id=o.workspace_id AND p.user_ref_id=common.current_user_ref_id()
     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to))));
$$;
CREATE FUNCTION security.authorization_visit(p_permission text,p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE((SELECT (security.authorization_allows(p_permission,v.workspace_id,v.recorder_user_ref_id,
  ARRAY[v.recorder_team_id] || ARRAY(SELECT DISTINCT tm.team_id
   FROM crm.opportunity_participant p JOIN platform.team_membership tm
    ON tm.user_ref_id=p.user_ref_id AND tm.workspace_id=p.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE (p.opportunity_id=v.opportunity_id OR (p_permission='visit.read' AND EXISTS(
     SELECT 1 FROM crm.opportunity related WHERE related.id=p.opportunity_id
      AND related.workspace_id=v.workspace_id AND related.customer_id=v.customer_id AND related.deleted_at IS NULL)))
    AND p.workspace_id=v.workspace_id
    AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
    AND security.fde_user_is_active(p.user_ref_id) AND t.status='active' AND t.deleted_at IS NULL
    AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to),
  (p_permission<>'visit.supplement' OR (v.recorder_user_ref_id=common.current_user_ref_id()
   AND v.created_by_user_ref_id=common.current_user_ref_id() AND v.confirmed_by_user_ref_id=common.current_user_ref_id()
   AND v.status='archived')) AND (
   (p_permission='visit.read' AND v.recorder_user_ref_id=common.current_user_ref_id()) OR
   (p_permission<>'visit.supplement' AND EXISTS(SELECT 1 FROM activity.visit_participant p
     WHERE p.visit_id=v.id AND p.workspace_id=v.workspace_id AND p.user_ref_id=common.current_user_ref_id()))
   OR EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
     AND (o.owner_user_ref_id=common.current_user_ref_id() OR EXISTS(
      SELECT 1 FROM crm.opportunity_participant p WHERE (p.opportunity_id=o.id OR (p_permission='visit.read' AND EXISTS(
        SELECT 1 FROM crm.opportunity related WHERE related.id=p.opportunity_id
         AND related.workspace_id=o.workspace_id AND related.customer_id=o.customer_id AND related.deleted_at IS NULL)))
       AND p.workspace_id=o.workspace_id
       AND p.user_ref_id=common.current_user_ref_id() AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to)))
  )) OR (p_permission IN ('battle_map.read','actual.read','overview.read')
  AND security.authorization_customer(p_permission,v.customer_id)))
 AND (v.opportunity_id IS NULL OR p_permission IN ('overview.read','dashboard.read','dashboard.ranking','profile.sales_read','profile.fde_read','profile.fde_activity','agent.chatbi','agent.customer_chatbi','agent.operating_report') OR security.has_opportunity_read_access(v.opportunity_id))
 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL),false);
$$;
CREATE FUNCTION security.authorization_visit_created(p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id()
 AND v.created_by_user_ref_id=common.current_user_ref_id() AND v.created_at>=transaction_timestamp()
 AND pg_xact_status(v.xmin::text::xid8)='in progress' AND security.authorization_visit_target(
 'visit.create',v.customer_id,v.opportunity_id,v.recorder_user_ref_id,v.recorder_team_id));
$$;
CREATE OR REPLACE FUNCTION security.can_write_visit(p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit('visit.supplement',p_visit) OR security.authorization_visit_created(p_visit) OR EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id()
  AND v.deleted_at IS NULL AND v.status IN ('draft','pending_confirm','confirmed')
  AND v.created_by_user_ref_id=common.current_user_ref_id() AND v.recorder_user_ref_id=common.current_user_ref_id()
  AND (v.confirmed_by_user_ref_id IS NULL OR v.confirmed_by_user_ref_id=common.current_user_ref_id())
  AND security.authorization_visit_target('visit.create',v.customer_id,v.opportunity_id,v.recorder_user_ref_id,v.recorder_team_id));
$$;
-- Human history imports retain their independently approved import policy.
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT tablename,policyname FROM pg_policies WHERE schemaname='activity'
 AND tablename IN ('visit','visit_field_value','visit_contact','visit_participant','visit_opportunity')
 AND policyname<>'visit_historical_multi_insert' LOOP
  EXECUTE format('DROP POLICY %I ON activity.%I',p.policyname,p.tablename);
 END LOOP;
END $$;
CREATE POLICY permission_read ON activity.visit FOR SELECT USING(workspace_id=common.current_workspace_id()
 AND (security.authorization_visit('visit.read',id) OR
 (created_by_user_ref_id=common.current_user_ref_id() AND recorder_user_ref_id=common.current_user_ref_id()
  AND security.authorization_visit_target('visit.read',customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id)) OR
 (opportunity_id IS NULL AND security.authorization_allows('visit.read',workspace_id,recorder_user_ref_id,ARRAY[recorder_team_id],false))));
CREATE POLICY permission_insert ON activity.visit FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id()
 AND created_by_user_ref_id=common.current_user_ref_id() AND recorder_user_ref_id=common.current_user_ref_id()
 AND (confirmed_by_user_ref_id IS NULL OR confirmed_by_user_ref_id=common.current_user_ref_id())
 AND (status NOT IN ('confirmed','archived') OR confirmed_by_user_ref_id=common.current_user_ref_id()
  OR security.authorization_allows('visit.create',workspace_id,recorder_user_ref_id,ARRAY[recorder_team_id],false))
 AND security.authorization_visit_target('visit.create',customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id));
CREATE POLICY permission_update ON activity.visit FOR UPDATE USING(workspace_id=common.current_workspace_id()
 AND security.can_write_visit(id)) WITH CHECK(workspace_id=common.current_workspace_id() AND security.can_write_visit(id));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['visit_field_value','visit_contact','visit_participant','visit_opportunity'] LOOP
  EXECUTE format('CREATE POLICY permission_read ON activity.%I FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.authorization_visit(''visit.read'',visit_id))',t);
  EXECUTE format('CREATE POLICY permission_insert ON activity.%I FOR INSERT WITH CHECK(workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id))',t);
  EXECUTE format('CREATE POLICY permission_update ON activity.%I FOR UPDATE USING(workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id)) WITH CHECK(workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id))',t);
  EXECUTE format('CREATE POLICY permission_delete ON activity.%I FOR DELETE USING(workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id))',t);
 END LOOP;
END $$;
-- Raw uploads remain owned by their uploader. Background processing must still
-- have current upload authorization; downloading originals has a separate gate.
DROP POLICY fde_import_insert ON activity.visit_import;
DROP POLICY fde_import_update ON activity.visit_import;
CREATE POLICY permission_import_insert ON activity.visit_import AS RESTRICTIVE FOR INSERT
 WITH CHECK(security.authorization_has('visit.upload'));
CREATE POLICY permission_import_update ON activity.visit_import AS RESTRICTIVE FOR UPDATE
 USING(security.authorization_has('visit.upload')) WITH CHECK(security.authorization_has('visit.upload'));

CREATE OR REPLACE FUNCTION activity.capture_visit_recording_identity()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF NEW.recording_role_code_snapshot IS DISTINCT FROM OLD.recording_role_code_snapshot
  OR NEW.recording_team_id_snapshot IS DISTINCT FROM OLD.recording_team_id_snapshot THEN
   RAISE EXCEPTION '拜访录入岗位及部门快照不可修改' USING ERRCODE='23514';
  END IF;
  IF OLD.recording_role_code_snapshot IN ('fde','fde_lead') THEN
   IF OLD.status IN ('draft','pending_confirm','confirmed') AND (
    (NEW.confirmed_by_user_ref_id IS NOT NULL AND NEW.confirmed_by_user_ref_id<>NEW.recorder_user_ref_id)
    OR NOT security.authorization_visit_target('visit.create',NEW.customer_id,NEW.opportunity_id,NEW.recorder_user_ref_id,NEW.recorder_team_id)) THEN
    RAISE EXCEPTION '待归档记录必须保持当前授权的客户、商机和本人身份' USING ERRCODE='42501';
   END IF;
   IF NEW.created_by_user_ref_id IS DISTINCT FROM OLD.created_by_user_ref_id
   OR NEW.recorder_user_ref_id IS DISTINCT FROM OLD.recorder_user_ref_id
   OR (OLD.status IN ('confirmed','archived')
       AND NEW.confirmed_by_user_ref_id IS DISTINCT FROM OLD.confirmed_by_user_ref_id) THEN
    RAISE EXCEPTION 'FDE本人录入及归档身份不可替换' USING ERRCODE='23514';
   END IF;
   IF NEW.status IN ('confirmed','archived') AND NEW.status IS DISTINCT FROM OLD.status
   AND (security.authorization_has('visit.create')
    AND NEW.created_by_user_ref_id=common.current_user_ref_id()
    AND NEW.recorder_user_ref_id=common.current_user_ref_id()
    AND NEW.confirmed_by_user_ref_id=common.current_user_ref_id()
    AND security.authorization_visit_target('visit.create',NEW.customer_id,NEW.opportunity_id,NEW.recorder_user_ref_id,NEW.recorder_team_id)) IS NOT TRUE THEN
    RAISE EXCEPTION '仅本人可确认归档当前参与商机的FDE拜访' USING ERRCODE='42501';
   END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF COALESCE(NEW.import_meta->>'import_type','')='crm_history' THEN
  -- The current importing operator is not the historical author's job identity.
  NEW.recording_role_code_snapshot:=NULL;
  NEW.recording_team_id_snapshot:=NULL;
  RETURN NEW;
 END IF;
 NEW.recording_role_code_snapshot:=CASE WHEN common.current_role_code() IN
 ('sales','supervisor','manager','operations','administrator','fde','fde_lead')
 THEN common.current_role_code() ELSE NULL END;
 IF common.current_role_code() IN ('fde','fde_lead') THEN
  SELECT tm.team_id INTO NEW.recording_team_id_snapshot FROM platform.team_membership tm
  WHERE tm.workspace_id=NEW.workspace_id AND tm.user_ref_id=common.current_user_ref_id()
  AND tm.membership_role IN ('fde','fde_lead')
  AND security.fde_user_is_active(common.current_user_ref_id(),common.current_role_code(),tm.team_id)
  AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
  ORDER BY (tm.team_id=NEW.recorder_team_id) DESC,tm.is_primary DESC,tm.team_id LIMIT 1;
 ELSE
  NEW.recording_team_id_snapshot:=NEW.recorder_team_id;
 END IF;
 RETURN NEW;
END $function$
;

CREATE OR REPLACE FUNCTION security.has_analysis_scope(p_identity jsonb, p_version smallint)
 RETURNS boolean
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT security.is_current_fact_scope(p_version)
 AND p_identity->>'workspace_id'=common.current_workspace_id()::text
 AND p_identity->>'user_id'=common.current_user_ref_id()::text
 AND p_identity->>'role'=common.current_role_code()
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(p_identity->'team_ids','[]')) prior_team
 WHERE NOT prior_team=ANY(COALESCE(string_to_array(current_setting('app.team_ids',true),','),ARRAY[]::text[])))
 AND p_identity->>'permission_version'=security.authorization_snapshot()->>'permission_version';
 $function$;


-- Replay a completed human archive using current visit access. Do not expose
-- an old model payload merely because its originating permission version aged.
CREATE FUNCTION security.archived_visit_receipt(p_run uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',v.id,'customer_id',v.customer_id,'opportunity_id',v.opportunity_id,
  'business_context',jsonb_build_object('visit_request',r.business_context->'visit_request'),
  'visit_goal',v.visit_goal,'follow_up_record',v.follow_up_record,'next_action',v.next_action,
  'is_first_visit',v.is_first_visit,'first_visit_profile',v.first_visit_profile,
  'opportunity_mutation_hash',v.opportunity_mutation_hash)
 FROM activity.visit v JOIN agent.artifact a ON a.id=v.source_artifact_id AND a.workspace_id=v.workspace_id
 JOIN agent.run r ON r.id=a.run_id AND r.workspace_id=v.workspace_id
 WHERE r.id=p_run AND r.intent_code='visit_entry' AND a.artifact_type='visit_entry'
 AND v.workspace_id=common.current_workspace_id() AND v.recorder_user_ref_id=common.current_user_ref_id()
 AND r.identity_context->>'user_id'=common.current_user_ref_id()::text
 AND v.deleted_at IS NULL AND security.authorization_visit('visit.read',v.id);
$$;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v130;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v130();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_visit_target(text,uuid,uuid,uuid,uuid),security.authorization_visit(text,uuid),security.authorization_visit_created(uuid),security.archived_visit_receipt(uuid) TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v130(),security.authorization_visit_target(text,uuid,uuid,uuid,uuid),security.authorization_visit(text,uuid),security.authorization_visit_created(uuid),security.archived_visit_receipt(uuid) FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V131','Scoped visits and current permission snapshots for analysis');
COMMIT;
