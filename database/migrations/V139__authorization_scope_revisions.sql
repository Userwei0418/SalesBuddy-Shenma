BEGIN;
-- A tenant counter invalidates previously captured asynchronous scopes when a
-- relationship changes. It stores no business bodies and avoids hashing all CRM
-- rows for every authenticated request. Ordinary follow-up edits do not bump it.
CREATE TABLE config.authorization_scope_revision (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id), revision bigint NOT NULL DEFAULT 1
);
INSERT INTO config.authorization_scope_revision(workspace_id) SELECT id FROM platform.workspace;
ALTER TABLE config.authorization_scope_revision ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.authorization_scope_revision FORCE ROW LEVEL SECURITY;
REVOKE ALL ON config.authorization_scope_revision FROM PUBLIC,salegent_feishu_worker;
CREATE FUNCTION security.bump_authorization_scope_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid;
BEGIN
 IF TG_TABLE_NAME='workspace' THEN ws:=COALESCE(NEW.id,OLD.id);
 ELSE ws:=COALESCE(NEW.workspace_id,OLD.workspace_id); END IF;
 INSERT INTO config.authorization_scope_revision(workspace_id) VALUES(ws)
 ON CONFLICT(workspace_id) DO UPDATE SET revision=config.authorization_scope_revision.revision+1;
 RETURN COALESCE(NEW,OLD);
END $$;
REVOKE ALL ON FUNCTION security.bump_authorization_scope_revision() FROM PUBLIC,salegent_feishu_worker;
CREATE TRIGGER permission_scope_initialize AFTER INSERT ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE TRIGGER permission_scope_membership AFTER INSERT OR UPDATE OR DELETE ON platform.team_membership
 FOR EACH ROW EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE TRIGGER permission_scope_role AFTER INSERT OR UPDATE OR DELETE ON platform.role_binding
 FOR EACH ROW EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE TRIGGER permission_scope_team AFTER UPDATE OF status,deleted_at,parent_team_id,valid_from,valid_to ON platform.team
 FOR EACH ROW EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE TRIGGER permission_scope_account AFTER UPDATE OF status,deleted_at ON platform.user_ref
 FOR EACH ROW EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE TRIGGER permission_scope_participation AFTER INSERT OR UPDATE OR DELETE ON crm.opportunity_participant
 FOR EACH ROW EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE TRIGGER permission_scope_owner AFTER UPDATE OR DELETE ON crm.customer_ownership
 FOR EACH ROW EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE TRIGGER permission_scope_customer_members AFTER INSERT OR UPDATE OR DELETE ON crm.customer_sales_member
 FOR EACH ROW EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE TRIGGER permission_scope_customer AFTER UPDATE OF owner_user_ref_id,owner_team_id,deleted_at ON crm.customer
 FOR EACH ROW WHEN ((OLD.owner_user_ref_id,OLD.owner_team_id,OLD.deleted_at) IS DISTINCT FROM
 (NEW.owner_user_ref_id,NEW.owner_team_id,NEW.deleted_at)) EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE TRIGGER permission_scope_opportunity AFTER UPDATE OF owner_user_ref_id,owner_team_id,customer_id,deleted_at ON crm.opportunity
 FOR EACH ROW WHEN ((OLD.owner_user_ref_id,OLD.owner_team_id,OLD.customer_id,OLD.deleted_at) IS DISTINCT FROM
 (NEW.owner_user_ref_id,NEW.owner_team_id,NEW.customer_id,NEW.deleted_at)) EXECUTE FUNCTION security.bump_authorization_scope_revision();
CREATE OR REPLACE FUNCTION security.authorization_snapshot(p_user uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target uuid:=COALESCE(p_user,common.current_user_ref_id()); result jsonb; scope_revision bigint; elapsed_boundary timestamptz;
BEGIN
 IF target<>common.current_user_ref_id() AND NOT security.authorization_has('authorization.read') THEN
  RAISE EXCEPTION '无权查看此账号权限' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE id=target AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL) THEN
  RAISE EXCEPTION '账号不存在或不属于当前公司' USING ERRCODE='42501'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(g) ORDER BY g.permission_code,g.effect,g.source_id,g.scope_code,g.team_ids),'[]'::jsonb) INTO result
 FROM (SELECT * FROM security.authorization_current_grants() WHERE target=common.current_user_ref_id()
 UNION ALL SELECT * FROM security.authorization_grants_for(common.current_workspace_id(),target) WHERE target<>common.current_user_ref_id()) g;
 SELECT revision INTO scope_revision FROM config.authorization_scope_revision WHERE workspace_id=common.current_workspace_id();
 -- Time-based expiry must invalidate a cached analysis even without an UPDATE.
 SELECT max(boundary) INTO elapsed_boundary FROM (
  SELECT unnest(ARRAY[valid_from,valid_to]) AS boundary FROM platform.team_membership WHERE workspace_id=common.current_workspace_id()
  UNION ALL SELECT unnest(ARRAY[valid_from,valid_to]) FROM platform.role_binding WHERE workspace_id=common.current_workspace_id()
  UNION ALL SELECT unnest(ARRAY[valid_from,valid_to]) FROM platform.team WHERE workspace_id=common.current_workspace_id()
  UNION ALL SELECT unnest(ARRAY[valid_from,valid_to]) FROM crm.opportunity_participant WHERE workspace_id=common.current_workspace_id()
 ) bounds WHERE boundary<=clock_timestamp() AND isfinite(boundary);
 RETURN jsonb_build_object('workspace_id',common.current_workspace_id(),'user_id',target,'grants',result,
   'permission_version','rbac-v1:'||md5(concat_ws(':',result::text,COALESCE(scope_revision,0),elapsed_boundary)));
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V139','Invalidate asynchronous authorization snapshots after scope relationship changes');
COMMIT;
