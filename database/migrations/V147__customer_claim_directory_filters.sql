-- Reference-only directory search; no business records or ownership are changed.
CREATE OR REPLACE FUNCTION security.company_customer_directory_search(p_query text, p_limit integer, p_offset integer, p_industry text, p_claim_status text, p_phonetic_names text[])
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH bounds AS (
  SELECT LEAST(GREATEST(p_limit,1),100) AS page_size,GREATEST(p_offset,0)::bigint AS page_start
 ), matched AS MATERIALIZED (
  SELECT c.id,c.name FROM crm.customer c
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND security.authorization_allows('customer.claim_directory',c.workspace_id,c.owner_user_ref_id,ARRAY[c.owner_team_id],false)
   AND (p_industry IS NULL OR c.industry_code=p_industry)
   AND (p_claim_status IS NULL
    OR (p_claim_status='unclaimed' AND o.state='unclaimed')
    OR (p_claim_status='claimed' AND o.state='claimed')
    OR (p_claim_status='mine' AND o.state='claimed' AND o.owner_user_ref_id=common.current_user_ref_id())
    OR (p_claim_status='legacy_review' AND o.state='legacy_review')
    OR (p_claim_status='pending' AND (SELECT r.status FROM crm.customer_claim_request r
     WHERE r.customer_id=c.id AND r.applicant_user_ref_id=common.current_user_ref_id()
     ORDER BY r.requested_at DESC,r.id DESC LIMIT 1)='pending'))
   AND (NULLIF(btrim(p_query),'') IS NULL OR strpos(lower(c.name),lower(p_query))>0
    OR c.name IN (SELECT unnest(p_phonetic_names)))
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
    AND r.applicant_user_ref_id=common.current_user_ref_id() ORDER BY r.requested_at DESC,r.id DESC LIMIT 1)
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
$function$;

-- Only the backend consumes this minimal name projection for pinyin matching.
-- Every request re-reads authorized current names; no customer payload is cached.
CREATE OR REPLACE FUNCTION security.company_customer_directory_search_names(p_industry text)
 RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT DISTINCT c.name FROM crm.customer c
 JOIN crm.customer_ownership o ON o.customer_id=c.id
 WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND security.authorization_allows('customer.claim_directory',c.workspace_id,c.owner_user_ref_id,ARRAY[c.owner_team_id],false) AND (p_industry IS NULL OR c.industry_code=p_industry);
$$;
CREATE OR REPLACE FUNCTION security.company_customer_directory_industries()
 RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('value',industry_code,'label',industry_code)
   ORDER BY industry_code),'[]'::jsonb)
 FROM (SELECT DISTINCT c.industry_code FROM crm.customer c
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND security.authorization_allows('customer.claim_directory',c.workspace_id,c.owner_user_ref_id,ARRAY[c.owner_team_id],false) AND NULLIF(btrim(c.industry_code),'') IS NOT NULL) industries;
$$;

-- Inherit owner, exact EXECUTE grants and grant options from the established
-- directory, removing any deployer's default grants before assigning ownership.
DO $$ DECLARE permission record; function_owner name; target text; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
 WHERE p.oid='security.company_customer_directory_page(text,integer,integer)'::regprocedure;
 FOREACH target IN ARRAY ARRAY[
  'security.company_customer_directory_search(text,integer,integer,text,text,text[])',
  'security.company_customer_directory_search_names(text)',
  'security.company_customer_directory_industries()'
 ] LOOP
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
   WHERE p.oid='security.company_customer_directory_page(text,integer,integer)'::regprocedure
    AND a.privilege_type='EXECUTE'
  LOOP
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %s%s',target,
    CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
    CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
  END LOOP;
 END LOOP;
END $$;
