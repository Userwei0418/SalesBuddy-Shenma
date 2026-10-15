-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V159__bounded_authorization_delegation.sql.
-- Original SHA-256: 52500d358abc3329d6c19c4ae92a4b23d38d184b05700e09f7ce2636f03f78ab. See shenma_whole_121_manifest.json.
BEGIN;
-- Company administrators issue bounded, non-recursive delegations.
CREATE TABLE config.authorization_delegation (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 issuer_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 enabled boolean NOT NULL DEFAULT true,
 expires_at timestamptz,
 recipient_ids uuid[] NOT NULL DEFAULT '{}',
 permissions jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(permissions)='array'),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,user_ref_id)
);
ALTER TABLE config.account_permission_role
 ADD COLUMN delegation_user_ref_id uuid,
 ADD COLUMN delegation_version integer,
 ADD COLUMN approved_role_version integer;
ALTER TABLE config.account_permission_override
 ADD COLUMN delegation_user_ref_id uuid,
 ADD COLUMN delegation_version integer;
REVOKE ALL ON config.authorization_delegation FROM PUBLIC;

CREATE FUNCTION security.company_administrator_identity(p_workspace uuid,p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
 JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
 WHERE u.id=p_user AND u.workspace_id=p_workspace AND u.status='active' AND u.deleted_at IS NULL
 AND w.status='active' AND w.deleted_at IS NULL AND b.role_code='administrator'
 AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to);
$$;

CREATE FUNCTION security.active_authorization_delegation(p_workspace uuid,p_user uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT to_jsonb(d) FROM config.authorization_delegation d JOIN platform.user_ref u
 ON u.id=d.user_ref_id AND u.workspace_id=d.workspace_id
 WHERE d.workspace_id=p_workspace AND d.user_ref_id=p_user AND d.enabled
 AND (d.expires_at IS NULL OR d.expires_at>clock_timestamp()) AND u.status='active' AND u.deleted_at IS NULL
 AND security.company_administrator_identity(p_workspace,d.issuer_user_ref_id);
$$;

CREATE FUNCTION security.delegation_scope_contains(p_permissions jsonb,p_permission text,p_scope text,p_teams uuid[]) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM jsonb_array_elements(p_permissions) g WHERE g->>'permission'=p_permission
  AND (g->>'scope'='workspace' OR (g->>'scope'=p_scope AND
   (p_scope<>'teams' OR (cardinality(p_teams)>0 AND p_teams<@ARRAY(SELECT x::uuid FROM jsonb_array_elements_text(g->'team_ids') x))))));
$$;

CREATE FUNCTION security.delegated_authority_valid(p_workspace uuid,p_recipient uuid,p_issuer uuid,p_version integer) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p_issuer IS NULL OR EXISTS(SELECT 1 FROM
  (SELECT security.active_authorization_delegation(p_workspace,p_issuer) d) current_policy
 WHERE (d->>'version_no')::integer=p_version AND p_recipient::text IN (SELECT jsonb_array_elements_text(d->'recipient_ids'))
 AND NOT EXISTS(SELECT 1 FROM platform.role_binding b WHERE b.workspace_id=p_workspace AND b.user_ref_id=p_recipient
  AND b.role_code='administrator' AND b.valid_to>clock_timestamp())
 AND NOT EXISTS(SELECT 1 FROM config.authorization_delegation other WHERE other.workspace_id=p_workspace AND other.user_ref_id=p_recipient));
$$;

CREATE FUNCTION security.require_delegation_recipient(p_user uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); actor uuid:=common.current_user_ref_id(); d jsonb;
BEGIN
 d:=security.active_authorization_delegation(ws,actor);
 IF d IS NULL OR p_user=actor OR p_user::text NOT IN (SELECT jsonb_array_elements_text(d->'recipient_ids'))
 OR NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE workspace_id=ws AND id=p_user AND deleted_at IS NULL)
 OR EXISTS(SELECT 1 FROM platform.role_binding WHERE workspace_id=ws AND user_ref_id=p_user
     AND role_code='administrator' AND valid_to>clock_timestamp())
 OR EXISTS(SELECT 1 FROM config.authorization_delegation WHERE workspace_id=ws AND user_ref_id=p_user) THEN
  RAISE EXCEPTION '账号不在有效的委派范围内，或属于受保护的管理身份' USING ERRCODE='42501';
 END IF;
 RETURN d;
END $$;

