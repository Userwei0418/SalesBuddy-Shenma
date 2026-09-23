BEGIN;
SET LOCAL check_function_bodies=on;

-- Historical source identity is not a fabricated company approval number.
-- The ordinary manual-customer verification and claim rules stay unchanged.
CREATE OR REPLACE FUNCTION crm.guard_customer_administration() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN RETURN NEW; END IF;
 IF TG_OP='INSERT' THEN
  IF COALESCE(NEW.import_meta->>'import_type','')='crm_history'
   AND current_setting('app.feishu_historical_import',true)='on'
   AND security.management_actor() AND NEW.workspace_id=common.current_workspace_id()
   AND NEW.created_by_user_ref_id=common.current_user_ref_id() AND NEW.owner_user_ref_id IS NULL
   AND NEW.company_reference IS NULL AND NEW.company_verified_at IS NULL AND NEW.company_verified_by IS NULL
   AND EXISTS(SELECT 1 FROM ops.crm_import_record r JOIN ops.crm_import_batch b ON b.id=r.batch_id AND b.workspace_id=r.workspace_id
    WHERE r.id=(NEW.import_meta->>'import_record_id')::uuid AND r.workspace_id=NEW.workspace_id
    AND r.object_kind='customer' AND r.target_id=NEW.id AND r.status='approved'
    AND NEW.import_meta->>'import_batch_id'=r.batch_id::text
    AND NEW.import_meta->>'source_key'=concat_ws(':',b.source_system,b.source_base_id,r.source_table_id,r.source_record_id)
    AND NEW.import_meta->'source_fields'=r.raw_fields AND NEW.name=r.normalized->>'name'
    AND b.status IN ('approved','applying','applied')) THEN RETURN NEW; END IF;
  IF NOT security.management_actor() OR NEW.owner_user_ref_id IS NOT NULL OR NULLIF(btrim(NEW.company_reference),'') IS NULL
   OR NEW.company_verified_by IS DISTINCT FROM common.current_user_ref_id() OR NEW.company_verified_at IS NULL THEN
   RAISE EXCEPTION '仅运营可在核实公司建档后创建未认领客户' USING ERRCODE='42501';
  END IF;
 ELSIF NEW.owner_user_ref_id IS DISTINCT FROM OLD.owner_user_ref_id
  AND current_setting('app.ownership_transition',true) IS DISTINCT FROM 'on' THEN
  RAISE EXCEPTION '客户归属必须通过认领审批或释放变更' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;

ALTER TABLE crm.customer_ownership_event
 ADD COLUMN import_record_id uuid,
 ADD CONSTRAINT customer_history_owner_source_fk FOREIGN KEY(import_record_id,workspace_id)
  REFERENCES ops.crm_import_record(id,workspace_id),
 ADD CONSTRAINT customer_history_owner_source_required CHECK(event_type<>'history_restored' OR import_record_id IS NOT NULL);
ALTER TABLE crm.customer_ownership_event DROP CONSTRAINT customer_ownership_event_event_type_check;
ALTER TABLE crm.customer_ownership_event ADD CONSTRAINT customer_ownership_event_event_type_check
 CHECK(event_type IN ('approved','released','legacy_resolved','history_restored'));
CREATE UNIQUE INDEX customer_history_owner_once ON crm.customer_ownership_event(import_record_id)
 WHERE event_type='history_restored';
CREATE FUNCTION security.restore_historical_customer_ownership(p_record uuid,p_user uuid,p_team uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ops.crm_import_record; c crm.customer; o crm.customer_ownership; prior_setting text;
BEGIN
 IF NOT security.management_actor() OR current_setting('app.feishu_historical_import',true) IS DISTINCT FROM 'on'
 THEN RAISE insufficient_privilege; END IF;
 SELECT x.* INTO r FROM ops.crm_import_record x JOIN ops.crm_import_batch b ON b.id=x.batch_id AND b.workspace_id=x.workspace_id
 WHERE x.id=p_record AND x.workspace_id=common.current_workspace_id() AND x.object_kind='customer'
  AND x.status='approved' AND b.status IN ('approved','applying') FOR UPDATE OF x;
 IF NOT FOUND OR jsonb_typeof(r.normalized->'source_owner_names') IS DISTINCT FROM 'array'
  OR jsonb_array_length(r.normalized->'source_owner_names')<>1
  OR NULLIF(btrim(r.normalized->'source_owner_names'->>0),'') IS NULL THEN RAISE insufficient_privilege; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
  JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
  WHERE u.id=p_user AND u.workspace_id=r.workspace_id AND u.status='active' AND u.deleted_at IS NULL
   AND upper(u.account_code)=upper(r.normalized->>'current_owner_account') AND tm.team_id=p_team
   AND tm.membership_role IN ('sales','supervisor','manager')
   AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
   AND EXISTS(SELECT 1 FROM platform.role_binding rb WHERE rb.workspace_id=u.workspace_id AND rb.user_ref_id=u.id
    AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
    AND rb.role_code=tm.membership_role
    AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to)) THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO c FROM crm.customer WHERE id=r.target_id AND workspace_id=r.workspace_id AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO o FROM crm.customer_ownership WHERE customer_id=c.id AND workspace_id=r.workspace_id FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 IF o.state<>'unclaimed' OR o.owner_user_ref_id IS NOT NULL OR c.owner_user_ref_id IS NOT NULL THEN
  RETURN jsonb_build_object('status','preserved_existing_ownership','customer_id',c.id);
 END IF;
 IF EXISTS(SELECT 1 FROM crm.customer_claim_request WHERE customer_id=c.id AND workspace_id=c.workspace_id AND status='pending') THEN
  RAISE EXCEPTION 'pending customer claim requires explicit reconciliation' USING ERRCODE='40001';
 END IF;
 IF c.import_meta->>'import_record_id' IS DISTINCT FROM r.id::text
  AND (r.expected_target_version IS NULL OR c.version_no<>r.expected_target_version) THEN
  RAISE EXCEPTION 'customer changed after history ownership review' USING ERRCODE='40001';
 END IF;
 UPDATE crm.customer_ownership SET state='claimed',owner_user_ref_id=p_user,version_no=version_no+1,updated_at=clock_timestamp()
  WHERE customer_id=c.id AND workspace_id=c.workspace_id;
 prior_setting:=current_setting('app.ownership_transition',true);
 PERFORM set_config('app.ownership_transition','on',true);
 UPDATE crm.customer SET owner_user_ref_id=p_user,owner_team_id=p_team WHERE id=c.id AND workspace_id=c.workspace_id;
 PERFORM set_config('app.ownership_transition',COALESCE(prior_setting,''),true);
 INSERT INTO crm.customer_ownership_event(workspace_id,customer_id,event_type,owner_user_ref_id,actor_user_ref_id,reason,import_record_id)
 VALUES(c.workspace_id,c.id,'history_restored',p_user,common.current_user_ref_id(),'历史源唯一在职销售归属恢复；非认领审批',r.id);
 RETURN jsonb_build_object('status','history_restored','customer_id',c.id,'owner_user_ref_id',p_user,'owner_team_id',p_team);
