BEGIN;
SET LOCAL check_function_bodies = on;
SET LOCAL search_path = pg_catalog, public;
-- FDE is an independent business identity. Customer panorama READ and commercial
-- WRITE are deliberately different capabilities; existing sales grants are preserved.
ALTER TABLE platform.role_binding DROP CONSTRAINT role_binding_role_code_check;
ALTER TABLE platform.role_binding ADD CONSTRAINT role_binding_role_code_check
 CHECK(role_code IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead'));
ALTER TABLE platform.role_binding ADD CONSTRAINT fde_role_scope_check
 CHECK((role_code<>'fde' OR data_scope_code='self') AND (role_code<>'fde_lead' OR data_scope_code='team'));
ALTER TABLE platform.team_membership DROP CONSTRAINT team_membership_membership_role_check;
ALTER TABLE platform.team_membership ADD CONSTRAINT team_membership_membership_role_check
 CHECK(membership_role IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead'));
ALTER TABLE workflow.task_candidate DROP CONSTRAINT task_candidate_role_code_check;
ALTER TABLE workflow.task_candidate ADD CONSTRAINT task_candidate_role_code_check
 CHECK(role_code IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead'));
ALTER TABLE workflow.task_assignee DROP CONSTRAINT task_assignee_assignee_role_check;
ALTER TABLE workflow.task_assignee ADD CONSTRAINT task_assignee_assignee_role_check
 CHECK(assignee_role IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead'));
ALTER TABLE ops.ai_usage_rule DROP CONSTRAINT ai_usage_rule_role_code_check;
ALTER TABLE ops.ai_usage_rule ADD CONSTRAINT ai_usage_rule_role_code_check
 CHECK(role_code IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead'));
ALTER TABLE workflow.task DROP CONSTRAINT task_target_position_check;
ALTER TABLE workflow.task ADD CONSTRAINT task_target_position_check
 CHECK(target_position IN ('self','supervisor','manager','operations','fde','fde_lead'));

ALTER TABLE crm.opportunity_participant ADD COLUMN assigned_by_user_ref_id uuid REFERENCES platform.user_ref(id);
ALTER TABLE crm.opportunity_participant ADD COLUMN ended_by_user_ref_id uuid REFERENCES platform.user_ref(id);
ALTER TABLE crm.opportunity_participant ADD COLUMN source_code text NOT NULL DEFAULT 'legacy'
 CHECK(source_code IN ('legacy','manual','visit_archive'));
ALTER TABLE crm.opportunity_participant ADD COLUMN source_visit_id uuid;
ALTER TABLE crm.opportunity_participant ADD COLUMN end_reason text;
ALTER TABLE crm.opportunity_participant ADD CONSTRAINT participant_opportunity_workspace_fk
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id);
ALTER TABLE crm.opportunity_participant ADD CONSTRAINT participant_user_workspace_fk
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id);
ALTER TABLE crm.opportunity_participant ADD CONSTRAINT participant_visit_workspace_fk
 FOREIGN KEY(source_visit_id,workspace_id) REFERENCES activity.visit(id,workspace_id);
CREATE INDEX opportunity_participant_effective_user ON crm.opportunity_participant(workspace_id,user_ref_id,valid_to,opportunity_id);
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;
-- The interval exclusion
-- also rejects overlapping historical periods, not just two open-ended rows.
ALTER TABLE crm.opportunity_participant ADD CONSTRAINT opportunity_participant_no_overlap
 EXCLUDE USING gist(workspace_id WITH =,opportunity_id WITH =,user_ref_id WITH =,
 tstzrange(valid_from,valid_to,'[)') WITH &&);
COMMENT ON TABLE crm.opportunity_participant IS '商机长期协助关系；有效期不重叠，FDE关联授予客户全景读取，不授予商业编辑或业绩分成';

ALTER TABLE platform.team ADD CONSTRAINT team_id_workspace_key UNIQUE(id,workspace_id);
CREATE TABLE activity.visit_participant (
 visit_id uuid NOT NULL,workspace_id uuid NOT NULL,user_ref_id uuid NOT NULL,
 participant_role text NOT NULL DEFAULT 'collaborator' CHECK(participant_role IN ('collaborator','fde')),
 team_id_at_event uuid,role_code_at_event text,
 created_by_user_ref_id uuid REFERENCES platform.user_ref(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(visit_id,user_ref_id),
 FOREIGN KEY(visit_id,workspace_id) REFERENCES activity.visit(id,workspace_id),
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(team_id_at_event,workspace_id) REFERENCES platform.team(id,workspace_id),
 CHECK(role_code_at_event IS NULL OR role_code_at_event IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead'))
);
COMMENT ON TABLE activity.visit_participant IS '本次拜访的内部实际参与事实；与外部联系人及长期商机关联分开，历史岗位未知时保持空值';
INSERT INTO activity.visit_participant(visit_id,workspace_id,user_ref_id,participant_role,created_by_user_ref_id,created_at,updated_at)
 SELECT DISTINCT v.id,v.workspace_id,person,'collaborator',v.created_by_user_ref_id,v.created_at,v.created_at
 FROM activity.visit v CROSS JOIN LATERAL unnest(v.collaborator_user_ref_ids) person;
-- There is one writable source. Old response shapes can project this view, never
-- dual-write an independent array. Historical entries are NOT inferred to be FDE.
ALTER TABLE activity.visit DROP COLUMN collaborator_user_ref_ids;
ALTER TABLE activity.visit_participant ENABLE ROW LEVEL SECURITY;
CREATE INDEX visit_participant_user_event ON activity.visit_participant(workspace_id,user_ref_id,visit_id);
CREATE VIEW activity.v_visit_collaborators WITH(security_invoker=true) AS
 SELECT visit_id,workspace_id,array_agg(user_ref_id ORDER BY user_ref_id) AS collaborator_user_ref_ids
 FROM activity.visit_participant GROUP BY visit_id,workspace_id;

CREATE FUNCTION security.fde_user_is_active(p_user uuid,p_role text DEFAULT NULL,p_team uuid DEFAULT NULL) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding rb
 ON rb.user_ref_id=u.id AND rb.workspace_id=u.workspace_id
 JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
 JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id AND t.deleted_at IS NULL AND t.status='active'
 AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
 WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL
 AND rb.role_code IN ('fde','fde_lead') AND (p_role IS NULL OR rb.role_code=p_role)
 AND tm.membership_role IN ('fde','fde_lead') AND (rb.role_code<>'fde_lead' OR tm.membership_role='fde_lead') AND (p_team IS NULL OR tm.team_id=p_team)
 AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
 AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to);
 $$;
CREATE FUNCTION security.is_fde_actor() RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_role_code() IN ('fde','fde_lead')
 AND security.fde_user_is_active(common.current_user_ref_id(),common.current_role_code());
 $$;
CREATE FUNCTION security.fde_manages_user(p_user uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_role_code()='fde_lead' AND security.is_fde_actor()
 AND EXISTS(SELECT 1 FROM platform.team_membership mine JOIN platform.role_binding rb
 ON rb.user_ref_id=mine.user_ref_id AND rb.workspace_id=mine.workspace_id AND rb.role_code='fde_lead'
 WHERE mine.workspace_id=common.current_workspace_id() AND mine.user_ref_id=common.current_user_ref_id()
 AND mine.membership_role='fde_lead' AND (rb.team_id IS NULL OR rb.team_id=mine.team_id)
 AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',mine.team_id)
 AND clock_timestamp()>=mine.valid_from AND clock_timestamp()<mine.valid_to
 AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
 AND security.fde_user_is_active(p_user,NULL,mine.team_id));
 $$;
CREATE FUNCTION security.fde_opportunity_in_scope(p_opportunity uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.is_fde_actor() AND EXISTS(SELECT 1 FROM crm.opportunity_participant p
 JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id
 WHERE p.workspace_id=common.current_workspace_id() AND p.opportunity_id=p_opportunity AND o.deleted_at IS NULL
 AND p.participant_role='fde' AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 AND security.fde_user_is_active(p.user_ref_id)
 AND (p.user_ref_id=common.current_user_ref_id() OR security.fde_manages_user(p.user_ref_id)));
 $$;
CREATE FUNCTION security.fde_customer_in_scope(p_customer uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.is_fde_actor() AND EXISTS(SELECT 1 FROM crm.opportunity o
 WHERE o.workspace_id=common.current_workspace_id() AND o.customer_id=p_customer AND o.deleted_at IS NULL
 AND security.fde_opportunity_in_scope(o.id));
 $$;
CREATE FUNCTION security.fde_user_has_opportunity_access(p_user uuid,p_opportunity uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.fde_user_is_active(p_user) AND EXISTS(SELECT 1 FROM crm.opportunity target
 JOIN crm.opportunity source ON source.customer_id=target.customer_id AND source.workspace_id=target.workspace_id
 JOIN crm.opportunity_participant p ON p.opportunity_id=source.id AND p.workspace_id=source.workspace_id
 WHERE target.id=p_opportunity AND target.workspace_id=common.current_workspace_id()
 AND target.deleted_at IS NULL AND source.deleted_at IS NULL AND p.participant_role='fde'
 AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 AND security.fde_user_is_active(p.user_ref_id)
 AND (p.user_ref_id=p_user OR EXISTS(SELECT 1 FROM platform.role_binding lead
 JOIN platform.team_membership lt ON lt.user_ref_id=lead.user_ref_id AND lt.workspace_id=lead.workspace_id
 JOIN platform.team_membership member ON member.team_id=lt.team_id AND member.workspace_id=lt.workspace_id
 WHERE lead.user_ref_id=p_user AND lead.workspace_id=target.workspace_id AND lead.role_code='fde_lead'
 AND lt.membership_role='fde_lead' AND (lead.team_id IS NULL OR lead.team_id=lt.team_id)
 AND security.fde_user_is_active(p_user,'fde_lead',lt.team_id)
 AND security.fde_user_is_active(p.user_ref_id,NULL,lt.team_id)
 AND member.user_ref_id=p.user_ref_id AND member.membership_role IN ('fde','fde_lead')
 AND clock_timestamp()>=lead.valid_from AND clock_timestamp()<lead.valid_to
 AND clock_timestamp()>=lt.valid_from AND clock_timestamp()<lt.valid_to
 AND clock_timestamp()>=member.valid_from AND clock_timestamp()<member.valid_to)));
 $$;

-- Snapshot the pre-FDE authorization as the explicit commercial mutation predicate.
CREATE FUNCTION security.has_customer_write_access(p_customer_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.customer c WHERE c.id=p_customer_id
 AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL AND (
 security.management_actor() OR (common.current_role_code()='manager' AND security.has_active_role('manager')) OR
 (security.supervises_team(c.owner_team_id) OR EXISTS(SELECT 1 FROM crm.customer_sales_member m
 JOIN platform.team_membership tm ON tm.user_ref_id=m.user_ref_id AND tm.workspace_id=m.workspace_id
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 WHERE m.customer_id=c.id AND security.supervises_team(tm.team_id))) OR
 (common.current_role_code()='sales' AND security.has_active_role('sales') AND EXISTS(
 SELECT 1 FROM crm.customer_sales_member m WHERE m.customer_id=c.id AND m.workspace_id=c.workspace_id
 AND m.user_ref_id=common.current_user_ref_id()))));
 $$;
CREATE OR REPLACE FUNCTION security.has_customer_access(p_customer_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.has_customer_write_access(p_customer_id) OR EXISTS(SELECT 1 FROM crm.customer c
 WHERE c.id=p_customer_id AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
 AND security.fde_customer_in_scope(c.id));
 $$;
CREATE FUNCTION security.has_opportunity_read_access(p_opportunity uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.has_opportunity_access(p_opportunity) OR EXISTS(SELECT 1 FROM crm.opportunity o
 WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
 AND security.fde_customer_in_scope(o.customer_id));
 $$;
CREATE FUNCTION security.can_manage_fde_members(p_opportunity uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.has_opportunity_access(p_opportunity) OR
 (common.current_role_code()='fde_lead' AND security.fde_opportunity_in_scope(p_opportunity));
 $$;

ALTER TABLE agent.conversation DROP CONSTRAINT conversation_role_code_check;
ALTER TABLE agent.conversation ADD CONSTRAINT conversation_role_code_check
 CHECK(role_code IN ('sales','supervisor','manager','fde','fde_lead'));
CREATE OR REPLACE FUNCTION security.company_rule_supported(p_code text) RETURNS boolean
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT p_code IN ('customer_quadrant','visit_admission','home_display','task_schedule','fde_capabilities',
 'agent_execution.battle_map_review','agent_execution.opportunity_draft','agent_execution.personal_risks',
 'agent_execution.visit_entry','agent_execution.today_tasks','agent_execution.operating_report','agent_execution.chatbi',
 'score.maturity','score.efficiency','score.competency','agent_execution.customer_advice',
 'agent_execution.opportunity_advice','agent_execution.visit_advice');
 $$;
INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition)
 VALUES('fde_capabilities','FDE自主录入与岗位能力','company_policy',1,'active',
 '{"schema_version":1,"visit_entry_enabled":false,"role_overrides":{},"user_overrides":{}}');
CREATE FUNCTION security.fde_visit_entry_enabled() RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.is_fde_actor() AND COALESCE((SELECT COALESCE(
 CASE WHEN jsonb_typeof(definition->'user_overrides'->common.current_user_ref_id()::text)='boolean'
 THEN (definition->'user_overrides'->>common.current_user_ref_id()::text)::boolean END,
 CASE WHEN jsonb_typeof(definition->'role_overrides'->common.current_role_code())='boolean'
 THEN (definition->'role_overrides'->>common.current_role_code())::boolean END,
 CASE WHEN jsonb_typeof(definition->'visit_entry_enabled')='boolean' THEN (definition->>'visit_entry_enabled')::boolean END,false)
 FROM config.rule_set WHERE id=security.active_company_rule('fde_capabilities')),false);
 $$;
CREATE FUNCTION security.fde_permission_version() RETURNS text
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN common.current_role_code() IN ('fde','fde_lead') THEN
 md5(concat_ws('|',common.current_workspace_id(),common.current_user_ref_id(),common.current_role_code(),
 security.is_fde_actor(),security.active_company_rule('fde_capabilities'),
 (SELECT string_agg(x.value,'|' ORDER BY x.value) FROM (
 SELECT concat_ws(':','role',rb.user_ref_id,rb.role_code,rb.team_id,rb.valid_from,rb.valid_to) value
 FROM platform.role_binding rb WHERE rb.workspace_id=common.current_workspace_id()
 AND rb.role_code IN ('fde','fde_lead') AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
 UNION ALL SELECT concat_ws(':','team',tm.user_ref_id,tm.team_id,tm.membership_role,tm.valid_from,tm.valid_to)
 FROM platform.team_membership tm WHERE tm.workspace_id=common.current_workspace_id()
 AND tm.membership_role IN ('fde','fde_lead') AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 UNION ALL SELECT concat_ws(':','department',t.id,t.status,t.deleted_at,t.version_no,t.valid_from,t.valid_to)
 FROM platform.team t WHERE t.workspace_id=common.current_workspace_id()
 AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
 AND EXISTS(SELECT 1 FROM platform.team_membership tm WHERE tm.workspace_id=t.workspace_id
 AND tm.team_id=t.id AND tm.membership_role IN ('fde','fde_lead'))
 UNION ALL SELECT concat_ws(':','person',u.id,u.status,u.deleted_at,u.version_no)
 FROM platform.user_ref u WHERE u.workspace_id=common.current_workspace_id()
 AND EXISTS(SELECT 1 FROM platform.role_binding rb WHERE rb.user_ref_id=u.id AND rb.role_code IN ('fde','fde_lead'))
 UNION ALL SELECT concat_ws(':','project',p.opportunity_id,p.user_ref_id,p.valid_from,p.valid_to,o.customer_id,o.deleted_at)
 FROM crm.opportunity_participant p JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id
 WHERE p.workspace_id=common.current_workspace_id() AND p.participant_role='fde'
 AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to) x))) ELSE NULL END;
 $$;
-- Personal historical participation is a narrow projection, not permission to
-- reopen the whole customer after the last current relationship has ended.
CREATE FUNCTION security.fde_participation_history() RETURNS TABLE(
 visit_id uuid,user_ref_id uuid,customer_id uuid,customer_name text,opportunity_id uuid,opportunity_name text,
 interaction_at timestamptz,recorder_name text,team_id_at_event uuid)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT v.id,p.user_ref_id,v.customer_id,COALESCE(v.archived_fields->>'customer_name',c.name),
 v.opportunity_id,COALESCE(v.archived_fields->>'opportunity_name',o.name),v.interaction_at,u.display_name,p.team_id_at_event
 FROM activity.visit_participant p JOIN activity.visit v ON v.id=p.visit_id AND v.workspace_id=p.workspace_id
 LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
 LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
 LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id AND u.workspace_id=v.workspace_id
 WHERE security.is_fde_actor() AND p.workspace_id=common.current_workspace_id() AND p.participant_role='fde'
 AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
 AND (p.user_ref_id=common.current_user_ref_id() OR (common.current_role_code()='fde_lead'
 AND EXISTS(SELECT 1 FROM platform.team_membership tm JOIN platform.role_binding rb
 ON rb.user_ref_id=tm.user_ref_id AND rb.workspace_id=tm.workspace_id AND rb.role_code='fde_lead'
 WHERE tm.user_ref_id=common.current_user_ref_id() AND tm.workspace_id=p.workspace_id
 AND tm.team_id=p.team_id_at_event AND tm.membership_role='fde_lead'
 AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',tm.team_id)
 AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
 AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to)));
 $$;

-- Split the inherited ALL policies into explicit command policies. The SELECT
-- expression uses panorama read predicates; INSERT/UPDATE/DELETE retain the old
-- customer qualification. Policy role lists and restrictive/permissive semantics
-- are preserved, including all historical opportunity boundaries.
DO $$ DECLARE p record; rusing text; wusing text; wcheck text; roles text; mode text; BEGIN
 FOR p IN SELECT pol.*,n.nspname,c.relname,pg_get_expr(pol.polqual,pol.polrelid) expr,
 pg_get_expr(pol.polwithcheck,pol.polrelid) check_expr
 FROM pg_policy pol JOIN pg_class c ON c.oid=pol.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname IN ('crm','activity','insight','workflow')
 AND (pol.polcmd='*' OR pol.polcmd='r')
 LOOP
 rusing:=COALESCE(p.expr,'true');
 rusing:=replace(rusing,'security.has_opportunity_access(', 'security.has_opportunity_read_access(');
 wusing:=replace(COALESCE(p.expr,'true'),'security.has_customer_access(', 'security.has_customer_write_access(');
 wcheck:=replace(COALESCE(p.check_expr,p.expr,'true'),'security.has_customer_access(', 'security.has_customer_write_access(');
 SELECT string_agg(CASE WHEN x=0 THEN 'PUBLIC' ELSE quote_ident(rolname) END,',') INTO roles
 FROM unnest(p.polroles) x LEFT JOIN pg_roles r ON r.oid=x;
 mode:=CASE WHEN p.polpermissive THEN 'PERMISSIVE' ELSE 'RESTRICTIVE' END;
 EXECUTE format('DROP POLICY %I ON %I.%I',p.polname,p.nspname,p.relname);
 EXECUTE format('CREATE POLICY %I ON %I.%I AS %s FOR SELECT TO %s USING (%s)',p.polname,p.nspname,p.relname,mode,roles,rusing);
 IF p.polcmd='*' THEN
 EXECUTE format('CREATE POLICY %I ON %I.%I AS %s FOR INSERT TO %s WITH CHECK (%s)',p.polname||'_insert',p.nspname,p.relname,mode,roles,wcheck);
 EXECUTE format('CREATE POLICY %I ON %I.%I AS %s FOR UPDATE TO %s USING (%s) WITH CHECK (%s)',p.polname||'_update',p.nspname,p.relname,mode,roles,wusing,wcheck);
 EXECUTE format('CREATE POLICY %I ON %I.%I AS %s FOR DELETE TO %s USING (%s)',p.polname||'_delete',p.nspname,p.relname,mode,roles,wusing);
 END IF;
 END LOOP;
END $$;
DROP POLICY opportunity_owner_scope ON crm.opportunity;
CREATE POLICY opportunity_owner_scope ON crm.opportunity FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND deleted_at IS NULL
 AND (security.opportunity_owner_in_scope(owner_user_ref_id,owner_team_id) OR security.fde_customer_in_scope(customer_id)));
-- Never permit commercial edits merely because a customer can now be read.
DO $$ DECLARE t record; cmd text; BEGIN
 FOR t IN SELECT c.oid,n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind='r' AND c.relrowsecurity AND ((n.nspname='crm' AND c.relname<>'opportunity_participant')
 OR (n.nspname='insight' AND c.relname IN ('risk','risk_event','sales_competency_review'))
 OR (n.nspname='activity' AND c.relname='action_item'))
 LOOP
 FOREACH cmd IN ARRAY ARRAY['INSERT','UPDATE','DELETE'] LOOP
 IF cmd='INSERT' THEN EXECUTE format('CREATE POLICY %I ON %I.%I AS RESTRICTIVE FOR INSERT WITH CHECK (common.current_role_code() NOT IN (''fde'',''fde_lead''))','fde_commercial_'||lower(cmd),t.nspname,t.relname);
 ELSIF cmd='UPDATE' THEN EXECUTE format('CREATE POLICY %I ON %I.%I AS RESTRICTIVE FOR UPDATE USING (common.current_role_code() NOT IN (''fde'',''fde_lead'')) WITH CHECK (common.current_role_code() NOT IN (''fde'',''fde_lead''))','fde_commercial_'||lower(cmd),t.nspname,t.relname);
 ELSE EXECUTE format('CREATE POLICY %I ON %I.%I AS RESTRICTIVE FOR DELETE USING (common.current_role_code() NOT IN (''fde'',''fde_lead''))','fde_commercial_'||lower(cmd),t.nspname,t.relname); END IF;
 END LOOP;
 END LOOP;
END $$;

-- Full customer visibility does not authorize changing participation. A leader
-- may coordinate an existing team project and may only add/remove team members.
DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT policyname FROM pg_policies WHERE schemaname='crm' AND tablename='opportunity_participant'
 LOOP EXECUTE format('DROP POLICY %I ON crm.opportunity_participant',p.policyname); END LOOP;
END $$;
CREATE POLICY participant_read ON crm.opportunity_participant FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.has_opportunity_read_access(opportunity_id));
CREATE POLICY participant_insert ON crm.opportunity_participant FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.can_manage_fde_members(opportunity_id)
 AND (common.current_role_code()<>'fde_lead' OR (participant_role='fde' AND security.fde_manages_user(user_ref_id)))
 AND (common.current_role_code()<>'fde' OR false));
CREATE POLICY participant_update ON crm.opportunity_participant FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.can_manage_fde_members(opportunity_id)
 AND (common.current_role_code()<>'fde_lead' OR security.fde_manages_user(user_ref_id))) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.can_manage_fde_members(opportunity_id)
 AND (common.current_role_code()<>'fde_lead' OR security.fde_manages_user(user_ref_id)));
-- Participation periods are ended, not deleted. Maintenance administrators retain
-- their separate owner path for migrations; the application has no DELETE policy.
CREATE FUNCTION security.can_write_visit(p_visit uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id()
 AND v.deleted_at IS NULL AND (CASE WHEN common.current_role_code() IN ('fde','fde_lead')
 THEN security.fde_visit_entry_enabled() AND v.recorder_user_ref_id=common.current_user_ref_id()
 AND v.created_by_user_ref_id=common.current_user_ref_id() AND security.has_customer_access(v.customer_id)
 ELSE security.management_actor() OR v.recorder_user_ref_id=common.current_user_ref_id()
 OR security.has_customer_write_access(v.customer_id) END));
 $$;
CREATE POLICY visit_participant_read ON activity.visit_participant FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=visit_id));
CREATE POLICY visit_participant_insert ON activity.visit_participant FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id));
CREATE POLICY visit_participant_update ON activity.visit_participant FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id));
CREATE POLICY visit_participant_delete ON activity.visit_participant FOR DELETE USING(
 workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id));
