-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V180__closed_company_export.sql.
-- Original SHA-256: 8916c02e90d6b4ccb5e0b8a379c66cebbb40fd6ddf009e1a617127ef78692cf6. See shenma_whole_121_manifest.json.
BEGIN;
-- Closed companies keep the ordinary login, transaction and RLS guards. Only
-- these session-bound fixed projections can read portable business history.
CREATE FUNCTION security._authorization_administrator_for_state(p_workspace uuid,p_user uuid,p_state text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT p_state IN ('active','closed') AND EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
 JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
 WHERE u.id=p_user AND u.workspace_id=p_workspace AND u.status='active' AND u.deleted_at IS NULL
 AND w.status=p_state AND w.deleted_at IS NULL AND b.role_code='administrator'
 AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to);
$$;
CREATE OR REPLACE FUNCTION security._authorization_grants_for_state(p_workspace uuid, p_user uuid,p_state text)
 RETURNS TABLE(permission_code text, scope_code text, team_ids uuid[], source_code text, source_id text, effect text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 WITH authority AS MATERIALIZED (
  SELECT security._authorization_administrator_for_state(p_workspace,p_user,p_state) administrator,
   security.active_authorization_delegation(p_workspace,p_user) delegation
 ), available_permissions AS MATERIALIZED (
  SELECT p.permission_code FROM control.permission_module p LEFT JOIN control.tenant_module t
   ON t.workspace_id=p_workspace AND t.module_code=p.module_code
  GROUP BY p.permission_code HAVING bool_and(t.enabled IS TRUE AND (t.expires_at IS NULL OR t.expires_at>clock_timestamp()))
 ), person AS (
  SELECT u.id FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
  WHERE u.id=p_user AND u.workspace_id=p_workspace AND u.status='active' AND u.deleted_at IS NULL
   AND p_state IN ('active','closed') AND w.status=p_state AND w.deleted_at IS NULL
   AND EXISTS(SELECT 1 FROM platform.role_binding identity_role WHERE identity_role.workspace_id=p_workspace
    AND identity_role.user_ref_id=p_user AND clock_timestamp()>=identity_role.valid_from AND clock_timestamp()<identity_role.valid_to
    AND (identity_role.role_code IN ('manager','operations','administrator') OR EXISTS(
     SELECT 1 FROM security.account_team_scope(p_workspace,p_user,identity_role.role_code))))
 ), appointments AS (
  SELECT DISTINCT b.role_code,b.data_scope_code,
   ARRAY(SELECT s.team_id::uuid FROM security.account_team_scope(p_workspace,p_user,b.role_code) s) teams
  FROM platform.role_binding b JOIN person p ON p.id=b.user_ref_id
  WHERE b.workspace_id=p_workspace AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
   AND (b.role_code IN ('manager','operations','administrator') OR EXISTS(
     SELECT 1 FROM security.account_team_scope(p_workspace,p_user,b.role_code)))
 ), sources AS (
  SELECT r.id,r.version_no,r.builtin_role_code source_code,
   CASE a.data_scope_code WHEN 'team' THEN 'teams' ELSE a.data_scope_code END scope_code,a.teams
  FROM appointments a JOIN config.permission_role r ON r.workspace_id=p_workspace
   AND r.builtin_role_code=a.role_code AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  UNION ALL
  SELECT r.id,r.version_no,'assigned_role',a.scope_code,a.team_ids
  FROM config.account_permission_role a JOIN person p ON p.id=a.user_ref_id
  JOIN config.permission_role r ON r.id=a.role_id AND r.workspace_id=a.workspace_id AND r.status='active' AND (r.expires_at IS NULL OR r.expires_at>clock_timestamp())
  WHERE a.workspace_id=p_workspace AND (a.expires_at IS NULL OR a.expires_at>clock_timestamp())
   AND security.delegated_authority_valid(p_workspace,p_user,a.delegation_user_ref_id,a.delegation_version)
   AND (a.delegation_user_ref_id IS NULL OR a.approved_role_version=r.version_no)
 ), combined AS (
  SELECT g.permission_code,CASE g.scope_code WHEN 'inherit' THEN s.scope_code ELSE g.scope_code END scope_code,
   CASE WHEN g.scope_code='teams' THEN g.team_ids WHEN g.scope_code='inherit' AND s.scope_code='teams' THEN s.teams ELSE '{}'::uuid[] END teams,
   s.source_code,s.id::text source_id,'allow'::text effect
  FROM sources s JOIN config.permission_role_grant g ON g.role_id=s.id AND g.workspace_id=p_workspace
  WHERE NOT (s.source_code IN ('fde','fde_lead') AND s.version_no=1 AND g.permission_code IN
   ('visit.create','visit.supplement','visit.upload','visit.retry_import','visit.download_original','visit.structure','visit.quality_review'))
   OR security.authorization_legacy_fde_visit(p_workspace,p_user,s.source_code)
  UNION ALL
  SELECT o.permission_code,o.scope_code,o.team_ids,'account_override',p_user::text,o.effect
  FROM config.account_permission_override o JOIN person p ON p.id=o.user_ref_id WHERE o.workspace_id=p_workspace AND (o.expires_at IS NULL OR o.expires_at>clock_timestamp()) AND security.delegated_authority_valid(p_workspace,p_user,o.delegation_user_ref_id,o.delegation_version)
 ), valid AS (
  SELECT c.*,ARRAY(SELECT t.id FROM platform.team t WHERE t.id=ANY(c.teams) AND t.workspace_id=p_workspace
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to ORDER BY t.id) active_teams
  FROM combined c JOIN config.permission_catalog d ON d.code=c.permission_code
  WHERE c.effect='deny' OR c.scope_code=ANY(d.scopes)
 )
 SELECT v.permission_code,v.scope_code,v.active_teams,v.source_code,v.source_id,v.effect FROM valid v CROSS JOIN authority a
 WHERE (v.scope_code<>'teams' OR cardinality(v.active_teams)>0 OR v.effect='deny')
 AND (v.effect='deny' OR (v.permission_code IN (SELECT permission_code FROM available_permissions)
  AND (a.administrator OR v.permission_code NOT IN ('authorization.roles_manage','account.create','account.password_policy','organization.manage'))
  AND (a.administrator OR a.delegation IS NOT NULL OR v.permission_code NOT IN ('authorization.accounts_manage','account.update','account.reset_password','account.unlock'))));
$function$
;


-- One maintained grant algorithm; ordinary callers keep the exact active-only
-- contract. The state-aware helper is never executable by a runtime identity.
CREATE OR REPLACE FUNCTION security.authorization_grants_for(p_workspace uuid,p_user uuid)
RETURNS TABLE(permission_code text,scope_code text,team_ids uuid[],source_code text,source_id text,effect text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT * FROM security._authorization_grants_for_state(p_workspace,p_user,'active');
$$;
CREATE TABLE security.closed_export_session (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), token_hash text NOT NULL UNIQUE CHECK(token_hash ~ '^[a-f0-9]{64}$'),
 workspace_id uuid NOT NULL, user_id uuid NOT NULL,
 credential_fingerprint text NOT NULL CHECK(credential_fingerprint ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
 revoked_at timestamptz, FOREIGN KEY(user_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '15 minutes')
);
CREATE INDEX closed_export_session_user ON security.closed_export_session(workspace_id,user_id,expires_at);
-- A reopen/reclose cycle never revives a previously issued export capability.
CREATE FUNCTION security._closed_export_revoke_lifecycle() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.status IS DISTINCT FROM OLD.status OR NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
  UPDATE security.closed_export_session SET revoked_at=clock_timestamp()
   WHERE workspace_id=NEW.id AND revoked_at IS NULL;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER closed_export_revoke_lifecycle AFTER UPDATE OF status,deleted_at ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION security._closed_export_revoke_lifecycle();
CREATE TABLE security.closed_export_event (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 session_id uuid REFERENCES security.closed_export_session(id), workspace_id uuid NOT NULL,
 user_id uuid NOT NULL, export_id uuid, action text NOT NULL
 CHECK(action IN ('login','logout','prepared','sent','cancelled','failed','denied')),
 detail jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(user_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE INDEX closed_export_event_tenant ON security.closed_export_event(workspace_id,id DESC);
REVOKE ALL ON security.closed_export_session,security.closed_export_event FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON SEQUENCE security.closed_export_event_id_seq FROM PUBLIC,salegent_feishu_worker;
CREATE TRIGGER closed_export_event_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON security.closed_export_event
 FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();

-- Static projections generated from tenant_export_catalog.SECTIONS. Tests compare
-- every column/predicate; no identifier or SQL fragment is accepted from HTTP.
CREATE FUNCTION security._closed_export_catalog() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $catalog$ SELECT '{
  "platform.workspace": {
    "section": "organization",
    "columns": [
      "id",
      "external_workspace_id",
      "name",
      "status",
      "default_timezone",
      "version_no",
      "created_at",
      "updated_at"
    ],
    "predicate": "id=common.current_workspace_id()",
    "guard": "true"
  },
  "platform.user_ref": {
    "section": "organization",
    "columns": [
      "id",
      "workspace_id",
      "account_code",
      "display_name",
      "phone_number",
      "email",
      "status",
      "version_no",
      "created_at",
      "updated_at",
      "deleted_at"
    ],
    "predicate": "true",
    "guard": "true"
  },
  "platform.team": {
    "section": "organization",
    "columns": [
      "id",
      "workspace_id",
      "parent_team_id",
      "code",
      "name",
      "region_code",
      "status",
      "valid_from",
      "valid_to",
      "version_no",
      "created_at",
      "updated_at",
      "deleted_at"
    ],
    "predicate": "true",
    "guard": "true"
  },
  "platform.team_membership": {
    "section": "organization",
    "columns": [
      "id",
      "workspace_id",
      "team_id",
      "user_ref_id",
      "membership_role",
      "is_primary",
      "valid_from",
      "valid_to",
      "created_at",
      "updated_at"
    ],
    "predicate": "true",
    "guard": "true"
  },
  "platform.role_binding": {
    "section": "organization",
    "columns": [
      "id",
      "workspace_id",
      "user_ref_id",
      "role_code",
      "data_scope_code",
      "team_id",
      "valid_from",
      "valid_to",
      "created_at",
      "updated_at"
    ],
    "predicate": "true",
    "guard": "true"
  },
  "crm.customer": {
    "section": "customers",
    "columns": [
      "id",
      "workspace_id",
      "external_customer_id",
      "customer_code",
      "name",
      "industry_code",
      "customer_type_code",
      "source_code",
      "lifecycle_status",
      "level_code",
      "owner_user_ref_id",
      "owner_team_id",
      "primary_partner_name",
      "demand_summary",
      "data_source",
      "created_by_user_ref_id",
      "version_no",
      "created_at",
      "updated_at",
      "next_action",
      "operation_type",
      "cooperation_years",
      "main_business",
      "customer_budget",
      "data_kind",
      "company_reference",
      "company_verified_at",
      "company_verified_by"
    ],
    "predicate": "deleted_at IS NULL",
    "guard": "security.authorization_customer(''customer.read'',id)"
  },
  "crm.contact": {
    "section": "customers",
    "columns": [
      "id",
      "workspace_id",
      "customer_id",
      "name",
      "title",
      "department",
      "contact_category_code",
      "relationship_role_code",
      "phone",
      "email",
      "is_primary",
      "version_no",
      "created_at",
      "updated_at"
    ],
    "predicate": "deleted_at IS NULL AND customer_id IN (SELECT id FROM crm.customer WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_customer(''customer.read'',customer_id)"
  },
  "crm.customer_ownership": {
    "section": "customers",
    "columns": [
      "customer_id",
      "workspace_id",
      "owner_user_ref_id",
      "state",
      "version_no",
      "updated_at"
    ],
    "predicate": "customer_id IN (SELECT id FROM crm.customer WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_customer(''customer.read'',customer_id)"
  },
  "crm.customer_sales_member": {
    "section": "customers",
    "columns": [
      "workspace_id",
      "customer_id",
      "user_ref_id",
      "added_by_user_ref_id",
      "joined_at"
    ],
    "predicate": "customer_id IN (SELECT id FROM crm.customer WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_customer(''customer.read'',customer_id)"
  },
  "crm.opportunity": {
    "section": "opportunities",
    "columns": [
      "id",
      "workspace_id",
      "customer_id",
      "external_opportunity_id",
      "name",
      "stage_code",
      "amount",
      "currency",
      "probability",
      "expected_close_date",
      "owner_user_ref_id",
      "owner_team_id",
      "status",
      "source_code",
      "created_by_user_ref_id",
      "version_no",
      "created_at",
      "updated_at",
      "partner_name",
      "product_line",
      "closed_at",
      "follow_up_plan",
      "sales_channel",
      "partner_id",
      "original_owner_name",
      "ownership_resolution",
      "expected_close_year",
      "expected_close_quarter"
    ],
    "predicate": "deleted_at IS NULL",
    "guard": "security.authorization_opportunity(''opportunity.read'',id)"
  },
  "crm.opportunity_forecast": {
    "section": "opportunities",
    "columns": [
      "id",
      "workspace_id",
      "opportunity_id",
      "year",
      "quarter",
      "recognized_amount",
      "collection_amount",
      "updated_by_user_ref_id",
      "updated_at",
      "collection_confidence"
    ],
    "predicate": "opportunity_id IN (SELECT id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_opportunity(''opportunity.read'',opportunity_id)"
  },
  "crm.opportunity_participant": {
    "section": "opportunities",
    "columns": [
      "opportunity_id",
      "workspace_id",
      "user_ref_id",
      "participant_role",
      "valid_from",
      "valid_to",
      "created_at",
      "assigned_by_user_ref_id",
      "ended_by_user_ref_id",
      "source_code",
      "source_visit_id",
      "end_reason"
    ],
    "predicate": "opportunity_id IN (SELECT id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_opportunity(''opportunity.read'',opportunity_id)"
  },
  "crm.opportunity_related_partner": {
    "section": "opportunities",
    "columns": [
      "workspace_id",
      "opportunity_id",
      "partner_id",
      "created_by_user_ref_id",
      "created_at"
    ],
    "predicate": "opportunity_id IN (SELECT id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_opportunity(''opportunity.read'',opportunity_id)"
  },
  "crm.opportunity_quote_reference": {
    "section": "opportunities",
    "columns": [
      "id",
      "workspace_id",
      "opportunity_id",
      "reference_no",
      "title",
      "url",
      "amount",
      "created_by_user_ref_id",
      "created_at"
    ],
    "predicate": "opportunity_id IN (SELECT id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_opportunity(''opportunity.read'',opportunity_id)"
  },
  "activity.visit": {
    "section": "visits",
    "columns": [
      "id",
      "workspace_id",
      "customer_id",
      "opportunity_id",
      "recorder_user_ref_id",
      "recorder_team_id",
      "form_version_id",
      "status",
      "interaction_at",
      "interaction_mode_code",
      "visit_location",
      "duration_minutes",
      "expectation_code",
      "follow_up_record",
      "next_action",
      "partner_name_snapshot",
      "lead_source_snapshot",
      "contact_category_snapshot",
      "contact_title_snapshot",
      "contact_name_snapshot",
      "confirmed_by_user_ref_id",
      "confirmed_at",
      "archived_at",
      "version_no",
      "created_at",
      "updated_at",
      "is_first_visit",
      "follow_up_score",
      "first_visit_profile",
      "visit_goal",
      "source_import_id",
      "customer_type_code_snapshot",
      "created_by_user_ref_id",
      "recorded_on",
      "archived_fields",
      "recording_role_code_snapshot",
      "recording_team_id_snapshot",
      "follow_up_task_mode",
      "partner_id",
      "original_recorder_name",
      "manager_user_ref_id"
    ],
    "predicate": "deleted_at IS NULL AND status IN (''confirmed'',''archived'')",
    "guard": "security.authorization_visit(''visit.read'',id)"
  },
  "activity.visit_field_value": {
    "section": "visits",
    "columns": [
      "visit_id",
      "workspace_id",
      "field_definition_id",
      "value_json",
      "value_text",
      "source_type",
      "confidence",
      "is_confirmed",
      "evidence",
      "edited_at",
      "created_at",
      "updated_at"
    ],
    "predicate": "visit_id IN (SELECT id FROM activity.visit WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND status IN (''confirmed'',''archived''))",
    "guard": "security.authorization_visit(''visit.read'',visit_id)"
  },
  "activity.visit_participant": {
    "section": "visits",
    "columns": [
      "visit_id",
      "workspace_id",
      "user_ref_id",
      "participant_role",
      "team_id_at_event",
      "role_code_at_event",
      "created_by_user_ref_id",
      "created_at",
      "updated_at"
    ],
    "predicate": "visit_id IN (SELECT id FROM activity.visit WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND status IN (''confirmed'',''archived''))",
    "guard": "security.authorization_visit(''visit.read'',visit_id)"
  },
  "activity.visit_opportunity": {
    "section": "visits",
    "columns": [
      "visit_id",
      "workspace_id",
      "opportunity_id",
      "created_by_user_ref_id",
      "created_at"
    ],
    "predicate": "visit_id IN (SELECT id FROM activity.visit WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND status IN (''confirmed'',''archived''))",
    "guard": "security.authorization_visit(''visit.read'',visit_id)"
  },
  "workflow.task": {
    "section": "tasks",
    "columns": [
      "id",
      "workspace_id",
      "task_type",
      "title",
      "description",
      "customer_id",
      "opportunity_id",
      "source_visit_id",
      "creator_user_ref_id",
      "creator_team_id",
      "priority_code",
      "status",
      "due_at",
      "accepted_at",
      "completed_at",
      "completion_note",
      "verification_status",
      "version_no",
      "created_at",
      "updated_at",
      "rejection_comment",
      "source_code",
      "agent_reason",
      "source_follow_up_record",
      "source_interaction_at",
      "target_position",
      "source_suggestion_id",
      "association_kind",
      "completion_submitted_at",
      "completion_review_note"
    ],
    "predicate": "deleted_at IS NULL",
    "guard": "security.authorization_task(''task.read'',id)"
  },
  "workflow.task_assignee": {
    "section": "tasks",
    "columns": [
      "task_id",
      "workspace_id",
      "assignee_user_ref_id",
      "assignee_team_id",
      "assignee_role",
      "responsibility",
      "assigned_at",
      "accepted_at"
    ],
    "predicate": "task_id IN (SELECT id FROM workflow.task WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_task(''task.read'',task_id)"
  },
  "workflow.task_event": {
    "section": "tasks",
    "columns": [
      "id",
      "workspace_id",
      "task_id",
      "event_type",
      "from_status",
      "to_status",
      "actor_user_ref_id",
      "note",
      "occurred_at"
    ],
    "predicate": "task_id IN (SELECT id FROM workflow.task WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_task(''task.read'',task_id)"
  },
  "crm.customer_actual": {
    "section": "actuals",
    "columns": [
      "id",
      "workspace_id",
      "customer_id",
      "opportunity_id",
      "kind",
      "amount",
      "occurred_on",
      "source_ref",
      "note",
      "confirmed_by_user_ref_id",
      "created_at",
      "voided_at",
      "voided_by_user_ref_id",
      "void_reason"
    ],
    "predicate": "true",
    "guard": "CASE WHEN opportunity_id IS NULL THEN security.authorization_customer(''actual.read'',customer_id) ELSE security.authorization_opportunity(''actual.read'',opportunity_id) END"
  },
  "crm.sales_target": {
    "section": "targets",
    "columns": [
      "id",
      "workspace_id",
      "target_year",
      "scope_type",
      "user_ref_id",
      "team_id",
      "kind",
      "amount",
      "updated_by_user_ref_id",
      "created_at",
      "updated_at",
      "version_no",
      "period_type",
      "period_start",
      "period_end",
      "department_code",
      "change_reason",
      "source"
    ],
    "predicate": "true",
    "guard": "true"
  },
  "crm.partner": {
    "section": "partners",
    "columns": [
      "id",
      "workspace_id",
      "name",
      "status",
      "version_no",
      "created_by_user_ref_id",
      "updated_by_user_ref_id",
      "created_at",
      "updated_at",
      "short_name",
      "principal_name",
      "channel_manager_user_ref_id",
      "original_channel_manager_name",
      "priority",
      "progress",
      "grade",
      "signed_on",
      "partner_type",
      "region",
      "province",
      "note"
    ],
    "predicate": "true",
    "guard": "true"
  }
}'::jsonb; $catalog$;
CREATE FUNCTION security._closed_export_sections() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $catalog$ SELECT '{"organization": ["organization.read"], "customers": ["customer.read", "customer.export"], "opportunities": ["opportunity.read", "opportunity.export"], "visits": ["visit.read"], "tasks": ["task.read"], "actuals": ["actual.read"], "targets": ["target.read"], "partners": ["partner.read"]}'::jsonb; $catalog$;

CREATE FUNCTION security._closed_export_has(p_profile jsonb,p_code text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM jsonb_array_elements(p_profile->'grants') g
  WHERE g->>'permission_code'=p_code AND g->>'scope_code'='workspace' AND g->>'effect'='allow')
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_profile->'grants') g
  WHERE g->>'permission_code'=p_code AND g->>'effect'='deny');
$$;
CREATE FUNCTION security._closed_export_person(p_workspace uuid,p_user uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('workspace_id',w.id,'user_id',u.id,'company_code',w.external_workspace_id,
  'company_name',w.name,'account_code',u.account_code,'display_name',u.display_name,
  'role','administrator','data_scope','workspace','team_ids','[]'::jsonb,
  'password_hash',p.password_hash,'must_change_password',
  p.must_change_password AND COALESCE(policy.require_initial_change,true),
  'grants',(SELECT COALESCE(jsonb_agg(to_jsonb(g) ORDER BY g.permission_code,g.effect,g.source_id,g.scope_code,g.team_ids),'[]')
   FROM security._authorization_grants_for_state(w.id,u.id,'closed') g
   WHERE g.permission_code IN ('company.export','access.console','visit.download_original')
    OR g.permission_code IN (SELECT jsonb_array_elements_text(value) FROM jsonb_each(security._closed_export_sections()))))
 FROM platform.workspace w JOIN platform.user_ref u ON u.workspace_id=w.id
 JOIN platform.password_credential p ON p.workspace_id=u.workspace_id AND p.user_ref_id=u.id
 LEFT JOIN security.password_policy policy ON policy.workspace_id=w.id
 WHERE w.id=p_workspace AND u.id=p_user AND w.status='closed' AND w.deleted_at IS NULL
 AND u.status='active' AND u.deleted_at IS NULL AND COALESCE(u.attributes->>'platform_managed','false')<>'true'
 AND security._authorization_administrator_for_state(w.id,u.id,'closed');
$$;
CREATE FUNCTION security.closed_export_candidate(p_company text,p_identifier text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH person AS MATERIALIZED (
  SELECT security._closed_export_person(w.id,u.id) value
  FROM platform.workspace w JOIN platform.user_ref u ON u.workspace_id=w.id
  WHERE lower(w.external_workspace_id)=lower(btrim(p_company)) AND w.status='closed' AND w.deleted_at IS NULL
  AND (u.account_code=upper(btrim(p_identifier)) OR u.phone_number=btrim(p_identifier))
 ), eligible AS (
  SELECT value FROM person WHERE value IS NOT NULL AND (value->>'must_change_password')::boolean IS FALSE
  AND security._closed_export_has(value,'company.export') AND security._closed_export_has(value,'access.console')
 ) SELECT value-'grants'-'role'-'data_scope'-'team_ids' FROM eligible WHERE (SELECT count(*) FROM eligible)=1;
$$;
CREATE FUNCTION security._closed_export_profile(p_hash text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s security.closed_export_session%ROWTYPE; person jsonb;
BEGIN
 IF p_hash IS NULL OR p_hash !~ '^[a-f0-9]{64}$' THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 SELECT * INTO s FROM security.closed_export_session WHERE token_hash=p_hash AND revoked_at IS NULL
  AND expires_at>clock_timestamp();
 IF NOT FOUND THEN RAISE EXCEPTION 'CLOSED_EXPORT_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 person:=security._closed_export_person(s.workspace_id,s.user_id);
 IF person IS NULL OR (person->>'must_change_password')::boolean IS NOT FALSE
 OR NOT security._closed_export_has(person,'company.export') OR NOT security._closed_export_has(person,'access.console') THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_DENIED' USING ERRCODE='P7403'; END IF;
 IF encode(public.digest(person->>'password_hash','sha256'),'hex') IS DISTINCT FROM s.credential_fingerprint THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 RETURN (person-'password_hash'-'must_change_password')||jsonb_build_object('session_id',s.id,'expires_at',s.expires_at);
END $$;
CREATE FUNCTION security.closed_export_status(p_session_hash text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security._closed_export_profile(p_session_hash);
$$;
CREATE FUNCTION security.closed_export_issue(p_workspace uuid,p_user uuid,p_identifier text,p_observed_hash text,
 p_session_hash text,p_attempt_key text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE person jsonb; sid uuid; created timestamptz:=clock_timestamp(); key text;
BEGIN
 IF p_session_hash IS NULL OR p_session_hash !~ '^[a-f0-9]{64}$' THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 PERFORM 1 FROM platform.workspace WHERE id=p_workspace AND status='closed' AND deleted_at IS NULL FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'CLOSED_EXPORT_DENIED' USING ERRCODE='P7403'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||p_workspace::text,0));
 PERFORM 1 FROM platform.user_ref u JOIN platform.password_credential p
  ON p.user_ref_id=u.id AND p.workspace_id=u.workspace_id
 WHERE u.id=p_user AND u.workspace_id=p_workspace AND p.password_hash=p_observed_hash
 AND (u.account_code=upper(btrim(p_identifier)) OR u.phone_number=btrim(p_identifier)) FOR SHARE OF u,p;
 IF NOT FOUND THEN RAISE EXCEPTION 'CLOSED_EXPORT_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 person:=security._closed_export_person(p_workspace,p_user);
 IF person IS NULL OR (person->>'must_change_password')::boolean IS NOT FALSE
 OR NOT security._closed_export_has(person,'company.export') OR NOT security._closed_export_has(person,'access.console') THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_DENIED' USING ERRCODE='P7403'; END IF;
 key:=encode(public.digest('closed-export-account:'||(person->>'company_code')||':'||(person->>'account_code'),'sha256'),'hex');
 IF key IS DISTINCT FROM p_attempt_key THEN RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 INSERT INTO security.closed_export_session(token_hash,workspace_id,user_id,credential_fingerprint,created_at,expires_at)
 VALUES(p_session_hash,p_workspace,p_user,encode(public.digest(p_observed_hash,'sha256'),'hex'),created,created+interval '15 minutes')
 RETURNING id INTO sid;
 INSERT INTO security.closed_export_event(session_id,workspace_id,user_id,action) VALUES(sid,p_workspace,p_user,'login');
 DELETE FROM security.login_throttle WHERE key_hash=key;
 RETURN security._closed_export_profile(p_session_hash);
END $$;
CREATE FUNCTION security.closed_export_logout(p_session_hash text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s security.closed_export_session%ROWTYPE;
BEGIN
 UPDATE security.closed_export_session SET revoked_at=clock_timestamp()
 WHERE token_hash=p_session_hash AND revoked_at IS NULL RETURNING * INTO s;
 IF FOUND THEN INSERT INTO security.closed_export_event(session_id,workspace_id,user_id,action)
  VALUES(s.id,s.workspace_id,s.user_id,'logout'); END IF;
END $$;
CREATE FUNCTION security.closed_export_authorize(p_session_hash text,p_sections text[],p_originals boolean) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE person jsonb:=security._closed_export_profile(p_session_hash); section text; code text;
BEGIN
 IF p_sections IS NULL OR cardinality(p_sections) NOT BETWEEN 1 AND 8 OR array_position(p_sections,NULL) IS NOT NULL
 OR cardinality(p_sections)<>(SELECT count(DISTINCT x) FROM unnest(p_sections) x)
 OR p_originals IS NULL OR (p_originals AND NOT 'visits'=ANY(p_sections)) THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 FOREACH section IN ARRAY p_sections LOOP
  IF NOT security._closed_export_sections() ? section THEN
   RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
  FOR code IN SELECT jsonb_array_elements_text(security._closed_export_sections()->section) LOOP
   IF NOT security._closed_export_has(person,code) THEN
    RAISE EXCEPTION 'CLOSED_EXPORT_DENIED' USING ERRCODE='P7403'; END IF;
  END LOOP;
 END LOOP;
 IF p_originals AND NOT security._closed_export_has(person,'visit.download_original') THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_DENIED' USING ERRCODE='P7403'; END IF;
 RETURN person;
END $$;
-- Object permission helpers are reused only inside the narrow definer read.
-- Restore every changed setting before returning; callers acquire no new GUC authority.
CREATE FUNCTION security._closed_export_context(p_profile jsonb) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE prior jsonb:='{}'; key text;
BEGIN
 FOREACH key IN ARRAY ARRAY['app.workspace_id','app.user_ref_id','app.role_code','app.team_ids',
  'app.authorization_context','app.authorized_feature','app.job_id'] LOOP
  prior:=prior||jsonb_build_object(key,current_setting(key,true));
 END LOOP;
 PERFORM set_config('app.workspace_id',p_profile->>'workspace_id',true),
  set_config('app.user_ref_id',p_profile->>'user_id',true),set_config('app.role_code','administrator',true),
  set_config('app.team_ids','',true),set_config('app.authorized_feature','',true),set_config('app.job_id','',true),
  set_config('app.authorization_context',jsonb_build_object('workspace_id',p_profile->>'workspace_id',
    'user_id',p_profile->>'user_id','grants',p_profile->'grants')::text,true);
 RETURN prior;
END $$;
CREATE FUNCTION security._closed_export_restore(p_prior jsonb) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE item record;
BEGIN
 FOR item IN SELECT * FROM jsonb_each_text(p_prior) LOOP PERFORM set_config(item.key,COALESCE(item.value,''),true); END LOOP;
END $$;
CREATE FUNCTION security._closed_export_page(p_offset integer,p_limit integer) RETURNS void
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 IF p_offset IS NULL OR p_offset NOT BETWEEN 0 AND 1000000 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
END $$;
CREATE FUNCTION security.closed_export_rows(p_session_hash text,p_table_code text,p_offset integer,p_limit integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE entry jsonb:=security._closed_export_catalog()->p_table_code; person jsonb; previous jsonb;
 relation regclass; projection text; ordering text; tenant text; result jsonb; column_count integer;
BEGIN
 PERFORM security._closed_export_page(p_offset,p_limit);
 IF entry IS NULL THEN RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 person:=security.closed_export_authorize(p_session_hash,ARRAY[entry->>'section'],false);
 relation:=p_table_code::regclass;
 -- Catalog columns only. NUMERIC must remain decimal strings (the existing
 -- Python export serializes Decimal as str); JSON float round-tripping is forbidden.
 SELECT string_agg(CASE WHEN a.atttypid='numeric'::regtype
  THEN format('%I::text AS %I',name,name) ELSE format('%I',name) END,',' ORDER BY ordinal),count(*)
 INTO projection,column_count FROM jsonb_array_elements_text(entry->'columns') WITH ORDINALITY c(name,ordinal)
 JOIN pg_attribute a ON a.attrelid=relation AND a.attname=c.name AND a.attnum>0 AND NOT a.attisdropped;
 IF column_count<>jsonb_array_length(entry->'columns') THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_CATALOG_MISMATCH' USING ERRCODE='P7403'; END IF;
 SELECT string_agg(format('%I',a.attname),',' ORDER BY k.ordinal) INTO ordering
 FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ordinal)
 JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
 WHERE i.indrelid=relation AND i.indisprimary;
 IF ordering IS NULL THEN RAISE EXCEPTION 'CLOSED_EXPORT_CATALOG_MISMATCH' USING ERRCODE='P7403'; END IF;
 tenant:=CASE WHEN p_table_code='platform.workspace' THEN 'id' ELSE 'workspace_id' END;
 previous:=security._closed_export_context(person);
 EXECUTE format('SELECT COALESCE(jsonb_agg(to_jsonb(selected)),''[]''::jsonb) FROM (SELECT %s FROM %s '
  'WHERE %I=$1 AND (%s) AND (%s) ORDER BY %s OFFSET $2 LIMIT $3) selected',
  projection,relation,tenant,entry->>'predicate',entry->>'guard',ordering)
 INTO result USING (person->>'workspace_id')::uuid,p_offset,p_limit;
 PERFORM security._closed_export_restore(previous);
 -- Time expiry is checked after bounded work as well as before it. The API must
 -- additionally re-authorize using a fresh snapshot before sending any package.
 PERFORM security.closed_export_authorize(p_session_hash,ARRAY[entry->>'section'],false);
 RETURN jsonb_build_object('table',p_table_code,'items',result,'next_offset',
  CASE WHEN jsonb_array_length(result)=p_limit THEN p_offset+p_limit ELSE NULL END);
END $$;

CREATE FUNCTION security.closed_export_originals(p_session_hash text,p_originals boolean,p_offset integer,p_limit integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE person jsonb; previous jsonb; result jsonb;
BEGIN
 PERFORM security._closed_export_page(p_offset,p_limit);
 person:=security.closed_export_authorize(p_session_hash,ARRAY['visits'],p_originals);
 previous:=security._closed_export_context(person);
 SELECT COALESCE(jsonb_agg(to_jsonb(selected)),'[]') INTO result FROM (
  SELECT i.id,i.workspace_id,i.filename,i.file_size,
   CASE WHEN p_originals THEN i.file_path ELSE '' END file_path,
   CASE WHEN p_originals THEN i.storage_profile END storage_profile,
   CASE WHEN p_originals THEN i.storage_driver END storage_driver,
   CASE WHEN p_originals THEN i.storage_key END storage_key,i.content_sha256,
   ARRAY(SELECT v.id FROM activity.visit v WHERE v.workspace_id=i.workspace_id AND v.source_import_id=i.id
    AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND security.authorization_visit('visit.read',v.id)
    ORDER BY v.id) visit_ids
  FROM activity.visit_import i WHERE i.workspace_id=(person->>'workspace_id')::uuid
  AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.workspace_id=i.workspace_id AND v.source_import_id=i.id
   AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND security.authorization_visit('visit.read',v.id))
  ORDER BY i.id OFFSET p_offset LIMIT p_limit
 ) selected;
 PERFORM security._closed_export_restore(previous);
 PERFORM security.closed_export_authorize(p_session_hash,ARRAY['visits'],p_originals);
 RETURN jsonb_build_object('items',result,'next_offset',CASE WHEN jsonb_array_length(result)=p_limit THEN p_offset+p_limit ELSE NULL END);
END $$;
CREATE FUNCTION security.closed_export_content_chunk(p_session_hash text,p_import uuid,p_chunk integer)
RETURNS TABLE(chunk_no integer,content bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE person jsonb; previous jsonb; imported activity.visit_import%ROWTYPE;
BEGIN
 IF p_import IS NULL OR p_chunk IS NULL OR p_chunk NOT BETWEEN 0 AND 104857600 THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 person:=security.closed_export_authorize(p_session_hash,ARRAY['visits'],true);
 previous:=security._closed_export_context(person);
 SELECT * INTO imported FROM activity.visit_import i WHERE i.id=p_import AND i.workspace_id=(person->>'workspace_id')::uuid
 AND i.storage_driver='postgres' AND i.file_size BETWEEN 1 AND 104857600
 AND i.storage_key=i.workspace_id::text||'/'||i.id::text
 AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.workspace_id=i.workspace_id AND v.source_import_id=i.id
  AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND security.authorization_visit('visit.read',v.id));
 IF NOT FOUND THEN RAISE EXCEPTION 'CLOSED_EXPORT_DENIED' USING ERRCODE='P7403'; END IF;
 -- Historical imports may use blocks smaller than 1 MiB. Each nonempty block
 -- consumes at least one registered byte; allow the final EOF probe as well.
 IF p_chunk>imported.file_size THEN RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 RETURN QUERY SELECT c.chunk_no,c.content FROM activity.visit_import_content c
 -- Return the next actual index so the shared reader detects a missing block
 -- followed by later/trailing content instead of mistaking the gap for EOF.
 WHERE c.import_id=p_import AND c.workspace_id=imported.workspace_id AND c.chunk_no>=p_chunk
 ORDER BY c.chunk_no LIMIT 1;
 PERFORM security._closed_export_restore(previous);
 PERFORM security.closed_export_authorize(p_session_hash,ARRAY['visits'],true);
END $$;
CREATE FUNCTION security.closed_export_event(p_session_hash text,p_export uuid,p_action text,p_detail jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s security.closed_export_session%ROWTYPE; code text;
BEGIN
 IF p_export IS NULL OR p_action IS NULL OR p_action NOT IN ('prepared','sent','cancelled','failed','denied')
 OR jsonb_typeof(p_detail) IS DISTINCT FROM 'object' OR octet_length(p_detail::text)>4096
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_detail) k WHERE k NOT IN
  ('sections','include_originals','reason','sha256','record_count','error_code')) THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 IF p_detail ? 'sections' AND (jsonb_typeof(p_detail->'sections') IS DISTINCT FROM 'array'
  OR jsonb_array_length(p_detail->'sections') NOT BETWEEN 1 AND 8) THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 IF p_detail ? 'sections' THEN
  FOR code IN SELECT jsonb_array_elements_text(p_detail->'sections') LOOP
   IF code IS NULL OR NOT security._closed_export_sections() ? code THEN
    RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
  END LOOP;
 END IF;
 IF (p_detail ? 'include_originals' AND jsonb_typeof(p_detail->'include_originals') IS DISTINCT FROM 'boolean')
 OR (p_detail ? 'sha256' AND (jsonb_typeof(p_detail->'sha256') IS DISTINCT FROM 'string' OR p_detail->>'sha256' !~ '^[a-f0-9]{64}$'))
 OR (p_detail ? 'reason' AND (jsonb_typeof(p_detail->'reason') IS DISTINCT FROM 'string' OR length(btrim(p_detail->>'reason')) NOT BETWEEN 3 AND 500))
 OR (p_detail ? 'error_code' AND (jsonb_typeof(p_detail->'error_code') IS DISTINCT FROM 'string' OR p_detail->>'error_code' !~ '^[A-Z][A-Z0-9_]{0,79}$'))
 OR (p_detail ? 'record_count' AND (jsonb_typeof(p_detail->'record_count') IS DISTINCT FROM 'number'
  OR p_detail->>'record_count' !~ '^[0-9]{1,7}$' OR (p_detail->>'record_count')::numeric>1000000)) THEN
  RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
 SELECT * INTO s FROM security.closed_export_session WHERE token_hash=p_session_hash;
 IF NOT FOUND THEN RAISE EXCEPTION 'CLOSED_EXPORT_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 IF p_action IN ('prepared','sent') THEN
  IF NOT p_detail ?& ARRAY['sections','include_originals','reason','sha256','record_count'] THEN
   RAISE EXCEPTION 'CLOSED_EXPORT_INVALID_REQUEST' USING ERRCODE='P7422'; END IF;
  PERFORM security.closed_export_authorize(p_session_hash,
   ARRAY(SELECT jsonb_array_elements_text(p_detail->'sections')),(p_detail->>'include_originals')::boolean);
 END IF;
 INSERT INTO security.closed_export_event(session_id,workspace_id,user_id,export_id,action,detail)
 VALUES(s.id,s.workspace_id,s.user_id,p_export,p_action,p_detail);
END $$;

-- The private algorithm and context helpers remain invisible to runtime users.
-- APIs receive only the reviewed facade. No ordinary business RLS is altered.
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT oid::regprocedure signature FROM pg_proc WHERE pronamespace='security'::regnamespace
 AND (proname LIKE 'closed_export_%' OR proname LIKE '_closed_export_%'
      OR proname IN ('_authorization_grants_for_state','_authorization_administrator_for_state')) LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,salegent_feishu_worker',f.signature);
 END LOOP;
END $$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_closed_export;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record; f record; is_api boolean;
BEGIN
 total:=security.reconcile_runtime_grants_before_closed_export();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE') LOOP
  is_api:=has_function_privilege(r.rolname,'security.save_account_authorization(uuid,jsonb)','EXECUTE');
  EXECUTE format('REVOKE ALL ON security.closed_export_session,security.closed_export_event FROM %I',r.rolname);
  EXECUTE format('REVOKE ALL ON SEQUENCE security.closed_export_event_id_seq FROM %I',r.rolname);
  FOR f IN SELECT oid::regprocedure signature,proname FROM pg_proc WHERE pronamespace='security'::regnamespace
   AND (proname LIKE 'closed_export_%' OR proname LIKE '_closed_export_%'
    OR proname IN ('_authorization_grants_for_state','_authorization_administrator_for_state','reconcile_runtime_grants_before_closed_export')) LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',f.signature,r.rolname);
   IF is_api AND f.proname IN ('closed_export_candidate','closed_export_issue','closed_export_status',
    'closed_export_logout','closed_export_authorize','closed_export_rows','closed_export_originals',
    'closed_export_content_chunk','closed_export_event') THEN
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',f.signature,r.rolname);
   END IF;
  END LOOP;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_closed_export()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V328','Closed-company administrator-only bounded business export');
COMMIT;
