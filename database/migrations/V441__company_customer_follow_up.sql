BEGIN;
-- A minimal recording directory, independent of customer/opportunity module RLS.
CREATE FUNCTION security.company_customer_follow_up_actor() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.customer_claim_visit_actor();
$$;

CREATE FUNCTION security.company_customer_follow_up_reference(p_customer uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('customer_reference',jsonb_build_object('id',c.id,'name',c.name),'can_follow_up',true)
 FROM crm.customer c WHERE c.id=p_customer AND c.workspace_id=common.current_workspace_id()
  AND c.deleted_at IS NULL AND security.company_customer_follow_up_actor();
$$;

CREATE FUNCTION security.company_customer_follow_up_options(p_query text,p_limit integer,p_offset integer,p_order text)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH own_recent AS MATERIALIZED (
  SELECT v.customer_id,max(COALESCE(v.recorded_on,(v.interaction_at AT TIME ZONE 'Asia/Shanghai')::date)) recent
  FROM activity.visit v WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
   AND v.status IN ('confirmed','archived') AND v.recorder_user_ref_id=common.current_user_ref_id()
   AND v.created_by_user_ref_id=common.current_user_ref_id() GROUP BY v.customer_id
 ), candidates AS MATERIALIZED (
  SELECT c.id,c.name,r.recent FROM crm.customer c LEFT JOIN own_recent r ON r.customer_id=c.id
  WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND security.company_customer_follow_up_actor()
   AND (COALESCE(p_query,'')='' OR strpos(lower(c.name),lower(p_query))>0)
 ), page AS (
  SELECT * FROM candidates ORDER BY CASE WHEN p_order='recent_follow_up' THEN recent END DESC NULLS LAST,name,id
  LIMIT least(greatest(p_limit,1),100) OFFSET greatest(p_offset,0)
 ) SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',id,'name',name)
  ORDER BY CASE WHEN p_order='recent_follow_up' THEN recent END DESC NULLS LAST,name,id) FROM page),'[]'::jsonb),
  'total',(SELECT count(*) FROM candidates),'limit',least(greatest(p_limit,1),100),'offset',greatest(p_offset,0));
$$;