CREATE POLICY fde_visit_insert ON activity.visit FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.fde_visit_entry_enabled()
 AND recorder_user_ref_id=common.current_user_ref_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND security.has_customer_access(customer_id));
CREATE POLICY fde_visit_update ON activity.visit FOR UPDATE USING(security.can_write_visit(id)) WITH CHECK(security.can_write_visit(id));
CREATE POLICY fde_visit_write_guard ON activity.visit AS RESTRICTIVE FOR INSERT WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR (security.fde_visit_entry_enabled()
 AND recorder_user_ref_id=common.current_user_ref_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND security.has_customer_access(customer_id)));
CREATE POLICY fde_visit_update_guard ON activity.visit AS RESTRICTIVE FOR UPDATE USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.can_write_visit(id)) WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.can_write_visit(id));
CREATE POLICY fde_visit_delete_guard ON activity.visit AS RESTRICTIVE FOR DELETE USING(common.current_role_code() NOT IN ('fde','fde_lead'));
-- An enabled FDE may record against a readable project without editing that project.
DROP POLICY visit_opportunity_boundary_insert ON activity.visit;
DROP POLICY visit_opportunity_boundary_update ON activity.visit;
CREATE POLICY visit_opportunity_boundary_insert ON activity.visit AS RESTRICTIVE FOR INSERT WITH CHECK(
 opportunity_id IS NULL OR CASE WHEN common.current_role_code() IN ('fde','fde_lead')
 THEN security.has_opportunity_read_access(opportunity_id) ELSE security.has_opportunity_access(opportunity_id) END);
