BEGIN;

-- One permission is evaluated against one record. Unrelated role scopes never
-- participate in this decision. Helpers always bind to the authenticated tenant.
CREATE FUNCTION security.authorization_allows(p_permission text,p_workspace uuid,p_owner uuid,
 p_teams uuid[] DEFAULT '{}',p_assigned boolean DEFAULT false) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS (SELECT * FROM security.authorization_current_grants()
   WHERE permission_code=p_permission)
 SELECT p_workspace=common.current_workspace_id()
 AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny')
 AND EXISTS(SELECT 1 FROM grants WHERE effect='allow' AND (
  scope_code='workspace' OR (scope_code='self' AND p_owner=common.current_user_ref_id())
  OR (scope_code='assigned' AND p_assigned) OR (scope_code='teams' AND team_ids&&p_teams)));
$$;

CREATE FUNCTION security.authorization_opportunity(p_permission text,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE((SELECT security.authorization_allows(p_permission,o.workspace_id,o.owner_user_ref_id,
  ARRAY[o.owner_team_id] || ARRAY(
   SELECT DISTINCT tm.team_id FROM crm.opportunity_participant p
   JOIN platform.team_membership tm ON tm.user_ref_id=p.user_ref_id AND tm.workspace_id=p.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
   WHERE p.workspace_id=o.workspace_id AND (p.opportunity_id=o.id OR (p_permission='opportunity.read' AND EXISTS(
    SELECT 1 FROM crm.opportunity related WHERE related.id=p.opportunity_id AND related.customer_id=o.customer_id
     AND related.workspace_id=o.workspace_id AND related.deleted_at IS NULL)))
    AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
    AND security.fde_user_is_active(p.user_ref_id)
    AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
    AND t.status='active' AND t.deleted_at IS NULL),
  o.owner_user_ref_id=common.current_user_ref_id() OR EXISTS(
   SELECT 1 FROM crm.opportunity_participant p JOIN platform.user_ref u ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
   WHERE p.workspace_id=o.workspace_id AND p.user_ref_id=common.current_user_ref_id()
   AND u.status='active' AND u.deleted_at IS NULL
   AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
   AND (p.opportunity_id=o.id OR (p_permission='opportunity.read' AND EXISTS(
    SELECT 1 FROM crm.opportunity related WHERE related.id=p.opportunity_id AND related.customer_id=o.customer_id
     AND related.workspace_id=o.workspace_id AND related.deleted_at IS NULL)))))
 FROM crm.opportunity o WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL),false);
$$;

-- Create-only grants authorize the append-only side effects of that INSERT in
-- this transaction; they do not authorize later edits to the created opportunity.
CREATE FUNCTION security.authorization_opportunity_mutation(p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity('opportunity.update',p_opportunity) OR EXISTS(
  SELECT 1 FROM crm.opportunity o WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id()
  AND o.created_by_user_ref_id=common.current_user_ref_id() AND pg_xact_status(o.xmin::text::xid8)='in progress' AND o.created_at>=transaction_timestamp()
  AND security.authorization_allows('opportunity.create',o.workspace_id,o.owner_user_ref_id,ARRAY[o.owner_team_id],
   o.owner_user_ref_id=common.current_user_ref_id()));
$$;

CREATE OR REPLACE FUNCTION security.has_opportunity_access(p_opportunity_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity_mutation(p_opportunity_id);
$$;
CREATE OR REPLACE FUNCTION security.has_opportunity_read_access(p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity('opportunity.read',p_opportunity);
$$;
CREATE OR REPLACE FUNCTION security.can_manage_fde_members(p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity('opportunity.fde_members',p_opportunity);
$$;

-- Replace role-name exclusions for this aggregate only. Other business tables
-- retain their existing policies until their own permission adapters are installed.
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT policyname FROM pg_policies WHERE schemaname='crm' AND tablename='opportunity' LOOP
  EXECUTE format('DROP POLICY %I ON crm.opportunity',p.policyname);
 END LOOP;
END $$;
CREATE POLICY permission_read ON crm.opportunity FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND (
 security.authorization_allows('opportunity.read',workspace_id,owner_user_ref_id,ARRAY[owner_team_id],
  owner_user_ref_id=common.current_user_ref_id()) OR security.authorization_opportunity('opportunity.read',id)));
CREATE POLICY permission_insert ON crm.opportunity FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND security.customer_reference(customer_id) IS NOT NULL
 AND EXISTS(SELECT 1 FROM platform.team t WHERE t.id=crm.opportunity.owner_team_id AND t.workspace_id=crm.opportunity.workspace_id
  AND t.workspace_id=common.current_workspace_id() AND t.status='active' AND t.deleted_at IS NULL
  AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to AND (
   EXISTS(SELECT 1 FROM platform.team_membership tm WHERE tm.workspace_id=t.workspace_id AND tm.team_id=t.id
    AND tm.user_ref_id=crm.opportunity.owner_user_ref_id AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to)
   OR security.authorization_allows('opportunity.create',t.workspace_id,NULL,ARRAY[t.id],false)))
 AND security.authorization_allows('opportunity.create',workspace_id,owner_user_ref_id,ARRAY[owner_team_id],
   owner_user_ref_id=common.current_user_ref_id())
 AND (owner_user_ref_id=common.current_user_ref_id() OR security.authorization_allows(
  'opportunity.create_for_others',workspace_id,owner_user_ref_id,ARRAY[owner_team_id],false)));
CREATE POLICY permission_update ON crm.opportunity FOR UPDATE
 USING(workspace_id=common.current_workspace_id() AND security.authorization_opportunity('opportunity.update',id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.authorization_allows(
  'opportunity.update',workspace_id,owner_user_ref_id,ARRAY[owner_team_id],owner_user_ref_id=common.current_user_ref_id())
  AND security.customer_reference(customer_id) IS NOT NULL);
-- No direct deletion API exists. Closing and reopening use status changes and
-- independent service permissions, retaining the audit history.

DO $$ DECLARE t text; p record; BEGIN
 FOREACH t IN ARRAY ARRAY['opportunity_forecast','business_change'] LOOP
  FOR p IN SELECT policyname FROM pg_policies WHERE schemaname='crm' AND tablename=t AND policyname LIKE 'fde_commercial_%' LOOP
   EXECUTE format('DROP POLICY %I ON crm.%I',p.policyname,t);
  END LOOP;
 END LOOP;
END $$;
CREATE POLICY permission_write_insert ON crm.opportunity_forecast AS RESTRICTIVE FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.authorization_opportunity_mutation(opportunity_id));
CREATE POLICY permission_write_update ON crm.opportunity_forecast AS RESTRICTIVE FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.authorization_opportunity_mutation(opportunity_id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.authorization_opportunity_mutation(opportunity_id));
CREATE POLICY permission_write_delete ON crm.opportunity_forecast AS RESTRICTIVE FOR DELETE USING(
 workspace_id=common.current_workspace_id() AND security.authorization_opportunity('opportunity.update',opportunity_id));

-- Side-effect permission is tied to the business event just written by this actor;
-- it cannot be used to send arbitrary notices to other company members.
CREATE POLICY permission_opportunity_notice ON workflow.notification FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND object_type='opportunity' AND template_code='business_changed'
 AND EXISTS(SELECT 1 FROM crm.business_change b JOIN crm.opportunity o ON o.id=b.opportunity_id AND o.workspace_id=b.workspace_id
  WHERE b.id::text=notification.payload->>'event_id' AND b.workspace_id=notification.workspace_id
  AND b.actor_user_ref_id=common.current_user_ref_id() AND b.opportunity_id=notification.object_id
  AND notification.recipient_user_ref_id IN (common.current_user_ref_id(),o.owner_user_ref_id)
  AND security.authorization_opportunity_mutation(o.id)));

REVOKE ALL ON FUNCTION security.authorization_allows(text,uuid,uuid,uuid[],boolean),
 security.authorization_opportunity(text,uuid),security.authorization_opportunity_mutation(uuid) FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v126;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v126();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_allows(text,uuid,uuid,uuid[],boolean),
   security.authorization_opportunity(text,uuid),security.authorization_opportunity_mutation(uuid) TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v126() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V127','Scoped configurable opportunity permissions');
CREATE OR REPLACE FUNCTION workflow.complete_opportunity_change_review(p_event uuid,p_review jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_workspace uuid; v_count integer;
BEGIN
 IF p_review->>'status' IS DISTINCT FROM 'completed'
    OR COALESCE(p_review->>'color','') NOT IN ('green','yellow','red','gray')
    OR COALESCE(p_review->>'source','') NOT IN ('agent_platform','rules')
    OR length(COALESCE(p_review->>'summary','')) NOT BETWEEN 1 AND 240 THEN
  RAISE EXCEPTION 'invalid opportunity change assessment' USING ERRCODE='22023';
 END IF;
 SELECT workspace_id INTO v_workspace FROM crm.business_change
 WHERE id=p_event AND workspace_id=common.current_workspace_id()
   AND actor_user_ref_id=common.current_user_ref_id() AND opportunity_id IS NOT NULL
   AND security.authorization_opportunity('opportunity.update',opportunity_id);
 IF v_workspace IS NULL THEN RAISE EXCEPTION 'change assessment outside current scope' USING ERRCODE='42501'; END IF;
 UPDATE workflow.notification SET payload=payload || jsonb_build_object('change_review',p_review),
   title=CASE p_review->>'color' WHEN 'green' THEN '商机变化向好' WHEN 'yellow' THEN '商机变化需关注'
          WHEN 'red' THEN '商机变化转差' ELSE '商机信息已更新' END
 WHERE workspace_id=v_workspace AND template_code='business_changed'
   AND payload->>'event_id'=p_event::text AND payload->'change_review'->>'status'='pending';
 GET DIAGNOSTICS v_count=ROW_COUNT;
 RETURN v_count;
END $$;

COMMIT;
