BEGIN;
-- Creation-time evidence is internal, immutable and never supplied by a client.
-- This table grants no customer, mutation, AI or historical-follow-up access.
CREATE TABLE security.opportunity_sales_visibility (
 opportunity_id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL,
 customer_id uuid NOT NULL,
 creator_user_ref_id uuid NOT NULL,
 creator_role_code text NOT NULL CHECK(creator_role_code='manager'),
 claimant_user_ref_id uuid NOT NULL,
 claimant_role_code text NOT NULL CHECK(claimant_role_code='sales'),
 opportunity_created_at timestamptz NOT NULL,
 evidence_kind text NOT NULL CHECK(evidence_kind IN ('native_creation','audit_backfill')),
 evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id)
);
ALTER TABLE security.opportunity_sales_visibility ENABLE ROW LEVEL SECURITY;
ALTER TABLE security.opportunity_sales_visibility FORCE ROW LEVEL SECURITY;
CREATE TRIGGER opportunity_sales_visibility_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON security.opportunity_sales_visibility FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();

CREATE FUNCTION security.sales_shared_opportunity_actor() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_role_code() IN ('sales','supervisor','manager')
  AND security.company_opportunity_follow_up_actor()
  AND NOT security.is_demo_readonly_identity(common.current_workspace_id(),common.current_user_ref_id());
$$;

CREATE FUNCTION security.capture_opportunity_sales_visibility() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ownership crm.customer_ownership%ROWTYPE; native_id uuid;
BEGIN
 IF common.current_role_code()<>'manager' OR NOT security.sales_shared_opportunity_actor()
  OR NEW.workspace_id IS DISTINCT FROM common.current_workspace_id()
  OR NEW.created_by_user_ref_id IS DISTINCT FROM common.current_user_ref_id()
  OR NEW.source_code IS DISTINCT FROM 'manual' OR NEW.external_opportunity_id IS NOT NULL
  OR COALESCE(NEW.import_meta,'{}'::jsonb)<>'{}'::jsonb
 THEN RETURN NEW; END IF;
 -- aa_opportunity_typed_history executes first and consumes the native marker.
 -- Its immutable event is the server's proof of an actual new manual creation.
 SELECT id INTO native_id FROM crm.opportunity_state_event
 WHERE workspace_id=NEW.workspace_id AND opportunity_id=NEW.id
  AND mutation_kind='insert' AND after_version=1 AND history_origin='native_creation'
  AND source_kind='native_application' AND opportunity_created_at=NEW.created_at;
 IF native_id IS NULL THEN RETURN NEW; END IF;
 -- Serialize with claim/release; a subsequent release/transfer cannot rewrite
 -- what was true when the opportunity was created.
 SELECT * INTO ownership FROM crm.customer_ownership
 WHERE customer_id=NEW.customer_id AND workspace_id=NEW.workspace_id FOR SHARE;
 IF NOT FOUND OR ownership.state<>'claimed' OR NOT EXISTS(
  SELECT 1 FROM crm.customer c WHERE c.id=NEW.customer_id AND c.workspace_id=NEW.workspace_id AND c.deleted_at IS NULL)
  OR NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding r
   ON r.workspace_id=u.workspace_id AND r.user_ref_id=u.id
   WHERE u.id=ownership.owner_user_ref_id AND u.workspace_id=NEW.workspace_id
    AND u.status='active' AND u.deleted_at IS NULL AND r.role_code='sales'
    AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
    AND EXISTS(SELECT 1 FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id
     WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND tm.membership_role='sales'
      AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
      AND t.workspace_id=u.workspace_id AND t.status='active' AND t.deleted_at IS NULL
      AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to))
 THEN RETURN NEW; END IF;
 INSERT INTO security.opportunity_sales_visibility(opportunity_id,workspace_id,customer_id,
  creator_user_ref_id,creator_role_code,claimant_user_ref_id,claimant_role_code,
  opportunity_created_at,evidence_kind,evidence)
 VALUES(NEW.id,NEW.workspace_id,NEW.customer_id,NEW.created_by_user_ref_id,'manager',
  ownership.owner_user_ref_id,'sales',NEW.created_at,'native_creation',
  jsonb_build_object('state_event_id',native_id,'ownership_version',ownership.version_no));
 RETURN NEW;
