BEGIN;
SET LOCAL check_function_bodies=on;

-- A source snapshot is evidence, not a second customer/opportunity database.
CREATE TABLE ops.crm_import_batch (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 source_system text NOT NULL CHECK(length(btrim(source_system))>0),
 source_base_id text NOT NULL CHECK(length(btrim(source_base_id))>0),
 manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','reviewing','approved','applying','applied','failed','cancelled')),
 source_snapshot_at timestamptz NOT NULL,
 summary jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(summary)='object'),
 created_by_user_ref_id uuid,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id),
 UNIQUE(workspace_id,source_system,source_base_id,manifest_sha256),
 FOREIGN KEY(created_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE TABLE ops.crm_import_record (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL,
 batch_id uuid NOT NULL,
 source_table_id text NOT NULL CHECK(length(btrim(source_table_id))>0),
 source_record_id text NOT NULL CHECK(length(btrim(source_record_id))>0),
 source_item_key text NOT NULL DEFAULT '',
 object_kind text NOT NULL CHECK(object_kind IN ('customer','partner','opportunity','visit','forecast','period_actual_snapshot')),
 source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 raw_fields jsonb NOT NULL CHECK(jsonb_typeof(raw_fields)='object'),
 normalized jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(normalized)='object'),
 anomalies jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(anomalies)='array'),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','quarantined','approved','applied','unchanged','conflict','rejected')),
 target_id uuid,
 expected_target_version integer CHECK(expected_target_version>0),
 last_imported_snapshot jsonb,
 decision jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(decision)='object'),
 applied_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id),
 UNIQUE(batch_id,source_table_id,source_record_id,object_kind,source_item_key),
 FOREIGN KEY(batch_id,workspace_id) REFERENCES ops.crm_import_batch(id,workspace_id),
 CHECK((status NOT IN ('applied','unchanged')) OR (target_id IS NOT NULL AND applied_at IS NOT NULL))
);
-- Independent of batches: preserves stable identity across repeated source exports.
-- Feishu's record_map is the outbound V4 identity map and cannot serve this purpose.
CREATE TABLE ops.crm_import_binding (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 source_system text NOT NULL,
 source_base_id text NOT NULL,
 source_table_id text NOT NULL,
 source_record_id text NOT NULL,
 source_item_key text NOT NULL DEFAULT '',
 object_kind text NOT NULL CHECK(object_kind IN ('customer','partner','opportunity','visit','forecast','period_actual_snapshot')),
 target_id uuid NOT NULL,
 target_version integer CHECK(target_version>0),
 last_source_sha256 text NOT NULL CHECK(last_source_sha256 ~ '^[0-9a-f]{64}$'),
 last_imported_snapshot jsonb NOT NULL CHECK(jsonb_typeof(last_imported_snapshot)='object'),
 applied_batch_id uuid NOT NULL,
 applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id,source_system,source_base_id,source_table_id,source_record_id,object_kind,source_item_key),
 FOREIGN KEY(applied_batch_id,workspace_id) REFERENCES ops.crm_import_batch(id,workspace_id)
);
CREATE INDEX crm_import_record_review ON ops.crm_import_record(workspace_id,batch_id,status,object_kind);
CREATE INDEX crm_import_binding_target ON ops.crm_import_binding(workspace_id,object_kind,target_id);

CREATE FUNCTION ops.guard_crm_import_evidence() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'import evidence is retained' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='crm_import_record' THEN
  IF (NEW.id,NEW.workspace_id,NEW.batch_id,NEW.source_table_id,NEW.source_record_id,NEW.source_item_key,NEW.object_kind,NEW.source_sha256,NEW.raw_fields,NEW.created_at)
   IS DISTINCT FROM
   (OLD.id,OLD.workspace_id,OLD.batch_id,OLD.source_table_id,OLD.source_record_id,OLD.source_item_key,OLD.object_kind,OLD.source_sha256,OLD.raw_fields,OLD.created_at)
  THEN RAISE EXCEPTION 'source snapshot is immutable; create a new batch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='crm_import_batch' THEN
  IF (NEW.id,NEW.workspace_id,NEW.source_system,NEW.source_base_id,NEW.manifest_sha256,NEW.source_snapshot_at,NEW.created_at)
   IS DISTINCT FROM
   (OLD.id,OLD.workspace_id,OLD.source_system,OLD.source_base_id,OLD.manifest_sha256,OLD.source_snapshot_at,OLD.created_at)
  THEN RAISE EXCEPTION 'batch source identity is immutable' USING ERRCODE='23514'; END IF;
 END IF;
 NEW.updated_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER source_evidence_immutable BEFORE UPDATE OR DELETE ON ops.crm_import_batch
 FOR EACH ROW EXECUTE FUNCTION ops.guard_crm_import_evidence();
