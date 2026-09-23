BEGIN;
SET LOCAL check_function_bodies=on;

-- New global default is a new published version. Preserve all previous rule
-- bodies and all workspace overlays; operators publish those through the normal
-- company-rule service. Explicit user/role overrides retain their precedence.
DO $$ DECLARE prior config.rule_set; published uuid; definition jsonb; next_version integer; BEGIN
 SELECT * INTO prior FROM config.rule_set WHERE rule_code='fde_capabilities'
 AND workspace_id IS NULL AND status='active' AND effective_from<=clock_timestamp()
 AND effective_to>clock_timestamp() ORDER BY version_no DESC LIMIT 1;
 SELECT COALESCE(max(version_no),0)+1 INTO next_version FROM config.rule_set
 WHERE rule_code='fde_capabilities' AND workspace_id IS NULL;
 definition:=COALESCE(prior.definition,'{"schema_version":1,"role_overrides":{},"user_overrides":{}}'::jsonb)
   || '{"visit_entry_enabled":true}'::jsonb;
 INSERT INTO config.rule_set(rule_code,name,rule_type,version_no,status,definition,
   base_rule_id,change_reason,published_at,effective_from)
 VALUES('fde_capabilities','FDE本人商机拜访录入','company_policy',next_version,'active',
   definition,prior.id,'V072：默认开放FDE本人直接参与商机的拜访录入',clock_timestamp(),clock_timestamp())
 RETURNING id INTO published;
 INSERT INTO ops.audit_log(actor_role_code,action_code,module_code,object_type,object_id,
   object_label,before_snapshot,after_snapshot,result_code,sensitivity)
 VALUES('system','company_rule.migration_publish','config','company_rule',published,
   'V072 FDE本人录入默认规则',jsonb_build_object('rule_id',prior.id,'definition',prior.definition),
   jsonb_build_object('rule_id',published,'rule_code','fde_capabilities','definition',definition,
     'migration','V072','workspace_overlays','preserved'),'success','internal');
END $$;

-- READ remains the entire customer panorama. Recording qualification is the
-- current actor's own live opportunity membership, including for team leaders.
-- Keep the configurable entry switch separate from this object qualification.
CREATE FUNCTION security.fde_can_record_opportunity(p_opportunity uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.is_fde_actor() AND EXISTS(
 SELECT 1 FROM crm.opportunity_participant p
 JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id
 JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 WHERE p.workspace_id=common.current_workspace_id() AND p.opportunity_id=p_opportunity
 AND p.user_ref_id=common.current_user_ref_id() AND p.participant_role='fde'
 AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 AND o.deleted_at IS NULL AND c.deleted_at IS NULL);
 $$;
COMMENT ON FUNCTION security.fde_can_record_opportunity(uuid) IS
 'FDE本人当前有效商机名单资格；负责人团队阅读不等于本人录入资格，功能开关另行检查';

CREATE OR REPLACE FUNCTION security.can_write_visit(p_visit uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM activity.visit v
 WHERE v.id=p_visit AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
 AND CASE WHEN common.current_role_code() IN ('fde','fde_lead') THEN
 security.fde_visit_entry_enabled() AND v.recorder_user_ref_id=common.current_user_ref_id()
 AND v.created_by_user_ref_id=common.current_user_ref_id()
 AND (v.confirmed_by_user_ref_id IS NULL OR v.confirmed_by_user_ref_id=common.current_user_ref_id())
 AND (v.status NOT IN ('confirmed','archived') OR v.confirmed_by_user_ref_id=common.current_user_ref_id())
 AND security.fde_can_record_opportunity(v.opportunity_id)
 AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=v.opportunity_id
   AND o.workspace_id=v.workspace_id AND o.customer_id=v.customer_id AND o.deleted_at IS NULL)
 ELSE security.management_actor() OR v.recorder_user_ref_id=common.current_user_ref_id()
 OR security.has_customer_write_access(v.customer_id) END);
 $$;