END $$;
REVOKE ALL ON FUNCTION security.restore_historical_customer_ownership(uuid,uuid,uuid) FROM PUBLIC;

ALTER TABLE crm.partner
 ADD COLUMN short_name text,
 ADD COLUMN principal_name text,
 ADD COLUMN channel_manager_user_ref_id uuid,
 ADD COLUMN original_channel_manager_name text,
 ADD COLUMN priority text,
 ADD COLUMN progress text,
 ADD COLUMN grade text,
 ADD COLUMN signed_on date,
 ADD COLUMN partner_type text,
 ADD COLUMN region text,
 ADD COLUMN province text,
 ADD COLUMN note text,
 ADD COLUMN import_meta jsonb NOT NULL DEFAULT '{}',
 ADD CONSTRAINT partner_channel_manager_workspace_fk FOREIGN KEY(channel_manager_user_ref_id,workspace_id)
  REFERENCES platform.user_ref(id,workspace_id);
COMMENT ON COLUMN crm.partner.principal_name IS '伙伴方负责人文本，不是本公司账号或渠道经理';
CREATE TRIGGER trg_partner_touch BEFORE UPDATE ON crm.partner
 FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();

CREATE TABLE crm.opportunity_related_partner (
 workspace_id uuid NOT NULL,
 opportunity_id uuid NOT NULL,
 partner_id uuid NOT NULL,
 created_by_user_ref_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(opportunity_id,partner_id),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(partner_id,workspace_id) REFERENCES crm.partner(id,workspace_id),
 FOREIGN KEY(created_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE INDEX opportunity_related_partner_reverse ON crm.opportunity_related_partner(workspace_id,partner_id,opportunity_id);
ALTER TABLE crm.opportunity_related_partner ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_related_partner FORCE ROW LEVEL SECURITY;
CREATE POLICY associated_partner_read ON crm.opportunity_related_partner FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.has_opportunity_read_access(opportunity_id));
CREATE POLICY associated_partner_insert ON crm.opportunity_related_partner FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.has_opportunity_access(opportunity_id));
CREATE POLICY associated_partner_delete ON crm.opportunity_related_partner FOR DELETE USING(
 workspace_id=common.current_workspace_id() AND security.has_opportunity_access(opportunity_id));
COMMENT ON TABLE crm.opportunity_related_partner IS '普通关联伙伴；不表示伙伴转售，独立于sales_channel与partner_id';
ALTER TABLE crm.opportunity DROP CONSTRAINT opportunity_partner_channel_check;
ALTER TABLE crm.opportunity ADD CONSTRAINT opportunity_partner_channel_check CHECK(
 (sales_channel='partner' AND (partner_id IS NOT NULL OR COALESCE(import_meta->>'import_type','')='crm_history'))
 OR (sales_channel IN ('direct','unknown') AND partner_id IS NULL));

ALTER TABLE crm.opportunity
 ADD COLUMN original_owner_name text,
 ADD COLUMN original_owner_captured boolean NOT NULL DEFAULT false,
 ADD COLUMN ownership_resolution text NOT NULL DEFAULT 'legacy' CHECK(ownership_resolution IN ('legacy','confirmed','provisional','unassigned')),
 ADD COLUMN ownership_resolution_evidence jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(ownership_resolution_evidence)='object'),
 ADD COLUMN expected_close_year integer,
 ADD COLUMN expected_close_quarter integer,
 ADD CONSTRAINT opportunity_close_quarter_precision CHECK(
  (expected_close_year IS NULL AND expected_close_quarter IS NULL) OR
  (expected_close_year BETWEEN 2000 AND 2100 AND expected_close_quarter BETWEEN 1 AND 4
   AND expected_close_year IS NOT NULL AND expected_close_quarter IS NOT NULL)),
 ADD CONSTRAINT opportunity_close_date_consistent CHECK(expected_close_date IS NULL OR expected_close_year IS NULL OR
  (extract(year FROM expected_close_date)=expected_close_year AND extract(quarter FROM expected_close_date)=expected_close_quarter)),
 ADD CONSTRAINT opportunity_owner_resolution_consistent CHECK(ownership_resolution='legacy' OR
  (ownership_resolution='unassigned' AND owner_user_ref_id IS NULL) OR
  (ownership_resolution IN ('confirmed','provisional') AND owner_user_ref_id IS NOT NULL));
