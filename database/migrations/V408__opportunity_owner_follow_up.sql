BEGIN;
-- Forward-only repair: an opportunity owner need not own its customer.
-- Retain the V404 collaborator path and the original legacy path unchanged.
-- Replacing the same signature preserves its existing precise EXECUTE grants.
CREATE OR REPLACE FUNCTION security.authorization_visit_target(p_permission text,p_customer uuid,p_opportunity uuid,
 p_recorder uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit_target_v255(p_permission,p_customer,p_opportunity,p_recorder,p_team) OR (
 p_recorder=common.current_user_ref_id() AND p_permission IN
 ('visit.read','visit.create','visit.structure','visit.quality_review')
 AND security.authorization_has(p_permission) AND security.authorization_has('opportunity.read')
 AND EXISTS(SELECT 1 FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id() AND o.customer_id=p_customer
 AND o.deleted_at IS NULL AND c.deleted_at IS NULL
 AND (security.opportunity_collaborates(o.id) OR (
  o.owner_user_ref_id=common.current_user_ref_id()
  AND security.authorization_opportunity('opportunity.read',o.id)
  AND security.authorization_allows(p_permission,o.workspace_id,p_recorder,ARRAY[p_team],true)))));
$$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V408','Scoped opportunity owner follow-up without customer ownership');
COMMIT;