DROP POLICY fde_visit_insert ON activity.visit;
CREATE POLICY fde_visit_insert ON activity.visit FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.fde_visit_entry_enabled()
 AND recorder_user_ref_id=common.current_user_ref_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND security.fde_can_record_opportunity(opportunity_id)
 AND (confirmed_by_user_ref_id IS NULL OR confirmed_by_user_ref_id=common.current_user_ref_id())
 AND (status NOT IN ('confirmed','archived') OR confirmed_by_user_ref_id=common.current_user_ref_id()));

DROP POLICY fde_visit_write_guard ON activity.visit;
CREATE POLICY fde_visit_write_guard ON activity.visit AS RESTRICTIVE FOR INSERT WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR (
 workspace_id=common.current_workspace_id() AND security.fde_visit_entry_enabled()
 AND recorder_user_ref_id=common.current_user_ref_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND (confirmed_by_user_ref_id IS NULL OR confirmed_by_user_ref_id=common.current_user_ref_id())
 AND (status NOT IN ('confirmed','archived') OR confirmed_by_user_ref_id=common.current_user_ref_id())
 AND security.fde_can_record_opportunity(opportunity_id)));

-- WITH CHECK must inspect NEW column values. Calling can_write_visit(id) there
-- alone would read the old stored row and miss a changed owner/confirmer/project.
DROP POLICY fde_visit_update_guard ON activity.visit;
CREATE POLICY fde_visit_update_guard ON activity.visit AS RESTRICTIVE FOR UPDATE USING(
 common.current_role_code() NOT IN ('fde','fde_lead') OR security.can_write_visit(id)) WITH CHECK(
 common.current_role_code() NOT IN ('fde','fde_lead') OR (
 workspace_id=common.current_workspace_id() AND security.fde_visit_entry_enabled()
 AND recorder_user_ref_id=common.current_user_ref_id() AND created_by_user_ref_id=common.current_user_ref_id()
 AND (confirmed_by_user_ref_id IS NULL OR confirmed_by_user_ref_id=common.current_user_ref_id())
 AND (status NOT IN ('confirmed','archived') OR confirmed_by_user_ref_id=common.current_user_ref_id())
 AND security.fde_can_record_opportunity(opportunity_id)));

DROP POLICY visit_opportunity_boundary_insert ON activity.visit;
CREATE POLICY visit_opportunity_boundary_insert ON activity.visit AS RESTRICTIVE FOR INSERT WITH CHECK(
 CASE WHEN common.current_role_code() IN ('fde','fde_lead') THEN
 security.fde_can_record_opportunity(opportunity_id) AND EXISTS(
 SELECT 1 FROM crm.opportunity o WHERE o.id=visit.opportunity_id AND o.customer_id=visit.customer_id
 AND o.workspace_id=visit.workspace_id AND o.deleted_at IS NULL)
 ELSE opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id) END);
DROP POLICY visit_opportunity_boundary_update ON activity.visit;
CREATE POLICY visit_opportunity_boundary_update ON activity.visit AS RESTRICTIVE FOR UPDATE USING(
 CASE WHEN common.current_role_code() IN ('fde','fde_lead') THEN security.fde_can_record_opportunity(opportunity_id)
 ELSE opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id) END) WITH CHECK(
 CASE WHEN common.current_role_code() IN ('fde','fde_lead') THEN
 security.fde_can_record_opportunity(opportunity_id) AND EXISTS(
 SELECT 1 FROM crm.opportunity o WHERE o.id=visit.opportunity_id AND o.customer_id=visit.customer_id
 AND o.workspace_id=visit.workspace_id AND o.deleted_at IS NULL)
 ELSE opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id) END);

-- Native immutable recording identity is the business statistic source. Audit
-- is consulted only once for trustworthy historical backfill, never per report.
ALTER TABLE activity.visit ADD COLUMN recording_role_code_snapshot text
 CHECK(recording_role_code_snapshot IS NULL OR recording_role_code_snapshot IN
 ('sales','supervisor','manager','operations','administrator','fde','fde_lead'));
ALTER TABLE activity.visit ADD COLUMN recording_team_id_snapshot uuid;
ALTER TABLE activity.visit ADD CONSTRAINT visit_recording_team_workspace_fk
 FOREIGN KEY(recording_team_id_snapshot,workspace_id) REFERENCES platform.team(id,workspace_id);