CREATE FUNCTION security.authorization_delegations() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE is_admin boolean:=security.company_administrator_identity(common.current_workspace_id(),common.current_user_ref_id());
BEGIN
 IF NOT security.authorization_has('authorization.read') THEN RAISE insufficient_privilege; END IF;
 RETURN jsonb_build_object('company_administrator',is_admin,'user_id',common.current_user_ref_id(),
  'mine',security.active_authorization_delegation(common.current_workspace_id(),common.current_user_ref_id()),
  'items',CASE WHEN is_admin THEN (SELECT COALESCE(jsonb_agg(to_jsonb(d) ORDER BY updated_at DESC),'[]')
     FROM config.authorization_delegation d WHERE workspace_id=common.current_workspace_id()) ELSE '[]'::jsonb END);
END $$;

CREATE FUNCTION security.save_authorization_delegation(p_user uuid,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); actor uuid:=common.current_user_ref_id(); old jsonb; result jsonb;
 version integer; recipients uuid[]; expiry timestamptz;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||ws::text,0));
 PERFORM set_config('app.authorization_context','',true);
 IF NOT security.company_administrator_identity(ws,actor) OR NOT security.authorization_has('authorization.accounts_manage') THEN
  RAISE EXCEPTION '委派上限由本公司管理员维护' USING ERRCODE='42501'; END IF;
 IF p_user=actor OR NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE id=p_user AND workspace_id=ws AND deleted_at IS NULL)
  OR EXISTS(SELECT 1 FROM platform.role_binding WHERE workspace_id=ws AND user_ref_id=p_user AND role_code='administrator' AND valid_to>clock_timestamp()) THEN RAISE insufficient_privilege; END IF;
 IF length(btrim(COALESCE(p_body->>'reason',''))) NOT BETWEEN 1 AND 500
 OR jsonb_typeof(p_body->'enabled') IS DISTINCT FROM 'boolean'
 OR jsonb_typeof(p_body->'recipient_ids') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION '请填写有效的委派对象和变更原因' USING ERRCODE='22023'; END IF;
 PERFORM security.validate_authorization_grants(p_body->'permissions');
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'permissions') g WHERE g->>'scope'='inherit'
  OR g->>'permission' IN ('authorization.accounts_manage','authorization.roles_manage','account.create','account.password_policy','organization.manage')) THEN
  RAISE EXCEPTION '委派上限须明确范围，且不能转授公司安全管理权限' USING ERRCODE='22023'; END IF;
 recipients:=ARRAY(SELECT x::uuid FROM jsonb_array_elements_text(p_body->'recipient_ids') x);
 IF cardinality(recipients)>2000 OR cardinality(recipients)<>(SELECT count(DISTINCT x) FROM unnest(recipients) x)
 OR p_user=ANY(recipients) OR actor=ANY(recipients)
 OR EXISTS(SELECT 1 FROM unnest(recipients) AS recipient(id) WHERE NOT EXISTS(SELECT 1 FROM platform.user_ref u
  WHERE u.id=recipient.id AND u.workspace_id=ws AND u.deleted_at IS NULL)
  OR EXISTS(SELECT 1 FROM platform.role_binding b WHERE b.user_ref_id=recipient.id AND b.workspace_id=ws
    AND b.role_code='administrator' AND b.valid_to>clock_timestamp())
  OR EXISTS(SELECT 1 FROM config.authorization_delegation d WHERE d.user_ref_id=recipient.id AND d.workspace_id=ws)) THEN
  RAISE EXCEPTION '请选择本公司普通成员，不能包含管理身份或重复账号' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_body->'permissions') g
  WHERE NOT security.tenant_permission_enabled(ws,g->>'permission')) THEN
  RAISE EXCEPTION '委派不能包含本公司未开通的功能' USING ERRCODE='22023'; END IF;
 expiry:=NULLIF(p_body->>'expires_at','')::timestamptz;
 SELECT to_jsonb(d),d.version_no INTO old,version FROM config.authorization_delegation d WHERE workspace_id=ws AND user_ref_id=p_user FOR UPDATE;
 IF COALESCE(version,0) IS DISTINCT FROM (p_body->>'version_no')::integer THEN
  RAISE EXCEPTION '委派设置已变化，请刷新' USING ERRCODE='40001'; END IF;
 INSERT INTO config.authorization_delegation(workspace_id,user_ref_id,issuer_user_ref_id,enabled,expires_at,recipient_ids,permissions)
 VALUES(ws,p_user,actor,(p_body->>'enabled')::boolean,expiry,recipients,p_body->'permissions')
 ON CONFLICT(workspace_id,user_ref_id) DO UPDATE SET issuer_user_ref_id=actor,version_no=authorization_delegation.version_no+1,
 enabled=EXCLUDED.enabled,expires_at=EXCLUDED.expires_at,recipient_ids=EXCLUDED.recipient_ids,
 permissions=EXCLUDED.permissions,updated_at=clock_timestamp()
 RETURNING to_jsonb(authorization_delegation) INTO result;
 INSERT INTO config.authorization_audit(workspace_id,actor_user_ref_id,subject_type,subject_id,before_snapshot,after_snapshot,reason)
 VALUES(ws,actor,'account',p_user,jsonb_build_object('delegation',old),jsonb_build_object('delegation',result),btrim(p_body->>'reason'));
 PERFORM set_config('app.authorization_context','',true);
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION security.authorization_grants_for(p_workspace uuid, p_user uuid)
 RETURNS TABLE(permission_code text, scope_code text, team_ids uuid[], source_code text, source_id text, effect text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH authority AS MATERIALIZED (
  SELECT security.company_administrator_identity(p_workspace,p_user) administrator,
   security.active_authorization_delegation(p_workspace,p_user) delegation
 ), available_permissions AS MATERIALIZED (
  SELECT p.permission_code FROM control.permission_module p LEFT JOIN control.tenant_module t
   ON t.workspace_id=p_workspace AND t.module_code=p.module_code
  GROUP BY p.permission_code HAVING bool_and(t.enabled IS TRUE AND (t.expires_at IS NULL OR t.expires_at>clock_timestamp()))
 ), person AS (
  SELECT u.id FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
  WHERE u.id=p_user AND u.workspace_id=p_workspace AND u.status='active' AND u.deleted_at IS NULL
   AND w.status='active' AND w.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM platform.role_binding identity_role WHERE identity_role.workspace_id=p_workspace
    AND identity_role.user_ref_id=p_user AND clock_timestamp()>=identity_role.valid_from AND clock_timestamp()<identity_role.valid_to
    AND (identity_role.role_code IN ('manager','operations','administrator') OR EXISTS(
     SELECT 1 FROM security.account_team_scope(p_workspace,p_user,identity_role.role_code))))
 ), appointments AS (
  SELECT DISTINCT b.role_code,b.data_scope_code,
   ARRAY(SELECT s.team_id::uuid FROM security.account_team_scope(p_workspace,p_user,b.role_code) s) teams
  FROM platform.role_binding b JOIN person p ON p.id=b.user_ref_id
  WHERE b.workspace_id=p_workspace AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
   AND (b.role_code IN ('manager','operations','administrator') OR EXISTS(
     SELECT 1 FROM security.account_team_scope(p_workspace,p_user,b.role_code)))
 ), sources AS (
  SELECT r.id,r.version_no,r.builtin_role_code source_code,
   CASE a.data_scope_code WHEN 'team' THEN 'teams' ELSE a.data_scope_code END scope_code,a.teams
  FROM appointments a JOIN config.permission_role r ON r.workspace_id=p_workspace
   AND r.builtin_role_code=a.role_code AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  UNION ALL
  SELECT r.id,r.version_no,'assigned_role',a.scope_code,a.team_ids
  FROM config.account_permission_role a JOIN person p ON p.id=a.user_ref_id
  JOIN config.permission_role r ON r.id=a.role_id AND r.workspace_id=a.workspace_id AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  WHERE a.workspace_id=p_workspace AND (a.expires_at IS NULL OR a.expires_at>clock_timestamp())
   AND security.delegated_authority_valid(p_workspace,p_user,a.delegation_user_ref_id,a.delegation_version)
   AND (a.delegation_user_ref_id IS NULL OR a.approved_role_version=r.version_no)
 ), combined AS (
  SELECT g.permission_code,CASE g.scope_code WHEN 'inherit' THEN s.scope_code ELSE g.scope_code END scope_code,
   CASE WHEN g.scope_code='teams' THEN g.team_ids WHEN g.scope_code='inherit' AND s.scope_code='teams' THEN s.teams ELSE '{}'::uuid[] END teams,
   s.source_code,s.id::text source_id,'allow'::text effect
  FROM sources s JOIN config.permission_role_grant g ON g.role_id=s.id AND g.workspace_id=p_workspace
  WHERE NOT (s.source_code IN ('fde','fde_lead') AND s.version_no=1 AND g.permission_code IN
   ('visit.create','visit.supplement','visit.upload','visit.retry_import','visit.download_original','visit.structure','visit.quality_review'))
   OR security.authorization_legacy_fde_visit(p_workspace,p_user,s.source_code)
  UNION ALL
  SELECT o.permission_code,o.scope_code,o.team_ids,'account_override',p_user::text,o.effect
  FROM config.account_permission_override o JOIN person p ON p.id=o.user_ref_id WHERE o.workspace_id=p_workspace AND (o.expires_at IS NULL OR o.expires_at>clock_timestamp()) AND security.delegated_authority_valid(p_workspace,p_user,o.delegation_user_ref_id,o.delegation_version)
 ), valid AS (
  SELECT c.*,ARRAY(SELECT t.id FROM platform.team t WHERE t.id=ANY(c.teams) AND t.workspace_id=p_workspace
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to ORDER BY t.id) active_teams
  FROM combined c JOIN config.permission_catalog d ON d.code=c.permission_code
  WHERE c.effect='deny' OR c.scope_code=ANY(d.scopes)
 )
 SELECT v.permission_code,v.scope_code,v.active_teams,v.source_code,v.source_id,v.effect FROM valid v CROSS JOIN authority a
 WHERE (v.scope_code<>'teams' OR cardinality(v.active_teams)>0 OR v.effect='deny')
 AND (v.effect='deny' OR (v.permission_code IN (SELECT permission_code FROM available_permissions)
  AND (a.administrator OR v.permission_code NOT IN ('authorization.roles_manage','account.create','account.password_policy','organization.manage'))
  AND (a.administrator OR a.delegation IS NOT NULL OR v.permission_code NOT IN ('authorization.accounts_manage','account.update','account.reset_password','account.unlock'))));