END $$;
CREATE TRIGGER zz_opportunity_sales_visibility AFTER INSERT ON crm.opportunity
 FOR EACH ROW EXECUTE FUNCTION security.capture_opportunity_sales_visibility();

-- Historical backfill requires three independent, immutable as-of facts:
-- native creation + manager INSERT audit; last approved ownership event before
-- creation; and the claimant's role-binding audit image as of creation. Current
-- role/current ownership is deliberately not used. Unknown/imported rows retain
-- their old scope, instead of guessing from today's account configuration.
WITH candidates AS MATERIALIZED (
 SELECT o.id,o.workspace_id,o.customer_id,o.created_by_user_ref_id,o.created_at,
  a.id audit_id,e.id state_event_id
 FROM crm.opportunity o JOIN crm.opportunity_state_event e
  ON e.workspace_id=o.workspace_id AND e.opportunity_id=o.id AND e.mutation_kind='insert'
   AND e.after_version=1 AND e.history_origin='native_creation' AND e.source_kind='native_application'
   AND e.opportunity_created_at=o.created_at
 JOIN ops.audit_log a ON a.workspace_id=o.workspace_id AND a.object_id=o.id
  AND a.object_type='opportunity' AND a.action_code='crm.opportunity.insert'
  AND a.actor_role_code='manager' AND a.actor_user_ref_id=o.created_by_user_ref_id
  AND a.after_snapshot->>'id'=o.id::text
  AND a.after_snapshot->>'customer_id'=o.customer_id::text
  AND a.after_snapshot->>'workspace_id'=o.workspace_id::text
  AND (a.after_snapshot->>'created_at')::timestamptz=o.created_at
  AND a.after_snapshot->>'source_code'='manual'
  AND a.after_snapshot->>'external_opportunity_id' IS NULL
  AND COALESCE(a.after_snapshot->'import_meta','{}'::jsonb)='{}'::jsonb
 WHERE o.deleted_at IS NULL AND o.source_code='manual' AND o.external_opportunity_id IS NULL
  AND COALESCE(o.import_meta,'{}'::jsonb)='{}'::jsonb
), eligible AS (
 SELECT o.*,claim.id claim_event_id,claim.owner_user_ref_id,role_audit.id role_audit_id
 FROM candidates o
 JOIN LATERAL (
  SELECT h.* FROM crm.customer_ownership_event h
  WHERE h.workspace_id=o.workspace_id AND h.customer_id=o.customer_id AND h.occurred_at<=o.created_at
  ORDER BY h.occurred_at DESC,h.id DESC LIMIT 1
 ) claim ON claim.event_type='approved' AND claim.owner_user_ref_id IS NOT NULL
 JOIN LATERAL (
  SELECT a.id FROM ops.audit_log a
  WHERE a.workspace_id=o.workspace_id AND a.object_type='role_binding'
   AND a.action_code IN ('platform.role_binding.insert','platform.role_binding.update')
   AND a.occurred_at<=o.created_at
   AND a.after_snapshot->>'user_ref_id'=claim.owner_user_ref_id::text
   AND a.after_snapshot->>'workspace_id'=o.workspace_id::text
   AND a.after_snapshot->>'role_code'='sales'
   AND (a.after_snapshot->>'valid_from')::timestamptz<=o.created_at
   AND (a.after_snapshot->>'valid_to')::timestamptz>o.created_at
   AND NOT EXISTS(SELECT 1 FROM ops.audit_log newer WHERE newer.workspace_id=a.workspace_id
    AND newer.object_type='role_binding' AND newer.object_id=a.object_id
    AND newer.occurred_at<=o.created_at AND (newer.occurred_at,newer.id)>(a.occurred_at,a.id))
  ORDER BY a.occurred_at DESC,a.id DESC LIMIT 1
 ) role_audit ON true
 WHERE NOT EXISTS(SELECT 1 FROM crm.customer_ownership_event ambiguous
  WHERE ambiguous.workspace_id=o.workspace_id AND ambiguous.customer_id=o.customer_id
   AND ambiguous.occurred_at=claim.occurred_at AND ambiguous.id<>claim.id)
)
INSERT INTO security.opportunity_sales_visibility(opportunity_id,workspace_id,customer_id,
 creator_user_ref_id,creator_role_code,claimant_user_ref_id,claimant_role_code,
 opportunity_created_at,evidence_kind,evidence)
