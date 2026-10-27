BEGIN;
-- Creation-only customer eligibility. Existing read/update/follow-up policies and
-- V440 sales visibility capture are intentionally unchanged.
CREATE FUNCTION security.opportunity_create_customer_reason(p_customer uuid) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE claimant uuid; actor uuid:=common.current_user_ref_id(); role text:=common.current_role_code();
BEGIN
 IF NOT security.authorization_has('opportunity.create') OR NOT security.has_active_role(role)
 THEN RETURN 'OPPORTUNITY_CREATE_FORBIDDEN'; END IF;
 IF NOT EXISTS(SELECT 1 FROM crm.customer WHERE id=p_customer AND workspace_id=common.current_workspace_id()
  AND deleted_at IS NULL) THEN RETURN 'OPPORTUNITY_CUSTOMER_NOT_FOUND'; END IF;
 -- A create directory never overrides the original reference feature/scope.
 IF security.customer_reference(p_customer) IS NULL OR NOT EXISTS(SELECT 1 FROM crm.customer
  WHERE id=p_customer AND 'customer'=ANY(business_identities)) THEN RETURN 'OPPORTUNITY_CREATE_FORBIDDEN'; END IF;
 SELECT o.owner_user_ref_id INTO claimant FROM crm.customer_ownership o
 JOIN platform.user_ref u ON u.id=o.owner_user_ref_id AND u.workspace_id=o.workspace_id
 WHERE o.customer_id=p_customer AND o.workspace_id=common.current_workspace_id() AND o.state='claimed'
  AND u.status='active' AND u.deleted_at IS NULL;
 IF claimant IS NULL OR NOT EXISTS(
  SELECT 1 FROM platform.role_binding rb JOIN platform.team_membership m
   ON m.workspace_id=rb.workspace_id AND m.user_ref_id=rb.user_ref_id
   AND (rb.team_id IS NULL OR rb.team_id=m.team_id)
   AND (m.membership_role=rb.role_code OR (rb.role_code='fde' AND m.membership_role='fde_lead'))
  JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=m.workspace_id
  WHERE rb.workspace_id=common.current_workspace_id() AND rb.user_ref_id=claimant
   AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
   AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
   AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
 ) THEN RETURN 'OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED'; END IF;
 IF claimant=actor OR role NOT IN ('sales','supervisor','manager') THEN RETURN NULL; END IF;
 IF role='sales' THEN RETURN 'OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED'; END IF;
 IF EXISTS(
  WITH RECURSIVE appointments AS MATERIALIZED (
   SELECT DISTINCT rb.user_ref_id,m.team_id,rb.role_code FROM platform.role_binding rb
   JOIN platform.team_membership m ON m.workspace_id=rb.workspace_id AND m.user_ref_id=rb.user_ref_id
    AND (rb.team_id IS NULL OR rb.team_id=m.team_id) AND m.membership_role=rb.role_code
   JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=m.workspace_id
   WHERE rb.workspace_id=common.current_workspace_id() AND rb.user_ref_id IN (actor,claimant)
    AND rb.role_code IN ('sales','supervisor')
    AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
    AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
  ), ancestors AS (
   SELECT t.id,t.parent_team_id,ARRAY[t.id] path FROM platform.team t JOIN appointments a ON a.team_id=t.id
    WHERE a.user_ref_id=claimant AND a.role_code='sales'
     AND NOT EXISTS(SELECT 1 FROM appointments supervisor WHERE supervisor.user_ref_id=claimant AND supervisor.role_code='supervisor')
   UNION ALL
   SELECT t.id,t.parent_team_id,a.path||t.id FROM platform.team t JOIN ancestors a ON t.id=a.parent_team_id
    WHERE t.workspace_id=common.current_workspace_id() AND t.status='active' AND t.deleted_at IS NULL
     AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to AND NOT t.id=ANY(a.path)
  ) SELECT 1 WHERE (role='manager' AND EXISTS(SELECT 1 FROM appointments WHERE user_ref_id=claimant))
   OR (role='supervisor' AND EXISTS(SELECT 1 FROM appointments me JOIN ancestors a ON a.id=me.team_id
    WHERE me.user_ref_id=actor AND me.role_code='supervisor'))
 ) THEN RETURN NULL; END IF;
 RETURN 'OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED';
END $$;