$function$
;

CREATE FUNCTION security.authorization_entry(p_entry jsonb,p_role boolean) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('scope',COALESCE(p_entry->>'scope',p_entry->>'scope_code','inherit'),
  'team_ids',COALESCE((SELECT jsonb_agg(x ORDER BY x) FROM jsonb_array_elements_text(p_entry->'team_ids') x),'[]'::jsonb),
  'expires_at',NULLIF(p_entry->>'expires_at','')::timestamptz)
 || CASE WHEN p_role THEN jsonb_build_object('role_id',p_entry->'role_id') ELSE
  jsonb_build_object('permission',COALESCE(p_entry->>'permission',p_entry->>'permission_code'),'effect',p_entry->>'effect') END;
$$;

CREATE FUNCTION security.require_delegation_grant(p_policy jsonb,p_permission text,p_scope text,p_teams uuid[],p_expiry timestamptz) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.delegation_scope_contains(p_policy->'permissions',p_permission,p_scope,p_teams)
 OR NOT security.tenant_permission_enabled(common.current_workspace_id(),p_permission)
 OR ((p_policy->>'expires_at') IS NOT NULL AND (p_expiry IS NULL OR p_expiry>(p_policy->>'expires_at')::timestamptz)) THEN
  RAISE EXCEPTION '授权超出委派的功能、数据范围或有效期' USING ERRCODE='42501'; END IF;