COMMENT ON COLUMN crm.opportunity.original_owner_name IS '源商机销售原值；原字段空必须保持NULL，不把客户销售推定结果当原值';
COMMENT ON COLUMN crm.opportunity.expected_close_quarter IS '仅年季度明确时与expected_close_year一起保存；不生成虚构具体关单日';
ALTER TABLE crm.opportunity_forecast ADD COLUMN collection_confidence text
 CHECK(collection_confidence IN ('high','low'));
COMMENT ON COLUMN crm.opportunity_forecast.collection_confidence IS '原始回款信心度：high=高(>=70%)，low=低(<70%)；NULL未知，不作为金额加权概率';

ALTER TABLE activity.visit
 ALTER COLUMN customer_id DROP NOT NULL,
 ALTER COLUMN recorder_user_ref_id DROP NOT NULL,
 ADD COLUMN partner_id uuid,
 ADD COLUMN original_recorder_name text,
 ADD COLUMN original_recorder_captured boolean NOT NULL DEFAULT false,
 ADD COLUMN manager_user_ref_id uuid,
 ADD CONSTRAINT visit_partner_workspace_fk FOREIGN KEY(workspace_id,partner_id) REFERENCES crm.partner(workspace_id,id),
 ADD CONSTRAINT visit_manager_workspace_fk FOREIGN KEY(manager_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 ADD CONSTRAINT visit_customer_workspace_fk FOREIGN KEY(customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id),
 ADD CONSTRAINT visit_recorder_workspace_fk FOREIGN KEY(recorder_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 ADD CONSTRAINT visit_unknown_recorder_requires_history CHECK(recorder_user_ref_id IS NOT NULL OR
  ((import_meta->>'import_type') IS NOT DISTINCT FROM 'crm_history' AND NULLIF(btrim(original_recorder_name),'') IS NOT NULL));
COMMENT ON COLUMN activity.visit.original_recorder_name IS '实际历史跟进人不可变文本；与当前管理人、导入操作人及原商机销售独立';
COMMENT ON COLUMN activity.visit.manager_user_ref_id IS '当前管理归属，不计入实际拜访次数；不能单独授予整条跟进的读取权限';

CREATE TABLE activity.visit_opportunity (
 visit_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 opportunity_id uuid NOT NULL,
 created_by_user_ref_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(visit_id,opportunity_id),
 FOREIGN KEY(visit_id,workspace_id) REFERENCES activity.visit(id,workspace_id),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(created_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
-- Populate before audit/outbox triggers: do not rewrite parent business rows or
-- queue every old visit just because the schema gained a relationship table.
INSERT INTO activity.visit_opportunity(visit_id,workspace_id,opportunity_id,created_by_user_ref_id,created_at)
 SELECT id,workspace_id,opportunity_id,created_by_user_ref_id,created_at FROM activity.visit WHERE opportunity_id IS NOT NULL;
CREATE INDEX visit_opportunity_reverse ON activity.visit_opportunity(workspace_id,opportunity_id,visit_id);
CREATE INDEX visit_partner_interaction ON activity.visit(workspace_id,partner_id,interaction_at DESC)
 WHERE partner_id IS NOT NULL AND deleted_at IS NULL AND status IN ('confirmed','archived');
COMMENT ON TABLE activity.visit_opportunity IS '一条跟进关联多条商机；统计按visit_id去重，旧单列只在单一关联时保留';

CREATE FUNCTION activity.guard_original_crm_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE historical boolean; controlled boolean;
BEGIN
 historical:=COALESCE(NEW.import_meta->>'import_type','')='crm_history';
 controlled:=COALESCE(current_setting('app.feishu_historical_import',true),'')='on' AND security.management_actor();
 IF TG_OP='UPDATE' AND (COALESCE(OLD.import_meta->>'import_type','')='crm_history') IS DISTINCT FROM historical THEN
  RAISE EXCEPTION 'historical origin cannot be changed on an existing business row' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' AND historical AND NOT controlled THEN
  RAISE EXCEPTION 'historical records require the controlled management import transaction' USING ERRCODE='42501';
 END IF;
 IF TG_TABLE_NAME='visit' THEN
  IF TG_OP='INSERT' THEN
   NEW.original_recorder_captured:=true;
   IF NOT historical THEN
    IF NEW.recorder_user_ref_id IS NULL THEN RAISE EXCEPTION 'normal visits require an actual recorder' USING ERRCODE='23514'; END IF;
    SELECT display_name INTO NEW.original_recorder_name FROM platform.user_ref
     WHERE id=NEW.recorder_user_ref_id AND workspace_id=NEW.workspace_id;
   END IF;
  ELSE
   IF OLD.original_recorder_captured THEN
    IF NOT NEW.original_recorder_captured OR NEW.original_recorder_name IS DISTINCT FROM OLD.original_recorder_name THEN
     RAISE EXCEPTION 'original visit author is immutable' USING ERRCODE='23514';
    END IF;
   ELSIF NEW.original_recorder_name IS DISTINCT FROM OLD.original_recorder_name OR NEW.original_recorder_captured THEN
    IF NOT controlled THEN RAISE EXCEPTION 'legacy author capture requires controlled history import' USING ERRCODE='42501'; END IF;
    NEW.original_recorder_captured:=true;
   END IF;
   IF (OLD.status IN ('confirmed','archived') OR COALESCE(OLD.import_meta->>'import_type','')='crm_history')
    AND NEW.recorder_user_ref_id IS DISTINCT FROM OLD.recorder_user_ref_id THEN
    RAISE EXCEPTION 'handover changes management, not the actual recorder' USING ERRCODE='23514';
   END IF;
  END IF;
 ELSE
  IF TG_OP='INSERT' THEN
   NEW.original_owner_captured:=true;
  ELSIF OLD.original_owner_captured THEN
   IF NOT NEW.original_owner_captured OR NEW.original_owner_name IS DISTINCT FROM OLD.original_owner_name THEN
    RAISE EXCEPTION 'original opportunity owner is immutable' USING ERRCODE='23514';
   END IF;
  ELSIF NEW.original_owner_name IS DISTINCT FROM OLD.original_owner_name OR NEW.original_owner_captured THEN
   IF NOT controlled THEN RAISE EXCEPTION 'legacy owner capture requires controlled history import' USING ERRCODE='42501'; END IF;
   NEW.original_owner_captured:=true;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER original_crm_identity BEFORE INSERT OR UPDATE ON activity.visit
 FOR EACH ROW EXECUTE FUNCTION activity.guard_original_crm_identity();
CREATE TRIGGER original_crm_identity BEFORE INSERT OR UPDATE ON crm.opportunity
 FOR EACH ROW EXECUTE FUNCTION activity.guard_original_crm_identity();

CREATE OR REPLACE FUNCTION activity.capture_visit_recording_identity() RETURNS trigger
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
 IF COALESCE(NEW.import_meta->>'import_type','')='crm_history' THEN
  -- The current importing operator is not the historical author's job identity.
  NEW.recording_role_code_snapshot:=NULL;
  NEW.recording_team_id_snapshot:=NULL;
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

-- Read complete narrative only when every linked opportunity is readable.
-- Definer functions inspect raw links, avoiding visit -> link -> visit RLS cycles.
CREATE FUNCTION security.visit_opportunities_readable(p_visit uuid,p_legacy_opportunity uuid DEFAULT NULL) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN p_legacy_opportunity IS NOT NULL
  THEN security.has_opportunity_read_access(p_legacy_opportunity)
  ELSE NOT EXISTS(SELECT 1 FROM activity.visit_opportunity x WHERE x.visit_id=p_visit
   AND x.workspace_id=common.current_workspace_id() AND NOT security.has_opportunity_read_access(x.opportunity_id)) END;
$$;
CREATE FUNCTION security.visit_opportunities_writable(p_visit uuid,p_legacy_opportunity uuid DEFAULT NULL) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN p_legacy_opportunity IS NOT NULL
  THEN security.has_opportunity_access(p_legacy_opportunity)
  ELSE NOT EXISTS(SELECT 1 FROM activity.visit_opportunity x WHERE x.visit_id=p_visit
   AND x.workspace_id=common.current_workspace_id() AND NOT security.has_opportunity_access(x.opportunity_id)) END;
$$;
CREATE FUNCTION security.has_partner_followup_access(p_partner uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.partner p WHERE p.id=p_partner AND p.workspace_id=common.current_workspace_id()
  AND (security.management_actor() OR (common.current_role_code()='manager' AND security.has_active_role('manager'))
   OR (p.channel_manager_user_ref_id=common.current_user_ref_id() AND security.has_active_role(common.current_role_code()))));
$$;
CREATE FUNCTION security.has_linked_visit_access(p_visit uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM activity.visit_opportunity x WHERE x.visit_id=p_visit AND x.workspace_id=common.current_workspace_id())
  AND security.visit_opportunities_readable(p_visit,NULL);
$$;
ALTER TABLE activity.visit_opportunity ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.visit_opportunity FORCE ROW LEVEL SECURITY;
CREATE POLICY visit_opportunity_read ON activity.visit_opportunity FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.has_opportunity_read_access(opportunity_id)
 AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=visit_id AND v.workspace_id=visit_opportunity.workspace_id));
CREATE POLICY visit_opportunity_insert ON activity.visit_opportunity FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id)
 AND (security.management_actor() OR security.visit_opportunities_readable(visit_id,NULL))
 AND CASE WHEN common.current_role_code() IN ('fde','fde_lead') THEN security.fde_can_record_opportunity(opportunity_id)
  ELSE security.has_opportunity_access(opportunity_id) END);