CREATE POLICY visit_opportunity_boundary_update ON activity.visit AS RESTRICTIVE FOR UPDATE USING(
 opportunity_id IS NULL OR CASE WHEN common.current_role_code() IN ('fde','fde_lead')
 THEN security.has_opportunity_read_access(opportunity_id) ELSE security.has_opportunity_access(opportunity_id) END) WITH CHECK(
 opportunity_id IS NULL OR CASE WHEN common.current_role_code() IN ('fde','fde_lead')
 THEN security.has_opportunity_read_access(opportunity_id) ELSE security.has_opportunity_access(opportunity_id) END);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['visit_contact','visit_field_value'] LOOP
 EXECUTE format('CREATE POLICY fde_visit_child_insert ON activity.%I AS RESTRICTIVE FOR INSERT WITH CHECK (common.current_role_code() NOT IN (''fde'',''fde_lead'') OR security.can_write_visit(visit_id))',t);
 EXECUTE format('CREATE POLICY fde_visit_child_update ON activity.%I AS RESTRICTIVE FOR UPDATE USING (common.current_role_code() NOT IN (''fde'',''fde_lead'') OR security.can_write_visit(visit_id)) WITH CHECK (common.current_role_code() NOT IN (''fde'',''fde_lead'') OR security.can_write_visit(visit_id))',t);
 EXECUTE format('CREATE POLICY fde_visit_child_delete ON activity.%I AS RESTRICTIVE FOR DELETE USING (common.current_role_code() NOT IN (''fde'',''fde_lead'') OR security.can_write_visit(visit_id))',t);
 END LOOP;
END $$;
CREATE POLICY fde_import_insert ON activity.visit_import AS RESTRICTIVE FOR INSERT WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.fde_visit_entry_enabled());
CREATE POLICY fde_import_update ON activity.visit_import AS RESTRICTIVE FOR UPDATE USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.fde_visit_entry_enabled()) WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.fde_visit_entry_enabled());

CREATE FUNCTION security.fde_user_direct_opportunity_scope(p_user uuid,p_role text,p_team uuid,p_opportunity uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.fde_user_is_active(p_user,p_role,p_team) AND EXISTS(
 SELECT 1 FROM crm.opportunity_participant p JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id
 WHERE p.workspace_id=common.current_workspace_id() AND p.opportunity_id=p_opportunity AND o.deleted_at IS NULL
 AND p.participant_role='fde' AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 AND security.fde_user_is_active(p.user_ref_id)
 AND (p.user_ref_id=p_user OR (p_role='fde_lead' AND p_team IS NOT NULL
 AND security.fde_user_is_active(p.user_ref_id,NULL,p_team))));
 $$;
CREATE FUNCTION security.fde_task_eligible(p_task uuid,p_user uuid,p_role text,p_team uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.fde_user_is_active(p_user,p_role,p_team) AND EXISTS(SELECT 1 FROM workflow.task t
 WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
 AND (t.opportunity_id IS NULL OR security.fde_user_direct_opportunity_scope(p_user,p_role,p_team,t.opportunity_id)));
 $$;
CREATE FUNCTION security.fde_can_coordinate_task(p_task uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM workflow.task t WHERE t.id=p_task AND t.workspace_id=common.current_workspace_id()
 AND t.deleted_at IS NULL AND (security.management_actor() OR t.creator_user_ref_id=common.current_user_ref_id()
 OR (common.current_role_code()='fde_lead' AND security.is_fde_actor() AND EXISTS(
 SELECT 1 FROM platform.team_membership mine JOIN platform.role_binding rb ON rb.user_ref_id=mine.user_ref_id
 AND rb.workspace_id=mine.workspace_id AND rb.role_code='fde_lead'
 WHERE mine.user_ref_id=common.current_user_ref_id() AND mine.workspace_id=t.workspace_id
 AND mine.membership_role='fde_lead' AND (rb.team_id IS NULL OR rb.team_id=mine.team_id)
 AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',mine.team_id)
 AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
 AND clock_timestamp()>=mine.valid_from AND clock_timestamp()<mine.valid_to
 AND (EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.assignee_team_id=mine.team_id AND a.assignee_role IN ('fde','fde_lead'))
 OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=t.id AND c.team_id=mine.team_id AND c.role_code IN ('fde','fde_lead')))))));
 $$;