CREATE TRIGGER source_evidence_immutable BEFORE UPDATE OR DELETE ON ops.crm_import_record
 FOR EACH ROW EXECUTE FUNCTION ops.guard_crm_import_evidence();

-- Quarterly source totals deliberately do not claim a business date or confirmer.
CREATE TABLE crm.opportunity_period_actual_snapshot (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL,
 opportunity_id uuid NOT NULL,
 year integer NOT NULL CHECK(year BETWEEN 2000 AND 2100),
 quarter integer NOT NULL CHECK(quarter BETWEEN 1 AND 4),
 kind text NOT NULL CHECK(kind IN ('recognized','collection')),
 source_field text NOT NULL CHECK(length(btrim(source_field))>0),
 raw_amount numeric(22,6) NOT NULL CHECK(raw_amount>=0),
 source_unit text NOT NULL CHECK(source_unit IN ('wan_cny','cny')),
 tax_basis text NOT NULL DEFAULT 'unknown' CHECK(tax_basis IN ('unknown','inclusive','exclusive','not_applicable')),
 source_record_id uuid NOT NULL,
 import_batch_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id),
 UNIQUE(workspace_id,source_record_id,source_field),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(source_record_id,workspace_id) REFERENCES ops.crm_import_record(id,workspace_id),
 FOREIGN KEY(import_batch_id,workspace_id) REFERENCES ops.crm_import_batch(id,workspace_id),
 CHECK(kind<>'collection' OR tax_basis IN ('unknown','not_applicable'))
);
COMMENT ON TABLE crm.opportunity_period_actual_snapshot IS
 '历史商机季度原始汇总；保留源金额和单位，不是逐笔确收回款实绩，不计入customer_actual。NULL不生成行，0保存为0。';
CREATE TRIGGER period_actual_snapshot_immutable BEFORE UPDATE OR DELETE ON crm.opportunity_period_actual_snapshot
 FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();

DO $$ DECLARE relation text; BEGIN
 FOREACH relation IN ARRAY ARRAY['ops.crm_import_batch','ops.crm_import_record','ops.crm_import_binding'] LOOP
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY',relation);
  EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY',relation);
  EXECUTE format('CREATE POLICY import_management ON %s USING (workspace_id=common.current_workspace_id() AND security.management_actor()) WITH CHECK (workspace_id=common.current_workspace_id() AND security.management_actor())',relation);
  EXECUTE format('CREATE TRIGGER business_audit AFTER INSERT OR UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row()',relation);
 END LOOP;
END $$;
ALTER TABLE crm.opportunity_period_actual_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_period_actual_snapshot FORCE ROW LEVEL SECURITY;
CREATE POLICY period_snapshot_read ON crm.opportunity_period_actual_snapshot FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.has_opportunity_read_access(opportunity_id));
CREATE POLICY period_snapshot_import ON crm.opportunity_period_actual_snapshot FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.management_actor()
 AND EXISTS(SELECT 1 FROM ops.crm_import_record r WHERE r.id=opportunity_period_actual_snapshot.source_record_id
  AND r.workspace_id=opportunity_period_actual_snapshot.workspace_id
  AND r.batch_id=import_batch_id AND r.status IN ('approved','applied','unchanged')));
CREATE TRIGGER business_audit AFTER INSERT ON crm.opportunity_period_actual_snapshot
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();

