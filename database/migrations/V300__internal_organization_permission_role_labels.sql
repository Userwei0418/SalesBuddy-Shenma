-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V152__organization_permission_role_labels.sql.
-- Original SHA-256: 7477dce40ef6c9c550d2906b0d210c5efa85446152739dd43aa194f540e3f1d7. See shenma_whole_121_manifest.json.
-- Organization readers see assigned role names and scopes, never permission
-- grants or account overrides. Adding a label does not create an appointment.
CREATE FUNCTION security.organization_permission_roles() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); result jsonb;
BEGIN
 IF NOT security.authorization_has('organization.read') THEN
  RAISE EXCEPTION 'organization read permission required' USING ERRCODE='42501';
 END IF;
 SELECT jsonb_build_object(
  'roles',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',r.id::text,'name',r.name) ORDER BY r.name,r.id)
    FROM config.permission_role r WHERE r.workspace_id=ws AND r.status='active'
     AND r.builtin_role_code IS NULL),'[]'::jsonb),
  'assignments',COALESCE((SELECT jsonb_agg(jsonb_build_object('user_ref_id',a.user_ref_id::text,
    'id',r.id::text,'name',r.name,'scope',a.scope_code) ORDER BY a.user_ref_id,r.name,r.id)
    FROM config.account_permission_role a
    JOIN config.permission_role r ON r.id=a.role_id AND r.workspace_id=a.workspace_id
    JOIN platform.user_ref u ON u.id=a.user_ref_id AND u.workspace_id=a.workspace_id
    WHERE a.workspace_id=ws AND r.status='active' AND r.builtin_role_code IS NULL AND u.deleted_at IS NULL),'[]'::jsonb)
 ) INTO result;
 RETURN result;
END $$;

-- Preserve the existing authorization-directory owner and exact runtime ACLs.
DO $$ DECLARE permission record; function_owner name;
 target text:='security.organization_permission_roles()';
BEGIN
 SELECT r.rolname INTO STRICT function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
 WHERE p.oid='security.authorization_directory()'::regprocedure;
 FOR permission IN
  SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=target::regprocedure
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',target,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
 END LOOP;
 EXECUTE format('ALTER FUNCTION %s OWNER TO %I',target,function_owner);
 FOR permission IN
  SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.authorization_directory()'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %s%s',target,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
   CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
 END LOOP;
END $$;