END $$;

-- Potential authority includes future/expired/disabled templates, independent of module caps.
CREATE FUNCTION security.managed_account_target_allowed(p_user uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); d jsonb;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref WHERE workspace_id=ws AND id=p_user AND deleted_at IS NULL
   AND COALESCE(attributes->>'platform_managed','false')<>'true') THEN RETURN false; END IF;
 IF security.company_administrator_identity(ws,common.current_user_ref_id()) THEN RETURN true; END IF;
 d:=security.active_authorization_delegation(ws,common.current_user_ref_id());
 IF d IS NULL OR p_user=common.current_user_ref_id() OR NOT security.delegated_authority_valid(ws,p_user,common.current_user_ref_id(),(d->>'version_no')::integer) THEN RETURN false; END IF;
 RETURN NOT EXISTS(WITH sources AS (
  SELECT r.id,CASE b.data_scope_code WHEN 'team' THEN 'teams' ELSE b.data_scope_code END scope,
   CASE WHEN b.data_scope_code='team' THEN ARRAY[b.team_id] ELSE '{}'::uuid[] END teams
  FROM platform.role_binding b JOIN config.permission_role r ON r.workspace_id=b.workspace_id AND r.builtin_role_code=b.role_code
  WHERE b.workspace_id=ws AND b.user_ref_id=p_user AND b.valid_to>clock_timestamp()
  UNION ALL SELECT a.role_id,a.scope_code,a.team_ids FROM config.account_permission_role a
  WHERE a.workspace_id=ws AND a.user_ref_id=p_user
 ), all_grants AS (
  SELECT g.permission_code,CASE g.scope_code WHEN 'inherit' THEN s.scope ELSE g.scope_code END scope,
    CASE WHEN g.scope_code='inherit' THEN s.teams ELSE g.team_ids END teams
  FROM sources s JOIN config.permission_role_grant g ON g.role_id=s.id AND g.workspace_id=ws
  UNION ALL SELECT o.permission_code,o.scope_code,o.team_ids FROM config.account_permission_override o
   WHERE o.workspace_id=ws AND o.user_ref_id=p_user AND o.effect='allow'
 ) SELECT 1 FROM all_grants g
 WHERE NOT security.delegation_scope_contains(d->'permissions',g.permission_code,g.scope,g.teams));
