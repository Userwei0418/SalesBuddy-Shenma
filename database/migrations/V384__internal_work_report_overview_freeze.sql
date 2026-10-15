-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V236__work_report_overview_freeze.sql.
-- Original SHA-256: c018bd6351b1dc9105999007cbb9a27e932007e74ea3610d47cb9948a94b51a1. See shenma_whole_121_manifest.json.
-- Freeze unified overview facts and their existing resource dependencies.
-- No old report backfill, new permission, table or runtime role.
BEGIN;
CREATE OR REPLACE FUNCTION agent.work_report_source_manifest(p_payload jsonb) RETURNS jsonb
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg(t.item ORDER BY t.kind,t.id,t.item),'[]'::jsonb)
 FROM (SELECT DISTINCT s->>'source_type' AS kind,(s->>'record_id')::uuid AS id,
   jsonb_build_object('source_type',s->>'source_type','record_id',(s->>'record_id')::uuid)
     || CASE WHEN s->>'source_type'='actual'
        THEN jsonb_build_object('actual_lineage',s->'actual_lineage') ELSE '{}'::jsonb END AS item
   FROM jsonb_array_elements(COALESCE(p_payload->'sources','[]'::jsonb)) s
   UNION
   SELECT 'actual_period',COALESCE(d->>'opportunity_id',d->>'customer_id')::uuid,
     jsonb_build_object('source_type','actual_period','record_id',COALESCE(d->>'opportunity_id',d->>'customer_id')::uuid,
                       'period_dependency',d)
   FROM jsonb_array_elements(COALESCE(p_payload->'sales_week_facts'->'finance'->'dependencies','[]'::jsonb)) d
   UNION
   SELECT 'opportunity_history',(h->>'opportunity_id')::uuid,
     jsonb_build_object('source_type','opportunity_history','record_id',(h->>'opportunity_id')::uuid,
                       'history_guard',h)
   FROM jsonb_array_elements(COALESCE(p_payload->'sales_week_facts'->'history_guards','[]'::jsonb)) h
   UNION
   SELECT m->>'source_type',(m->>'record_id')::uuid,m
   FROM jsonb_array_elements(COALESCE(p_payload->'meeting_dependencies','[]'::jsonb)
      || COALESCE(p_payload->'overview_dependencies','[]'::jsonb)) m) t;
$$;

CREATE OR REPLACE FUNCTION agent.protect_work_report() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE s agent.work_report_snapshot;
BEGIN
 IF TG_TABLE_NAME='work_report_document' AND TG_OP='INSERT' THEN
  SELECT * INTO s FROM agent.work_report_snapshot WHERE id=NEW.snapshot_id AND workspace_id=NEW.workspace_id AND status='ready';
  IF s.id IS NULL OR s.owner_user_ref_id<>NEW.owner_user_ref_id OR s.snapshot_hash<>NEW.snapshot_hash THEN
   RAISE insufficient_privilege; END IF;
  NEW.source_manifest=s.source_manifest; NEW.selected_sources=s.selection->'sources';
  NEW.context=jsonb_build_object('period',s.period,'scope',s.scope,'coverage',s.payload->'coverage','metrics',s.payload->'metrics');
  IF s.payload ? 'meeting_facts' THEN NEW.context=NEW.context||jsonb_build_object('meeting_facts',s.payload->'meeting_facts'); END IF;
  IF s.payload ? 'overview_facts' THEN
   NEW.context=NEW.context||jsonb_build_object('overview_facts',s.payload->'overview_facts'->'facts');
  END IF;
  RETURN NEW;
 END IF;
 IF (NEW.id,NEW.workspace_id,NEW.owner_user_ref_id,NEW.created_at)
  IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.owner_user_ref_id,OLD.created_at) THEN
  RAISE EXCEPTION '报告标识、租户和作者不可修改' USING ERRCODE='22023'; END IF;
 IF TG_TABLE_NAME='work_report_snapshot' THEN
  IF OLD.status<>'preparing' OR (NEW.selection,NEW.period,NEW.scope) IS DISTINCT FROM (OLD.selection,OLD.period,OLD.scope) THEN
   RAISE EXCEPTION '事实快照不可覆盖' USING ERRCODE='22023'; END IF;
 ELSIF TG_TABLE_NAME='work_report_document' THEN
  IF (NEW.snapshot_id,NEW.snapshot_hash,NEW.context,NEW.source_manifest,NEW.selected_sources)
   IS DISTINCT FROM (OLD.snapshot_id,OLD.snapshot_hash,OLD.context,OLD.source_manifest,OLD.selected_sources) THEN
   RAISE EXCEPTION '报告的事实快照不可替换' USING ERRCODE='22023'; END IF;
  IF (NEW.shared_revision_id,NEW.audience) IS DISTINCT FROM (OLD.shared_revision_id,OLD.audience)
   AND NOT security.authorization_has('work_report.share') THEN RAISE insufficient_privilege; END IF;
  NEW.version_no=OLD.version_no+1; NEW.updated_at=clock_timestamp();
 ELSIF TG_TABLE_NAME='work_report_run' THEN
  IF (NEW.snapshot_id,NEW.request,NEW.generation_input) IS DISTINCT FROM (OLD.snapshot_id,OLD.request,OLD.generation_input)
   OR OLD.status IN ('succeeded','failed','cancelled') THEN
   RAISE EXCEPTION '作业输入与终态不可覆盖' USING ERRCODE='22023'; END IF;
  NEW.version_no=OLD.version_no+1;
 ELSE
  NEW.version_no=OLD.version_no+1; NEW.updated_at=clock_timestamp();
 END IF;
 RETURN NEW;
END $$;
INSERT INTO ops.schema_migration(version, description)
VALUES ('V384', 'Freeze unified work-report overview facts and source dependencies')
ON CONFLICT (version) DO NOTHING;
COMMIT;