CREATE FUNCTION security.opportunity_create_customer_access(p_customer uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH access AS MATERIALIZED (SELECT security.opportunity_create_customer_reason(p_customer) reason)
 SELECT jsonb_build_object('can_create_opportunity',reason IS NULL,'denial_reason',reason,
  'customer_reference',CASE WHEN reason IS NULL THEN security.customer_reference(p_customer) ELSE NULL END) FROM access;
$$;

CREATE FUNCTION security.opportunity_create_customer_options(p_query text,p_limit integer,p_offset integer) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH candidates AS MATERIALIZED (
  SELECT c.id,c.name FROM crm.customer c WHERE c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
   AND 'customer'=ANY(c.business_identities)
   AND (COALESCE(p_query,'')='' OR strpos(lower(c.name),lower(p_query))>0)
   AND security.opportunity_create_customer_reason(c.id) IS NULL
 ), page AS (SELECT * FROM candidates ORDER BY name,id LIMIT least(greatest(p_limit,1),100) OFFSET greatest(p_offset,0))
 SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',id,'name',name) ORDER BY name,id) FROM page),'[]'::jsonb),
 'total',(SELECT count(*) FROM candidates),'limit',least(greatest(p_limit,1),100),'offset',greatest(p_offset,0));
$$;

CREATE FUNCTION security.require_opportunity_create_customer(p_customer uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE claimant uuid; reason text;
BEGIN
 -- Same order as financial saves and account/claim maintenance. Claim writers
 -- lock ownership before customer: never take a customer tuple lock first.
 PERFORM security.finance_write_lock();
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||common.current_workspace_id()::text,0));
 SELECT owner_user_ref_id INTO claimant FROM crm.customer_ownership
  WHERE customer_id=p_customer AND workspace_id=common.current_workspace_id() FOR SHARE;
 PERFORM 1 FROM platform.user_ref WHERE workspace_id=common.current_workspace_id()
  AND id IN (common.current_user_ref_id(),claimant) ORDER BY id FOR SHARE;
 PERFORM 1 FROM platform.role_binding WHERE workspace_id=common.current_workspace_id()
  AND user_ref_id IN (common.current_user_ref_id(),claimant) ORDER BY id FOR SHARE;
 PERFORM 1 FROM platform.team_membership WHERE workspace_id=common.current_workspace_id()
  AND user_ref_id IN (common.current_user_ref_id(),claimant) ORDER BY id FOR SHARE;
 -- Team changes can invalidate a managed ancestor, not just a leaf appointment.
 PERFORM 1 FROM platform.team WHERE workspace_id=common.current_workspace_id() ORDER BY id FOR SHARE;
 PERFORM security.authorization_refresh();
 reason:=security.opportunity_create_customer_reason(p_customer);
 IF reason IS NOT NULL THEN RAISE EXCEPTION '%',reason USING ERRCODE='P0297'; END IF;
 RETURN security.customer_reference(p_customer);
END $$;

CREATE FUNCTION security.guard_opportunity_create_customer() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 -- Retain the reviewed historical-import lane; flags alone never qualify.
 IF NEW.workspace_id=common.current_workspace_id() AND NEW.created_by_user_ref_id=common.current_user_ref_id()
  AND NEW.import_meta->>'import_type'='crm_history'
  AND security.authorization_history_provenance('opportunity',NEW.id,
   NEW.import_meta->>'import_record_id',NEW.import_meta->>'import_batch_id') THEN RETURN NEW; END IF;
 IF NEW.workspace_id IS DISTINCT FROM common.current_workspace_id()
  OR NEW.created_by_user_ref_id IS DISTINCT FROM common.current_user_ref_id()
 THEN RAISE EXCEPTION 'OPPORTUNITY_CREATE_FORBIDDEN' USING ERRCODE='P0297'; END IF;
 PERFORM security.require_opportunity_create_customer(NEW.customer_id);
 RETURN NEW;
END $$;
CREATE TRIGGER opportunity_create_claim_guard BEFORE INSERT ON crm.opportunity
 FOR EACH ROW EXECUTE FUNCTION security.guard_opportunity_create_customer();

DO $acl$ DECLARE r record; BEGIN
 FOR r IN SELECT p.oid,a.grantee,role.rolname FROM pg_proc p
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 LEFT JOIN pg_roles role ON role.oid=a.grantee
 WHERE p.oid=ANY(ARRAY['security.opportunity_create_customer_reason(uuid)'::regprocedure,
  'security.opportunity_create_customer_access(uuid)'::regprocedure,
  'security.opportunity_create_customer_options(text,integer,integer)'::regprocedure,
  'security.require_opportunity_create_customer(uuid)'::regprocedure,
  'security.guard_opportunity_create_customer()'::regprocedure]) AND a.grantee<>p.proowner
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',r.oid::regprocedure,
  CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END); END LOOP;
END $acl$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V445','Creation-only customer claim and effective organization scope with atomic insert guard');
COMMIT;
