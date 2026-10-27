-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V226__own_unarchived_import_team_authorization.sql.
-- Original SHA-256: 576c1f46ff971dc0ab879aba122998bd6af8ff82f8705f3ab117ded5df9a6a23. See shenma_whole_121_manifest.json.
BEGIN;
-- SB120-MINI002, source only. The incident is historical, not current account evidence.
-- Preserve function identity/owner/ACL and both V142 branches. Fail closed if a
-- separately changed implementation is installed; never overwrite it blindly.
DO $preflight$
DECLARE body text;
BEGIN
 SELECT p.prosrc INTO body FROM pg_proc p
 WHERE p.oid='security.authorization_import(text,uuid)'::regprocedure
  AND p.prokind='f' AND p.prorettype='boolean'::regtype AND p.prosecdef AND p.provolatile='s'
  AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')
  AND p.proconfig=ARRAY['search_path=pg_catalog'];
 IF NOT FOUND OR md5(body)<>'00e3bdef636420763e1d6df254cb5105' THEN
  RAISE EXCEPTION 'V226_UNRECOGNIZED_IMPORT_AUTHORIZATION';
 END IF;
END $preflight$;

-- Person resources use current, active memberships/teams through V209's
-- authorization_subject, not actor-provided teams or the teams from a grant.
-- Explicit action denies and required feature intersections remain centralized.
-- Formal visits retain their recorded team/resource authorization. A foreign
-- tenant's fabricated source_import_id neither authorizes nor classifies ours.
CREATE OR REPLACE FUNCTION security.authorization_import(p_permission text,p_import uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM activity.visit_import i WHERE i.id=p_import
 AND i.workspace_id=common.current_workspace_id() AND (
  (i.created_by_user_ref_id=common.current_user_ref_id() AND security.authorization_allows(
   p_permission,i.workspace_id,i.created_by_user_ref_id,ARRAY[]::uuid[],true))
  -- BEGIN V226 own unarchived team resource
  OR (i.created_by_user_ref_id=common.current_user_ref_id()
   AND p_permission IN ('visit.read','visit.download_original')
   AND NOT EXISTS(SELECT 1 FROM activity.visit linked
    WHERE linked.source_import_id=i.id AND linked.workspace_id=i.workspace_id
     AND linked.status IN ('confirmed','archived') AND linked.deleted_at IS NULL)
   AND security.authorization_subject(p_permission,'person',i.created_by_user_ref_id,NULL))
  -- END V226 own unarchived team resource
  OR (p_permission IN ('visit.read','visit.download_original') AND EXISTS(
   SELECT 1 FROM activity.visit v WHERE v.source_import_id=i.id AND v.workspace_id=i.workspace_id
   AND v.status IN ('confirmed','archived') AND v.deleted_at IS NULL
   AND security.authorization_visit(p_permission,v.id)))));
$$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V374','Own unarchived import read authorization through actual person teams');
COMMIT;
