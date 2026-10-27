BEGIN;
-- A new recording capability, not an opportunity/customer read grant. Existing
-- permission configuration and historical records are left unchanged.
CREATE FUNCTION security.company_opportunity_follow_up_actor() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.workspace_is_active(common.current_workspace_id()) AND EXISTS(
  SELECT 1 FROM platform.user_ref u JOIN platform.role_binding r
   ON r.user_ref_id=u.id AND r.workspace_id=u.workspace_id
  WHERE u.id=common.current_user_ref_id() AND u.workspace_id=common.current_workspace_id()
   AND u.status='active' AND u.deleted_at IS NULL
   AND r.role_code=common.current_role_code()
   AND r.role_code IN ('sales','supervisor','manager','fde','fde_lead','operations','administrator')
   AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
   AND (r.role_code IN ('manager','operations','administrator') OR EXISTS(
    SELECT 1 FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id
    WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id
     AND t.workspace_id=u.workspace_id AND t.deleted_at IS NULL AND t.status='active'
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
     AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
     AND (tm.membership_role=r.role_code OR (r.role_code='fde' AND tm.membership_role='fde_lead'))
     AND (r.role_code NOT IN ('fde','fde_lead') OR r.team_id IS NULL OR r.team_id=tm.team_id))));
$$;

CREATE FUNCTION security.company_opportunity_follow_up_reference(p_opportunity uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('opportunity_id',o.id,'name',o.name,
  'customer_reference',jsonb_build_object('id',c.id,'name',c.name),'can_follow_up',true)
 FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 WHERE security.company_opportunity_follow_up_actor() AND o.workspace_id=common.current_workspace_id()
  AND o.id=p_opportunity AND o.deleted_at IS NULL AND c.deleted_at IS NULL;
$$;

CREATE FUNCTION security.company_opportunity_follow_up_options(p_query text,p_customer uuid,p_limit integer,
 p_offset integer,p_order text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH own_recent AS MATERIALIZED (
  SELECT v.opportunity_id,max(COALESCE(v.recorded_on,
   (v.interaction_at AT TIME ZONE 'Asia/Shanghai')::date)) recent
  FROM activity.visit v WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
   AND v.opportunity_id IS NOT NULL AND v.status IN ('confirmed','archived')
   AND v.recorder_user_ref_id=common.current_user_ref_id()
   AND v.created_by_user_ref_id=common.current_user_ref_id()
  GROUP BY v.opportunity_id
 ), candidates AS MATERIALIZED (
  SELECT o.id,o.name,c.id customer_id,c.name customer_name,r.recent
  FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
  LEFT JOIN own_recent r ON r.opportunity_id=o.id
  WHERE security.company_opportunity_follow_up_actor() AND o.workspace_id=common.current_workspace_id()
   AND o.deleted_at IS NULL AND c.deleted_at IS NULL AND (p_customer IS NULL OR c.id=p_customer)
   AND (COALESCE(p_query,'')='' OR strpos(lower(o.name),lower(p_query))>0
    OR strpos(lower(c.name),lower(p_query))>0)
 ), page AS (
  SELECT * FROM candidates ORDER BY CASE WHEN p_order='recent_follow_up' THEN recent END DESC NULLS LAST,name,id
  LIMIT least(greatest(p_limit,1),100) OFFSET greatest(p_offset,0)
 ) SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(jsonb_build_object(
  'opportunity_id',id,'name',name,'customer_reference',jsonb_build_object('id',customer_id,'name',customer_name),
  'can_follow_up',true) ORDER BY CASE WHEN p_order='recent_follow_up' THEN recent END DESC NULLS LAST,name,id)
  FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM candidates),
  'limit',least(greatest(p_limit,1),100),'offset',greatest(p_offset,0));
$$;

-- Preserve original function identities used by RLS. Backup helpers are private.
DO $$ DECLARE name text; signature text; BEGIN
 FOR name,signature IN SELECT * FROM (VALUES
  ('authorization_visit_target','text,uuid,uuid,uuid,uuid'),
  ('authorization_visit','text,uuid'),('authorization_import','text,uuid'),
  ('authorization_has','text')) x(name,signature) LOOP
  EXECUTE replace(pg_get_functiondef(to_regprocedure('security.'||name||'('||signature||')')),
   'FUNCTION security.'||name||'(', 'FUNCTION security.'||name||'_v290(');
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION security.authorization_visit_target(p_permission text,p_customer uuid,p_opportunity uuid,
 p_recorder uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit_target_v290(p_permission,p_customer,p_opportunity,p_recorder,p_team)
 OR (p_recorder=common.current_user_ref_id()
  AND p_permission IN ('visit.create','visit.structure','visit.quality_review','visit.read',
   'visit.first_visit','visit.attendance_manage')
  AND security.company_opportunity_follow_up_reference(p_opportunity)#>>'{customer_reference,id}'=p_customer::text);
$$;

CREATE OR REPLACE FUNCTION security.authorization_visit(p_permission text,p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit_v290(p_permission,p_visit) OR (
  p_permission='visit.read' AND security.company_opportunity_follow_up_actor()
  AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id()
   AND v.opportunity_id IS NOT NULL AND v.deleted_at IS NULL
   AND v.recorder_user_ref_id=common.current_user_ref_id()
   AND v.created_by_user_ref_id=common.current_user_ref_id()
   AND (v.confirmed_by_user_ref_id IS NULL OR v.confirmed_by_user_ref_id=common.current_user_ref_id())));
$$;

-- These functions protect only an actor's upload/ASR resources. No original
-- download, export, roster, customer-only target or other AI action is added.
CREATE OR REPLACE FUNCTION security.authorization_has(p_permission text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_has_v290(p_permission) OR (
  p_permission IN ('visit.create','visit.structure','visit.quality_review','visit.upload','visit.transcribe','visit.retry_import')
  AND security.company_opportunity_follow_up_actor());
$$;

CREATE OR REPLACE FUNCTION security.authorization_import(p_permission text,p_import uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_import_v290(p_permission,p_import) OR (
  p_permission IN ('visit.upload','visit.read','visit.retry_import')
  AND security.company_opportunity_follow_up_actor()
  AND EXISTS(SELECT 1 FROM activity.visit_import i WHERE i.id=p_import
   AND i.workspace_id=common.current_workspace_id() AND i.created_by_user_ref_id=common.current_user_ref_id()));
$$;

DO $$ DECLARE sig text; grantee record; BEGIN
 FOREACH sig IN ARRAY ARRAY[
  'security.company_opportunity_follow_up_actor()',
  'security.company_opportunity_follow_up_reference(uuid)',
  'security.company_opportunity_follow_up_options(text,uuid,integer,integer,text)',
  'security.authorization_visit_target_v290(text,uuid,uuid,uuid,uuid)',
  'security.authorization_visit_v290(text,uuid)',
  'security.authorization_import_v290(text,uuid)',
  'security.authorization_has_v290(text)'] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',sig);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
    JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=to_regprocedure(sig) AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',sig,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V439','Same-company own opportunity follow-up with minimal references and private materials');
COMMIT;
