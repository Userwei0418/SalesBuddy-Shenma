-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V234__opportunity_product_line_intents.sql.
-- Original SHA-256: 55131d90466fc14e9f53209720d7202ef80abd92a25f8a48e3332fe11ea8663c. See shenma_whole_121_manifest.json.
BEGIN;
SET LOCAL check_function_bodies=on;

-- Historical product_line text and every frozen material/actual remain intact.
-- An empty normalized intent set is not inferred from names or old transactions.
ALTER TABLE crm.opportunity ADD COLUMN intent_product_line_ids uuid[] NOT NULL DEFAULT '{}';
COMMENT ON COLUMN crm.opportunity.intent_product_line_ids IS
 '无有效物料时使用的意向产品线；有物料时显示冻结物料关联的去重集合，意向独立保留';

CREATE FUNCTION crm.guard_opportunity_product_line_intents() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE old_ids uuid[] := '{}';
BEGIN
 IF TG_OP='UPDATE' THEN
  old_ids:=OLD.intent_product_line_ids;
  IF NEW.intent_product_line_ids IS NOT DISTINCT FROM old_ids THEN RETURN NEW; END IF;
 END IF;
 IF NEW.intent_product_line_ids IS NULL OR cardinality(NEW.intent_product_line_ids)>100
  OR array_position(NEW.intent_product_line_ids,NULL) IS NOT NULL
  OR cardinality(NEW.intent_product_line_ids)<>(SELECT count(DISTINCT x) FROM unnest(NEW.intent_product_line_ids) x)
 THEN RAISE EXCEPTION '意向产品线须为不重复的目录ID集合' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('material_catalog:'||NEW.workspace_id::text,212));
 IF EXISTS(SELECT 1 FROM unnest(NEW.intent_product_line_ids) selected_id
   WHERE NOT(selected_id=ANY(old_ids)) AND NOT EXISTS(
    SELECT 1 FROM config.dictionary_item i JOIN config.dictionary d ON d.id=i.dictionary_id
    WHERE i.id=selected_id AND d.workspace_id=NEW.workspace_id AND d.code='material_product_line'
    AND d.status='active' AND i.is_active))
 THEN RAISE EXCEPTION '请选择本公司有效的意向产品线；停用项只能保留或移除' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION crm.guard_opportunity_product_line_intents() FROM PUBLIC;
CREATE TRIGGER opportunity_product_line_intents_guard
 BEFORE INSERT OR UPDATE OF intent_product_line_ids ON crm.opportunity
 FOR EACH ROW EXECUTE FUNCTION crm.guard_opportunity_product_line_intents();

-- Check final composition, so removing all lines and selecting intentions in
-- the same opportunity transaction is valid regardless of statement order.
CREATE FUNCTION crm.check_product_line_intent_composition() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' AND NEW.intent_product_line_ids IS NOT DISTINCT FROM OLD.intent_product_line_ids THEN RETURN NULL; END IF;
 IF TG_OP='INSERT' AND cardinality(NEW.intent_product_line_ids)=0 THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM crm.opportunity_material m
   WHERE m.opportunity_id=NEW.id AND m.workspace_id=NEW.workspace_id AND m.is_active)
 THEN RAISE EXCEPTION '已有物料时产品线由物料自动汇总，请在无物料时维护意向产品线' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION crm.check_product_line_intent_composition() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER opportunity_product_line_intent_composition
 AFTER INSERT OR UPDATE ON crm.opportunity DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION crm.check_product_line_intent_composition();

-- Match the catalog API and V213 dictionary write lock before any material row
-- lock is acquired. A checked catalog version stays stable until atomic save.
CREATE FUNCTION crm.lock_material_catalog_write() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('material_catalog:'||common.current_workspace_id()::text,212));
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION crm.lock_material_catalog_write() FROM PUBLIC;
CREATE TRIGGER material_catalog_write_lock BEFORE INSERT OR UPDATE OR DELETE ON crm.material
 FOR EACH STATEMENT EXECUTE FUNCTION crm.lock_material_catalog_write();