CREATE FUNCTION security.fde_can_act_on_task(p_task uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.is_fde_actor() AND (
 EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=p_task AND a.workspace_id=common.current_workspace_id()
 AND a.assignee_user_ref_id=common.current_user_ref_id() AND a.assignee_role=common.current_role_code() AND a.responsibility='owner'
 AND security.fde_task_eligible(p_task,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
 OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=p_task AND c.workspace_id=common.current_workspace_id()
 AND c.user_ref_id=common.current_user_ref_id() AND c.role_code=common.current_role_code() AND c.decision='pending'
 AND security.fde_task_eligible(p_task,c.user_ref_id,c.role_code,c.team_id)));
 $$;
CREATE OR REPLACE VIEW workflow.v_task_action_recipient WITH(security_invoker=true) AS
 SELECT a.task_id,a.workspace_id,a.assignee_user_ref_id,a.assignee_team_id,a.assignee_role,'owner'::text AS responsibility
 FROM workflow.task_assignee a WHERE a.responsibility='owner' AND (a.assignee_role NOT IN ('fde','fde_lead')
 OR security.fde_task_eligible(a.task_id,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))
 UNION ALL
 SELECT c.task_id,c.workspace_id,c.user_ref_id,c.team_id,c.role_code,'candidate'::text
 FROM workflow.task_candidate c JOIN workflow.task t ON t.id=c.task_id
 JOIN platform.user_ref u ON u.id=c.user_ref_id AND u.workspace_id=c.workspace_id
 WHERE t.status='pending_confirm' AND c.decision='pending' AND u.status='active' AND u.deleted_at IS NULL
 AND EXISTS(SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=u.id AND r.workspace_id=c.workspace_id
 AND r.role_code=c.role_code AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to)
 AND (t.target_position<>'supervisor' OR EXISTS(SELECT 1 FROM platform.team_membership tm
 WHERE tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id AND tm.team_id=c.team_id
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to))
 AND (c.role_code NOT IN ('fde','fde_lead') OR security.fde_task_eligible(c.task_id,c.user_ref_id,c.role_code,c.team_id));
CREATE POLICY fde_task_create ON workflow.task FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor() AND creator_user_ref_id=common.current_user_ref_id()
 AND (customer_id IS NULL OR security.has_customer_access(customer_id)));
CREATE POLICY fde_task_coordinate_read ON workflow.task FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor() AND security.fde_can_coordinate_task(id));
CREATE POLICY fde_task_coordinate_update ON workflow.task FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor() AND security.fde_can_coordinate_task(id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor() AND security.fde_can_coordinate_task(id));
CREATE POLICY fde_task_write_guard ON workflow.task AS RESTRICTIVE FOR UPDATE USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.fde_can_act_on_task(id) OR security.fde_can_coordinate_task(id)) WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.fde_can_act_on_task(id) OR security.fde_can_coordinate_task(id));
CREATE POLICY fde_task_delete_guard ON workflow.task AS RESTRICTIVE FOR DELETE USING(common.current_role_code() NOT IN ('fde','fde_lead'));
DROP POLICY task_opportunity_boundary ON workflow.task;
DROP POLICY task_opportunity_boundary_insert ON workflow.task;
DROP POLICY task_opportunity_boundary_update ON workflow.task;
CREATE POLICY task_opportunity_boundary ON workflow.task AS RESTRICTIVE FOR SELECT USING(
 opportunity_id IS NULL OR security.has_opportunity_read_access(opportunity_id)
 OR (security.is_fde_actor() AND security.fde_can_coordinate_task(id)));
CREATE POLICY task_opportunity_boundary_insert ON workflow.task AS RESTRICTIVE FOR INSERT WITH CHECK(
 opportunity_id IS NULL OR CASE WHEN common.current_role_code() IN ('fde','fde_lead')
 THEN security.has_opportunity_read_access(opportunity_id) ELSE security.has_opportunity_access(opportunity_id) END);
CREATE POLICY task_opportunity_boundary_update ON workflow.task AS RESTRICTIVE FOR UPDATE USING(
 opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id) OR (security.is_fde_actor()
 AND (security.fde_can_act_on_task(id) OR security.fde_can_coordinate_task(id)))) WITH CHECK(
 opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id) OR (security.is_fde_actor()
 AND (security.fde_can_act_on_task(id) OR security.fde_can_coordinate_task(id))));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['task_candidate','task_assignee'] LOOP
 EXECUTE format('CREATE POLICY fde_task_child_insert ON workflow.%I AS RESTRICTIVE FOR INSERT WITH CHECK (common.current_role_code() NOT IN (''fde'',''fde_lead'') OR security.fde_can_coordinate_task(task_id) OR security.fde_can_act_on_task(task_id))',t);
 EXECUTE format('CREATE POLICY fde_task_child_update ON workflow.%I AS RESTRICTIVE FOR UPDATE USING (common.current_role_code() NOT IN (''fde'',''fde_lead'') OR security.fde_can_coordinate_task(task_id) OR security.fde_can_act_on_task(task_id)) WITH CHECK (common.current_role_code() NOT IN (''fde'',''fde_lead'') OR security.fde_can_coordinate_task(task_id) OR security.fde_can_act_on_task(task_id))',t);
 EXECUTE format('CREATE POLICY fde_task_child_delete ON workflow.%I AS RESTRICTIVE FOR DELETE USING (common.current_role_code() NOT IN (''fde'',''fde_lead''))',t);
 END LOOP;
END $$;
-- Response receipts are written after the candidate leaves pending. This is
-- narrower than permission to act: it only permits recording one's own response,
-- and inserting the newly claimed owner for the same account/role/team.
CREATE FUNCTION security.fde_can_record_task_response(p_task uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.is_fde_actor() AND EXISTS(SELECT 1 FROM workflow.task_candidate c
 WHERE c.task_id=p_task AND c.workspace_id=common.current_workspace_id()
 AND c.user_ref_id=common.current_user_ref_id() AND c.role_code=common.current_role_code()
 AND c.decision IN ('pending','claimed','declined')
 AND security.fde_task_eligible(c.task_id,c.user_ref_id,c.role_code,c.team_id));
 $$;
DROP POLICY fde_task_child_insert ON workflow.task_assignee;
CREATE POLICY fde_task_child_insert ON workflow.task_assignee AS RESTRICTIVE FOR INSERT WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.fde_can_coordinate_task(task_id)
 OR (assignee_user_ref_id=common.current_user_ref_id() AND assignee_role=common.current_role_code()
 AND responsibility='owner' AND security.fde_can_record_task_response(task_id)
 AND EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=task_assignee.task_id
 AND c.user_ref_id=common.current_user_ref_id() AND c.role_code=task_assignee.assignee_role
 AND c.team_id IS NOT DISTINCT FROM task_assignee.assignee_team_id AND c.decision='claimed')));
CREATE POLICY fde_task_event_insert ON workflow.task_event AS RESTRICTIVE FOR INSERT WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR (actor_user_ref_id=common.current_user_ref_id()
 AND (security.fde_can_coordinate_task(task_id) OR security.fde_can_act_on_task(task_id)
 OR security.fde_can_record_task_response(task_id))));

DROP POLICY fde_task_child_delete ON workflow.task_assignee;
CREATE POLICY fde_task_child_delete ON workflow.task_assignee AS RESTRICTIVE FOR DELETE USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.fde_can_coordinate_task(task_id));
CREATE POLICY fde_task_event_update ON workflow.task_event AS RESTRICTIVE FOR UPDATE USING(common.current_role_code() NOT IN ('fde','fde_lead'));
CREATE POLICY fde_task_event_delete ON workflow.task_event AS RESTRICTIVE FOR DELETE USING(common.current_role_code() NOT IN ('fde','fde_lead'));
CREATE FUNCTION security.bump_fde_membership_version(p_opportunity uuid,p_expected_version integer) RETURNS integer
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
 DECLARE current_version integer;
 BEGIN
 IF NOT security.can_manage_fde_members(p_opportunity) THEN RAISE insufficient_privilege; END IF;
 SELECT version_no INTO current_version FROM crm.opportunity
 WHERE id=p_opportunity AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF current_version IS DISTINCT FROM p_expected_version THEN
 RAISE EXCEPTION '商机已变化，请刷新后重试' USING ERRCODE='40001'; END IF;
 UPDATE crm.opportunity SET version_no=version_no+1,updated_at=clock_timestamp()
 WHERE id=p_opportunity RETURNING version_no INTO current_version;
 RETURN current_version;
 END; $$;

CREATE FUNCTION ops.audit_participation_row() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
 DECLARE prior jsonb; after_value jsonb; object_value jsonb; oid uuid; label text; changed text[];
 BEGIN
 IF TG_OP<>'INSERT' THEN prior:=to_jsonb(OLD); END IF;
 IF TG_OP<>'DELETE' THEN after_value:=to_jsonb(NEW); END IF;
 IF prior IS NOT DISTINCT FROM after_value THEN RETURN NULL; END IF;
 object_value:=COALESCE(after_value,prior);
 oid:=COALESCE(object_value->>'opportunity_id',object_value->>'visit_id')::uuid;
 SELECT COALESCE(display_name,'同事') INTO label FROM platform.user_ref WHERE id=(object_value->>'user_ref_id')::uuid;
 SELECT array_agg(key ORDER BY key) INTO changed FROM jsonb_object_keys(COALESCE(prior,'{}')||COALESCE(after_value,'{}')) key
 WHERE prior->key IS DISTINCT FROM after_value->key AND key NOT IN ('updated_at','created_at');
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,object_type,object_id,object_label,
 request_id,client_ip,user_agent,before_snapshot,after_snapshot,changed_fields)
 VALUES((object_value->>'workspace_id')::uuid,common.current_user_ref_id(),COALESCE(common.current_role_code(),'system'),
 TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME||'.'||lower(TG_OP),TG_TABLE_SCHEMA,TG_TABLE_NAME,oid,label,
 NULLIF(current_setting('app.request_id',true),'')::uuid,NULLIF(current_setting('app.client_ip',true),'')::inet,
 left(current_setting('app.user_agent',true),500),security.audit_redact(prior),security.audit_redact(after_value),COALESCE(changed,'{}'));
 RETURN NULL;
 END; $$;