SELECT id,workspace_id,customer_id,created_by_user_ref_id,'manager',owner_user_ref_id,'sales',
 created_at,'audit_backfill',jsonb_build_object('creation_audit_id',audit_id,'state_event_id',state_event_id,
  'claim_event_id',claim_event_id,'claimant_role_audit_id',role_audit_id)
FROM eligible ON CONFLICT(opportunity_id) DO NOTHING;

CREATE FUNCTION security.sales_shared_opportunity_read(p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.sales_shared_opportunity_actor()
  AND COALESCE(current_setting('app.authorized_feature',true),'') IN ('','opportunity.read')
  AND EXISTS(SELECT 1 FROM security.opportunity_sales_visibility s
   JOIN crm.opportunity o ON o.id=s.opportunity_id AND o.workspace_id=s.workspace_id
   JOIN crm.customer c ON c.id=s.customer_id AND c.workspace_id=s.workspace_id
   WHERE s.opportunity_id=p_opportunity AND s.workspace_id=common.current_workspace_id()
    AND o.customer_id=s.customer_id AND o.deleted_at IS NULL AND c.deleted_at IS NULL);
$$;

DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['authorization_opportunity','authorization_opportunity_direct'] LOOP
  EXECUTE replace(pg_get_functiondef(to_regprocedure('security.'||name||'(text,uuid)')),
   'FUNCTION security.'||name||'(', 'FUNCTION security.'||name||'_v291(');
 END LOOP;
END $$;
CREATE OR REPLACE FUNCTION security.authorization_opportunity(p_permission text,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity_v291(p_permission,p_opportunity)
  OR (p_permission='opportunity.read' AND security.sales_shared_opportunity_read(p_opportunity));
$$;
CREATE OR REPLACE FUNCTION security.authorization_opportunity_direct(p_permission text,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity_direct_v291(p_permission,p_opportunity)
  OR (p_permission='opportunity.read' AND security.sales_shared_opportunity_read(p_opportunity));
$$;

-- The legacy follow-up, collaboration and collection-finance endpoints must
-- not infer extra history/roster rights from the new read branch. Preserve all
-- their old conditions, substituting only the pre-V440 opportunity predicate.
DO $$ DECLARE signature text; BEGIN
 FOREACH signature IN ARRAY ARRAY['security.authorization_visit_v290(text,uuid)',
  'security.authorization_visit_target_v290(text,uuid,uuid,uuid,uuid)',
  'security.opportunity_access(uuid)','security.opportunity_collection_actual(uuid,integer,integer)'] LOOP
  EXECUTE replace(pg_get_functiondef(to_regprocedure(signature)),
   'security.authorization_opportunity(', 'security.authorization_opportunity_v291(');
 END LOOP;
END $$;

-- Recording any company opportunity does not grant historical visit reads.
-- Current claim ownership is a separate, revocable read-only rule.
CREATE FUNCTION security.customer_claim_visit_actor() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.workspace_is_active(common.current_workspace_id())
 AND NOT security.is_demo_readonly_identity(common.current_workspace_id(),common.current_user_ref_id())
 AND EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding r
  ON r.user_ref_id=u.id AND r.workspace_id=u.workspace_id
  WHERE u.id=common.current_user_ref_id() AND u.workspace_id=common.current_workspace_id()
   AND u.status='active' AND u.deleted_at IS NULL AND r.role_code=common.current_role_code()
   AND r.role_code IN ('sales','supervisor','manager','fde','fde_lead','operations','administrator')
   AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
   AND (r.role_code IN ('manager','operations','administrator') OR EXISTS(
    SELECT 1 FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id
    WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND t.workspace_id=u.workspace_id
     AND t.deleted_at IS NULL AND t.status='active'
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
     AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
     AND (tm.membership_role=r.role_code OR (r.role_code='fde' AND tm.membership_role='fde_lead'))
     AND (r.role_code NOT IN ('fde','fde_lead') OR r.team_id IS NULL OR r.team_id=tm.team_id))));
$$;
CREATE FUNCTION security.customer_claim_visit_reader() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.customer_claim_visit_actor() AND EXISTS(
  SELECT 1 FROM crm.customer_ownership own JOIN crm.customer c
   ON c.id=own.customer_id AND c.workspace_id=own.workspace_id
  WHERE own.workspace_id=common.current_workspace_id() AND own.state='claimed'
   AND own.owner_user_ref_id=common.current_user_ref_id() AND c.deleted_at IS NULL);
$$;
CREATE FUNCTION security.customer_claim_visit_reference(p_customer uuid,p_opportunity uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',c.id,'name',c.name)
 FROM crm.customer_ownership own JOIN crm.customer c ON c.id=own.customer_id AND c.workspace_id=own.workspace_id
 WHERE security.customer_claim_visit_actor() AND own.workspace_id=common.current_workspace_id()
  AND own.state='claimed' AND own.owner_user_ref_id=common.current_user_ref_id()
  AND c.id=p_customer AND c.deleted_at IS NULL
  AND (p_opportunity IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity o
   WHERE o.id=p_opportunity AND o.workspace_id=c.workspace_id AND o.customer_id=c.id AND o.deleted_at IS NULL));
$$;
CREATE FUNCTION security.customer_claim_visit_match_current(p_visit uuid,p_customer uuid,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.customer_claim_visit_actor() AND EXISTS(
  SELECT 1 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id()
   AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND EXISTS(
    SELECT 1 FROM crm.customer_ownership own JOIN crm.customer c ON c.id=own.customer_id AND c.workspace_id=own.workspace_id
    WHERE own.workspace_id=v.workspace_id AND own.state='claimed' AND c.deleted_at IS NULL
     AND own.owner_user_ref_id=common.current_user_ref_id() AND (p_customer IS NULL OR c.id=p_customer)
     AND ((p_opportunity IS NULL AND v.customer_id=c.id) OR EXISTS(
      SELECT 1 FROM crm.opportunity o WHERE o.workspace_id=v.workspace_id AND o.customer_id=c.id AND o.deleted_at IS NULL
       AND (p_opportunity IS NULL OR o.id=p_opportunity) AND (o.id=v.opportunity_id OR EXISTS(
        SELECT 1 FROM activity.visit_opportunity vo WHERE vo.visit_id=v.id AND vo.workspace_id=v.workspace_id
         AND vo.opportunity_id=o.id))))));
$$;
CREATE FUNCTION security.customer_claim_visit_match(p_visit uuid,p_customer uuid,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT COALESCE(current_setting('app.authorized_feature',true),'') IN ('','visit.read','customer.read','opportunity.read')
 AND security.customer_claim_visit_match_current(p_visit,p_customer,p_opportunity);
$$;
CREATE FUNCTION security.customer_claim_visit_read(p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.customer_claim_visit_match(p_visit,NULL,NULL);
$$;
-- Metadata only: exclude the transaction-local save receipt allowance. Export
-- continues to require its existing feature grant and original object scope.
CREATE FUNCTION security.visit_saved_read_permissions(p_visit uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('can_read',COALESCE(security.authorization_visit_v290('visit.read',p_visit),false)
  OR security.customer_claim_visit_match_current(p_visit,NULL,NULL),
  'can_export',COALESCE(security.authorization_visit_v290('visit.read',p_visit),false));
$$;
-- The new recording capability can inspect only its unarchived processing
-- resources. Applied conversations are not an alternative history reader.
CREATE FUNCTION security.company_opportunity_follow_up_processing(p_run uuid,p_conversation uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.company_opportunity_follow_up_actor() AND EXISTS(
  SELECT 1 FROM agent.conversation c WHERE c.workspace_id=common.current_workspace_id()
   AND c.user_ref_id=common.current_user_ref_id() AND c.context->>'mode'='visit_entry'
   AND (p_conversation IS NULL OR c.id=p_conversation)
   AND (p_run IS NULL OR EXISTS(SELECT 1 FROM agent.run r WHERE r.id=p_run
    AND r.workspace_id=c.workspace_id AND r.conversation_id=c.id AND r.intent_code='visit_entry'
    AND r.business_context->>'mode'='visit_entry'))
   AND NOT EXISTS(SELECT 1 FROM agent.run r JOIN agent.artifact a ON a.run_id=r.id AND a.workspace_id=r.workspace_id
    WHERE r.conversation_id=c.id AND r.workspace_id=c.workspace_id
     AND a.artifact_type='visit_entry' AND a.status='applied'));
$$;
CREATE OR REPLACE FUNCTION security.authorization_visit_target(p_permission text,p_customer uuid,p_opportunity uuid,
 p_recorder uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit_target_v290(p_permission,p_customer,p_opportunity,p_recorder,p_team)
 OR (p_recorder=common.current_user_ref_id()
  AND p_permission IN ('visit.create','visit.structure','visit.quality_review','visit.first_visit','visit.attendance_manage')
  AND security.company_opportunity_follow_up_reference(p_opportunity)#>>'{customer_reference,id}'=p_customer::text);
$$;
CREATE OR REPLACE FUNCTION security.authorization_visit(p_permission text,p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit_v290(p_permission,p_visit)
 OR (p_permission='visit.read' AND security.customer_claim_visit_read(p_visit))
 -- The archive transaction must bind its review/participants and produce its
 -- immediate receipt. xmin/in-progress authority vanishes on commit; GETs do
 -- not use visit.create, so this cannot reopen a stored record later.
 OR (p_permission='visit.read' AND current_setting('app.authorized_feature',true)='visit.create'
  AND security.company_opportunity_follow_up_actor() AND security.authorization_visit_created(p_visit));
$$;
DO $$ BEGIN
 EXECUTE replace(pg_get_functiondef('security.authorization_import_v290(text,uuid)'::regprocedure),
  'security.authorization_visit(', 'security.authorization_visit_v290(');
END $$;
CREATE OR REPLACE FUNCTION security.authorization_import(p_permission text,p_import uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_import_v290(p_permission,p_import) OR (
  p_permission IN ('visit.upload','visit.read','visit.retry_import') AND security.company_opportunity_follow_up_actor()
  AND EXISTS(SELECT 1 FROM activity.visit_import i WHERE i.id=p_import
   AND i.workspace_id=common.current_workspace_id() AND i.created_by_user_ref_id=common.current_user_ref_id()
   AND NOT EXISTS(SELECT 1 FROM activity.visit v WHERE v.workspace_id=i.workspace_id AND v.source_import_id=i.id
    AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived'))));
$$;

-- A lost save response may be replayed only by the original actor and exact
-- reviewed run. This receipt has no record body or file content and is not a
-- general visit reader. The service compares the supplied reviewed snapshot.
CREATE FUNCTION security.archived_visit_save_receipt(p_run uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',v.id,'customer_id',v.customer_id,'opportunity_id',v.opportunity_id,
  'status','archived','save_only',true,
  'business_context',jsonb_build_object('visit_request',r.business_context->'visit_request'),
  'fde_participant_ids',COALESCE((SELECT jsonb_agg(p.user_ref_id::text ORDER BY p.user_ref_id)
   FROM activity.visit_participant p WHERE p.visit_id=v.id AND p.workspace_id=v.workspace_id
    AND p.participant_role='fde'),'[]'::jsonb),
  'completed_count',(SELECT count(*) FROM config.form_version_field f JOIN config.field_definition d
   ON d.id=f.field_definition_id WHERE f.form_version_id=v.form_version_id
    AND COALESCE(v.archived_fields->>d.field_key,'') NOT IN ('','false','null')),
  'total_count',(SELECT count(*) FROM config.form_version_field f WHERE f.form_version_id=v.form_version_id))
 FROM activity.visit v JOIN agent.artifact a ON a.id=v.source_artifact_id AND a.workspace_id=v.workspace_id
 JOIN agent.run r ON r.id=a.run_id AND r.workspace_id=v.workspace_id
 WHERE current_setting('app.authorized_feature',true)='visit.create'
  AND security.company_opportunity_follow_up_actor() AND r.id=p_run AND r.intent_code='visit_entry'
  AND a.artifact_type='visit_entry' AND a.status='applied' AND a.created_by_user_ref_id=common.current_user_ref_id()
  AND v.workspace_id=common.current_workspace_id() AND v.recorder_user_ref_id=common.current_user_ref_id()
  AND v.created_by_user_ref_id=common.current_user_ref_id() AND v.confirmed_by_user_ref_id=common.current_user_ref_id()
  AND r.identity_context->>'user_id'=common.current_user_ref_id()::text
  AND v.deleted_at IS NULL AND v.status='archived';
$$;

-- Safe metadata for an already readable opportunity. Never return customer
-- master data here: only the display label and the original access decisions.
CREATE FUNCTION security.opportunity_read_context(p_opportunity uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('shared_read_only',NOT security.authorization_opportunity_v291('opportunity.read',o.id),
  'can_read_customer',security.authorization_customer('customer.read',c.id),'customer_name',c.name)
 FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id()
  AND o.deleted_at IS NULL AND c.deleted_at IS NULL
  AND security.authorization_opportunity('opportunity.read',o.id);
$$;

-- Independent follow-up selection, NOT customer module visibility or permission
-- to archive a customer-only visit. Exact target validation remains unchanged.
CREATE FUNCTION security.company_opportunity_follow_up_customers(p_query text,p_limit integer,p_offset integer) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH candidates AS MATERIALIZED (
  SELECT c.id,c.name FROM crm.customer c WHERE c.workspace_id=common.current_workspace_id()
   AND c.deleted_at IS NULL AND security.company_opportunity_follow_up_actor()
   AND (COALESCE(p_query,'')='' OR strpos(lower(c.name),lower(p_query))>0)
 ), page AS (
  SELECT * FROM candidates ORDER BY name,id
  LIMIT least(greatest(p_limit,1),100) OFFSET greatest(p_offset,0)
 ) SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',id,'name',name)
  ORDER BY name,id) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM candidates),
  'limit',least(greatest(p_limit,1),100),'offset',greatest(p_offset,0));
$$;

-- Strip installation defaults. No runtime identity receives a direct table
-- privilege or EXECUTE on the trigger, old authorization snapshots or predicate.
DO $acl$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT a.grantee,role.rolname FROM pg_class c
  CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
  LEFT JOIN pg_roles role ON role.oid=a.grantee
  WHERE c.oid='security.opportunity_sales_visibility'::regclass AND a.grantee<>c.relowner
 LOOP EXECUTE format('REVOKE ALL ON TABLE security.opportunity_sales_visibility FROM %s CASCADE',
  CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END); END LOOP;
 FOR r IN SELECT p.oid,a.grantee,role.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles role ON role.oid=a.grantee
  WHERE p.oid=ANY(ARRAY[
   'security.sales_shared_opportunity_actor()'::regprocedure,
   'security.capture_opportunity_sales_visibility()'::regprocedure,
   'security.sales_shared_opportunity_read(uuid)'::regprocedure,
   'security.authorization_opportunity_v291(text,uuid)'::regprocedure,
   'security.authorization_opportunity_direct_v291(text,uuid)'::regprocedure,
   'security.customer_claim_visit_actor()'::regprocedure,
   'security.customer_claim_visit_reader()'::regprocedure,
   'security.customer_claim_visit_reference(uuid,uuid)'::regprocedure,
   'security.customer_claim_visit_read(uuid)'::regprocedure,
   'security.customer_claim_visit_match(uuid,uuid,uuid)'::regprocedure,
   'security.customer_claim_visit_match_current(uuid,uuid,uuid)'::regprocedure,
   'security.visit_saved_read_permissions(uuid)'::regprocedure,
   'security.company_opportunity_follow_up_processing(uuid,uuid)'::regprocedure,
   'security.archived_visit_save_receipt(uuid)'::regprocedure,
   'security.opportunity_read_context(uuid)'::regprocedure,
   'security.company_opportunity_follow_up_customers(text,integer,integer)'::regprocedure])
   AND a.grantee<>p.proowner
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',r.oid::regprocedure,
  CASE WHEN r.grantee=0 THEN 'PUBLIC' ELSE quote_ident(r.rolname) END); END LOOP;
END $acl$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V440','Creation-evidenced sales opportunity reads and independent follow-up customer references');
COMMIT;