CREATE POLICY visit_opportunity_delete ON activity.visit_opportunity FOR DELETE USING(
 workspace_id=common.current_workspace_id() AND security.can_write_visit(visit_id)
 AND (security.management_actor() OR security.visit_opportunities_writable(visit_id,NULL)));
DROP POLICY visit_opportunity_boundary ON activity.visit;
CREATE POLICY visit_opportunity_boundary ON activity.visit AS RESTRICTIVE FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND CASE WHEN opportunity_id IS NOT NULL
  THEN security.has_opportunity_read_access(opportunity_id)
  ELSE security.visit_opportunities_readable(id,NULL) END);
CREATE POLICY visit_all_opportunities_update ON activity.visit AS RESTRICTIVE FOR UPDATE USING(
 common.current_role_code() IN ('fde','fde_lead') OR security.visit_opportunities_writable(id,opportunity_id)) WITH CHECK(
 common.current_role_code() IN ('fde','fde_lead') OR security.visit_opportunities_writable(id,opportunity_id));
CREATE POLICY visit_related_subject_read ON activity.visit FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND
 ((partner_id IS NOT NULL AND security.has_partner_followup_access(partner_id))
  OR (opportunity_id IS NULL AND security.has_linked_visit_access(id))));
CREATE POLICY visit_partner_insert ON activity.visit FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND partner_id IS NOT NULL
 AND (security.management_actor() OR (common.current_role_code()='sales' AND security.has_active_role('sales')
  AND recorder_user_ref_id=common.current_user_ref_id() AND created_by_user_ref_id=common.current_user_ref_id()
  AND security.has_partner_followup_access(partner_id))));