CREATE FUNCTION crm.guard_fde_participation_period() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
 BEGIN
 IF TG_OP='UPDATE' AND ROW(NEW.opportunity_id,NEW.workspace_id,NEW.user_ref_id,NEW.participant_role,
 NEW.valid_from,NEW.assigned_by_user_ref_id,NEW.source_code,NEW.source_visit_id,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.opportunity_id,OLD.workspace_id,OLD.user_ref_id,OLD.participant_role,
 OLD.valid_from,OLD.assigned_by_user_ref_id,OLD.source_code,OLD.source_visit_id,OLD.created_at) THEN
 RAISE EXCEPTION '协助历史身份及来源不能覆盖，请结束旧关系后重新加入' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND NEW.valid_to>OLD.valid_to THEN
 RAISE EXCEPTION '已结束的协助关系不能重新打开，请新增关系' USING ERRCODE='23514'; END IF;
 IF NEW.participant_role='fde' AND TG_OP='INSERT' AND (
 NEW.source_code='legacy' OR NEW.assigned_by_user_ref_id IS NULL
 OR NOT security.fde_user_is_active(NEW.user_ref_id)) THEN
 RAISE EXCEPTION '新增协助必须指向有效FDE并记录操作来源' USING ERRCODE='23514'; END IF;
 RETURN NEW;
 END; $$;
CREATE TRIGGER fde_participation_period_guard BEFORE INSERT OR UPDATE ON crm.opportunity_participant
 FOR EACH ROW EXECUTE FUNCTION crm.guard_fde_participation_period();
CREATE TRIGGER business_audit AFTER INSERT OR UPDATE OR DELETE ON crm.opportunity_participant
 FOR EACH ROW EXECUTE FUNCTION ops.audit_participation_row();
CREATE TRIGGER business_audit AFTER INSERT OR UPDATE OR DELETE ON activity.visit_participant
 FOR EACH ROW EXECUTE FUNCTION ops.audit_participation_row();
CREATE FUNCTION workflow.notify_fde_membership() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
 DECLARE event_code text; customer_name text; opportunity_name text; actor_name text; participant_name text; recipient uuid;
 BEGIN
 IF NEW.participant_role<>'fde' OR NEW.source_code='legacy' THEN RETURN NULL; END IF;
 IF TG_OP='INSERT' AND NEW.valid_from<=clock_timestamp() AND NEW.valid_to>clock_timestamp() THEN event_code:='fde_joined';
 ELSIF TG_OP='UPDATE' AND OLD.valid_to>NEW.valid_to AND NEW.valid_to<=clock_timestamp() THEN event_code:='fde_removed';
 ELSE RETURN NULL; END IF;
 SELECT c.name,o.name INTO customer_name,opportunity_name FROM crm.opportunity o JOIN crm.customer c
 ON c.id=o.customer_id AND c.workspace_id=o.workspace_id WHERE o.id=NEW.opportunity_id AND o.workspace_id=NEW.workspace_id;
 SELECT display_name INTO actor_name FROM platform.user_ref WHERE id=common.current_user_ref_id();
 SELECT display_name INTO participant_name FROM platform.user_ref WHERE id=NEW.user_ref_id;
 FOR recipient IN SELECT u.id FROM platform.user_ref u WHERE u.workspace_id=NEW.workspace_id AND u.status='active' AND u.deleted_at IS NULL
 AND (u.id=NEW.user_ref_id OR EXISTS(SELECT 1 FROM platform.role_binding rb JOIN platform.team_membership lead
 ON lead.user_ref_id=rb.user_ref_id AND lead.workspace_id=rb.workspace_id
 JOIN platform.team_membership member ON member.team_id=lead.team_id AND member.workspace_id=lead.workspace_id
 WHERE rb.user_ref_id=u.id AND rb.role_code='fde_lead' AND lead.membership_role='fde_lead'
 AND (rb.team_id IS NULL OR rb.team_id=lead.team_id) AND member.user_ref_id=NEW.user_ref_id
 AND security.fde_user_is_active(u.id,'fde_lead',lead.team_id)
 AND member.membership_role IN ('fde','fde_lead') AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
 AND clock_timestamp()>=lead.valid_from AND clock_timestamp()<lead.valid_to
 AND clock_timestamp()>=member.valid_from AND clock_timestamp()<member.valid_to))
 LOOP
 INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,body,object_type,object_id,status,dedupe_key,payload)
 VALUES(NEW.workspace_id,recipient,event_code,CASE WHEN event_code='fde_joined' THEN '商机协助已加入' ELSE '商机协助已结束' END,
 COALESCE(actor_name,'运营')||CASE WHEN event_code='fde_joined' THEN '将' ELSE '已将' END||COALESCE(participant_name,'同事')||
 CASE WHEN event_code='fde_joined' THEN '加入' ELSE '移出' END||COALESCE(customer_name,'客户')||' / '||COALESCE(opportunity_name,'商机'),
 'fde_membership',NEW.opportunity_id,'sent',concat_ws(':',event_code,NEW.opportunity_id,NEW.user_ref_id,NEW.valid_from,recipient),
 jsonb_build_object('membership_opportunity_id',NEW.opportunity_id,'customer_name',customer_name,'opportunity_name',opportunity_name,
 'participant_user_ref_id',NEW.user_ref_id,'participant_name',participant_name,'actor_name',actor_name,'end_reason',NEW.end_reason))
 ON CONFLICT DO NOTHING;
 END LOOP;
 RETURN NULL;
 END; $$;
CREATE TRIGGER fde_membership_notification AFTER INSERT OR UPDATE ON crm.opportunity_participant
 FOR EACH ROW EXECUTE FUNCTION workflow.notify_fde_membership();
CREATE FUNCTION workflow.enqueue_fde_collaboration_notification(p_opportunity uuid,p_recipient uuid,p_template text,p_event uuid,p_payload jsonb)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
 DECLARE customer_name text; opportunity_name text; inserted integer;
 BEGIN
 IF p_template NOT IN ('visit_archived','business_changed') THEN RAISE insufficient_privilege; END IF;
 IF NOT security.has_opportunity_access(p_opportunity) AND NOT (p_template='visit_archived' AND EXISTS(
 SELECT 1 FROM activity.visit v WHERE v.id=p_event AND v.opportunity_id=p_opportunity AND v.workspace_id=common.current_workspace_id()
 AND v.recorder_user_ref_id=common.current_user_ref_id() AND v.status IN ('confirmed','archived') AND v.deleted_at IS NULL)) THEN
 RAISE insufficient_privilege; END IF;
 IF NOT security.fde_user_is_active(p_recipient) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM crm.opportunity_participant p WHERE p.opportunity_id=p_opportunity
 AND p.workspace_id=common.current_workspace_id() AND p.user_ref_id=p_recipient AND p.participant_role='fde'
 AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to) AND NOT (p_template='visit_archived' AND EXISTS(
 SELECT 1 FROM activity.visit_participant vp WHERE vp.visit_id=p_event AND vp.workspace_id=common.current_workspace_id()
 AND vp.user_ref_id=p_recipient AND vp.participant_role='fde')) THEN RETURN false; END IF;
 SELECT c.name,o.name INTO customer_name,opportunity_name FROM crm.opportunity o JOIN crm.customer c
 ON c.id=o.customer_id AND c.workspace_id=o.workspace_id WHERE o.id=p_opportunity AND o.workspace_id=common.current_workspace_id();
 INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,body,object_type,object_id,status,dedupe_key,payload)
 VALUES(common.current_workspace_id(),p_recipient,p_template,
 CASE WHEN p_template='visit_archived' THEN '协同拜访已归档' ELSE '协助商机有更新' END,
 COALESCE(customer_name,'客户')||' / '||COALESCE(opportunity_name,'商机'),
 CASE WHEN p_template='visit_archived' THEN 'visit' ELSE 'opportunity' END,
 CASE WHEN p_template='visit_archived' THEN p_event ELSE p_opportunity END,'sent',
 concat_ws(':','fde',p_template,p_event,p_recipient),
 COALESCE(p_payload,'{}')||jsonb_build_object('opportunity_id',p_opportunity,'customer_name',customer_name,'opportunity_name',opportunity_name))
 ON CONFLICT DO NOTHING;
 GET DIAGNOSTICS inserted=ROW_COUNT;
 RETURN inserted=1;
 END; $$;

CREATE OR REPLACE FUNCTION security.resolve_account_actor(p_workspace_external_id text, p_account_code text, p_role text DEFAULT NULL) RETURNS TABLE(workspace_id text, user_id text, account_code text, display_name text, role_code text, data_scope_code text, team_ids text[], team_names text[])
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'security'
    AS $$
  SELECT
    w.id::text,
    u.id::text,
    u.account_code,
    u.display_name,
    rb.role_code,
    rb.data_scope_code,
    COALESCE(array_agg(DISTINCT tm.team_id::text)
      FILTER (WHERE tm.team_id IS NOT NULL), ARRAY[]::text[]),
    COALESCE(array_agg(DISTINCT t.name)
      FILTER (WHERE t.name IS NOT NULL), ARRAY[]::text[])
  FROM platform.workspace w
  JOIN platform.user_ref u
    ON u.workspace_id = w.id AND u.deleted_at IS NULL AND u.status = 'active'
  JOIN platform.role_binding rb
    ON rb.workspace_id = w.id AND rb.user_ref_id = u.id
   AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
  LEFT JOIN platform.team_membership tm
    ON tm.workspace_id = w.id AND tm.user_ref_id = u.id
   AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
   AND (rb.role_code NOT IN ('fde','fde_lead') OR (tm.membership_role IN ('fde','fde_lead')
     AND (rb.role_code<>'fde_lead' OR tm.membership_role='fde_lead')
     AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)))
  LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL AND t.status='active'
   AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
  WHERE w.external_workspace_id = p_workspace_external_id
    AND w.status = 'active'
    AND u.account_code = upper(btrim(p_account_code))
    AND (p_role IS NULL OR rb.role_code=p_role)
    AND (rb.role_code NOT IN ('fde','fde_lead') OR t.id IS NOT NULL)
  GROUP BY w.id, u.id, u.account_code, u.display_name,
           rb.role_code, rb.data_scope_code
  ORDER BY CASE rb.role_code WHEN 'administrator' THEN 1 WHEN 'operations' THEN 2 WHEN 'manager' THEN 3 WHEN 'supervisor' THEN 4 WHEN 'fde_lead' THEN 5 WHEN 'fde' THEN 6 ELSE 7 END, rb.data_scope_code
  LIMIT 1
