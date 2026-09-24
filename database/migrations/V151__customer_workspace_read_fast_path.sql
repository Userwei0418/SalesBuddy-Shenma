BEGIN;

-- A workspace grant is independent of each customer. Evaluate it once per
-- statement, using this request's feature and current authorization snapshot.
-- Keep the tenant/deletion predicates outside the shortcut; narrower scopes
-- and explicit denies still use the original record-level authorization.
ALTER POLICY permission_read ON crm.customer USING (
 workspace_id=(SELECT common.current_workspace_id()) AND deleted_at IS NULL AND CASE
 WHEN (SELECT security.authorization_allows(security.authorization_read_feature('customer'),
   common.current_workspace_id(),NULL,'{}'::uuid[],false)) THEN true
 ELSE security.authorization_customer(security.authorization_read_feature('customer'),id)
   OR security.authorization_allows(security.authorization_read_feature('customer'),
     workspace_id,owner_user_ref_id,ARRAY[owner_team_id],owner_user_ref_id=common.current_user_ref_id())
 END);

-- The management ownership path originally required a live parent customer.
-- Preserve that condition using the parent's RLS-protected existence check.
-- This shortcut applies only to workspace READ, never to claim-review grants;
-- review-only accounts retain the original function and scope semantics.
ALTER POLICY management_read ON crm.customer_ownership USING (
 workspace_id=(SELECT common.current_workspace_id()) AND CASE
 WHEN (SELECT security.authorization_allows(security.authorization_read_feature('customer'),
   common.current_workspace_id(),NULL,'{}'::uuid[],false)) THEN EXISTS (
   SELECT 1 FROM crm.customer c WHERE c.id=customer_ownership.customer_id
    AND c.workspace_id=customer_ownership.workspace_id AND c.deleted_at IS NULL)
 ELSE security.authorization_customer(security.authorization_read_feature('customer'),customer_id)
   OR security.authorization_customer('customer.claim_review',customer_id)
 END);

INSERT INTO ops.schema_migration(version,description)
 VALUES('V151','Statement-local workspace customer read authorization without changing scoped access');
COMMIT;