END $$;
CREATE FUNCTION security.require_managed_account_target(p_user uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||common.current_workspace_id()::text,0));
 PERFORM set_config('app.authorization_context','',true);
 IF NOT security.managed_account_target_allowed(p_user) THEN
  RAISE EXCEPTION '此账号不在委派范围内或具有更高权限，请由公司管理员维护' USING ERRCODE='42501'; END IF;
END $$;
CREATE FUNCTION security.organization_management() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE admin boolean:=security.company_administrator_identity(common.current_workspace_id(),common.current_user_ref_id()); targets jsonb;
BEGIN
 IF NOT security.authorization_has('organization.read') THEN RAISE insufficient_privilege; END IF;
 SELECT COALESCE(jsonb_agg(id),'[]') INTO targets FROM platform.user_ref u
 WHERE u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL
 AND COALESCE(u.attributes->>'platform_managed','false')<>'true'
 AND (admin OR security.managed_account_target_allowed(u.id));
 RETURN jsonb_build_object('company_administrator',admin,'target_ids',targets,
  'actions',(SELECT jsonb_object_agg(p,security.authorization_has(p)) FROM unnest(ARRAY[
    'account.create','account.update','account.reset_password','account.unlock','account.password_policy','organization.manage']) p));
END $$;
ALTER FUNCTION security.save_account_authorization(uuid,jsonb) RENAME TO save_account_authorization_v156;
CREATE FUNCTION security.save_account_authorization(p_user uuid,p_body jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); actor uuid:=common.current_user_ref_id(); d jsonb;
 old_roles jsonb; old_overrides jsonb; old jsonb; item jsonb; incoming jsonb; g record; result jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||ws::text,0));
 PERFORM set_config('app.authorization_context','',true);
 IF NOT security.authorization_has('authorization.accounts_manage') THEN RAISE insufficient_privilege; END IF;
 IF security.company_administrator_identity(ws,actor) THEN
  RETURN security.save_account_authorization_v156(p_user,p_body);
 END IF;
 d:=security.require_delegation_recipient(p_user);
 SELECT COALESCE(jsonb_agg(to_jsonb(a)),'[]') INTO old_roles FROM config.account_permission_role a WHERE workspace_id=ws AND user_ref_id=p_user;
 SELECT COALESCE(jsonb_agg(to_jsonb(a)),'[]') INTO old_overrides FROM config.account_permission_override a WHERE workspace_id=ws AND user_ref_id=p_user;
 -- Rows issued by the administrator or another editor are immutable, including denies.
 FOR old IN SELECT * FROM jsonb_array_elements(old_roles) LOOP
  IF (old->>'delegation_user_ref_id') IS DISTINCT FROM actor::text AND NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_body->'roles') i WHERE security.authorization_entry(i,true)=security.authorization_entry(old,true)) THEN
   RAISE EXCEPTION '不能修改或移除其他管理者的角色授权' USING ERRCODE='42501'; END IF;
 END LOOP;
 FOR old IN SELECT * FROM jsonb_array_elements(old_overrides) LOOP
  IF (old->>'delegation_user_ref_id') IS DISTINCT FROM actor::text AND NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(p_body->'overrides') i WHERE security.authorization_entry(i,false)=security.authorization_entry(old,false)) THEN
   RAISE EXCEPTION '不能修改或移除其他管理者的账号设置' USING ERRCODE='42501'; END IF;
 END LOOP;
 FOR item IN SELECT * FROM jsonb_array_elements(p_body->'roles') LOOP
  SELECT o INTO old FROM jsonb_array_elements(old_roles) o WHERE o->>'role_id'=item->>'role_id';
  IF old IS NOT NULL AND (old->>'delegation_user_ref_id') IS DISTINCT FROM actor::text THEN CONTINUE; END IF;
  FOR g IN SELECT permission_code,CASE scope_code WHEN 'inherit' THEN item->>'scope' ELSE scope_code END scope,
   CASE WHEN scope_code='inherit' THEN ARRAY(SELECT x::uuid FROM jsonb_array_elements_text(item->'team_ids') x) ELSE team_ids END teams
   FROM config.permission_role_grant WHERE workspace_id=ws AND role_id=(item->>'role_id')::uuid LOOP
   PERFORM security.require_delegation_grant(d,g.permission_code,g.scope,g.teams,NULLIF(item->>'expires_at','')::timestamptz);
  END LOOP;
 END LOOP;
 FOR item IN SELECT * FROM jsonb_array_elements(p_body->'overrides') LOOP
  SELECT o INTO old FROM jsonb_array_elements(old_overrides) o WHERE o->>'permission_code'=item->>'permission';
  IF old IS NOT NULL AND (old->>'delegation_user_ref_id') IS DISTINCT FROM actor::text THEN CONTINUE; END IF;
  -- An explicit deny applies to the whole function, so it requires a company-wide ceiling.
  PERFORM security.require_delegation_grant(d,item->>'permission',CASE WHEN item->>'effect'='deny' THEN 'workspace' ELSE item->>'scope' END,
   ARRAY(SELECT x::uuid FROM jsonb_array_elements_text(item->'team_ids') x),NULLIF(item->>'expires_at','')::timestamptz);
 END LOOP;
 result:=security.save_account_authorization_v156(p_user,p_body);
 UPDATE config.account_permission_role SET delegation_user_ref_id=actor,delegation_version=(d->>'version_no')::integer,
  approved_role_version=(SELECT version_no FROM config.permission_role r WHERE r.id=role_id AND r.workspace_id=ws)
  WHERE workspace_id=ws AND user_ref_id=p_user;
 UPDATE config.account_permission_override SET delegation_user_ref_id=actor,delegation_version=(d->>'version_no')::integer
  WHERE workspace_id=ws AND user_ref_id=p_user;
 FOR old IN SELECT * FROM jsonb_array_elements(old_roles) LOOP
  IF (old->>'delegation_user_ref_id') IS DISTINCT FROM actor::text THEN
   UPDATE config.account_permission_role SET delegation_user_ref_id=(old->>'delegation_user_ref_id')::uuid,
    delegation_version=(old->>'delegation_version')::integer,approved_role_version=(old->>'approved_role_version')::integer
    WHERE workspace_id=ws AND user_ref_id=p_user AND role_id=(old->>'role_id')::uuid;
  END IF;
 END LOOP;
 FOR old IN SELECT * FROM jsonb_array_elements(old_overrides) LOOP
  IF (old->>'delegation_user_ref_id') IS DISTINCT FROM actor::text THEN
   UPDATE config.account_permission_override SET delegation_user_ref_id=(old->>'delegation_user_ref_id')::uuid,
    delegation_version=(old->>'delegation_version')::integer WHERE workspace_id=ws AND user_ref_id=p_user AND permission_code=old->>'permission_code';
  END IF;
 END LOOP;
 -- Audit the provenance in addition to the original before/after business configuration.
 result:=result||jsonb_build_object('delegation_user_ref_id',actor,'delegation_version',d->'version_no');
 INSERT INTO config.authorization_audit(workspace_id,actor_user_ref_id,subject_type,subject_id,before_snapshot,after_snapshot,reason)
 VALUES(ws,actor,'account',p_user,jsonb_build_object('roles',old_roles,'overrides',old_overrides),result,'委派授权来源：'||btrim(p_body->>'reason'));
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION security.revoke_managed_account_sessions(p_user uuid)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 PERFORM security.require_managed_account_target(p_user);
 IF (security.authorization_has('account.update') OR security.authorization_has('account.reset_password')) IS NOT TRUE OR NOT EXISTS(
  SELECT 1 FROM platform.user_ref u WHERE u.id=p_user
  AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL
  AND COALESCE(u.attributes->>'platform_managed','false')<>'true'
 ) THEN RAISE EXCEPTION '无权管理此账号会话' USING ERRCODE='42501'; END IF;
 IF NOT security.authorization_has('authorization.accounts_manage') AND EXISTS(
  SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=p_user
  AND r.workspace_id=common.current_workspace_id() AND r.role_code IN ('operations','administrator')
  AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
 ) THEN RAISE EXCEPTION '管理账号会话由系统管理员管理' USING ERRCODE='42501'; END IF;
 UPDATE platform.auth_session SET status='revoked',revoked_at=clock_timestamp()
 WHERE user_ref_id=p_user AND workspace_id=common.current_workspace_id() AND status='active';
