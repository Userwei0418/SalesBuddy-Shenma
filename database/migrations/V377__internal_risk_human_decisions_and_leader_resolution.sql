-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V229__risk_human_decisions_and_leader_resolution.sql.
-- Original SHA-256: 0d74f15306044ee50ea9158f7f178a1310ae7f2e303a6295b4f139fbd43779db. See shenma_whole_121_manifest.json.
BEGIN;

-- Suggestions are a human decision, never an AI-generation or read grant.
INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('risk.decide','确认风险建议动作','经营建议与风险',ARRAY['self','assigned','teams','workspace','departments'],false);
INSERT INTO control.permission_module(permission_code,module_code) VALUES ('risk.decide','core');
-- Reuse existing built-in advice-decision scopes; do not widen custom roles or
-- re-enable omitted grants. The object gate below additionally restricts this
-- new action to the owner or an actual leader in that risk's management scope.
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code,team_ids)
 SELECT g.workspace_id,g.role_id,'risk.decide',g.scope_code,g.team_ids
 FROM config.permission_role_grant g JOIN config.permission_role r ON r.id=g.role_id
 WHERE g.permission_code='advice.decide' AND r.builtin_role_code IS NOT NULL;
UPDATE config.permission_role_default d SET permissions=d.permissions||COALESCE((
 SELECT jsonb_agg((p-'permission')||jsonb_build_object('permission','risk.decide'))
 FROM jsonb_array_elements(d.permissions) p WHERE p->>'permission'='advice.decide'),'[]'::jsonb);
-- Preserve an existing explicit refusal when deriving the new default action.
INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,
 scope_code,team_ids,expires_at,delegation_user_ref_id,delegation_version)
 SELECT workspace_id,user_ref_id,'risk.decide',effect,scope_code,team_ids,expires_at,
 delegation_user_ref_id,delegation_version FROM config.account_permission_override
 WHERE permission_code='advice.decide' AND effect='deny';
UPDATE config.permission_role SET version_no=version_no+1,updated_at=clock_timestamp()
 WHERE EXISTS(SELECT 1 FROM config.permission_role_grant g
 WHERE g.role_id=config.permission_role.id AND g.permission_code='risk.decide');
UPDATE config.authorization_scope_revision SET revision=revision+1;

