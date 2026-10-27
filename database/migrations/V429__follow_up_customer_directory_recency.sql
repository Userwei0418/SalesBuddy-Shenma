BEGIN;

-- V429 keeps directory visibility distinct from customer.read. The API joins
-- these narrow candidates to activity.visit under its own RLS, then requests
-- the page projection. No SECURITY DEFINER function reads follow-up records.
CREATE FUNCTION security.company_customer_directory_candidates(
 p_query text,p_industry text,p_claim_status text,p_phonetic_names text[],p_business_identity text)
RETURNS TABLE(id uuid,name text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $function$
 WITH grants AS MATERIALIZED (
  SELECT scope_code,team_ids,effect FROM security.authorization_current_grants()
  WHERE permission_code='customer.claim_directory'
 ), matched AS MATERIALIZED (
  SELECT c.id,c.name FROM crm.customer c
  JOIN crm.customer_ownership o ON o.customer_id=c.id
  WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny')
   AND EXISTS(SELECT 1 FROM grants g WHERE g.effect='allow' AND (
     g.scope_code='workspace'
     OR (g.scope_code='self' AND c.owner_user_ref_id=common.current_user_ref_id())
     OR (g.scope_code='teams' AND g.team_ids && ARRAY[c.owner_team_id])))
   AND (p_business_identity='all' OR p_business_identity=ANY(c.business_identities)
     OR (p_business_identity='customer_partner' AND c.business_identities=ARRAY['customer','partner']))
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

 ) SELECT matched.id,matched.name FROM matched;
$function$;

-- The caller supplies only the bounded, sorted page. Recheck the independent
-- directory grant here: IDs are selectors, never authority to read a customer.
CREATE FUNCTION security.company_customer_directory_page_items(p_ids uuid[])
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $function$
BEGIN
 IF p_ids IS NULL OR cardinality(p_ids)>100 OR array_position(p_ids,NULL) IS NOT NULL THEN
  RAISE EXCEPTION 'INVALID_CUSTOMER_DIRECTORY_PAGE' USING ERRCODE='22023';
 END IF;
 RETURN (
  WITH requested AS MATERIALIZED (
   SELECT requested.id,min(requested.ordinality) AS ordinal
   FROM unnest(p_ids) WITH ORDINALITY requested(id,ordinality)
   GROUP BY requested.id
  ), grants AS MATERIALIZED (
   SELECT scope_code,team_ids,effect FROM security.authorization_current_grants()
   WHERE permission_code='customer.claim_directory'
  ), page AS MATERIALIZED (
   SELECT c.*,requested.ordinal FROM requested
   JOIN crm.customer c ON c.id=requested.id
   JOIN crm.customer_ownership o ON o.customer_id=c.id
   WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
    AND NOT EXISTS(SELECT 1 FROM grants WHERE effect='deny')
    AND EXISTS(SELECT 1 FROM grants g WHERE g.effect='allow' AND (
      g.scope_code='workspace'
      OR (g.scope_code='self' AND c.owner_user_ref_id=common.current_user_ref_id())
      OR (g.scope_code='teams' AND g.team_ids && ARRAY[c.owner_team_id])))
  )
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
   'id',c.id::text,'name',c.name,'industry_code',c.industry_code,'level_code',c.level_code,
   'business_identities',c.business_identities,'partner_profile_id',c.partner_profile_id,
   'primary_partner_id',c.primary_partner_id,'customer_type_code',c.customer_type_code,'team_name',t.name,'owner_name',u.display_name,
   'ownership_state',o.state,'claimed',o.owner_user_ref_id=common.current_user_ref_id(),
   'can_claim',security.can_claim_customer(c.id),
   'can_read_customer',security.authorization_customer('customer.read',c.id),
   'claim_status',(SELECT r.status FROM crm.customer_claim_request r WHERE r.customer_id=c.id
    AND r.applicant_user_ref_id=common.current_user_ref_id() ORDER BY r.requested_at DESC,r.id DESC LIMIT 1)
  ) ORDER BY c.ordinal),'[]'::jsonb)
  FROM page c JOIN crm.customer_ownership o ON o.customer_id=c.id
  LEFT JOIN platform.team t ON t.id=c.owner_team_id
  LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id
 );
END;
$function$;

REVOKE ALL ON FUNCTION security.company_customer_directory_candidates(text,text,text,text[],text),
 security.company_customer_directory_page_items(uuid[]) FROM PUBLIC,salegent_feishu_worker;
-- Runtime EXECUTE is granted by the exact reviewed service permission manifest.
-- Existing search signatures, RLS policies, and business grants stay unchanged.
INSERT INTO ops.schema_migration(version,description)
 VALUES('V429','Expose narrow authorized customer candidates and bounded directory page projection for follow-up recency');
COMMIT;