END $function$
;
CREATE OR REPLACE FUNCTION security.set_account_password(p_user uuid, p_hash text, p_must_change boolean)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 PERFORM security.require_managed_account_target(p_user);
 IF security.authorization_password_write(p_user) IS NOT TRUE OR NOT EXISTS(SELECT 1 FROM platform.user_ref u
 WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL) THEN
 RAISE EXCEPTION '无权重置账号口令' USING ERRCODE='42501'; END IF;
 IF NOT security.authorization_has('authorization.accounts_manage') AND EXISTS(SELECT 1 FROM platform.role_binding r
 WHERE r.user_ref_id=p_user AND r.workspace_id=common.current_workspace_id() AND r.role_code IN ('operations','administrator')
 AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to) THEN
 RAISE EXCEPTION '管理账号口令由系统管理员重置' USING ERRCODE='42501'; END IF;
 IF p_hash NOT LIKE 'scrypt$32768$8$1$%' THEN RAISE EXCEPTION 'invalid password encoding' USING ERRCODE='22023'; END IF;
 INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password)
 VALUES(p_user,common.current_workspace_id(),p_hash,p_must_change)
 ON CONFLICT(user_ref_id) DO UPDATE SET password_hash=EXCLUDED.password_hash,must_change_password=EXCLUDED.must_change_password,password_changed_at=clock_timestamp();
 UPDATE platform.auth_session SET status='revoked',revoked_at=clock_timestamp() WHERE user_ref_id=p_user AND workspace_id=common.current_workspace_id() AND status='active';
 PERFORM security.unlock_account_after_password(p_user,'重置密码时解除账号限制');