$$;



CREATE OR REPLACE FUNCTION security.rotate_auth_session(p_old_hash text, p_new_hash text, p_new_expires_at timestamp with time zone) RETURNS TABLE(session_id text, workspace_id text, user_id text, account_code text, display_name text, role_code text, data_scope_code text, team_ids text[], team_names text[])
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'security'
    AS $$
BEGIN
  RETURN QUERY
  WITH rotated AS (
    UPDATE platform.auth_session s
       SET refresh_token_hash = p_new_hash,
           expires_at = p_new_expires_at,
           last_seen_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE s.refresh_token_hash = p_old_hash
       AND s.status = 'active'
       AND s.expires_at > clock_timestamp()
    RETURNING s.id, s.workspace_id, s.user_ref_id, s.active_role
  )
  SELECT
    rotated.id::text,
    u.workspace_id::text,
    u.id::text,
    u.account_code,
    u.display_name,
    rb.role_code,
    rb.data_scope_code,
    COALESCE(array_agg(DISTINCT tm.team_id::text)
      FILTER (WHERE tm.team_id IS NOT NULL), ARRAY[]::text[]),
    COALESCE(array_agg(DISTINCT t.name)
      FILTER (WHERE t.name IS NOT NULL), ARRAY[]::text[])
  FROM rotated
  JOIN platform.user_ref u ON u.id = rotated.user_ref_id AND u.status='active' AND u.deleted_at IS NULL
  JOIN platform.role_binding rb
    ON rb.workspace_id = u.workspace_id AND rb.user_ref_id = u.id AND (rotated.active_role IS NULL OR rb.role_code=rotated.active_role)
   AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
  LEFT JOIN platform.team_membership tm
    ON tm.workspace_id = u.workspace_id AND tm.user_ref_id = u.id
   AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
   AND (rb.role_code NOT IN ('fde','fde_lead') OR (tm.membership_role IN ('fde','fde_lead')
     AND (rb.role_code<>'fde_lead' OR tm.membership_role='fde_lead')
     AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)))
  LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL AND t.status='active'
   AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
  WHERE rb.role_code NOT IN ('fde','fde_lead') OR t.id IS NOT NULL
  GROUP BY rotated.id, u.workspace_id, u.id, u.account_code, u.display_name,
           rb.role_code, rb.data_scope_code
  ORDER BY rb.role_code LIMIT 1;
END;
$$;




CREATE OR REPLACE FUNCTION security.has_analysis_scope(p_identity jsonb,p_version smallint) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.is_current_fact_scope(p_version)
 AND p_identity->>'workspace_id'=common.current_workspace_id()::text
 AND p_identity->>'user_id'=common.current_user_ref_id()::text
 AND p_identity->>'role'=common.current_role_code()
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(p_identity->'team_ids','[]')) prior_team
 WHERE NOT prior_team=ANY(COALESCE(string_to_array(current_setting('app.team_ids',true),','),ARRAY[]::text[])))
 AND (common.current_role_code() NOT IN ('fde','fde_lead') OR
 (security.is_fde_actor() AND p_identity->>'permission_version'=security.fde_permission_version()));
 $$;
CREATE POLICY advice_fde_permission_version ON insight.business_advice AS RESTRICTIVE FOR SELECT USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR (security.is_fde_actor()
 AND identity_snapshot->>'permission_version'=security.fde_permission_version()));
-- FDE may generate its own advice; accepting sales decisions remains separate.
CREATE POLICY fde_advice_insert ON insight.business_advice FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor()
 AND actor_user_ref_id=common.current_user_ref_id() AND actor_role_code=common.current_role_code()
 AND security.has_customer_access(customer_id));
CREATE POLICY fde_advice_update ON insight.business_advice FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor() AND actor_user_ref_id=common.current_user_ref_id()
 AND actor_role_code=common.current_role_code() AND security.has_customer_access(customer_id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor() AND actor_user_ref_id=common.current_user_ref_id()
 AND actor_role_code=common.current_role_code() AND security.has_customer_access(customer_id));
DROP POLICY advice_subject_insert ON insight.business_advice;
DROP POLICY advice_subject_update ON insight.business_advice;
CREATE POLICY advice_subject_insert ON insight.business_advice AS RESTRICTIVE FOR INSERT WITH CHECK(
 CASE subject_kind WHEN 'customer' THEN security.has_customer_access(customer_id)
 WHEN 'opportunity' THEN security.has_opportunity_read_access(opportunity_id)
 ELSE EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=visit_id AND v.deleted_at IS NULL) END);
CREATE POLICY advice_subject_update ON insight.business_advice AS RESTRICTIVE FOR UPDATE USING(
 CASE subject_kind WHEN 'customer' THEN security.has_customer_access(customer_id)
 WHEN 'opportunity' THEN security.has_opportunity_read_access(opportunity_id)
 ELSE EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=visit_id AND v.deleted_at IS NULL) END) WITH CHECK(
 CASE subject_kind WHEN 'customer' THEN security.has_customer_access(customer_id)
 WHEN 'opportunity' THEN security.has_opportunity_read_access(opportunity_id)
 ELSE EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=visit_id AND v.deleted_at IS NULL) END);
CREATE POLICY fde_suggestion_decision_guard ON insight.business_suggestion AS RESTRICTIVE FOR UPDATE USING(
 common.current_role_code() NOT IN ('fde','fde_lead'));

-- Files linked to visible visits are readable; private unarchived uploads stay
-- private. No workspace-wide file entitlement is derived from the FDE role.
CREATE POLICY fde_file_read ON ops.file_asset AS RESTRICTIVE FOR SELECT USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR (security.is_fde_actor() AND
 (uploaded_by_user_ref_id=common.current_user_ref_id() OR EXISTS(SELECT 1 FROM activity.visit v
 WHERE v.audio_asset_id=file_asset.id AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')))));
CREATE POLICY fde_file_insert ON ops.file_asset AS RESTRICTIVE FOR INSERT WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR
 (security.fde_visit_entry_enabled() AND uploaded_by_user_ref_id=common.current_user_ref_id()));
CREATE POLICY fde_file_update ON ops.file_asset AS RESTRICTIVE FOR UPDATE USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR
 (security.fde_visit_entry_enabled() AND uploaded_by_user_ref_id=common.current_user_ref_id())) WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR
 (security.fde_visit_entry_enabled() AND uploaded_by_user_ref_id=common.current_user_ref_id()));
CREATE POLICY fde_file_delete ON ops.file_asset AS RESTRICTIVE FOR DELETE USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR
 (security.fde_visit_entry_enabled() AND uploaded_by_user_ref_id=common.current_user_ref_id()));
-- Native database restrictions supplement management HTTP dependencies. A business
-- identity cannot use newly broadened read scope to manage accounts or rules.
DO $$ DECLARE t record; cmd text; BEGIN
 FOR t IN SELECT c.oid,n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind='r' AND c.relrowsecurity AND (n.nspname='config' OR
 (n.nspname='platform' AND c.relname IN ('user_ref','role_binding','team','team_membership','identity_binding')))
 LOOP
 FOREACH cmd IN ARRAY ARRAY['INSERT','UPDATE','DELETE'] LOOP
 IF cmd='INSERT' THEN EXECUTE format('CREATE POLICY fde_admin_insert ON %I.%I AS RESTRICTIVE FOR INSERT WITH CHECK (common.current_role_code() NOT IN (''fde'',''fde_lead''))',t.nspname,t.relname);
 ELSIF cmd='UPDATE' THEN EXECUTE format('CREATE POLICY fde_admin_update ON %I.%I AS RESTRICTIVE FOR UPDATE USING (common.current_role_code() NOT IN (''fde'',''fde_lead'')) WITH CHECK (common.current_role_code() NOT IN (''fde'',''fde_lead''))',t.nspname,t.relname);
 ELSE EXECUTE format('CREATE POLICY fde_admin_delete ON %I.%I AS RESTRICTIVE FOR DELETE USING (common.current_role_code() NOT IN (''fde'',''fde_lead''))',t.nspname,t.relname); END IF;
 END LOOP;
 END LOOP;