-- Reading another department is not an appointment to manage it. Check actual
-- effective role bindings and memberships independently of configurable grants.
CREATE FUNCTION security.risk_management_scope(p_workspace uuid,p_team uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p_workspace=common.current_workspace_id() AND EXISTS(
  SELECT 1 FROM platform.role_binding b
  JOIN platform.user_ref u ON u.id=b.user_ref_id AND u.workspace_id=b.workspace_id
  WHERE b.workspace_id=p_workspace AND b.user_ref_id=common.current_user_ref_id()
   AND u.status='active' AND u.deleted_at IS NULL
   AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
   AND b.role_code IN ('manager','supervisor','fde_lead')
   AND ((b.role_code='manager' AND b.data_scope_code='workspace')
     OR (b.data_scope_code IN ('team','workspace') AND p_team IN (
       SELECT s.team_id::uuid FROM security.account_team_scope(p_workspace,b.user_ref_id,b.role_code) s))));
$$;
REVOKE ALL ON FUNCTION security.risk_management_scope(uuid,uuid) FROM PUBLIC,salegent_feishu_worker;

CREATE OR REPLACE FUNCTION security.authorization_risk_scope(p_permission text,p_workspace uuid,p_owner uuid,
 p_team uuid,p_customer uuid,p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN p_permission IN ('risk.resolve','risk.decide') THEN
  (security.risk_management_scope(p_workspace,p_team)
    OR (p_permission='risk.decide' AND p_owner=common.current_user_ref_id()))
  AND security.authorization_allows(p_permission,p_workspace,p_owner,ARRAY[p_team],
                                    p_owner=common.current_user_ref_id())
 ELSE security.authorization_allows(p_permission,p_workspace,p_owner,
 ARRAY[p_team] || ARRAY(SELECT DISTINCT tm.team_id FROM crm.opportunity_participant p
 JOIN platform.team_membership tm ON tm.user_ref_id=p.user_ref_id AND tm.workspace_id=p.workspace_id
 JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
 WHERE p.workspace_id=p_workspace AND p.opportunity_id=p_opportunity
 AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 AND security.fde_user_is_active(p.user_ref_id)
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 AND t.status='active' AND t.deleted_at IS NULL),
 p_owner=common.current_user_ref_id() OR EXISTS(SELECT 1 FROM crm.opportunity_participant p
 WHERE p.workspace_id=p_workspace AND p.opportunity_id=p_opportunity
 AND p.user_ref_id=common.current_user_ref_id()
 AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 AND security.fde_user_is_active(p.user_ref_id)))
 OR ((p_owner IS NULL OR (p_opportunity IS NULL AND p_permission NOT IN ('risk.resolve','risk.auto_review')))
 AND p_workspace=common.current_workspace_id() AND p_customer IS NOT NULL
 AND security.authorization_customer(p_permission,p_customer)) END;
$$;

CREATE OR REPLACE FUNCTION security.authorization_risk(p_permission text,p_risk uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM insight.risk r WHERE r.id=p_risk
 AND r.workspace_id=common.current_workspace_id() AND r.deleted_at IS NULL
 AND security.authorization_risk_scope(p_permission,r.workspace_id,r.owner_user_ref_id,
 r.owner_team_id,r.customer_id,r.opportunity_id));
$$;

ALTER POLICY permission_update ON insight.risk USING(
 security.authorization_risk('risk.resolve',id) OR security.authorization_risk('risk.decide',id)
 OR security.authorization_risk_generation(customer_id,owner_user_ref_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND (
 security.authorization_risk('risk.resolve',id) OR security.authorization_risk('risk.decide',id)
 OR security.authorization_risk_generation(customer_id,owner_user_ref_id)));

CREATE OR REPLACE FUNCTION security.guard_risk_permission() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_user AND rolsuper) THEN RETURN NEW; END IF;
 IF (NEW.workspace_id,NEW.customer_id,NEW.opportunity_id,NEW.owner_user_ref_id,NEW.owner_team_id,NEW.source_visit_id)
 IS DISTINCT FROM (OLD.workspace_id,OLD.customer_id,OLD.opportunity_id,OLD.owner_user_ref_id,OLD.owner_team_id,OLD.source_visit_id)
 OR NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN RAISE insufficient_privilege; END IF;
 IF (NEW.status,NEW.resolved_at,NEW.resolution_note) IS DISTINCT FROM (OLD.status,OLD.resolved_at,OLD.resolution_note)
 AND security.authorization_risk('risk.resolve',OLD.id) IS NOT TRUE THEN RAISE insufficient_privilege; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','resolved_at','resolution_note','version_no','updated_at'])
 IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','resolved_at','resolution_note','version_no','updated_at'])
 AND security.authorization_risk_generation(OLD.customer_id,OLD.owner_user_ref_id) IS NOT TRUE
 THEN RAISE insufficient_privilege; END IF;
 -- A decision-only actor can only advance the optimistic lock by exactly one;
 -- it cannot change suggestion, evidence, status, ownership or resolution.
 -- Generation can be NULL on an ownerless risk. Only an explicit TRUE grants
 -- permission; SQL's NOT NULL must never bypass either field or version guards.
 IF security.authorization_risk('risk.resolve',OLD.id) IS NOT TRUE
 AND security.authorization_risk_generation(OLD.customer_id,OLD.owner_user_ref_id) IS NOT TRUE
 AND (security.authorization_risk('risk.decide',OLD.id) IS NOT TRUE OR OLD.status='resolved'
      OR NEW.version_no<>OLD.version_no+1)
 THEN RAISE insufficient_privilege; END IF;
 RETURN NEW;
END $$;

ALTER POLICY permission_insert ON insight.risk_event WITH CHECK(
 workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id()
 AND EXISTS(SELECT 1 FROM insight.risk r WHERE r.id=risk_id AND r.workspace_id=common.current_workspace_id()
 AND (CASE WHEN event_type='resolved' THEN security.authorization_risk('risk.resolve',r.id)
 WHEN event_type='suggestion_decided' THEN r.status<>'resolved' AND security.authorization_risk('risk.decide',r.id)
 ELSE security.authorization_risk_generation(r.customer_id,r.owner_user_ref_id) END)));
CREATE UNIQUE INDEX risk_suggestion_request_once ON insight.risk_event
 (workspace_id,risk_id,actor_user_ref_id,(payload->>'request_id'))
 WHERE event_type='suggestion_decided';
ALTER TABLE insight.risk_event ADD CONSTRAINT risk_suggestion_decision_payload CHECK (
 event_type<>'suggestion_decided' OR (
   payload->>'decision' IN ('adopt','defer') AND length(btrim(payload->>'note')) BETWEEN 1 AND 2000
   AND payload->>'request_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
   AND jsonb_typeof(payload->'request')='object' AND jsonb_typeof(payload->'evidence')='array'
   AND jsonb_typeof(payload->'risk_version_before')='number'
   AND jsonb_typeof(payload->'risk_version_after')='number'
   AND length(btrim(payload->>'suggested_action'))>0
   AND from_status=to_status AND to_status<>'resolved') IS TRUE);

INSERT INTO ops.schema_migration(version,description)
 VALUES('V377','Independent human risk suggestion decisions and leader-scoped resolution');
COMMIT;