END $function$
;
CREATE OR REPLACE FUNCTION security.unlock_account_after_password(p_user uuid, p_reason text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE throttle_key text; target_name text; prior jsonb; after_value jsonb;
BEGIN
 PERFORM security.require_managed_account_target(p_user);
 IF (security.authorization_has('account.unlock') OR security.authorization_password_write(p_user)) IS NOT TRUE THEN
  RAISE EXCEPTION '需要账号管理权限' USING ERRCODE='42501';
 END IF;
 IF p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 1 AND 500 THEN
  RAISE EXCEPTION '请填写解除原因（1–500 字）' USING ERRCODE='22023';
 END IF;
 SELECT encode(public.digest('account:'||w.external_workspace_id||':'||upper(btrim(u.account_code)),'sha256'),'hex'),u.display_name
 INTO throttle_key,target_name FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
 WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION '账号不存在' USING ERRCODE='P0002'; END IF;
 IF throttle_key IS NULL THEN RAISE EXCEPTION '账号尚未配置登录标识' USING ERRCODE='22023'; END IF;
 IF NOT security.authorization_has('authorization.accounts_manage') AND EXISTS(
  SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=p_user AND r.workspace_id=common.current_workspace_id()
  AND r.role_code IN ('operations','administrator') AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
 ) THEN RAISE EXCEPTION '运营及系统管理员账号由系统管理员管理' USING ERRCODE='42501'; END IF;
 -- Lock the existing row so the before-state belongs to this exact deletion.
 PERFORM 1 FROM security.login_throttle WHERE key_hash=throttle_key FOR UPDATE;
 SELECT to_jsonb(s) INTO prior FROM security.account_login_status(p_user) s;
 DELETE FROM security.login_throttle WHERE key_hash=throttle_key;
 SELECT to_jsonb(s)||jsonb_build_object('reason',btrim(p_reason)) INTO after_value FROM security.account_login_status(p_user) s;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,object_type,object_id,object_label,
 request_id,client_ip,user_agent,before_snapshot,after_snapshot,changed_fields)
 VALUES(common.current_workspace_id(),common.current_user_ref_id(),common.current_role_code(),
 'account.login_unlock','platform','user_ref',p_user,target_name,
 NULLIF(current_setting('app.request_id',true),'')::uuid,NULLIF(current_setting('app.client_ip',true),'')::inet,
 left(current_setting('app.user_agent',true),500),prior,after_value,
 ARRAY['login_locked','login_retry_at','login_attempts']);
END $function$
;

