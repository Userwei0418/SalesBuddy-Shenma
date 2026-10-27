BEGIN;
-- Explicit opportunity collaboration is independent of customer ownership,
-- project/FDE participation and visit attendance. No existing grants are widened.
INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive)
 VALUES('opportunity.transfer','转交商机主责','商机管理',ARRAY['teams','workspace'],true);
INSERT INTO control.permission_module VALUES('opportunity.transfer','opportunity');
UPDATE config.permission_role_default SET permissions=permissions ||
 '[{"permission":"opportunity.transfer","scope":"inherit","team_ids":[]}]'::jsonb
 WHERE role_code IN ('administrator','operations','manager','supervisor');

CREATE TABLE crm.opportunity_collaborator (
 workspace_id uuid NOT NULL, opportunity_id uuid NOT NULL, user_ref_id uuid NOT NULL,
 added_by_user_ref_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,opportunity_id,user_ref_id),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(added_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE INDEX opportunity_collaborator_user ON crm.opportunity_collaborator(workspace_id,user_ref_id,opportunity_id);
CREATE TABLE crm.opportunity_access_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, opportunity_id uuid NOT NULL,
 actor_user_ref_id uuid NOT NULL, action text NOT NULL CHECK(action='collaborators.updated'),
 before_members uuid[] NOT NULL, after_members uuid[] NOT NULL,
 version_before integer NOT NULL, version_after integer NOT NULL CHECK(version_after=version_before+1),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(actor_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
ALTER TABLE crm.opportunity_collaborator ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_collaborator FORCE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_access_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_access_event FORCE ROW LEVEL SECURITY;
-- No runtime table DML: the guarded function is the sole write path.
REVOKE ALL ON crm.opportunity_collaborator,crm.opportunity_access_event FROM PUBLIC,salegent_feishu_worker;
CREATE TRIGGER access_event_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON crm.opportunity_access_event
 FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
CREATE TRIGGER collaborator_scope_revision AFTER INSERT OR DELETE ON crm.opportunity_collaborator
 FOR EACH ROW EXECUTE FUNCTION security.bump_authorization_scope_revision();

CREATE FUNCTION security.opportunity_collaborates(p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.opportunity_collaborator c JOIN platform.user_ref u
 ON u.id=c.user_ref_id AND u.workspace_id=c.workspace_id JOIN crm.opportunity o
 ON o.id=c.opportunity_id AND o.workspace_id=c.workspace_id
 WHERE c.workspace_id=common.current_workspace_id() AND c.opportunity_id=p_opportunity
 AND c.user_ref_id=common.current_user_ref_id() AND u.status='active' AND u.deleted_at IS NULL
 AND o.deleted_at IS NULL);
$$;

-- Preserve original function OIDs used by policies: copy, then replace in place.
DO $$ BEGIN
 EXECUTE replace(pg_get_functiondef('security.authorization_opportunity(text,uuid)'::regprocedure),
 'FUNCTION security.authorization_opportunity(', 'FUNCTION security.authorization_opportunity_v255(');
 -- Pin the legacy branches to legacy opportunity read authority. Otherwise
 -- indirect has_opportunity_read_access calls would turn a new read grant into
 -- unrelated attendance, first-visit or supplement authority.
 EXECUTE replace(replace(pg_get_functiondef('security.authorization_visit_target(text,uuid,uuid,uuid,uuid)'::regprocedure),
 'FUNCTION security.authorization_visit_target(', 'FUNCTION security.authorization_visit_target_v255('),
 'security.has_opportunity_read_access(', 'security.authorization_opportunity_v255(''opportunity.read'',');
 EXECUTE replace(replace(pg_get_functiondef('security.authorization_visit(text,uuid)'::regprocedure),
 'FUNCTION security.authorization_visit(', 'FUNCTION security.authorization_visit_v255('),
 'security.has_opportunity_read_access(', 'security.authorization_opportunity_v255(''opportunity.read'',');
END $$;

CREATE OR REPLACE FUNCTION security.authorization_opportunity(p_permission text,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_opportunity_v255(p_permission,p_opportunity) OR (
 p_permission='opportunity.read' AND security.authorization_has('opportunity.read')
 AND COALESCE(current_setting('app.authorized_feature',true),'') IN
 ('','opportunity.read','visit.read','visit.create','visit.structure','visit.quality_review')
 AND security.opportunity_collaborates(p_opportunity));
$$;

CREATE OR REPLACE FUNCTION security.authorization_visit_target(p_permission text,p_customer uuid,p_opportunity uuid,
 p_recorder uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit_target_v255(p_permission,p_customer,p_opportunity,p_recorder,p_team) OR (
 p_recorder=common.current_user_ref_id() AND p_permission IN
 ('visit.read','visit.create','visit.structure','visit.quality_review')
 AND security.authorization_has(p_permission) AND security.authorization_has('opportunity.read')
 AND security.opportunity_collaborates(p_opportunity)
 AND EXISTS(SELECT 1 FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id() AND o.customer_id=p_customer
 AND o.deleted_at IS NULL AND c.deleted_at IS NULL));
$$;

CREATE OR REPLACE FUNCTION security.authorization_visit(p_permission text,p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.authorization_visit_v255(p_permission,p_visit) OR (
 p_permission='visit.read' AND security.authorization_has('visit.read')
 AND COALESCE(current_setting('app.authorized_feature',true),'') IN
 ('','visit.read','visit.create','visit.structure','visit.quality_review','opportunity.read')
 AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id()
 AND v.deleted_at IS NULL AND security.opportunity_collaborates(v.opportunity_id)
 AND security.authorization_opportunity('opportunity.read',v.opportunity_id)));
$$;

CREATE FUNCTION security.opportunity_follow_up_reference(p_customer uuid,p_opportunity uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',c.id,'name',c.name) FROM crm.customer c JOIN crm.opportunity o
 ON o.customer_id=c.id AND o.workspace_id=c.workspace_id
 WHERE c.id=p_customer AND o.id=p_opportunity AND c.workspace_id=common.current_workspace_id()
 AND c.deleted_at IS NULL AND o.deleted_at IS NULL
 AND security.authorization_opportunity('opportunity.read',o.id)
 AND security.authorization_visit_target('visit.create',c.id,o.id,common.current_user_ref_id(),NULLIF(split_part(COALESCE(current_setting('app.team_ids',true),''),',',1),'')::uuid);
$$;

CREATE FUNCTION security.opportunity_collaborator_eligible(p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS MATERIALIZED (
 SELECT * FROM security.authorization_grants_for(common.current_workspace_id(),p_user)
 ), required AS (SELECT unnest(ARRAY['opportunity.read','visit.read','visit.create','visit.structure','visit.quality_review']) code)
 SELECT NOT EXISTS(SELECT 1 FROM required r WHERE NOT EXISTS(
 SELECT 1 FROM grants g WHERE g.permission_code=r.code AND g.effect='allow') OR EXISTS(
 SELECT 1 FROM grants g WHERE g.permission_code=r.code AND g.effect='deny'));
$$;

CREATE FUNCTION security.opportunity_access(p_opportunity uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o crm.opportunity%ROWTYPE; result jsonb;
BEGIN
 SELECT * INTO o FROM crm.opportunity WHERE id=p_opportunity AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND OR NOT security.authorization_opportunity('opportunity.read',p_opportunity) THEN
  RAISE EXCEPTION 'OPPORTUNITY_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('contract_version',1,'opportunity_id',o.id,'name',o.name,'stage_code',o.stage_code,
  'status',o.status,'version_no',o.version_no,
  'owner',jsonb_build_object('user_id',o.owner_user_ref_id,'display_name',u.display_name,'team_id',o.owner_team_id,'team_name',t.name),
  'customer_reference',jsonb_build_object('id',c.id,'name',c.name),
  'collaborators',COALESCE((SELECT jsonb_agg(jsonb_build_object('user_id',m.user_ref_id,'display_name',person.display_name)
    ORDER BY person.display_name,m.user_ref_id) FROM crm.opportunity_collaborator m JOIN platform.user_ref person
    ON person.id=m.user_ref_id AND person.workspace_id=m.workspace_id
    WHERE m.workspace_id=o.workspace_id AND m.opportunity_id=o.id),'[]'::jsonb),
  'capabilities',jsonb_build_object('can_transfer',false,
   'can_edit',security.authorization_opportunity('opportunity.update',o.id),
   'can_manage_collaborators',COALESCE(o.owner_user_ref_id=common.current_user_ref_id(),false) AND security.authorization_opportunity('opportunity.update',o.id),
   'can_follow_up',NOT EXISTS(SELECT 1 FROM unnest(ARRAY['visit.create','visit.structure','visit.quality_review','visit.read']) p
    WHERE NOT security.authorization_visit_target(p,o.customer_id,o.id,common.current_user_ref_id(),NULLIF(split_part(COALESCE(current_setting('app.team_ids',true),''),',',1),'')::uuid))),
  'capability_reasons',jsonb_build_object('transfer','TRANSFER_NOT_READY')) INTO result
 FROM crm.customer c LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id AND u.workspace_id=o.workspace_id
 LEFT JOIN platform.team t ON t.id=o.owner_team_id AND t.workspace_id=o.workspace_id
 WHERE c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL;
 IF result IS NULL THEN RAISE EXCEPTION 'OPPORTUNITY_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 RETURN result;
END $$;

CREATE FUNCTION security.opportunity_follow_up_options(p_query text,p_customer uuid,p_limit integer,p_offset integer) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH matched AS MATERIALIZED (
 SELECT o.id,o.name FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL AND c.deleted_at IS NULL
 AND (p_customer IS NULL OR o.customer_id=p_customer)
 AND (COALESCE(p_query,'')='' OR strpos(lower(o.name),lower(p_query))>0 OR strpos(lower(c.name),lower(p_query))>0)
 AND security.authorization_opportunity('opportunity.read',o.id)
 AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['visit.create','visit.structure','visit.quality_review','visit.read']) p
 WHERE NOT security.authorization_visit_target(p,o.customer_id,o.id,common.current_user_ref_id(),NULLIF(split_part(COALESCE(current_setting('app.team_ids',true),''),',',1),'')::uuid))
 ), page AS (SELECT * FROM matched ORDER BY name,id LIMIT least(greatest(p_limit,1),100) OFFSET greatest(p_offset,0))
 SELECT jsonb_build_object('contract_version',1,'items',COALESCE((SELECT jsonb_agg(security.opportunity_access(id)-'collaborators'
 ORDER BY name,id) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM matched),
 'limit',least(greatest(p_limit,1),100),'offset',greatest(p_offset,0));
$$;

CREATE FUNCTION security.opportunity_collaborator_candidates(p_opportunity uuid,p_query text,p_limit integer,p_offset integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o crm.opportunity%ROWTYPE;
BEGIN
 SELECT * INTO o FROM crm.opportunity WHERE id=p_opportunity AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF NOT FOUND OR o.owner_user_ref_id IS DISTINCT FROM common.current_user_ref_id()
 OR NOT security.authorization_opportunity('opportunity.update',o.id) THEN
 RAISE EXCEPTION 'OPPORTUNITY_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 RETURN (WITH candidates AS MATERIALIZED (
 SELECT u.id,u.display_name FROM platform.user_ref u WHERE u.workspace_id=o.workspace_id AND u.id<>o.owner_user_ref_id
 AND u.status='active' AND u.deleted_at IS NULL AND (COALESCE(p_query,'')='' OR strpos(lower(u.display_name),lower(p_query))>0)
 AND security.opportunity_collaborator_eligible(u.id)
 ), page AS (SELECT * FROM candidates ORDER BY display_name,id LIMIT least(greatest(p_limit,1),100) OFFSET greatest(p_offset,0))
 SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(jsonb_build_object('user_id',id,'display_name',display_name,'teams','[]'::jsonb)
 ORDER BY display_name,id) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM candidates)));
END $$;

CREATE FUNCTION security.save_opportunity_collaborators(p_opportunity uuid,p_version integer,p_members uuid[]) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o crm.opportunity%ROWTYPE; before_ids uuid[]; desired uuid[]; event_id uuid; result jsonb;
BEGIN
 PERFORM security.finance_write_lock();
 SELECT * INTO o FROM crm.opportunity WHERE id=p_opportunity AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 PERFORM security.authorization_refresh();
 IF o.id IS NULL OR o.owner_user_ref_id IS DISTINCT FROM common.current_user_ref_id()
 OR NOT security.authorization_opportunity('opportunity.update',o.id)
 OR NOT security.authorization_opportunity('opportunity.read',o.id) THEN
  RAISE EXCEPTION 'OPPORTUNITY_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF p_version IS NULL OR p_version<1 THEN RAISE EXCEPTION 'INVALID_ACCESS_VERSION' USING ERRCODE='22023'; END IF;
 IF o.version_no<>p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 IF p_members IS NULL OR cardinality(p_members)>30 OR array_position(p_members,NULL) IS NOT NULL
 OR cardinality(p_members)<>(SELECT count(DISTINCT m) FROM unnest(p_members) m)
 OR o.owner_user_ref_id=ANY(p_members) OR EXISTS(SELECT 1 FROM unnest(p_members) m
 WHERE NOT security.opportunity_collaborator_eligible(m)) THEN
  RAISE EXCEPTION 'INVALID_ACCESS_TARGET' USING ERRCODE='22023'; END IF;
 SELECT COALESCE(array_agg(m ORDER BY m),'{}'::uuid[]) INTO desired FROM unnest(p_members) m;
 SELECT COALESCE(array_agg(user_ref_id ORDER BY user_ref_id),'{}'::uuid[]) INTO before_ids
 FROM crm.opportunity_collaborator WHERE workspace_id=o.workspace_id AND opportunity_id=o.id;
 IF before_ids IS DISTINCT FROM desired THEN
  DELETE FROM crm.opportunity_collaborator WHERE workspace_id=o.workspace_id AND opportunity_id=o.id AND NOT(user_ref_id=ANY(desired));
  INSERT INTO crm.opportunity_collaborator(workspace_id,opportunity_id,user_ref_id,added_by_user_ref_id)
   SELECT o.workspace_id,o.id,m,common.current_user_ref_id() FROM unnest(desired) m
   ON CONFLICT(workspace_id,opportunity_id,user_ref_id) DO NOTHING;
  UPDATE crm.opportunity SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=o.id;
  INSERT INTO crm.opportunity_access_event(workspace_id,opportunity_id,actor_user_ref_id,action,before_members,after_members,version_before,version_after)
   VALUES(o.workspace_id,o.id,common.current_user_ref_id(),'collaborators.updated',before_ids,desired,o.version_no,o.version_no+1) RETURNING id INTO event_id;
 END IF;
 result:=security.opportunity_access(o.id);
 RETURN jsonb_build_object('changed',event_id IS NOT NULL,'event_id',event_id,'version_no',result->'version_no','collaborators',result->'collaborators');
END $$;

REVOKE ALL ON FUNCTION security.opportunity_collaborates(uuid),security.opportunity_collaborator_eligible(uuid),
 security.authorization_opportunity_v255(text,uuid),security.authorization_visit_v255(text,uuid),
 security.authorization_visit_target_v255(text,uuid,uuid,uuid,uuid),
 security.opportunity_follow_up_reference(uuid,uuid),security.opportunity_access(uuid),
 security.opportunity_follow_up_options(text,uuid,integer,integer),security.opportunity_collaborator_candidates(uuid,text,integer,integer),
 security.save_opportunity_collaborators(uuid,integer,uuid[]) FROM PUBLIC,salegent_feishu_worker;
-- Exact API/worker EXECUTE permissions are applied by the reviewed runtime policy.
INSERT INTO ops.schema_migration(version,description) VALUES('V404','Explicit opportunity collaboration and scoped follow-up references');
COMMIT;