END $$;
CREATE POLICY fde_archived_import_read ON activity.visit_import FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.is_fde_actor() AND EXISTS(
 SELECT 1 FROM activity.visit v WHERE v.source_import_id=visit_import.id
 AND v.workspace_id=visit_import.workspace_id AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')));
CREATE OR REPLACE FUNCTION workflow.enqueue_task_notification(p_workspace_id uuid,p_recipient_user_ref_id uuid,
 p_template_code text,p_title text,p_body text,p_task_id uuid,p_dedupe_key text,p_payload jsonb DEFAULT '{}'::jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t workflow.task; event_receipt workflow.task_event; coordinating boolean:=false;
BEGIN
 IF p_workspace_id IS DISTINCT FROM common.current_workspace_id() THEN
  RAISE EXCEPTION 'WORKSPACE_SCOPE_VIOLATION';
 END IF;
 SELECT * INTO t FROM workflow.task WHERE id=p_task_id AND workspace_id=p_workspace_id AND deleted_at IS NULL;
 IF p_template_code IN ('task_reassigned','task_cancelled') THEN
 SELECT e.* INTO event_receipt FROM workflow.task_event e JOIN ops.audit_log a
 ON a.object_type='task_event' AND a.object_id=e.id AND a.workspace_id=e.workspace_id
 AND a.action_code='workflow.task_event.insert' AND a.transaction_id=txid_current()
 WHERE e.task_id=t.id AND e.workspace_id=p_workspace_id AND e.actor_user_ref_id=common.current_user_ref_id()
 AND e.event_type=CASE WHEN p_template_code='task_reassigned' THEN 'reassign' ELSE 'cancel' END
 AND e.to_status=t.status AND p_payload->>'event_version'=t.version_no::text
 ORDER BY e.occurred_at DESC,e.id DESC LIMIT 1;
 coordinating:=event_receipt.id IS NOT NULL AND security.fde_can_coordinate_task(t.id);
 IF NOT coordinating THEN RAISE EXCEPTION 'TASK_NOTIFICATION_FORBIDDEN'; END IF;
 END IF;
 IF t.id IS NULL OR NOT(coordinating OR t.creator_user_ref_id=common.current_user_ref_id()
   OR EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.assignee_user_ref_id=common.current_user_ref_id())
   OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=t.id AND c.user_ref_id=common.current_user_ref_id())) THEN
  RAISE EXCEPTION 'TASK_NOTIFICATION_FORBIDDEN';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=p_recipient_user_ref_id AND u.workspace_id=p_workspace_id
   AND u.status='active' AND u.deleted_at IS NULL) THEN RETURN false; END IF;
 IF NOT((coordinating AND (EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(event_receipt.payload->'previous_owners','[]')) previous
 WHERE previous->>'user_id'=p_recipient_user_ref_id::text) OR COALESCE(event_receipt.payload->'new_owner'->>'user_id'=p_recipient_user_ref_id::text,false)))
   OR t.creator_user_ref_id=p_recipient_user_ref_id
   OR EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.assignee_user_ref_id=p_recipient_user_ref_id)
   OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=t.id AND c.user_ref_id=p_recipient_user_ref_id
     AND EXISTS(SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=c.user_ref_id AND r.workspace_id=c.workspace_id
       AND r.role_code=c.role_code AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to))) THEN
  RETURN false;
 END IF;
 INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,channel_code,template_code,title,body,
   object_type,object_id,status,dedupe_key,payload)
 VALUES(p_workspace_id,p_recipient_user_ref_id,'in_app',p_template_code,p_title,p_body,'task',p_task_id,
   'pending',p_dedupe_key,COALESCE(p_payload,'{}'::jsonb))
 ON CONFLICT(workspace_id,recipient_user_ref_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION security.business_activity_rows(p_start timestamptz,p_end timestamptz)
RETURNS TABLE(event_id text,occurred_at timestamptz,actor_id uuid,actor_name text,actor_role text,actor_department text,
 action_code text,object_type text,object_id uuid,object_name text,customer_id uuid,customer_name text,
 execution_kind text,evidence_kind text,payload jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
WITH raw AS (
 SELECT 'customer:'||c.id AS eid,c.workspace_id AS ws,c.created_at AS at,c.created_by_user_ref_id AS actor,
 'customer.create'::text AS action,'customer'::text AS kind,c.id AS oid,c.name AS label,c.id AS cid,
 'saved_record'::text AS evidence,'human'::text AS executor,
 jsonb_build_object('data_source',c.data_source,'source_workbook',c.import_meta->>'sourceWorkbook','company_reference',c.company_reference,
 'source_note',CASE WHEN c.data_source='excel_import' THEN '历史导入：创建人是系统登记人，不代表原始文件上传人' ELSE '根据已保存的创建人和创建时间展示；对象名称为当前名称' END) AS body
 FROM crm.customer c
 UNION ALL
 SELECT 'opportunity:'||o.id,o.workspace_id,o.created_at,o.created_by_user_ref_id,'opportunity.create','opportunity',o.id,o.name,o.customer_id,'saved_record','human',
 jsonb_build_object('source_note','根据已保存的创建人和创建时间展示；对象名称为当前名称') FROM crm.opportunity o
 UNION ALL
 SELECT 'change:'||b.id,b.workspace_id,b.created_at,b.actor_user_ref_id,
 CASE WHEN b.opportunity_id IS NULL THEN 'customer.update' ELSE 'opportunity.update' END,
 CASE WHEN b.opportunity_id IS NULL THEN 'customer' ELSE 'opportunity' END,COALESCE(b.opportunity_id,b.customer_id),COALESCE(o.name,c.name),b.customer_id,'business_event','human',
 jsonb_build_object('changes',b.changes) FROM crm.business_change b LEFT JOIN crm.opportunity o ON o.id=b.opportunity_id LEFT JOIN crm.customer c ON c.id=b.customer_id
 WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(b.changes) d WHERE d->>'before'='新建')
 UNION ALL
 SELECT 'visit:'||v.id,v.workspace_id,v.archived_at,v.confirmed_by_user_ref_id,'visit.archive','visit',v.id,
 COALESCE(v.archived_fields->>'customer_name',c.name)||' · 拜访记录',v.customer_id,'saved_record','human',
 jsonb_build_object('visit_date',v.interaction_at::date,'recorded_on',v.recorded_on,'communication',v.follow_up_record,'next_action',v.next_action,
 'score',v.follow_up_score,'opportunity_name',o.name,'confirmer',u.display_name,
 'materials',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',i.id,'filename',i.filename,'uploaded_at',i.created_at,'uploader',up.display_name,'status',i.status))
 FROM activity.visit_import i LEFT JOIN platform.user_ref up ON up.id=i.created_by_user_ref_id WHERE i.id=v.source_import_id),'[]'::jsonb))
 FROM activity.visit v LEFT JOIN crm.customer c ON c.id=v.customer_id LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id LEFT JOIN platform.user_ref u ON u.id=v.confirmed_by_user_ref_id
 WHERE v.archived_at IS NOT NULL
 UNION ALL
 SELECT 'upload:'||i.id,i.workspace_id,i.created_at,i.created_by_user_ref_id,'material.upload','visit_import',i.id,i.filename,NULL,'saved_record','human',
 jsonb_build_object('filename',i.filename,'file_size',i.file_size,'processing_status',i.status,'error_message',i.error_message,
 'visits',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',v.id,'customer_id',v.customer_id,'customer_name',c.name,'archived_at',v.archived_at,'confirmer',u.display_name))
 FROM activity.visit v LEFT JOIN crm.customer c ON c.id=v.customer_id LEFT JOIN platform.user_ref u ON u.id=v.confirmed_by_user_ref_id WHERE v.source_import_id=i.id),'[]'::jsonb)) FROM activity.visit_import i
 UNION ALL
 SELECT 'task:'||e.id,e.workspace_id,e.occurred_at,e.actor_user_ref_id,'task.'||e.event_type,'task',e.task_id,t.title,t.customer_id,'business_event','human',
 jsonb_build_object('note',e.note,'from_status',e.from_status,'to_status',e.to_status,'recipient',
 (SELECT string_agg(u.display_name,'、' ORDER BY u.display_name) FROM workflow.task_assignee a JOIN platform.user_ref u ON u.id=a.assignee_user_ref_id WHERE a.task_id=t.id AND a.responsibility='owner'))
 FROM workflow.task_event e JOIN workflow.task t ON t.id=e.task_id
 UNION ALL
 SELECT 'claim-request:'||r.id,r.workspace_id,r.requested_at,r.applicant_user_ref_id,'claim.request','customer',r.customer_id,c.name,r.customer_id,'business_event','human',
 jsonb_build_object('current_status',r.status) FROM crm.customer_claim_request r JOIN crm.customer c ON c.id=r.customer_id
 UNION ALL
 SELECT 'claim-review:'||r.id,r.workspace_id,r.reviewed_at,r.reviewer_user_ref_id,'claim.'||r.status,'customer',r.customer_id,c.name,r.customer_id,'business_event','human',
 jsonb_build_object('reason',r.decision_reason,'applicant',u.display_name) FROM crm.customer_claim_request r JOIN crm.customer c ON c.id=r.customer_id LEFT JOIN platform.user_ref u ON u.id=r.applicant_user_ref_id
 WHERE r.status IN ('approved','rejected') AND r.reviewed_at IS NOT NULL
 UNION ALL
 SELECT 'ownership:'||e.id,e.workspace_id,e.occurred_at,e.actor_user_ref_id,'claim.'||e.event_type,'customer',e.customer_id,c.name,e.customer_id,'business_event','human',
 jsonb_build_object('reason',e.reason,'previous_owner',old.display_name,'owner',n.display_name)
 FROM crm.customer_ownership_event e JOIN crm.customer c ON c.id=e.customer_id LEFT JOIN platform.user_ref old ON old.id=e.previous_owner_user_ref_id LEFT JOIN platform.user_ref n ON n.id=e.owner_user_ref_id
 WHERE e.event_type<>'approved'
 UNION ALL
 SELECT 'audio:'||a.id,a.workspace_id,a.created_at,a.created_by_user_ref_id,'material.transcribe','audio_transcript',a.id,'语音转写',NULL,'saved_record','system',
 jsonb_build_object('purpose',a.payload->>'purpose','duration_seconds',a.payload->'duration_seconds','source_note','已保存转写结果，不代表已经人工确认归档')
 FROM agent.artifact a WHERE a.artifact_type='audio_transcript'
 UNION ALL
 SELECT 'advice:'||s.id,s.workspace_id,s.decided_at,s.decided_by_user_ref_id,'advice.'||s.decision,
 'business_suggestion',s.id,s.title,a.customer_id,'saved_record','human',
 jsonb_build_object('advice_id',a.id,'subject_kind',a.subject_kind,'suggestion',s.title,'evidence',s.evidence,
 'action',s.action,'decision_note',s.decision_note,'task_id',s.task_id,'task_description',t.description,
 'actor_snapshot',snap.actor_name_snapshot,'role_snapshot',snap.actor_role_code,'team_snapshot',snap.actor_team_snapshot)
 FROM insight.business_suggestion s JOIN insight.business_advice a ON a.id=s.advice_id
 LEFT JOIN workflow.task t ON t.id=s.task_id
 LEFT JOIN LATERAL (SELECT l.actor_name_snapshot,l.actor_role_code,l.actor_team_snapshot FROM ops.audit_log l
 WHERE l.workspace_id=s.workspace_id AND l.object_type='business_suggestion' AND l.object_id=s.id
 AND l.changed_fields @> ARRAY['decision'] AND l.after_snapshot->>'decision'=s.decision
 ORDER BY l.id LIMIT 1) snap ON true
 WHERE s.decision IN ('adopted','no_task') AND s.decided_at IS NOT NULL
), audit_candidates AS (
 SELECT a.*,
 CASE WHEN a.object_type IN ('user_ref','role_binding','team_membership','password_credential') THEN 'account'
 WHEN a.object_type IN ('customer','contact') THEN 'customer' ELSE a.object_type END AS family,
 CASE WHEN a.object_type IN ('role_binding','team_membership','password_credential') THEN COALESCE(a.after_snapshot,a.before_snapshot)->>'user_ref_id'
 WHEN a.object_type='contact' THEN COALESCE(a.after_snapshot,a.before_snapshot)->>'customer_id' ELSE a.object_id::text END AS subject
 FROM ops.audit_log a WHERE a.occurred_at>=p_start AND a.occurred_at<p_end AND a.workspace_id=common.current_workspace_id() AND (
 (a.object_type IN ('customer','contact','visit') AND a.action_code LIKE '%.update') OR
 a.object_type IN ('user_ref','role_binding','team_membership','password_credential','team','customer_actual','sales_target','opportunity_quote_reference','ai_usage_rule','partner','opportunity_participant','visit_participant','company_rule') OR
 (a.object_type='visit_import' AND a.action_code LIKE '%.update' AND a.changed_fields @> ARRAY['status'] AND a.after_snapshot->>'status' IN ('succeeded','failed')) OR
 a.action_code='http.export')
 AND (a.object_type<>'customer' OR cardinality(array_remove(a.changed_fields,'owner_user_ref_id'))>0)
 AND NOT (a.object_type IN ('customer','contact') AND a.request_id IS NOT NULL AND EXISTS(
 SELECT 1 FROM ops.audit_log b WHERE b.workspace_id=a.workspace_id AND b.request_id=a.request_id AND b.object_type='business_change'
 AND b.after_snapshot->>'customer_id'=CASE WHEN a.object_type='customer' THEN a.object_id::text ELSE COALESCE(a.after_snapshot,a.before_snapshot)->>'customer_id' END))
), grouped AS (
 SELECT min(id) AS first_id,max(occurred_at) AS at,workspace_id AS ws,actor_user_ref_id AS actor,family,subject,
 max(actor_name_snapshot) AS actor_label,max(actor_role_code) AS role_label,max(actor_team_snapshot) AS team_label,
 max(execution_kind) AS executor,
 jsonb_agg(jsonb_build_object('type',object_type,'operation',action_code,'label',object_label,'before',before_snapshot,'after',after_snapshot,'fields',changed_fields,'result',result_code) ORDER BY id) AS entries
 FROM audit_candidates GROUP BY workspace_id,actor_user_ref_id,family,subject,COALESCE('tx:'||transaction_id::text||':'||COALESCE(request_id::text,''),'request:'||request_id::text,'row:'||id::text)
), audit_raw AS (
 SELECT 'audit:'||g.first_id AS eid,g.ws,g.at,g.actor,
 CASE WHEN g.family='account' THEN 'account.change' WHEN g.family='customer' THEN 'customer.update'
 WHEN g.family='visit' THEN 'visit.supplement' WHEN g.family='visit_import' THEN 'material.process'
 WHEN g.family='api_request' THEN 'data.export' WHEN g.family='team' THEN 'department.change'
 WHEN g.family='customer_actual' THEN 'actual.change' WHEN g.family='sales_target' THEN 'target.change'
 WHEN g.family='opportunity_participant' THEN 'opportunity.fde_members' WHEN g.family='visit_participant' THEN 'visit.fde_participants'
 WHEN g.family='company_rule' THEN 'company_rule.change' WHEN g.family='opportunity_quote_reference' THEN 'quote.change' WHEN g.family='partner' THEN 'partner.change' ELSE 'ai_rule.change' END AS action,
 g.family AS kind,g.subject::uuid AS oid,
 COALESCE(u.display_name,c.name,project.name,visit_customer.name,rule.name,CASE WHEN g.family IN ('visit','visit_import') THEN NULL ELSE g.entries->0->>'label' END,'业务记录') AS label,
 CASE WHEN g.family='opportunity_participant' THEN project.customer_id::text
 WHEN g.family='visit_participant' THEN visit.customer_id::text WHEN g.family='customer' THEN g.subject ELSE COALESCE(g.entries->0->'after',g.entries->0->'before')->>'customer_id' END::uuid AS cid,
 'row_audit'::text AS evidence,CASE WHEN g.family='visit_import' THEN 'system' ELSE COALESCE(g.executor,'unknown') END AS executor,
 jsonb_build_object('audit_id',g.first_id,'entries',g.entries,'actor_snapshot',g.actor_label,'role_snapshot',g.role_label,'team_snapshot',g.team_label,
 'changes',CASE WHEN g.family IN ('opportunity_participant','visit_participant') THEN
 (SELECT jsonb_agg(jsonb_build_object('label',COALESCE(entry->>'label','参与同事'),
 'before',CASE WHEN entry->'before' IS NULL OR entry->'before'='null'::jsonb THEN '未参与' ELSE '参与中' END,
 'after',CASE WHEN entry->'after' IS NULL OR entry->'after'='null'::jsonb THEN '已移除'
 WHEN g.family='opportunity_participant' AND entry->'after'->>'valid_to'<>'infinity' THEN '已结束协助' ELSE '参与中' END))
 FROM jsonb_array_elements(g.entries) entry) ELSE '[]'::jsonb END,
 'rule_name',rule.name,'rule_version',rule.version_no,'change_reason',rule.change_reason) AS body
 FROM grouped g LEFT JOIN platform.user_ref u ON g.family='account' AND u.id::text=g.subject LEFT JOIN crm.customer c ON g.family='customer' AND c.id::text=g.subject
 LEFT JOIN crm.opportunity project ON g.family='opportunity_participant' AND project.id::text=g.subject
 LEFT JOIN activity.visit visit ON g.family='visit_participant' AND visit.id::text=g.subject
 LEFT JOIN crm.customer visit_customer ON visit_customer.id=visit.customer_id
 LEFT JOIN config.rule_set rule ON g.family='company_rule' AND rule.id::text=g.subject
), combined AS (SELECT * FROM raw UNION ALL SELECT * FROM audit_raw)
SELECT r.eid,r.at,r.actor,COALESCE(r.body->>'actor_snapshot',snapshot.actor_name_snapshot,u.display_name,'未记录'),COALESCE(r.body->>'role_snapshot',snapshot.actor_role_code),COALESCE(r.body->>'team_snapshot',snapshot.actor_team_snapshot),
 r.action,r.kind,r.oid,r.label,r.cid,c.name,r.executor,r.evidence,security.audit_redact(r.body)
