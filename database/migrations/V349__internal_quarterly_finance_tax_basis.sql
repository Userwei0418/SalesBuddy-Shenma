-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V201__quarterly_finance_tax_basis.sql.
-- Original SHA-256: 160bba9aa70a855796701d5ec3fb7cc42f4d0f328aeaba7de2577772bfd3b1ec. See shenma_whole_121_manifest.json.
BEGIN;
-- Additive evidence; no rewrite, global refresh, or assumed VAT conversion.
ALTER TABLE crm.customer_actual ADD COLUMN tax_basis text NOT NULL DEFAULT 'unknown'
 CHECK (tax_basis IN ('unknown','inclusive','exclusive','not_applicable'));
COMMENT ON COLUMN crm.customer_actual.tax_basis IS 'Explicit source tax basis. Existing records remain unknown; no tax conversion.';
CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; extra jsonb; ws uuid;
BEGIN
 result:=ops.feishu_source_before_opportunity_finance(p_connection,p_kind,p_id);
 IF p_kind='actual' AND result->>'excluded' IS DISTINCT FROM 'true' THEN
  SELECT to_jsonb(tax_basis) INTO extra FROM crm.customer_actual WHERE id=p_id AND workspace_id=(SELECT workspace_id FROM config.feishu_connection WHERE id=p_connection);
  RETURN result||jsonb_build_object('tax_basis',extra);
 END IF;
 IF p_kind<>'opportunity' OR result->>'excluded'='true' OR result->>'deleted'='true' THEN RETURN result; END IF;
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 SELECT jsonb_build_object('workspace_id',ws,'finance_company_code',w.external_workspace_id,
  'finance_actuals',COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'id',a.id,'opportunity_id',a.opportunity_id,'kind',a.kind,'amount',a.amount::text,
   'tax_basis',a.tax_basis,'occurred_on',a.occurred_on,'voided_at',a.voided_at,'void_reason',a.void_reason,'source_ref',a.source_ref,
   'created_at',a.created_at,'updated_at',COALESCE(a.voided_at,a.created_at)) ORDER BY a.id)
   FROM crm.customer_actual a WHERE a.workspace_id=ws AND a.opportunity_id=p_id),'[]'::jsonb),
  'finance_history',COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'id',h.id,'opportunity_id',h.opportunity_id,'year',h.year,'quarter',h.quarter,'kind',h.kind,
   'raw_amount',h.raw_amount::text,'source_unit',h.source_unit,'tax_basis',h.tax_basis,'source_field',h.source_field,
   'source_record_id',h.source_record_id,'import_batch_id',h.import_batch_id) ORDER BY h.id)
   FROM crm.opportunity_period_actual_snapshot h WHERE h.workspace_id=ws AND h.opportunity_id=p_id),'[]'::jsonb),
  'finance_decisions',COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'id',d.id,'year',d.year,'quarter',d.quarter,'kind',d.kind,'source',d.source,'selected_history_id',d.selected_history_id,
   'source_fingerprint',d.source_fingerprint,'reason',d.reason,'decided_by_user_ref_id',d.decided_by_user_ref_id,
   'created_at',d.created_at,'updated_at',d.updated_at,'version_no',d.version_no) ORDER BY d.id)
   FROM crm.opportunity_actual_basis d WHERE d.workspace_id=ws AND d.opportunity_id=p_id),'[]'::jsonb))
 INTO extra FROM platform.workspace w JOIN crm.opportunity o ON o.workspace_id=w.id AND o.id=p_id WHERE w.id=ws;
 RETURN result||COALESCE(extra,'{}'::jsonb);
END $$;

COMMIT;