CREATE POLICY visit_historical_multi_insert ON activity.visit FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.management_actor()
 AND current_setting('app.feishu_historical_import',true)='on' AND import_meta->>'import_type'='crm_history'
 AND EXISTS(SELECT 1 FROM ops.crm_import_record r JOIN ops.crm_import_batch b ON b.id=r.batch_id AND b.workspace_id=r.workspace_id
  WHERE r.id=(import_meta->>'import_record_id')::uuid AND r.workspace_id=activity.visit.workspace_id
  AND r.object_kind='visit' AND r.target_id=activity.visit.id AND r.status='approved'
  AND b.status IN ('approved','applying','applied')));
CREATE POLICY visit_partner_update ON activity.visit FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND partner_id IS NOT NULL AND security.has_partner_followup_access(partner_id)
 AND (security.management_actor() OR recorder_user_ref_id=common.current_user_ref_id())) WITH CHECK(
 workspace_id=common.current_workspace_id() AND partner_id IS NOT NULL AND security.has_partner_followup_access(partner_id)
 AND (security.management_actor() OR recorder_user_ref_id=common.current_user_ref_id()));

-- Keep old single-opportunity writers working. Multi-opportunity writers set
-- the legacy column NULL and replace links in one transaction. No invented primary.
CREATE FUNCTION activity.sync_legacy_visit_opportunity() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.opportunity_id IS NOT NULL THEN
   INSERT INTO activity.visit_opportunity(visit_id,workspace_id,opportunity_id,created_by_user_ref_id)
    VALUES(NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.created_by_user_ref_id) ON CONFLICT DO NOTHING;
  END IF;
 ELSIF NEW.opportunity_id IS DISTINCT FROM OLD.opportunity_id THEN
  DELETE FROM activity.visit_opportunity WHERE visit_id=NEW.id AND workspace_id=NEW.workspace_id;
  IF NEW.opportunity_id IS NOT NULL THEN
   INSERT INTO activity.visit_opportunity(visit_id,workspace_id,opportunity_id,created_by_user_ref_id)
    VALUES(NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.created_by_user_ref_id);
  END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER sync_legacy_visit_opportunity AFTER INSERT OR UPDATE OF opportunity_id ON activity.visit
 FOR EACH ROW EXECUTE FUNCTION activity.sync_legacy_visit_opportunity();

CREATE FUNCTION activity.check_visit_opportunity_consistency() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v activity.visit; p_visit uuid; total integer;
BEGIN
 p_visit:=COALESCE(to_jsonb(NEW)->>'id',to_jsonb(OLD)->>'id',to_jsonb(NEW)->>'visit_id',to_jsonb(OLD)->>'visit_id')::uuid;
 SELECT * INTO v FROM activity.visit WHERE id=p_visit;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT count(*) INTO total FROM activity.visit_opportunity WHERE visit_id=v.id AND workspace_id=v.workspace_id;
 IF v.customer_id IS NULL AND v.partner_id IS NULL AND total=0 THEN
  RAISE EXCEPTION 'visit requires a customer, partner or linked opportunity' USING ERRCODE='23514';
 END IF;
 IF v.opportunity_id IS NOT NULL AND (total<>1 OR NOT EXISTS(SELECT 1 FROM activity.visit_opportunity
  WHERE visit_id=v.id AND workspace_id=v.workspace_id AND opportunity_id=v.opportunity_id)) THEN
  RAISE EXCEPTION 'legacy opportunity must match the sole normalized relationship' USING ERRCODE='23514';
 END IF;
 IF v.opportunity_id IS NOT NULL AND v.customer_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM crm.opportunity WHERE id=v.opportunity_id AND workspace_id=v.workspace_id AND customer_id=v.customer_id) THEN
  RAISE EXCEPTION 'single opportunity must belong to the stated customer' USING ERRCODE='23514';
 END IF;
 IF total>0 AND v.customer_id IS NOT NULL AND EXISTS(SELECT 1 FROM activity.visit_opportunity x
  JOIN crm.opportunity o ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id
  WHERE x.visit_id=v.id AND x.workspace_id=v.workspace_id AND o.customer_id<>v.customer_id) THEN
  RAISE EXCEPTION 'multi-customer visits cannot invent a primary customer' USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER visit_opportunity_consistency AFTER INSERT OR UPDATE ON activity.visit
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION activity.check_visit_opportunity_consistency();
CREATE CONSTRAINT TRIGGER visit_opportunity_consistency AFTER INSERT OR UPDATE OR DELETE ON activity.visit_opportunity
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION activity.check_visit_opportunity_consistency();

-- Relationship writes enqueue just their parent visit, not a workspace refresh.
CREATE FUNCTION ops.capture_visit_opportunity_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v activity.visit;
BEGIN
 SELECT * INTO v FROM activity.visit WHERE id=COALESCE(NEW.visit_id,OLD.visit_id)
  AND workspace_id=COALESCE(NEW.workspace_id,OLD.workspace_id);
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF pg_trigger_depth()=1 THEN
  -- Direct list replacement is a real parent edit for optimistic import/version
  -- protection. Its existing capture trigger writes the outbox event.
  UPDATE activity.visit SET updated_at=clock_timestamp() WHERE id=v.id AND workspace_id=v.workspace_id;
 END IF;
 -- Nested legacy-column maintenance is already captured by the parent write.
 -- Emitting here would shadow first_formal_create with a second non-create event.
 RETURN NULL;
END $$;
CREATE TRIGGER feishu_capture AFTER INSERT OR DELETE ON activity.visit_opportunity
 FOR EACH ROW EXECUTE FUNCTION ops.capture_visit_opportunity_event();