FROM combined r LEFT JOIN platform.user_ref u ON u.id=r.actor LEFT JOIN crm.customer c ON c.id=r.cid
LEFT JOIN LATERAL (SELECT a.actor_name_snapshot,a.actor_role_code,a.actor_team_snapshot FROM ops.audit_log a
 WHERE r.evidence<>'row_audit' AND a.workspace_id=r.ws AND a.actor_user_ref_id=r.actor AND a.object_id::text=split_part(r.eid,':',2)
 AND a.object_type=CASE split_part(r.eid,':',1) WHEN 'customer' THEN 'customer' WHEN 'opportunity' THEN 'opportunity'
 WHEN 'change' THEN 'business_change' WHEN 'visit' THEN 'visit' WHEN 'upload' THEN 'visit_import' WHEN 'task' THEN 'task_event'
 WHEN 'claim-request' THEN 'customer_claim_request' WHEN 'claim-review' THEN 'customer_claim_request' WHEN 'ownership' THEN 'customer_ownership_event' ELSE '' END
 AND CASE WHEN r.action='visit.archive' THEN a.after_snapshot->>'status'='archived' AND a.before_snapshot->>'status' IS DISTINCT FROM 'archived'
 WHEN split_part(r.eid,':',1)='claim-review' THEN a.after_snapshot->>'status'=split_part(r.action,'.',2) AND a.action_code LIKE '%.update'
 ELSE a.action_code LIKE '%.insert' END
 ORDER BY a.id LIMIT 1) snapshot ON true
WHERE security.management_actor() AND r.ws=common.current_workspace_id() AND r.at>=p_start AND r.at<p_end;
$$;


-- Copy only the established runtime ACLs, including views used by the compatibility
-- reader. Newly created objects must work when migrations run as a maintenance user.
DO $$ DECLARE permission record; BEGIN
 FOR permission IN SELECT DISTINCT r.rolname,a.privilege_type
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
 WHERE n.nspname='activity' AND c.relname='visit' AND a.grantee<>c.relowner
 AND a.privilege_type IN ('SELECT','INSERT','UPDATE','DELETE')
 LOOP
 EXECUTE format('GRANT %s ON activity.visit_participant TO %I',permission.privilege_type,permission.rolname);
 IF permission.privilege_type='SELECT' THEN
 EXECUTE format('GRANT SELECT ON activity.v_visit_collaborators TO %I',permission.rolname); END IF;
 END LOOP;
 END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V069','FDE独立身份、协助与实际参与事实、客户全景只读、任务资格和变更审计');
COMMIT;
