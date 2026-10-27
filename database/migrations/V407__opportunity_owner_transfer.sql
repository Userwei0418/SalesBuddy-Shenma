BEGIN;

-- Private immutable evidence, never granted as a generic runtime write surface.
CREATE TABLE crm.opportunity_owner_transfer_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,opportunity_id uuid NOT NULL,
 actor_user_ref_id uuid NOT NULL,request_key uuid NOT NULL,reason text NOT NULL,
 before_owner_user_ref_id uuid NOT NULL,before_owner_team_id uuid NOT NULL,
 after_owner_user_ref_id uuid NOT NULL,after_owner_team_id uuid NOT NULL,
 version_before integer NOT NULL,version_after integer NOT NULL CHECK(version_after=version_before+1),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(actor_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE TABLE crm.opportunity_transfer_request (
 workspace_id uuid NOT NULL,actor_user_ref_id uuid NOT NULL,request_key uuid NOT NULL,
 opportunity_id uuid NOT NULL,body jsonb NOT NULL,response jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(workspace_id,actor_user_ref_id,request_key),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id)
);
-- Keeps first attribution evidence, including unresolved legacy owner/team gaps.
-- Nullable normalized pairs never turn partial evidence into a guessed owner.
CREATE TABLE crm.opportunity_actual_attribution (
 workspace_id uuid NOT NULL,opportunity_id uuid NOT NULL,source_kind text NOT NULL,
 fact_id text NOT NULL,year integer NOT NULL,quarter integer NOT NULL,kind text NOT NULL,
 owner_user_ref_id uuid,owner_team_id uuid,evidence jsonb NOT NULL,
 CHECK ((owner_user_ref_id IS NULL)=(owner_team_id IS NULL)),
 captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,opportunity_id,source_kind,fact_id),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(owner_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(owner_team_id,workspace_id) REFERENCES platform.team(id,workspace_id)
);
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['opportunity_owner_transfer_event','opportunity_transfer_request','opportunity_actual_attribution'] LOOP
  EXECUTE format('ALTER TABLE crm.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE crm.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('REVOKE ALL ON crm.%I FROM PUBLIC,salegent_feishu_worker',tab);
  EXECUTE format('CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE OR TRUNCATE ON crm.%I FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation()',tab);
 END LOOP;
END $$;

CREATE FUNCTION security.transfer_snapshot_complete(p_workspace uuid,p_snapshot jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE owner_id uuid; team_id uuid;
BEGIN
 BEGIN
  owner_id:=(p_snapshot->>'owner_user_ref_id')::uuid; team_id:=(p_snapshot->>'owner_team_id')::uuid;
 EXCEPTION WHEN invalid_text_representation THEN RETURN false; END;
 RETURN owner_id IS NOT NULL AND team_id IS NOT NULL
  AND EXISTS(SELECT 1 FROM platform.user_ref WHERE id=owner_id AND workspace_id=p_workspace)
  AND EXISTS(SELECT 1 FROM platform.team WHERE id=team_id AND workspace_id=p_workspace);
END $$;

-- Capture partial snapshots too: switching a source clears adopted_snapshot in
-- V360, so its unknown attribution must survive independently of the active row.
INSERT INTO crm.opportunity_actual_attribution(workspace_id,opportunity_id,source_kind,fact_id,year,quarter,kind,owner_user_ref_id,owner_team_id,evidence)
 SELECT workspace_id,opportunity_id,'operations',id::text,year,quarter,kind,
 CASE WHEN security.transfer_snapshot_complete(workspace_id,material_snapshot) THEN (material_snapshot->>'owner_user_ref_id')::uuid END,
 CASE WHEN security.transfer_snapshot_complete(workspace_id,material_snapshot) THEN (material_snapshot->>'owner_team_id')::uuid END,material_snapshot
 FROM crm.opportunity_actual_confirmation;
INSERT INTO crm.opportunity_actual_attribution(workspace_id,opportunity_id,source_kind,fact_id,year,quarter,kind,owner_user_ref_id,owner_team_id,evidence)
 SELECT b.workspace_id,b.opportunity_id,b.source,f->>'id',b.year,b.quarter,b.kind,
 CASE WHEN security.transfer_snapshot_complete(b.workspace_id,f) THEN (f->>'owner_user_ref_id')::uuid END,
 CASE WHEN security.transfer_snapshot_complete(b.workspace_id,f) THEN (f->>'owner_team_id')::uuid END,f
 FROM crm.opportunity_actual_basis b CROSS JOIN LATERAL jsonb_array_elements(COALESCE(b.adopted_snapshot->'facts','[]')) f
 WHERE b.source IN ('entries','historical') AND f->>'id' IS NOT NULL;
INSERT INTO crm.opportunity_actual_attribution(workspace_id,opportunity_id,source_kind,fact_id,year,quarter,kind,owner_user_ref_id,owner_team_id,evidence)
 SELECT workspace_id,opportunity_id,'empty',id::text,year,quarter,kind,
 CASE WHEN security.transfer_snapshot_complete(workspace_id,operational_snapshot) THEN (operational_snapshot->>'owner_user_ref_id')::uuid END,
 CASE WHEN security.transfer_snapshot_complete(workspace_id,operational_snapshot) THEN (operational_snapshot->>'owner_team_id')::uuid END,operational_snapshot
 FROM crm.opportunity_actual_basis WHERE operational_snapshot IS NOT NULL;

-- Called only by the guarded finance triggers. Existing identity keeps its first
-- evidence (complete or unresolved) through source switches and corrections.
-- BEFORE triggers only normalize NEW; rejected INSERT candidates write nothing.
CREATE FUNCTION security.transfer_preserve_attribution(p_workspace uuid,p_opportunity uuid,p_source text,p_fact text,
 p_year integer,p_quarter integer,p_kind text,p_snapshot jsonb,p_prior jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE known crm.opportunity_actual_attribution; result jsonb:=COALESCE(p_snapshot,'{}'::jsonb); transfer_at timestamptz;
BEGIN
 SELECT * INTO known FROM crm.opportunity_actual_attribution WHERE workspace_id=p_workspace AND opportunity_id=p_opportunity
  AND source_kind=p_source AND fact_id=p_fact;
 IF FOUND THEN
  RETURN result||jsonb_build_object('owner_user_ref_id',known.evidence->'owner_user_ref_id','owner_team_id',known.evidence->'owner_team_id');
 END IF;
 -- Keep even partial legacy evidence; a new owner must never fill its gaps.
 IF p_prior IS NOT NULL THEN
  result:=result||jsonb_build_object('owner_user_ref_id',p_prior->'owner_user_ref_id','owner_team_id',p_prior->'owner_team_id');
 ELSE
  SELECT max(created_at) INTO transfer_at FROM crm.opportunity_owner_transfer_event WHERE workspace_id=p_workspace AND opportunity_id=p_opportunity;
  IF transfer_at IS NOT NULL AND (make_date(p_year,(p_quarter-1)*3+1,1)<date_trunc('quarter',transfer_at)::date
    OR (p_snapshot->>'occurred_on' IS NOT NULL AND (p_snapshot->>'occurred_on')::date<transfer_at::date))
  THEN RAISE EXCEPTION 'TRANSFER_ATTRIBUTION_UNRESOLVED' USING ERRCODE='40001'; END IF;
 END IF;
 RETURN result;
END $$;

CREATE FUNCTION crm.preserve_transferred_actual_attribution() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE prior jsonb; fact jsonb; facts jsonb:='[]'::jsonb;
BEGIN
 IF TG_TABLE_NAME='opportunity_actual_confirmation' THEN
  NEW.material_snapshot:=security.transfer_preserve_attribution(NEW.workspace_id,NEW.opportunity_id,'operations',NEW.id::text,
   NEW.year,NEW.quarter,NEW.kind,NEW.material_snapshot,CASE WHEN TG_OP='UPDATE' THEN OLD.material_snapshot ELSE NULL END);
 ELSE
  IF NEW.adopted_snapshot IS NOT NULL THEN
   FOR fact IN SELECT value FROM jsonb_array_elements(NEW.adopted_snapshot->'facts') LOOP
    prior:=NULL;
    IF TG_OP='UPDATE' AND OLD.source=NEW.source THEN
     SELECT value INTO prior FROM jsonb_array_elements(COALESCE(OLD.adopted_snapshot->'facts','[]')) WHERE value->>'id'=fact->>'id';
    END IF;
    facts:=facts||jsonb_build_array(security.transfer_preserve_attribution(NEW.workspace_id,NEW.opportunity_id,NEW.source,fact->>'id',
     NEW.year,NEW.quarter,NEW.kind,fact,prior));
   END LOOP;
   NEW.adopted_snapshot:=jsonb_set(NEW.adopted_snapshot,'{facts}',facts);
  END IF;
  IF NEW.operational_snapshot IS NOT NULL THEN
   NEW.operational_snapshot:=security.transfer_preserve_attribution(NEW.workspace_id,NEW.opportunity_id,'empty',NEW.id::text,
    NEW.year,NEW.quarter,NEW.kind,NEW.operational_snapshot,CASE WHEN TG_OP='UPDATE' THEN OLD.operational_snapshot ELSE NULL END);
  END IF;
 END IF;
 RETURN NEW;
END $$;
-- Alphabetically after the existing guards generate/validate new snapshots.
CREATE TRIGGER z_preserve_actual_attribution BEFORE INSERT OR UPDATE ON crm.opportunity_actual_confirmation
 FOR EACH ROW EXECUTE FUNCTION crm.preserve_transferred_actual_attribution();
CREATE TRIGGER z_preserve_basis_attribution BEFORE INSERT OR UPDATE ON crm.opportunity_actual_basis
 FOR EACH ROW EXECUTE FUNCTION crm.preserve_transferred_actual_attribution();

-- Only an actually inserted/updated row may establish first evidence. In
-- particular ON CONFLICT DO UPDATE/NOTHING must not capture the BEFORE INSERT
-- candidate that was discarded. Finance guards already hold the finance lock.
CREATE FUNCTION security.transfer_capture_attribution(p_workspace uuid,p_opportunity uuid,p_source text,p_fact text,
 p_year integer,p_quarter integer,p_kind text,p_snapshot jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE complete boolean:=security.transfer_snapshot_complete(p_workspace,p_snapshot);
BEGIN
 INSERT INTO crm.opportunity_actual_attribution(workspace_id,opportunity_id,source_kind,fact_id,year,quarter,kind,owner_user_ref_id,owner_team_id,evidence)
 VALUES(p_workspace,p_opportunity,p_source,p_fact,p_year,p_quarter,p_kind,
  CASE WHEN complete THEN (p_snapshot->>'owner_user_ref_id')::uuid END,
  CASE WHEN complete THEN (p_snapshot->>'owner_team_id')::uuid END,COALESCE(p_snapshot,'{}'::jsonb))
 ON CONFLICT(workspace_id,opportunity_id,source_kind,fact_id) DO NOTHING;
END $$;
CREATE FUNCTION crm.capture_transferred_actual_attribution() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE fact jsonb;
BEGIN
 IF TG_TABLE_NAME='opportunity_actual_confirmation' THEN
  PERFORM security.transfer_capture_attribution(NEW.workspace_id,NEW.opportunity_id,'operations',NEW.id::text,
   NEW.year,NEW.quarter,NEW.kind,NEW.material_snapshot);
 ELSE
  FOR fact IN SELECT value FROM jsonb_array_elements(COALESCE(NEW.adopted_snapshot->'facts','[]')) LOOP
   PERFORM security.transfer_capture_attribution(NEW.workspace_id,NEW.opportunity_id,NEW.source,fact->>'id',
    NEW.year,NEW.quarter,NEW.kind,fact);
  END LOOP;
  IF NEW.operational_snapshot IS NOT NULL THEN
   PERFORM security.transfer_capture_attribution(NEW.workspace_id,NEW.opportunity_id,'empty',NEW.id::text,
    NEW.year,NEW.quarter,NEW.kind,NEW.operational_snapshot);
  END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER z_capture_actual_attribution AFTER INSERT OR UPDATE ON crm.opportunity_actual_confirmation
 FOR EACH ROW EXECUTE FUNCTION crm.capture_transferred_actual_attribution();
CREATE TRIGGER z_capture_basis_attribution AFTER INSERT OR UPDATE ON crm.opportunity_actual_basis
 FOR EACH ROW EXECUTE FUNCTION crm.capture_transferred_actual_attribution();

CREATE FUNCTION security.transfer_scope_allows(p_workspace uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH grants AS MATERIALIZED (SELECT * FROM security.authorization_grants_for(p_workspace,common.current_user_ref_id()))
 SELECT p_workspace=common.current_workspace_id()
 AND NOT EXISTS(SELECT 1 FROM grants WHERE permission_code='opportunity.transfer' AND effect='deny')
 AND EXISTS(SELECT 1 FROM grants WHERE permission_code='opportunity.transfer' AND effect='allow'
  AND (scope_code='workspace' OR (scope_code='teams' AND p_team=ANY(team_ids))));
$$;
CREATE FUNCTION security.transfer_target_eligible(p_workspace uuid,p_user uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.team t ON t.workspace_id=u.workspace_id
 WHERE u.id=p_user AND u.workspace_id=p_workspace AND u.status='active' AND u.deleted_at IS NULL
 AND t.id=p_team AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
 AND EXISTS(SELECT 1 FROM platform.role_binding r WHERE r.workspace_id=u.workspace_id AND r.user_ref_id=u.id AND r.role_code='sales'
  AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to)
 AND EXISTS(SELECT 1 FROM security.account_team_scope(p_workspace,p_user,'sales') s WHERE s.team_id::uuid=p_team))
 AND security.transfer_scope_allows(p_workspace,p_team)
 AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['opportunity.read','opportunity.update']) required(permission) WHERE
  EXISTS(SELECT 1 FROM security.authorization_grants_for(p_workspace,p_user) g WHERE g.permission_code=required.permission AND g.effect='deny')
  OR NOT EXISTS(SELECT 1 FROM security.authorization_grants_for(p_workspace,p_user) g WHERE g.permission_code=required.permission AND g.effect='allow'
   AND (g.scope_code IN ('self','assigned','workspace') OR (g.scope_code='teams' AND p_team=ANY(g.team_ids)))));
$$;

-- Full graph check. row_security=off fails loudly if the trusted function owner
-- lacks complete visibility; an RLS-empty result can never authorize a transfer.
CREATE FUNCTION security.transfer_attribution_safe(p_opportunity uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE o crm.opportunity; b crm.opportunity_actual_basis; fact jsonb;
BEGIN
 SELECT * INTO o FROM crm.opportunity WHERE id=p_opportunity AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF o.id IS NULL OR o.owner_user_ref_id IS NULL OR o.owner_team_id IS NULL THEN RETURN false; END IF;
 -- Unknown first evidence remains a blocker even while another source is active.
 IF EXISTS(SELECT 1 FROM crm.opportunity_actual_attribution h WHERE h.opportunity_id=o.id AND h.workspace_id=o.workspace_id
  AND (h.owner_user_ref_id IS NULL OR h.owner_team_id IS NULL)) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM crm.opportunity_actual_confirmation a WHERE a.opportunity_id=o.id AND a.workspace_id=o.workspace_id
  AND NOT security.transfer_snapshot_complete(o.workspace_id,a.material_snapshot)) THEN RETURN false; END IF;
 FOR b IN SELECT * FROM crm.opportunity_actual_basis WHERE workspace_id=o.workspace_id AND opportunity_id=o.id LOOP
  IF b.source IN ('entries','historical') AND (b.adopted_snapshot IS NULL OR jsonb_typeof(b.adopted_snapshot->'facts') IS DISTINCT FROM 'array'
    OR jsonb_array_length(b.adopted_snapshot->'facts')=0) THEN RETURN false; END IF;
  IF b.operational_snapshot IS NOT NULL AND NOT security.transfer_snapshot_complete(o.workspace_id,b.operational_snapshot) THEN RETURN false; END IF;
  IF b.empty_confirmation AND b.operational_snapshot IS NULL THEN RETURN false; END IF;
  FOR fact IN SELECT value FROM jsonb_array_elements(COALESCE(b.adopted_snapshot->'facts','[]')) LOOP
   IF NOT security.transfer_snapshot_complete(o.workspace_id,fact) THEN RETURN false; END IF;
  END LOOP;
 END LOOP;
 -- Raw/unselected/voided legacy references are included: they can be restored or
 -- adopted later. Missing evidence is conservative rejection, never auto-adopt.
 IF EXISTS(SELECT 1 FROM crm.customer_actual a WHERE a.workspace_id=o.workspace_id AND a.opportunity_id=o.id AND NOT EXISTS(
   SELECT 1 FROM crm.opportunity_actual_attribution h WHERE h.workspace_id=o.workspace_id AND h.opportunity_id=o.id AND h.source_kind='entries' AND h.fact_id=a.id::text))
 OR EXISTS(SELECT 1 FROM crm.opportunity_period_actual_snapshot a WHERE a.workspace_id=o.workspace_id AND a.opportunity_id=o.id AND NOT EXISTS(
   SELECT 1 FROM crm.opportunity_actual_attribution h WHERE h.workspace_id=o.workspace_id AND h.opportunity_id=o.id AND h.source_kind='historical' AND h.fact_id=a.id::text))
 THEN RETURN false; END IF;
 -- A previously adopted reference may have become dynamic after a source switch.
 IF EXISTS(SELECT 1 FROM crm.finance_period_resolution r WHERE r.workspace_id=o.workspace_id AND r.opportunity_id=o.id
  AND ((r.source IN ('entries','historical') AND r.adopted_snapshot IS NULL)
   OR (r.source IS NULL AND (r.entry_count>0 OR r.history_count>0)))) THEN RETURN false; END IF;
 RETURN true;
END $$;

CREATE FUNCTION security.opportunity_transfer_capability(p_opportunity uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE o crm.opportunity; reason text;
BEGIN
 SELECT * INTO o FROM crm.opportunity WHERE id=p_opportunity AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL;
 IF o.id IS NULL OR NOT security.transfer_scope_allows(o.workspace_id,o.owner_team_id) THEN reason:='OPPORTUNITY_ACCESS_DENIED';
 ELSIF o.status<>'open' THEN reason:='TRANSFER_STATE_NOT_ALLOWED';
 ELSIF NOT security.transfer_attribution_safe(o.id) THEN reason:='TRANSFER_ATTRIBUTION_UNRESOLVED'; END IF;
 RETURN jsonb_build_object('can_transfer',reason IS NULL,'reason',reason);
END $$;

CREATE FUNCTION security.opportunity_transfer_candidates(p_opportunity uuid,p_query text,p_limit integer,p_offset integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE o crm.opportunity; reason text;
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 OR p_offset IS NULL OR p_offset<0 OR length(COALESCE(p_query,''))>100
 THEN RAISE EXCEPTION 'INVALID_ACCESS_TARGET' USING ERRCODE='22023'; END IF;
 reason:=security.opportunity_transfer_capability(p_opportunity)->>'reason';
 IF reason='OPPORTUNITY_ACCESS_DENIED' THEN RAISE EXCEPTION '%',reason USING ERRCODE='42501';
 ELSIF reason IS NOT NULL THEN RAISE EXCEPTION '%',reason USING ERRCODE='40001'; END IF;
 SELECT * INTO o FROM crm.opportunity WHERE id=p_opportunity;
 RETURN (WITH candidates AS MATERIALIZED (
  SELECT u.id,u.display_name,jsonb_agg(jsonb_build_object('team_id',t.id,'name',t.name) ORDER BY t.name,t.id) teams
  FROM platform.user_ref u JOIN platform.team t ON t.workspace_id=u.workspace_id
  WHERE u.workspace_id=o.workspace_id AND security.transfer_target_eligible(o.workspace_id,u.id,t.id)
  AND (COALESCE(p_query,'')='' OR strpos(lower(u.display_name),lower(p_query))>0 OR strpos(lower(COALESCE(u.account_code,'')),lower(p_query))>0)
  GROUP BY u.id,u.display_name), page AS (SELECT * FROM candidates ORDER BY display_name,id LIMIT p_limit OFFSET p_offset)
  SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(jsonb_build_object('user_id',id,'display_name',display_name,'teams',teams) ORDER BY display_name,id) FROM page),'[]'::jsonb),
   'total',(SELECT count(*) FROM candidates)));
END $$;

CREATE FUNCTION security.transfer_opportunity_owner(p_opportunity uuid,p_version integer,p_user uuid,p_team uuid,p_reason text,p_key uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE o crm.opportunity; receipt crm.opportunity_transfer_request; body jsonb; result jsonb; event_id uuid;
 actor uuid:=common.current_user_ref_id(); changed boolean; sync_state text; members jsonb;
BEGIN
 PERFORM security.finance_write_lock();
 SELECT * INTO o FROM crm.opportunity WHERE id=p_opportunity AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 PERFORM security.authorization_refresh();
 IF o.id IS NULL OR NOT security.transfer_scope_allows(o.workspace_id,o.owner_team_id)
  OR NOT security.transfer_scope_allows(o.workspace_id,p_team) THEN RAISE EXCEPTION 'OPPORTUNITY_ACCESS_DENIED' USING ERRCODE='42501'; END IF;
 IF NOT security.transfer_target_eligible(o.workspace_id,p_user,p_team) THEN RAISE EXCEPTION 'INVALID_ACCESS_TARGET' USING ERRCODE='22023'; END IF;
 IF p_key IS NULL THEN RAISE EXCEPTION 'IDEMPOTENCY_KEY_REQUIRED' USING ERRCODE='22023'; END IF;
 IF p_version IS NULL OR p_version<1 OR length(btrim(COALESCE(p_reason,''))) NOT BETWEEN 1 AND 1000
 THEN RAISE EXCEPTION 'INVALID_ACCESS_TARGET' USING ERRCODE='22023'; END IF;
 body:=jsonb_build_object('opportunity_id',p_opportunity,'version_no',p_version,'new_owner_user_ref_id',p_user,'new_owner_team_id',p_team,'reason',btrim(p_reason));
 SELECT * INTO receipt FROM crm.opportunity_transfer_request WHERE workspace_id=o.workspace_id AND actor_user_ref_id=actor AND request_key=p_key;
 IF FOUND THEN
  IF receipt.body<>body THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE='40001'; END IF;
  RETURN receipt.response;
 END IF;
 IF o.version_no<>p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 IF o.status<>'open' THEN RAISE EXCEPTION 'TRANSFER_STATE_NOT_ALLOWED' USING ERRCODE='40001'; END IF;
 changed:=(o.owner_user_ref_id,o.owner_team_id) IS DISTINCT FROM (p_user,p_team);
 IF changed THEN
  IF NOT security.transfer_attribution_safe(o.id) THEN RAISE EXCEPTION 'TRANSFER_ATTRIBUTION_UNRESOLVED' USING ERRCODE='40001'; END IF;
  UPDATE crm.opportunity SET owner_user_ref_id=p_user,owner_team_id=p_team,version_no=version_no+1,updated_at=clock_timestamp() WHERE id=o.id;
  DELETE FROM crm.opportunity_collaborator WHERE workspace_id=o.workspace_id AND opportunity_id=o.id AND user_ref_id=p_user;
  INSERT INTO crm.opportunity_owner_transfer_event(workspace_id,opportunity_id,actor_user_ref_id,request_key,reason,
   before_owner_user_ref_id,before_owner_team_id,after_owner_user_ref_id,after_owner_team_id,version_before,version_after)
  VALUES(o.workspace_id,o.id,actor,p_key,btrim(p_reason),o.owner_user_ref_id,o.owner_team_id,p_user,p_team,o.version_no,o.version_no+1) RETURNING id INTO event_id;
  -- Minimal notification only to the new responsible person with live read grant.
  IF EXISTS(SELECT 1 FROM security.authorization_grants_for(o.workspace_id,p_user) WHERE permission_code='opportunity.read' AND effect='allow'
    AND (scope_code IN ('self','assigned','workspace') OR (scope_code='teams' AND p_team=ANY(team_ids))))
   AND NOT EXISTS(SELECT 1 FROM security.authorization_grants_for(o.workspace_id,p_user) WHERE permission_code='opportunity.read' AND effect='deny') THEN
   INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,channel_code,template_code,title,body,object_type,object_id,status,dedupe_key,payload)
   VALUES(o.workspace_id,p_user,'in_app','opportunity_owner_transferred','你已接手一条商机','请打开商机查看当前负责人和团队','opportunity',o.id,'pending',
    'opportunity_transfer:'||event_id::text,jsonb_build_object('event_id',event_id,'version_no',o.version_no+1));
  END IF;
 END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('user_id',c.user_ref_id,'display_name',u.display_name) ORDER BY u.display_name,u.id),'[]') INTO members
 FROM crm.opportunity_collaborator c JOIN platform.user_ref u ON u.id=c.user_ref_id AND u.workspace_id=c.workspace_id WHERE c.workspace_id=o.workspace_id AND c.opportunity_id=o.id;
 SELECT CASE WHEN NOT enabled THEN 'disabled' WHEN COALESCE((settings#>>'{mappings,opportunity,enabled}')::boolean,false) THEN 'pending' ELSE 'not_configured' END INTO sync_state
 FROM config.feishu_connection WHERE workspace_id=o.workspace_id;
 result:=jsonb_build_object('changed',changed,'event_id',event_id,'version_no',o.version_no+CASE WHEN changed THEN 1 ELSE 0 END,
  'owner',jsonb_build_object('user_id',p_user,'display_name',(SELECT display_name FROM platform.user_ref WHERE id=p_user),
  'team_id',p_team,'team_name',(SELECT name FROM platform.team WHERE id=p_team)),'collaborators',members,
  'sync_status',CASE WHEN NOT changed THEN 'unchanged' ELSE COALESCE(sync_state,'not_configured') END);
 INSERT INTO crm.opportunity_transfer_request(workspace_id,actor_user_ref_id,request_key,opportunity_id,body,response)
 VALUES(o.workspace_id,actor,p_key,o.id,body,result);
 RETURN result;
END $$;

-- Ordinary API writes have no switch to assume the trusted function identity.
CREATE OR REPLACE FUNCTION crm.guard_opportunity_permission_transition() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE owner_role name;
BEGIN
 IF (NEW.workspace_id,NEW.customer_id,NEW.created_by_user_ref_id) IS DISTINCT FROM (OLD.workspace_id,OLD.customer_id,OLD.created_by_user_ref_id)
 THEN RAISE EXCEPTION '商机归属不可通过普通修改变更' USING ERRCODE='42501'; END IF;
 IF (NEW.owner_user_ref_id,NEW.owner_team_id) IS DISTINCT FROM (OLD.owner_user_ref_id,OLD.owner_team_id) THEN
  SELECT pg_get_userbyid(proowner) INTO owner_role FROM pg_proc WHERE oid='security.transfer_opportunity_owner(uuid,integer,uuid,uuid,text,uuid)'::regprocedure;
  IF current_user<>owner_role THEN RAISE EXCEPTION 'OPPORTUNITY_TRANSFER_REQUIRED' USING ERRCODE='42501'; END IF;
 END IF;
 IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AND NEW.status IS DISTINCT FROM OLD.status
  AND NOT security.authorization_opportunity(CASE WHEN NEW.status='open' THEN 'opportunity.reopen' ELSE 'opportunity.close' END,OLD.id)
 THEN RAISE insufficient_privilege; END IF;
 RETURN NEW;
END $$;

DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE (n.nspname='security' AND (p.proname LIKE 'transfer_%' OR p.proname LIKE 'opportunity_transfer_%'))
 OR (n.nspname='crm' AND p.proname IN ('preserve_transferred_actual_attribution','capture_transferred_actual_attribution')) LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,salegent_feishu_worker',f.signature);
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V407','Controlled opportunity owner transfer and preserved first proven financial attribution');
COMMIT;