CREATE FUNCTION ops.audit_visit_opportunity() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE before_value jsonb; after_value jsonb; record_value jsonb;
BEGIN
 IF TG_OP<>'INSERT' THEN before_value:=to_jsonb(OLD); END IF;
 IF TG_OP<>'DELETE' THEN after_value:=to_jsonb(NEW); END IF;
 record_value:=COALESCE(after_value,before_value);
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,
  object_type,object_id,object_label,before_snapshot,after_snapshot,changed_fields)
 VALUES((record_value->>'workspace_id')::uuid,common.current_user_ref_id(),COALESCE(common.current_role_code(),'system'),
  'activity.visit_opportunity.'||lower(TG_OP),'activity','visit',(record_value->>'visit_id')::uuid,
  '跟进商机关联',before_value,after_value,ARRAY['opportunity_ids']);
 RETURN NULL;
END $$;
CREATE TRIGGER business_audit AFTER INSERT OR DELETE ON activity.visit_opportunity
 FOR EACH ROW EXECUTE FUNCTION ops.audit_visit_opportunity();

CREATE FUNCTION ops.capture_associated_partner_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; previous jsonb;
BEGIN
 r:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 IF TG_OP='DELETE' THEN previous:=r; END IF;
 UPDATE crm.opportunity SET updated_at=clock_timestamp()
  WHERE id=(r->>'opportunity_id')::uuid AND workspace_id=(r->>'workspace_id')::uuid;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,
  object_type,object_id,object_label,before_snapshot,after_snapshot,changed_fields)
 VALUES((r->>'workspace_id')::uuid,common.current_user_ref_id(),COALESCE(common.current_role_code(),'system'),
  'crm.opportunity_related_partner.'||lower(TG_OP),'crm','opportunity',(r->>'opportunity_id')::uuid,
  '商机普通关联伙伴',previous,CASE WHEN TG_OP='INSERT' THEN r END,ARRAY['associated_partner_ids']);
 RETURN NULL;
END $$;
CREATE TRIGGER related_partner_changed AFTER INSERT OR DELETE ON crm.opportunity_related_partner
 FOR EACH ROW EXECUTE FUNCTION ops.capture_associated_partner_event();

-- Do not change historical rows to populate names or current managers. Existing
-- fields remain readable and future imports provide the explicit source values.
DO $$ DECLARE r record; f text; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
  WHERE table_schema='activity' AND table_name='visit' AND privilege_type='SELECT' AND grantee<>'PUBLIC' LOOP
  EXECUTE format('GRANT SELECT ON activity.visit_opportunity TO %I',r.grantee);
  FOREACH f IN ARRAY ARRAY['security.visit_opportunities_readable(uuid,uuid)','security.visit_opportunities_writable(uuid,uuid)',
   'security.has_partner_followup_access(uuid)','security.has_linked_visit_access(uuid)'] LOOP
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',f,r.grantee);
  END LOOP;
 END LOOP;
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
  WHERE table_schema='activity' AND table_name='visit' AND privilege_type='INSERT' AND grantee<>'PUBLIC' LOOP
  EXECUTE format('GRANT INSERT ON activity.visit_opportunity TO %I',r.grantee);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION security.visit_opportunities_readable(uuid,uuid),security.visit_opportunities_writable(uuid,uuid),
 security.has_partner_followup_access(uuid),security.has_linked_visit_access(uuid) FROM PUBLIC;

