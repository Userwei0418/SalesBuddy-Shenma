-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V210__claim_directory_grants_once.sql.
-- Original SHA-256: 75d72158c3eb3c2fbaa80c47eb9c03b0c5aa6a3a25426fc3f4da364c23138319. See shenma_whole_121_manifest.json.
BEGIN;
-- WEB003: evaluate the existing normalized directory grants once per statement.
-- Equivalent to V209 authorization_allows with p_assigned=false: assigned-only
-- grants cannot match, self/teams retain the exact owner/team row predicates,
-- deny wins. Identity/module/expiry checks remain in authorization_current_grants.
-- claim_directory is not a composite business feature, so no extra feature gate
-- is introduced or removed. CREATE OR REPLACE preserves owner and existing ACL.
CREATE OR REPLACE FUNCTION security.company_customer_directory_search(p_query text, p_limit integer, p_offset integer, p_industry text, p_claim_status text, p_phonetic_names text[])
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH grants AS MATERIALIZED (
  SELECT scope_code,team_ids,effect FROM security.authorization_current_grants()
  WHERE permission_code='customer.claim_directory'
 ), bounds AS (
  SELECT LEAST(GREATEST(p_limit,1),100) AS page_size,GREATEST(p_offset,0)::bigint AS page_start
 ), matched AS MATERIALIZED (
  SELECT c.id,c.name FROM crm.customer c
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny')
   AND EXISTS(SELECT 1 FROM grants g WHERE g.effect='allow' AND (
     g.scope_code='workspace'
     OR (g.scope_code='self' AND c.owner_user_ref_id=common.current_user_ref_id())
     OR (g.scope_code='teams' AND g.team_ids && ARRAY[c.owner_team_id])))
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

INSERT INTO ops.schema_migration(version,description) VALUES('V358','Evaluate claim directory normalized grants once per statement');
COMMIT;