-- Forward correction of V212; its applied bytes and permissions stay intact.
-- This unreleased V234 still applies only once to the verified 197 baseline.
CREATE OR REPLACE VIEW crm.effective_actual WITH (security_invoker=true) AS
 SELECT COALESCE(f.id,'period:'||r.opportunity_id||':'||r.year||':'||r.quarter||':'||r.kind) AS id,
  r.workspace_id,r.customer_id,r.opportunity_id,f.material_line_id,r.year,r.quarter,r.kind,
  CASE WHEN NOT guard.current_conflict AND (r.kind='collection' OR f.tax_basis='exclusive') THEN f.amount ELSE NULL END AS amount,
  f.amount AS raw_amount,(r.kind='recognized' AND f.amount IS NOT NULL AND COALESCE(f.tax_basis,'unknown')<>'exclusive') AS tax_unresolved,
  COALESCE(f.tax_basis,'unknown') AS tax_basis,r.source,
  CASE WHEN guard.current_conflict OR (r.source IS NOT NULL AND f.id IS NULL AND r.status NOT IN ('withdrawn','missing')) THEN 'needs_confirmation' ELSE r.status END AS status,
  f.occurred_on,make_date(r.year,(r.quarter-1)*3+1,1) AS period_start,
  (make_date(r.year,(r.quarter-1)*3+1,1)+interval '3 months'-interval '1 day')::date AS period_end,
  CASE WHEN f.occurred_on IS NULL THEN 'quarter' ELSE 'day' END AS period_precision,
  CASE WHEN r.source='operations' OR r.adopted_snapshot IS NOT NULL THEN f.owner_user_ref_id ELSE r.owner_user_ref_id END AS owner_user_ref_id,
  CASE WHEN r.source='operations' OR r.adopted_snapshot IS NOT NULL THEN f.owner_team_id ELSE r.owner_team_id END AS owner_team_id,f.confirmed_at,f.source_ref,f.note,
  r.expected_material_count,CASE WHEN r.kind='recognized' THEN r.confirmed_count ELSE 0 END AS confirmed_material_count,r.missing_material_count,
  (NOT guard.current_conflict AND f.id IS NOT NULL AND r.missing_material_count=0 AND (r.kind='collection' OR f.tax_basis='exclusive')
   AND r.status NOT IN ('withdrawn','missing','needs_confirmation','stale_confirmation')) AS complete,
  r.reference_changed,r.source_fingerprint,r.version_no
 FROM crm.finance_period_resolution r LEFT JOIN LATERAL (
  SELECT a.id::text,a.material_line_id,a.amount,a.tax_basis,NULL::date AS occurred_on,
   (a.material_snapshot->>'owner_user_ref_id')::uuid AS owner_user_ref_id,
   (a.material_snapshot->>'owner_team_id')::uuid AS owner_team_id,a.confirmed_at,a.source_ref,a.note
   FROM crm.opportunity_actual_confirmation a WHERE r.source='operations' AND a.workspace_id=r.workspace_id
    AND a.opportunity_id=r.opportunity_id AND a.kind=r.kind AND a.year=r.year AND a.quarter=r.quarter AND a.status='confirmed'

  -- No monetary facts survive withdrawal. Retain each frozen attribution,
  -- rather than assigning the null period to today's opportunity owner.
  UNION ALL
  SELECT 'withdrawn:'||a.id,NULL::uuid,NULL::numeric,'unknown',NULL::date,
   (a.material_snapshot->>'owner_user_ref_id')::uuid,
   (a.material_snapshot->>'owner_team_id')::uuid,NULL::timestamptz,NULL::text,NULL::text
   FROM crm.opportunity_actual_confirmation a WHERE r.source='operations' AND r.confirmed_count=0
    AND NOT r.empty_confirmation AND a.workspace_id=r.workspace_id
    AND a.opportunity_id=r.opportunity_id AND a.kind=r.kind AND a.year=r.year AND a.quarter=r.quarter
    AND a.status='withdrawn'
  UNION ALL
  SELECT 'withdrawn-empty:'||r.basis_id,NULL::uuid,NULL::numeric,'unknown',NULL::date,
   (r.operational_snapshot->>'owner_user_ref_id')::uuid,
   (r.operational_snapshot->>'owner_team_id')::uuid,NULL::timestamptz,NULL::text,NULL::text
   WHERE r.source='operations' AND r.kind='recognized' AND r.confirmed_count=0 AND NOT r.empty_confirmation
    AND r.operational_snapshot IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM crm.opportunity_actual_confirmation a WHERE a.workspace_id=r.workspace_id
      AND a.opportunity_id=r.opportunity_id AND a.kind=r.kind AND a.year=r.year AND a.quarter=r.quarter
      AND a.status='withdrawn')
  UNION ALL
  SELECT 'empty:'||r.basis_id,NULL::uuid,0::numeric,'exclusive',NULL::date,
   (r.operational_snapshot->>'owner_user_ref_id')::uuid,(r.operational_snapshot->>'owner_team_id')::uuid,
   (r.operational_snapshot->>'captured_at')::timestamptz,'explicit_empty_population',NULL::text
   WHERE r.source='operations' AND r.empty_confirmation AND r.kind='recognized'
  UNION ALL
  SELECT a.id::text,NULL::uuid,a.amount,a.tax_basis,a.occurred_on,NULL::uuid,NULL::uuid,a.created_at,a.source_ref,a.note
   FROM crm.customer_actual a WHERE r.source='entries' AND r.adopted_snapshot IS NULL
    AND a.workspace_id=r.workspace_id AND a.opportunity_id=r.opportunity_id AND a.kind=r.kind
    AND extract(year FROM a.occurred_on)=r.year AND extract(quarter FROM a.occurred_on)=r.quarter AND a.voided_at IS NULL
  UNION ALL
  SELECT h.id::text,NULL::uuid,h.raw_amount*CASE h.source_unit WHEN 'wan_cny' THEN 10000 ELSE 1 END,h.tax_basis,
   NULL::date,NULL::uuid,NULL::uuid,NULL::timestamptz,h.source_record_id::text,h.source_field
   FROM crm.opportunity_period_actual_snapshot h WHERE r.source='historical' AND r.adopted_snapshot IS NULL
    AND h.workspace_id=r.workspace_id AND h.opportunity_id=r.opportunity_id AND h.kind=r.kind AND h.year=r.year AND h.quarter=r.quarter
    AND h.source_unit IN ('cny','wan_cny') AND h.raw_amount IS NOT NULL
    AND (h.id=r.selected_history_id OR (r.selected_history_id IS NULL AND r.history_count=1))
  UNION ALL
  SELECT x.id,x.material_line_id,x.amount,x.tax_basis,x.occurred_on,x.owner_user_ref_id,x.owner_team_id,x.confirmed_at,x.source_ref,x.note
   FROM jsonb_to_recordset(COALESCE(r.adopted_snapshot->'facts','[]'::jsonb)) AS x(id text,material_line_id uuid,
    amount numeric,tax_basis text,occurred_on date,owner_user_ref_id uuid,owner_team_id uuid,confirmed_at timestamptz,source_ref text,note text)
   WHERE r.source IN ('entries','historical') AND r.adopted_snapshot IS NOT NULL
 ) f ON true
 CROSS JOIN LATERAL (SELECT COALESCE(r.trade_relation='downstream' AND (
  (r.kind='collection' AND f.amount<>0)
  OR (r.kind='recognized' AND r.source IN ('entries','historical') AND r.adopted_snapshot IS NULL AND (
   NOT EXISTS(SELECT 1 FROM crm.opportunity parent WHERE parent.id=r.upstream_opportunity_id
    AND parent.workspace_id=r.workspace_id
    AND security.authorization_opportunity(security.authorization_read_feature('actual'),parent.id)
    AND security.authorization_customer(security.authorization_read_feature('actual'),parent.customer_id))
   OR EXISTS(SELECT 1 FROM crm.finance_period_resolution parent WHERE parent.workspace_id=r.workspace_id
    AND parent.opportunity_id=r.upstream_opportunity_id AND parent.year=r.year AND parent.quarter=r.quarter
    AND parent.kind='recognized' AND COALESCE(parent.decided_source,'')<>'operations'
    AND (parent.entry_count>0 OR parent.history_count>0 OR parent.adopted_snapshot IS NOT NULL))
  ))),false) AS current_conflict) guard
 UNION ALL
 SELECT a.id::text,a.workspace_id,a.customer_id,NULL::uuid,NULL::uuid,
  extract(year FROM a.occurred_on)::integer,extract(quarter FROM a.occurred_on)::integer,a.kind,
  CASE WHEN a.kind='collection' OR a.tax_basis='exclusive' THEN a.amount ELSE NULL END,a.amount,
  (a.kind='recognized' AND a.tax_basis<>'exclusive'),a.tax_basis,
  'entries','single_source',a.occurred_on,date_trunc('quarter',a.occurred_on)::date,
  (date_trunc('quarter',a.occurred_on)+interval '3 months'-interval '1 day')::date,'day',c.owner_user_ref_id,c.owner_team_id,
  a.created_at,a.source_ref,a.note,0,0::bigint,0::bigint,(a.kind='collection' OR a.tax_basis='exclusive'),false,NULL::text,0
 FROM crm.customer_actual a JOIN crm.customer c ON c.id=a.customer_id AND c.workspace_id=a.workspace_id
 WHERE a.opportunity_id IS NULL AND a.voided_at IS NULL AND c.deleted_at IS NULL;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V382','Unified material-derived product lines and preserved normalized opportunity intents');
COMMIT;