-- Preserve the previous source projection and its fail-closed parent checks.
ALTER FUNCTION ops.feishu_source(uuid,text,uuid) RENAME TO feishu_source_v120;
CREATE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; result jsonb; extra jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL THEN RAISE insufficient_privilege; END IF;
 IF p_kind='period_actual_snapshot' THEN
  SELECT jsonb_build_object('id',s.id,'opportunity_id',s.opportunity_id,'customer_id',o.customer_id,
   'opportunity_name',o.name,'customer_name',c.name,'year',s.year,'quarter',s.quarter,'kind',s.kind,
   'source_field',s.source_field,'raw_amount',s.raw_amount,'source_unit',s.source_unit,'tax_basis',s.tax_basis,
   'source_record_id',s.source_record_id,'import_batch_id',s.import_batch_id,'created_at',s.created_at,
   'source_system',b.source_system,'source_base_id',b.source_base_id,
   'source_table_id',r.source_table_id,'source_external_record_id',r.source_record_id,
   'company_name',(SELECT name FROM platform.workspace WHERE id=ws)) INTO result
  FROM crm.opportunity_period_actual_snapshot s
  JOIN crm.opportunity o ON o.id=s.opportunity_id AND o.workspace_id=s.workspace_id
  JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
  JOIN ops.crm_import_record r ON r.id=s.source_record_id AND r.workspace_id=s.workspace_id
  JOIN ops.crm_import_batch b ON b.id=s.import_batch_id AND b.workspace_id=s.workspace_id
  WHERE s.id=p_id AND s.workspace_id=ws AND o.deleted_at IS NULL AND c.deleted_at IS NULL AND c.data_kind='production';
  IF result IS NULL THEN RETURN jsonb_build_object('id',p_id,'excluded',true); END IF;
  RETURN result;
 END IF;
 result:=ops.feishu_source_v120(p_connection,p_kind,p_id);
 IF result->>'excluded'='true' OR result->>'deleted'='true' THEN RETURN result; END IF;
 IF p_kind='opportunity' THEN
  SELECT jsonb_build_object('original_owner_name',original_owner_name,'ownership_resolution',ownership_resolution,
   'ownership_resolution_evidence',ownership_resolution_evidence,'expected_close_year',expected_close_year,
   'expected_close_quarter',expected_close_quarter,
   'associated_partner_ids',(SELECT COALESCE(jsonb_agg(r.partner_id ORDER BY r.partner_id),'[]'::jsonb)
    FROM crm.opportunity_related_partner r WHERE r.workspace_id=ws AND r.opportunity_id=p_id),
   'associated_partner_names',(SELECT COALESCE(jsonb_agg(p.name ORDER BY r.partner_id),'[]'::jsonb)
    FROM crm.opportunity_related_partner r JOIN crm.partner p ON p.id=r.partner_id AND p.workspace_id=r.workspace_id
    WHERE r.workspace_id=ws AND r.opportunity_id=p_id)) INTO extra FROM crm.opportunity WHERE id=p_id AND workspace_id=ws;
 ELSIF p_kind='forecast' THEN
  SELECT jsonb_build_object('collection_confidence',collection_confidence) INTO extra
   FROM crm.opportunity_forecast WHERE id=p_id AND workspace_id=ws;
 ELSIF p_kind='partner' THEN
  SELECT jsonb_build_object('short_name',p.short_name,'principal_name',p.principal_name,
   'channel_manager_user_ref_id',p.channel_manager_user_ref_id,'channel_manager_name',u.display_name,
   'original_channel_manager_name',p.original_channel_manager_name,'priority',p.priority,'progress',p.progress,
   'grade',p.grade,'signed_on',p.signed_on,'partner_type',p.partner_type,'region',p.region,'province',p.province,'note',p.note,
   'last_interaction_at',(SELECT max(v.interaction_at) FROM activity.visit v WHERE v.workspace_id=ws
    AND v.partner_id=p.id AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')))
   INTO extra FROM crm.partner p LEFT JOIN platform.user_ref u ON u.id=p.channel_manager_user_ref_id AND u.workspace_id=p.workspace_id
   WHERE p.id=p_id AND p.workspace_id=ws;
 ELSIF p_kind='visit' THEN
  -- A multi-link narrative is not exported if any linked parent is excluded.
  IF EXISTS(SELECT 1 FROM activity.visit_opportunity x LEFT JOIN crm.opportunity o
    ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id
    LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
    WHERE x.visit_id=p_id AND x.workspace_id=ws AND
     (o.id IS NULL OR o.deleted_at IS NOT NULL OR c.id IS NULL OR c.deleted_at IS NOT NULL OR c.data_kind<>'production')) THEN
   RETURN jsonb_build_object('id',p_id,'excluded',true);
  END IF;
  SELECT jsonb_build_object('partner_id',v.partner_id,'partner_name',p.name,
   'original_recorder_name',v.original_recorder_name,'manager_user_ref_id',v.manager_user_ref_id,
   'manager_name',u.display_name,'manager_account',u.account_code,
   'opportunity_ids',(SELECT COALESCE(jsonb_agg(x.opportunity_id ORDER BY x.opportunity_id),'[]'::jsonb)
    FROM activity.visit_opportunity x WHERE x.visit_id=v.id AND x.workspace_id=ws),
   'opportunity_names',(SELECT COALESCE(jsonb_agg(o.name ORDER BY o.id),'[]'::jsonb)
    FROM activity.visit_opportunity x JOIN crm.opportunity o ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id
    WHERE x.visit_id=v.id AND x.workspace_id=ws),
   'customer_ids',(SELECT COALESCE(jsonb_agg(cid ORDER BY cid),'[]'::jsonb) FROM
    (SELECT v.customer_id AS cid WHERE v.customer_id IS NOT NULL UNION
     SELECT o.customer_id FROM activity.visit_opportunity x JOIN crm.opportunity o
     ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id WHERE x.visit_id=v.id AND x.workspace_id=ws) customers),
   'customer_names',(SELECT COALESCE(jsonb_agg(c.name ORDER BY c.id),'[]'::jsonb) FROM crm.customer c
    WHERE c.workspace_id=ws AND c.id IN (
     SELECT v.customer_id WHERE v.customer_id IS NOT NULL UNION
     SELECT o.customer_id FROM activity.visit_opportunity x JOIN crm.opportunity o
      ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id WHERE x.visit_id=v.id AND x.workspace_id=ws)))
   INTO extra FROM activity.visit v LEFT JOIN crm.partner p ON p.id=v.partner_id AND p.workspace_id=v.workspace_id
   LEFT JOIN platform.user_ref u ON u.id=v.manager_user_ref_id AND u.workspace_id=v.workspace_id WHERE v.id=p_id AND v.workspace_id=ws;
 END IF;
 RETURN result||COALESCE(extra,'{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION ops.feishu_source(uuid,text,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION ops.feishu_source_v120(uuid,text,uuid) FROM PUBLIC,salegent_feishu_worker;
GRANT EXECUTE ON FUNCTION ops.feishu_source(uuid,text,uuid) TO salegent_feishu_worker;
ALTER TABLE ops.feishu_event DROP CONSTRAINT feishu_event_object_kind_check;
ALTER TABLE ops.feishu_event ADD CONSTRAINT feishu_event_object_kind_check CHECK(object_kind IN
 ('customer','opportunity','visit','partner','task','demo_scene','actual','target','member','contact','forecast','period_actual_snapshot','refresh'));
CREATE FUNCTION ops.capture_period_actual_snapshot_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE connection uuid;
BEGIN
 SELECT id INTO connection FROM config.feishu_connection WHERE workspace_id=NEW.workspace_id;
 IF connection IS NOT NULL THEN
  INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical,queue_origin)
   VALUES(connection,NEW.workspace_id,'period_actual_snapshot',NEW.id,false,true,'reconcile');
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER feishu_capture AFTER INSERT ON crm.opportunity_period_actual_snapshot
 FOR EACH ROW EXECUTE FUNCTION ops.capture_period_actual_snapshot_event();

-- Repeat grant reconciliation after application roles are created on a new host.
-- Global form definitions are deliberately hidden by config's ordinary tenant
-- RLS. This import-only predicate exposes validity, never configuration rows.
CREATE FUNCTION security.crm_history_visit_form_valid(p_form uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.management_actor() AND EXISTS(
  SELECT 1 FROM config.form_version v JOIN config.form_definition d ON d.id=v.form_definition_id
  WHERE v.id=p_form AND v.status='active' AND d.object_type='visit'
   AND (d.workspace_id IS NULL OR d.workspace_id=common.current_workspace_id())
   AND statement_timestamp()>=v.effective_from AND statement_timestamp()<v.effective_to
 )
$$;
REVOKE ALL ON FUNCTION security.crm_history_visit_form_valid(uuid) FROM PUBLIC,salegent_feishu_worker;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v070;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r record; permission record; total integer; fn text;
BEGIN
 total:=security.reconcile_runtime_grants_v070();
 FOR permission IN SELECT DISTINCT grant_role.rolname,a.privilege_type FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles grant_role ON grant_role.oid=a.grantee
  WHERE n.nspname='activity' AND c.relname='visit' AND a.grantee<>c.relowner AND a.privilege_type IN ('SELECT','INSERT') LOOP
  EXECUTE format('GRANT %s ON activity.visit_opportunity TO %I',permission.privilege_type,permission.rolname);
  IF permission.privilege_type='SELECT' THEN
   FOREACH fn IN ARRAY ARRAY['security.visit_opportunities_readable(uuid,uuid)','security.visit_opportunities_writable(uuid,uuid)',
    'security.has_partner_followup_access(uuid)','security.has_linked_visit_access(uuid)'] LOOP
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',fn,permission.rolname);
   END LOOP;
  END IF;
  total:=total+1;
 END LOOP;
 FOR permission IN SELECT DISTINCT grant_role.rolname,a.privilege_type FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles grant_role ON grant_role.oid=a.grantee
  WHERE n.nspname='crm' AND c.relname='opportunity' AND a.grantee<>c.relowner AND a.privilege_type IN ('SELECT','INSERT','UPDATE') LOOP
  EXECUTE format('GRANT %s ON crm.opportunity_related_partner TO %I',
   CASE WHEN permission.privilege_type='UPDATE' THEN 'DELETE' ELSE permission.privilege_type END,permission.rolname);
 END LOOP;
 FOR r IN SELECT grant_role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles grant_role ON grant_role.oid=a.grantee
  WHERE n.nspname='activity' AND c.relname='visit' AND a.grantee<>c.relowner AND a.privilege_type IN ('INSERT','UPDATE')
  GROUP BY grant_role.rolname HAVING count(DISTINCT a.privilege_type)=2 LOOP
  EXECUTE format('GRANT DELETE ON activity.visit_opportunity TO %I',r.rolname);
 END LOOP;
 FOR r IN SELECT DISTINCT grant_role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles grant_role ON grant_role.oid=a.grantee
  WHERE n.nspname='crm' AND c.relname='customer' AND a.grantee<>c.relowner AND a.privilege_type='INSERT' LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON ops.crm_import_batch,ops.crm_import_record,ops.crm_import_binding TO %I',r.rolname);
  EXECUTE format('GRANT SELECT,INSERT ON crm.opportunity_period_actual_snapshot TO %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.check_crm_import_target(uuid,uuid) TO %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.restore_historical_customer_ownership(uuid,uuid,uuid) TO %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.crm_history_visit_form_valid(uuid) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM PUBLIC;
SELECT security.reconcile_runtime_grants();

-- Trigger internals are not worker entrypoints. The wrapper source function can
-- call its private predecessor as owner; the worker may execute only its narrow
-- existing source/reconcile/identity allowlist.
REVOKE ALL ON FUNCTION activity.sync_legacy_visit_opportunity(),activity.check_visit_opportunity_consistency(),
 ops.capture_visit_opportunity_event(),ops.audit_visit_opportunity(),ops.capture_period_actual_snapshot_event(),
 ops.capture_associated_partner_event()
 FROM PUBLIC,salegent_feishu_worker;

-- Preserve worker/admin ACLs while extending the existing bounded reconciliation.
CREATE OR REPLACE FUNCTION ops.feishu_reconcile(p_connection uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; item text[]; total bigint:=0; n bigint;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND enabled;
 IF ws IS NULL THEN RETURN 0; END IF;
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact'],['crm.opportunity_forecast','forecast'],['crm.opportunity_period_actual_snapshot','period_actual_snapshot']] LOOP
  IF NOT EXISTS(SELECT 1 FROM config.feishu_connection c WHERE c.id=p_connection AND (c.settings->'mappings'->item[2]->>'enabled')::boolean IS TRUE) THEN CONTINUE; END IF;
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin) SELECT $1,$2,$3,id,true,''reconcile'' FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''running'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 RETURN total;
END $$;

CREATE OR REPLACE FUNCTION security.feishu_initialize(p_connection uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; item text[]; n bigint; total bigint:=0;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND workspace_id=common.current_workspace_id();
 IF ws IS NULL OR NOT (security.has_active_role('operations') OR security.has_active_role('administrator')) THEN RAISE insufficient_privilege; END IF;
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact'],['crm.opportunity_forecast','forecast'],['crm.opportunity_period_actual_snapshot','period_actual_snapshot']] LOOP
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin) SELECT $1,$2,$3,id,true,''reconcile'' FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''running'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,action,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'initialize',jsonb_build_object('queued',total));
 RETURN total;
END $$;

COMMIT;
