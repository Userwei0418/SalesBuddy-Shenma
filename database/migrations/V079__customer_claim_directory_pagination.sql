BEGIN;

-- The company directory is a reference-only read model. Count and items share
-- one statement/snapshot; customer history remains behind its existing RLS.
CREATE FUNCTION security.company_customer_directory_page(p_query text,p_limit integer,p_offset integer)
 RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH bounds AS (
  SELECT LEAST(GREATEST(p_limit,1),100) AS page_size,GREATEST(p_offset,0)::bigint AS page_start
 ), matched AS MATERIALIZED (
  SELECT c.id,c.name FROM crm.customer c
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND security.has_active_role(common.current_role_code())
   AND (NULLIF(btrim(p_query),'') IS NULL OR c.name ILIKE '%'||p_query||'%')
 ), page AS (
  SELECT id,name FROM matched ORDER BY name,id
  LIMIT (SELECT page_size FROM bounds) OFFSET (SELECT page_start FROM bounds)
 ), items AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
   'id',c.id::text,'name',c.name,'industry_code',c.industry_code,'level_code',c.level_code,
   'customer_type_code',c.customer_type_code,'team_name',t.name,'owner_name',u.display_name,
   'ownership_state',o.state,'claimed',o.owner_user_ref_id=common.current_user_ref_id(),
   'can_claim',security.can_claim_customer(c.id),
   'claim_status',(SELECT r.status FROM crm.customer_claim_request r WHERE r.customer_id=c.id
    AND r.applicant_user_ref_id=common.current_user_ref_id() ORDER BY r.requested_at DESC LIMIT 1)
  ) ORDER BY page.name,page.id),'[]'::jsonb) AS value
  FROM page JOIN crm.customer c ON c.id=page.id
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  LEFT JOIN platform.team t ON t.id=c.owner_team_id
  LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id
 ), totals AS (SELECT count(*) AS value FROM matched)
 SELECT jsonb_build_object('items',items.value,'total',totals.value,
  'has_more',bounds.page_start+jsonb_array_length(items.value)<totals.value,
  'next_offset',CASE WHEN bounds.page_start+jsonb_array_length(items.value)<totals.value
    THEN bounds.page_start+jsonb_array_length(items.value) ELSE NULL END)
 FROM items CROSS JOIN totals CROSS JOIN bounds;
$$;

-- Inherit the directory's actual owner and effective EXECUTE ACL, including
-- grant options and installations that revoked PUBLIC. Deployer defaults must
-- not silently expose this new SECURITY DEFINER function to extra roles.
DO $$ DECLARE permission record; function_owner name; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
 WHERE p.oid='security.company_customer_directory(text,integer,integer)'::regprocedure;
 FOR permission IN
  SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.company_customer_directory_page(text,integer,integer)'::regprocedure
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.company_customer_directory_page(text,integer,integer) FROM %s CASCADE',
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
 END LOOP;
 EXECUTE format('ALTER FUNCTION security.company_customer_directory_page(text,integer,integer) OWNER TO %I',function_owner);
 FOR permission IN
  SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='security.company_customer_directory(text,integer,integer)'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.company_customer_directory_page(text,integer,integer) TO %s%s',
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
   CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
 END LOOP;
END $$;

-- Existing company/department pickers keep their SETOF jsonb contract and the
-- two-argument customer_claim_pool wrapper continues to return the first page.
CREATE OR REPLACE FUNCTION security.company_customer_directory(p_query text,p_limit integer,p_offset integer DEFAULT 0)
 RETURNS SETOF jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT value FROM jsonb_array_elements(
  security.company_customer_directory_page(p_query,p_limit,p_offset)->'items');
$$;
COMMENT ON FUNCTION security.company_customer_directory_page(text,integer,integer) IS
 '公司客户认领目录分页；总数为搜索条件内全部目录客户，仅返回基础引用，不授予客户历史权限';
INSERT INTO ops.schema_migration(version,description)
 VALUES('V079','客户认领目录分页与同源全量统计');
COMMIT;