-- Keep all earlier recording rules. Only the new customer-only branch excludes
-- first-visit/profile, attendance management and historical read/edit/export.
CREATE OR REPLACE FUNCTION security.authorization_visit_target(p_permission text,p_customer uuid,p_opportunity uuid,
 p_recorder uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit_target_v290(p_permission,p_customer,p_opportunity,p_recorder,p_team)
 OR (p_recorder=common.current_user_ref_id()
  AND p_permission IN ('visit.create','visit.structure','visit.quality_review','visit.first_visit','visit.attendance_manage')
  AND security.company_opportunity_follow_up_reference(p_opportunity)#>>'{customer_reference,id}'=p_customer::text)
 OR (p_opportunity IS NULL AND p_recorder=common.current_user_ref_id()
  AND p_permission IN ('visit.create','visit.structure','visit.quality_review')
  AND security.company_customer_follow_up_reference(p_customer) IS NOT NULL);
$$;

-- This private predicate is used only for formal follow-up reading. Effective
-- appointments, not snapshot owner_team/manager fields, define the hierarchy.
-- Ambiguous multi-department appointments do not create supervisor access.
CREATE FUNCTION security.customer_claim_visit_customer_access(p_customer uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH RECURSIVE customer AS (
  SELECT c.id,own.owner_user_ref_id FROM crm.customer c
  LEFT JOIN crm.customer_ownership own ON own.customer_id=c.id AND own.workspace_id=c.workspace_id AND own.state='claimed'
  WHERE c.id=p_customer AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
 ), appointments AS MATERIALIZED (
  SELECT DISTINCT u.id user_id,tm.team_id,tm.membership_role
  FROM platform.user_ref u JOIN platform.role_binding rb ON rb.user_ref_id=u.id AND rb.workspace_id=u.workspace_id
  JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
  JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
  WHERE u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL
   AND (u.id=common.current_user_ref_id() OR u.id=(SELECT owner_user_ref_id FROM customer))
   AND rb.role_code IN ('sales','supervisor','manager','fde','fde_lead','operations','administrator')
   AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
   AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
   AND (tm.membership_role=rb.role_code OR (rb.role_code='fde' AND tm.membership_role='fde_lead'))
   AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   AND t.deleted_at IS NULL AND t.status='active' AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
 ), single_department AS (
  SELECT user_id,(array_agg(DISTINCT team_id))[1] team_id FROM appointments
  GROUP BY user_id HAVING count(DISTINCT team_id)=1
 ), ancestors AS (
  SELECT t.id,t.parent_team_id,ARRAY[t.id] path FROM platform.team t
  JOIN single_department s ON s.team_id=t.id JOIN customer c ON c.owner_user_ref_id=s.user_id
  UNION ALL
  SELECT t.id,t.parent_team_id,a.path||t.id FROM platform.team t JOIN ancestors a ON t.id=a.parent_team_id
  WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL AND t.status='active'
   AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to AND NOT t.id=ANY(a.path)
 ) SELECT security.customer_claim_visit_actor() AND EXISTS(SELECT 1 FROM customer c WHERE
  c.owner_user_ref_id=common.current_user_ref_id()
  OR common.current_role_code()='manager'
  OR (common.current_role_code() IN ('supervisor','fde_lead') AND EXISTS(
   SELECT 1 FROM single_department s JOIN appointments a ON a.user_id=s.user_id AND a.team_id=s.team_id
   JOIN ancestors h ON h.id=s.team_id WHERE s.user_id=common.current_user_ref_id()
    AND a.membership_role=common.current_role_code())));
$$;

CREATE OR REPLACE FUNCTION security.customer_claim_visit_reader() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.customer_claim_visit_actor() AND EXISTS(SELECT 1 FROM crm.customer c
  WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND security.customer_claim_visit_customer_access(c.id));
$$;

CREATE OR REPLACE FUNCTION security.customer_claim_visit_reference(p_customer uuid,p_opportunity uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',c.id,'name',c.name) FROM crm.customer c
 WHERE c.id=p_customer AND security.customer_claim_visit_customer_access(c.id)
  AND (p_opportunity IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity o
   WHERE o.id=p_opportunity AND o.workspace_id=c.workspace_id AND o.customer_id=c.id AND o.deleted_at IS NULL));
$$;

CREATE OR REPLACE FUNCTION security.customer_claim_visit_match_current(p_visit uuid,p_customer uuid,p_opportunity uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.customer_claim_visit_actor() AND EXISTS(
  SELECT 1 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id()
   AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND EXISTS(
    SELECT 1 FROM crm.customer c WHERE c.workspace_id=v.workspace_id AND c.deleted_at IS NULL
     AND (p_customer IS NULL OR c.id=p_customer) AND security.customer_claim_visit_customer_access(c.id)
     AND ((p_opportunity IS NULL AND v.customer_id=c.id) OR EXISTS(
      SELECT 1 FROM crm.opportunity o WHERE o.workspace_id=v.workspace_id AND o.customer_id=c.id AND o.deleted_at IS NULL
       AND (p_opportunity IS NULL OR o.id=p_opportunity) AND (o.id=v.opportunity_id OR EXISTS(
        SELECT 1 FROM activity.visit_opportunity vo WHERE vo.visit_id=v.id AND vo.workspace_id=v.workspace_id
         AND vo.opportunity_id=o.id))))));
$$;

-- Remove installation defaults, including grants inherited from default ACLs.
-- Runtime grants are applied only through the reviewed installation policy.
DO $acl$ DECLARE r record; BEGIN
 FOR r IN SELECT p.oid,a.grantee,role.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles role ON role.oid=a.grantee
  WHERE p.oid=ANY(ARRAY['security.company_customer_follow_up_actor()'::regprocedure,
   'security.company_customer_follow_up_reference(uuid)'::regprocedure,
   'security.company_customer_follow_up_options(text,integer,integer,text)'::regprocedure,
   'security.customer_claim_visit_customer_access(uuid)'::regprocedure]) AND a.grantee<>p.proowner
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',r.oid::regprocedure,
  CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END); END LOOP;
END $acl$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V441','Independent company customer follow-up recording and current claimant hierarchy formal visit reads');
COMMIT;