CREATE FUNCTION security.guard_native_account(p_user uuid,p_action text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||common.current_workspace_id()::text,0));
 PERFORM set_config('app.authorization_context','',true);
 IF p_action NOT IN ('account.create','account.update','organization.manage') OR NOT security.authorization_has(p_action) THEN RAISE insufficient_privilege; END IF;
 IF p_action<>'account.update' THEN
  IF NOT security.company_administrator_identity(common.current_workspace_id(),common.current_user_ref_id()) THEN
   RAISE EXCEPTION '公司任职、部门和开户由公司管理员维护' USING ERRCODE='42501'; END IF;
 ELSE PERFORM security.require_managed_account_target(p_user);
 END IF;
END $$;
CREATE FUNCTION security.guard_native_identity_mutation() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE target uuid; action text;
BEGIN
 -- The actual table owner provisions schemas/tenants; an empty app context is never a bypass.
 IF pg_has_role(current_user,(SELECT relowner FROM pg_class WHERE oid=TG_RELID),'USAGE') THEN
  IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
 END IF;
 IF TG_TABLE_NAME='user_ref' THEN
  IF TG_OP='INSERT' THEN target:=NEW.id; action:='account.create';
  ELSIF TG_OP='DELETE' THEN target:=OLD.id; action:='organization.manage';
  ELSE
   IF (to_jsonb(NEW)-ARRAY['updated_at','version_no','last_seen_at'])=(to_jsonb(OLD)-ARRAY['updated_at','version_no','last_seen_at']) THEN RETURN NEW; END IF;
   target:=OLD.id; action:='account.update';
   IF NEW.id<>OLD.id OR NEW.workspace_id<>OLD.workspace_id OR NEW.external_user_id<>OLD.external_user_id OR
    NEW.attributes IS DISTINCT FROM OLD.attributes THEN action:='organization.manage'; END IF;
  END IF;
 ELSE target:=NULL; action:='organization.manage'; END IF;
 PERFORM security.guard_native_account(target,action);
 IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
CREATE TRIGGER delegated_user_guard BEFORE INSERT OR UPDATE OR DELETE ON platform.user_ref
 FOR EACH ROW EXECUTE FUNCTION security.guard_native_identity_mutation();
CREATE TRIGGER delegated_binding_guard BEFORE INSERT OR UPDATE OR DELETE ON platform.role_binding
 FOR EACH ROW EXECUTE FUNCTION security.guard_native_identity_mutation();
CREATE TRIGGER delegated_membership_guard BEFORE INSERT OR UPDATE OR DELETE ON platform.team_membership
 FOR EACH ROW EXECUTE FUNCTION security.guard_native_identity_mutation();
CREATE TRIGGER delegated_team_guard BEFORE INSERT OR UPDATE OR DELETE ON platform.team
 FOR EACH ROW EXECUTE FUNCTION security.guard_native_identity_mutation();
REVOKE ALL ON FUNCTION security.active_authorization_delegation(uuid,uuid),security.authorization_delegations(),security.authorization_entry(jsonb,boolean),security.company_administrator_identity(uuid,uuid),security.delegated_authority_valid(uuid,uuid,uuid,integer),security.delegation_scope_contains(jsonb,text,text,uuid[]),security.guard_native_account(uuid,text),security.guard_native_identity_mutation(),security.require_delegation_grant(jsonb,text,text,uuid[],timestamptz),security.require_delegation_recipient(uuid),security.require_managed_account_target(uuid),security.save_account_authorization_v156(uuid,jsonb),security.save_authorization_delegation(uuid,jsonb) FROM PUBLIC,salegent_feishu_worker;

REVOKE ALL ON FUNCTION security.save_account_authorization(uuid,jsonb),security.managed_account_target_allowed(uuid),security.organization_management() FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_internal_v158;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_internal_v158();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('REVOKE ALL ON config.authorization_delegation FROM %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.managed_account_target_allowed(uuid) FROM %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.organization_management() TO %I',r.rolname);
  EXECUTE format('REVOKE ALL ON FUNCTION security.active_authorization_delegation(uuid,uuid),security.authorization_entry(jsonb,boolean),security.company_administrator_identity(uuid,uuid),security.delegated_authority_valid(uuid,uuid,uuid,integer),security.delegation_scope_contains(jsonb,text,text,uuid[]),security.guard_native_identity_mutation(),security.require_delegation_grant(jsonb,text,text,uuid[],timestamptz),security.require_delegation_recipient(uuid),security.require_managed_account_target(uuid),security.save_account_authorization_v156(uuid,jsonb),security.reconcile_runtime_grants_internal_v158() FROM %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_delegations(),security.guard_native_account(uuid,text),security.save_authorization_delegation(uuid,jsonb) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_internal_v158() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V307','Bounded company authorization delegation and account takeover guards');
COMMIT;
