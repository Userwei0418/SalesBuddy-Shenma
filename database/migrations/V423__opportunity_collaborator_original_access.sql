BEGIN;
-- V404 gave explicit collaborators this opportunity's follow-up read scope,
-- but omitted the original-download route and action after formal archival.
-- Keep the legacy branches and function identities/ACLs. No new grants, table
-- policies, customer access, or upload/retry/supplement authority are introduced.
-- A current opportunity owner's scoped follow-up access uses that opportunity's
-- owner/team, not an unrelated author's team. Transfer removes this new branch.
DO $preflight$
DECLARE expected record; body text;
BEGIN
 FOR expected IN SELECT * FROM (VALUES
  ('security.authorization_opportunity(text,uuid)', 'e495a14b225c32bc8c0c0ff13b16d0d8'),
  ('security.authorization_visit(text,uuid)', '5749ab544b64c4f8dd9617cc1100b492')
 ) AS definitions(signature, digest) LOOP
  SELECT p.prosrc INTO body FROM pg_proc p
   WHERE p.oid=expected.signature::regprocedure AND p.prokind='f'
   AND p.prorettype='boolean'::regtype AND p.prosecdef AND p.provolatile='s'
   AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')
   AND p.proconfig=ARRAY['search_path=pg_catalog'];
  IF NOT FOUND OR md5(body)<>expected.digest THEN
   RAISE EXCEPTION 'V275_UNRECOGNIZED_COLLABORATION_AUTHORIZATION';
  END IF;
 END LOOP;
END $preflight$;

CREATE OR REPLACE FUNCTION security.authorization_opportunity(p_permission text,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity_v255(p_permission,p_opportunity) OR (
 p_permission='opportunity.read' AND security.authorization_has('opportunity.read')
 AND (COALESCE(current_setting('app.authorized_feature',true),'') IN
 ('','opportunity.read','visit.read','visit.create','visit.structure','visit.quality_review')
 OR (current_setting('app.authorized_feature',true)='visit.download_original'
  AND security.authorization_has('visit.read') AND security.authorization_has('visit.download_original')))
 AND security.opportunity_collaborates(p_opportunity));
$$;

CREATE OR REPLACE FUNCTION security.authorization_visit(p_permission text,p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit_v255(p_permission,p_visit) OR (
 p_permission IN ('visit.read','visit.download_original')
 AND security.authorization_has('visit.read') AND security.authorization_has(p_permission)
 AND (COALESCE(current_setting('app.authorized_feature',true),'') IN
 ('','visit.read','visit.create','visit.structure','visit.quality_review','opportunity.read')
 OR (current_setting('app.authorized_feature',true)='visit.download_original'
  AND security.authorization_has('visit.download_original')))
 AND EXISTS(SELECT 1 FROM activity.visit v JOIN crm.opportunity o
 ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
 WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id()
 AND v.deleted_at IS NULL
 AND o.deleted_at IS NULL
 AND (p_permission='visit.read' OR v.status IN ('confirmed','archived'))
 AND (security.opportunity_collaborates(o.id) OR (
  o.owner_user_ref_id=common.current_user_ref_id()
  AND security.authorization_allows('visit.read',o.workspace_id,o.owner_user_ref_id,ARRAY[o.owner_team_id],true)
  AND security.authorization_allows(p_permission,o.workspace_id,o.owner_user_ref_id,ARRAY[o.owner_team_id],true)))
 AND security.authorization_opportunity('opportunity.read',v.opportunity_id)));
$$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V423','Explicit opportunity collaborators original-download action after formal archival');
COMMIT;