COMMENT ON COLUMN activity.visit.recording_role_code_snapshot IS
 '数据库写入时记录的岗位，不可修改；旧FDE仅按本人创建及首次归档事务审计回填，未知保持空';
COMMENT ON COLUMN activity.visit.recording_team_id_snapshot IS
 '录入时有效部门快照，不因调动或后续字段补充改变；旧数据从创建事务审计回填';

WITH evidenced AS (
 SELECT v.id,created.actor_role_code,
 NULLIF(created.after_snapshot->>'recorder_team_id','')::uuid AS team_id
 FROM activity.visit v
 JOIN LATERAL(SELECT a.actor_user_ref_id,a.actor_role_code,a.after_snapshot FROM ops.audit_log a
   WHERE a.workspace_id=v.workspace_id AND a.object_type='visit' AND a.object_id=v.id
   AND a.action_code='activity.visit.insert' ORDER BY a.id LIMIT 1) created ON true
 JOIN LATERAL(SELECT a.actor_user_ref_id,a.actor_role_code,a.after_snapshot FROM ops.audit_log a
   WHERE a.workspace_id=v.workspace_id AND a.object_type='visit' AND a.object_id=v.id
   AND a.action_code IN ('activity.visit.insert','activity.visit.update')
   AND a.after_snapshot->>'status'='archived'
   AND a.before_snapshot->>'status' IS DISTINCT FROM 'archived' ORDER BY a.id LIMIT 1) archived ON true
 WHERE v.status='archived' AND v.created_by_user_ref_id=v.recorder_user_ref_id
 AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
 AND created.actor_user_ref_id=v.recorder_user_ref_id AND created.actor_role_code IN ('fde','fde_lead')
 AND created.after_snapshot->>'created_by_user_ref_id'=v.recorder_user_ref_id::text
 AND created.after_snapshot->>'recorder_user_ref_id'=v.recorder_user_ref_id::text
 AND archived.actor_user_ref_id=v.recorder_user_ref_id AND archived.actor_role_code IN ('fde','fde_lead')
 AND archived.after_snapshot->>'created_by_user_ref_id'=v.recorder_user_ref_id::text
 AND archived.after_snapshot->>'recorder_user_ref_id'=v.recorder_user_ref_id::text
 AND archived.after_snapshot->>'confirmed_by_user_ref_id'=v.recorder_user_ref_id::text
)
UPDATE activity.visit v SET recording_role_code_snapshot=e.actor_role_code,
 recording_team_id_snapshot=e.team_id FROM evidenced e WHERE v.id=e.id;