-- Call BEFORE mutating the target in the same transaction. Locks remain until
-- commit. A changed source and changed system row is a conflict, never an upsert.
CREATE FUNCTION security.check_crm_import_target(p_record uuid,p_target uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ops.crm_import_record; b ops.crm_import_batch; binding ops.crm_import_binding;
 source_table text; target_row jsonb; target_version integer; resolved_target uuid; exists_elsewhere boolean;
BEGIN
 IF NOT security.management_actor() THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO r FROM ops.crm_import_record WHERE id=p_record AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO b FROM ops.crm_import_batch WHERE id=r.batch_id AND workspace_id=r.workspace_id;
 IF r.status NOT IN ('approved','applied','unchanged') OR b.status NOT IN ('approved','applying','applied') THEN
  RAISE EXCEPTION 'import record and batch require approval' USING ERRCODE='23514';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(concat_ws('|',r.workspace_id,b.source_system,b.source_base_id,r.source_table_id,r.source_record_id,r.object_kind,r.source_item_key),0));
 SELECT * INTO binding FROM ops.crm_import_binding WHERE workspace_id=r.workspace_id
  AND source_system=b.source_system AND source_base_id=b.source_base_id AND source_table_id=r.source_table_id
  AND source_record_id=r.source_record_id AND object_kind=r.object_kind AND source_item_key=r.source_item_key FOR UPDATE;
 IF binding.id IS NOT NULL AND p_target IS NOT NULL AND p_target<>binding.target_id THEN
  RAISE EXCEPTION 'source identity already bound to another target' USING ERRCODE='23514';
 END IF;
 resolved_target:=COALESCE(binding.target_id,p_target,r.target_id);
 source_table:=CASE r.object_kind WHEN 'customer' THEN 'crm.customer' WHEN 'partner' THEN 'crm.partner'
  WHEN 'opportunity' THEN 'crm.opportunity' WHEN 'visit' THEN 'activity.visit'
  WHEN 'forecast' THEN 'crm.opportunity_forecast' WHEN 'period_actual_snapshot' THEN 'crm.opportunity_period_actual_snapshot' END;
 IF resolved_target IS NOT NULL THEN
  EXECUTE format('SELECT to_jsonb(t) FROM %s t WHERE id=$1 AND workspace_id=$2 FOR UPDATE',source_table)
   INTO target_row USING resolved_target,r.workspace_id;
  IF target_row IS NULL THEN
   EXECUTE format('SELECT EXISTS(SELECT 1 FROM %s WHERE id=$1)',source_table) INTO exists_elsewhere USING resolved_target;
   IF exists_elsewhere THEN RAISE insufficient_privilege; END IF;
  END IF;
 END IF;
 IF binding.id IS NOT NULL AND target_row IS NULL THEN
  RAISE EXCEPTION 'bound import target is missing' USING ERRCODE='23514';
 END IF;
 IF binding.id IS NOT NULL AND binding.last_source_sha256=r.source_sha256 THEN
  RETURN jsonb_build_object('action','unchanged','target_id',binding.target_id);
 END IF;
 target_version:=(target_row->>'version_no')::integer;
 IF target_row IS NOT NULL THEN
  IF r.object_kind='period_actual_snapshot' THEN
   RAISE EXCEPTION 'quarterly source evidence is immutable; retain the new revision for review' USING ERRCODE='23514';
  END IF;
  IF binding.id IS NOT NULL AND
   ((binding.target_version IS NOT NULL AND target_version IS DISTINCT FROM binding.target_version)
    OR (binding.target_version IS NULL AND target_row IS DISTINCT FROM binding.last_imported_snapshot)) THEN
   RAISE EXCEPTION 'system record changed after import; manual reconciliation required' USING ERRCODE='40001';
  END IF;
  IF binding.id IS NULL THEN
   IF r.object_kind='forecast' AND target_version IS NULL THEN
    IF jsonb_typeof(r.decision->'expected_target_snapshot') IS DISTINCT FROM 'object'
     OR target_row IS DISTINCT FROM r.decision->'expected_target_snapshot' THEN
     RAISE EXCEPTION 'existing forecast requires an exact reviewed full snapshot' USING ERRCODE='40001';
    END IF;
   ELSIF r.expected_target_version IS NULL OR target_version IS DISTINCT FROM r.expected_target_version THEN
    RAISE EXCEPTION 'existing target requires an exact reviewed version' USING ERRCODE='40001';
   END IF;
  END IF;
 END IF;
 RETURN jsonb_build_object('action',CASE WHEN target_row IS NULL THEN 'insert' ELSE 'update' END,
  'target_id',resolved_target,'target_version',target_version,'current_snapshot',target_row);
END $$;
REVOKE ALL ON FUNCTION security.check_crm_import_target(uuid,uuid) FROM PUBLIC;

DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
  WHERE table_schema='crm' AND table_name='customer' AND privilege_type='INSERT'
  AND grantee NOT IN ('PUBLIC','salegent_feishu_worker') LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON ops.crm_import_batch,ops.crm_import_record,ops.crm_import_binding TO %I',r.grantee);
  EXECUTE format('GRANT SELECT,INSERT ON crm.opportunity_period_actual_snapshot TO %I',r.grantee);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.check_crm_import_target(uuid,uuid) TO %I',r.grantee);
 END LOOP;
END $$;
COMMIT;