CREATE FUNCTION activity.capture_visit_recording_identity() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF NEW.recording_role_code_snapshot IS DISTINCT FROM OLD.recording_role_code_snapshot
  OR NEW.recording_team_id_snapshot IS DISTINCT FROM OLD.recording_team_id_snapshot THEN
   RAISE EXCEPTION '拜访录入岗位及部门快照不可修改' USING ERRCODE='23514';
  END IF;
  IF OLD.recording_role_code_snapshot IN ('fde','fde_lead') THEN
   IF NEW.created_by_user_ref_id IS DISTINCT FROM OLD.created_by_user_ref_id
   OR NEW.recorder_user_ref_id IS DISTINCT FROM OLD.recorder_user_ref_id
   OR (OLD.status IN ('confirmed','archived')
       AND NEW.confirmed_by_user_ref_id IS DISTINCT FROM OLD.confirmed_by_user_ref_id) THEN
    RAISE EXCEPTION 'FDE本人录入及归档身份不可替换' USING ERRCODE='23514';
   END IF;
   IF NEW.status IN ('confirmed','archived') AND NEW.status IS DISTINCT FROM OLD.status
   AND (security.fde_visit_entry_enabled()
    AND NEW.created_by_user_ref_id=common.current_user_ref_id()
    AND NEW.recorder_user_ref_id=common.current_user_ref_id()
    AND NEW.confirmed_by_user_ref_id=common.current_user_ref_id()
    AND security.fde_can_record_opportunity(NEW.opportunity_id)) IS NOT TRUE THEN
    RAISE EXCEPTION '仅本人可确认归档当前参与商机的FDE拜访' USING ERRCODE='42501';
   END IF;
  END IF;
  RETURN NEW;
 END IF;
 NEW.recording_role_code_snapshot:=CASE WHEN common.current_role_code() IN
 ('sales','supervisor','manager','operations','administrator','fde','fde_lead')
 THEN common.current_role_code() ELSE NULL END;
 IF common.current_role_code() IN ('fde','fde_lead') THEN
  SELECT tm.team_id INTO NEW.recording_team_id_snapshot FROM platform.team_membership tm
  WHERE tm.workspace_id=NEW.workspace_id AND tm.user_ref_id=common.current_user_ref_id()
  AND tm.membership_role IN ('fde','fde_lead')
  AND security.fde_user_is_active(common.current_user_ref_id(),common.current_role_code(),tm.team_id)
  AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
  ORDER BY (tm.team_id=NEW.recorder_team_id) DESC,tm.is_primary DESC,tm.team_id LIMIT 1;
 ELSE
  NEW.recording_team_id_snapshot:=NEW.recorder_team_id;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER visit_recording_identity BEFORE INSERT OR UPDATE ON activity.visit
 FOR EACH ROW EXECUTE FUNCTION activity.capture_visit_recording_identity();

CREATE INDEX visit_fde_recording_author ON activity.visit(workspace_id,recorder_user_ref_id,interaction_at DESC)
 WHERE deleted_at IS NULL AND status='archived' AND recording_role_code_snapshot IN ('fde','fde_lead');
CREATE INDEX visit_fde_recording_team ON activity.visit(workspace_id,recording_team_id_snapshot,interaction_at DESC)
 WHERE deleted_at IS NULL AND status='archived' AND recording_role_code_snapshot IN ('fde','fde_lead');

CREATE FUNCTION security.fde_recorded_visit_history() RETURNS TABLE(
 visit_id uuid,user_ref_id uuid,customer_id uuid,customer_name text,opportunity_id uuid,opportunity_name text,
 interaction_at timestamptz,recorder_name text,team_id_at_event uuid)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT v.id,v.recorder_user_ref_id,v.customer_id,COALESCE(v.archived_fields->>'customer_name',c.name),
 v.opportunity_id,COALESCE(v.archived_fields->>'opportunity_name',o.name),v.interaction_at,u.display_name,
 v.recording_team_id_snapshot
 FROM activity.visit v
 LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
 LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
 LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id AND u.workspace_id=v.workspace_id
 WHERE security.is_fde_actor() AND v.workspace_id=common.current_workspace_id()
 AND v.deleted_at IS NULL AND v.status='archived'
 AND v.recording_role_code_snapshot IN ('fde','fde_lead')
 AND v.created_by_user_ref_id=v.recorder_user_ref_id AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
 AND (v.recorder_user_ref_id=common.current_user_ref_id() OR (common.current_role_code()='fde_lead'
 AND EXISTS(SELECT 1 FROM platform.team_membership tm JOIN platform.role_binding rb
 ON rb.user_ref_id=tm.user_ref_id AND rb.workspace_id=tm.workspace_id AND rb.role_code='fde_lead'
 WHERE tm.user_ref_id=common.current_user_ref_id() AND tm.workspace_id=v.workspace_id
 AND tm.team_id=v.recording_team_id_snapshot AND tm.membership_role='fde_lead'
 AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',tm.team_id)
 AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
 AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
 AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to)));
 $$;
COMMENT ON FUNCTION security.fde_recorded_visit_history() IS
 'FDE本人创建/记录/确认的归档摘要，岗位与部门取不可变历史快照；负责人的历史部门边界沿用参与历史，详情仍由RLS限制';

INSERT INTO ops.schema_migration(version,description)
 VALUES('V072','FDE默认本人商机录入、数据库写入窄权限及不可变记录身份统计');
COMMIT;
