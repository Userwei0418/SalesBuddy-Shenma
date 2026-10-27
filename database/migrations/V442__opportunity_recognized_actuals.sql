BEGIN;
-- No historic money or attribution is backfilled. Collection semantics stay intact.
ALTER TABLE crm.opportunity_actual_basis ADD COLUMN attribution_snapshot jsonb
 CHECK(attribution_snapshot IS NULL OR jsonb_typeof(attribution_snapshot)='object');
ALTER TABLE crm.customer_actual ADD COLUMN attribution_snapshot jsonb
 CHECK(attribution_snapshot IS NULL OR jsonb_typeof(attribution_snapshot)='object');

-- Internal only. Existing evidence wins; an unresolved old period must never
-- acquire today's owner merely because somebody corrects its amount.
CREATE FUNCTION security.recognized_period_attribution(p_id uuid,p_year integer,p_quarter integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); b crm.opportunity_actual_basis%ROWTYPE;
 attrs jsonb; parent crm.opportunity%ROWTYPE; result jsonb;
BEGIN
 SELECT * INTO b FROM crm.opportunity_actual_basis WHERE workspace_id=ws AND opportunity_id=p_id
  AND year=p_year AND quarter=p_quarter AND kind='recognized';
 IF b.attribution_snapshot IS NOT NULL THEN RETURN b.attribution_snapshot; END IF;
 -- Use the adopted source first; unused historical candidates never override it.
 SELECT jsonb_agg(DISTINCT jsonb_build_object('owner_user_ref_id',h.owner_user_ref_id,
   'owner_team_id',h.owner_team_id)) INTO attrs
 FROM crm.opportunity_actual_attribution h WHERE h.workspace_id=ws AND h.opportunity_id=p_id
  AND h.year=p_year AND h.quarter=p_quarter AND h.kind='recognized'
  AND h.owner_user_ref_id IS NOT NULL AND h.owner_team_id IS NOT NULL
  AND (b.source IS NULL OR h.source_kind=b.source OR (b.source='operations' AND h.source_kind='empty'));
 -- First proven attribution remains evidence even after a source switch clears its snapshot.
 IF attrs IS NULL THEN
  SELECT jsonb_agg(DISTINCT jsonb_build_object('owner_user_ref_id',h.owner_user_ref_id,
    'owner_team_id',h.owner_team_id)) INTO attrs
  FROM crm.opportunity_actual_attribution h WHERE h.workspace_id=ws AND h.opportunity_id=p_id
   AND h.year=p_year AND h.quarter=p_quarter AND h.kind='recognized'
   AND h.owner_user_ref_id IS NOT NULL AND h.owner_team_id IS NOT NULL;
 END IF;
 IF jsonb_array_length(attrs)=1 AND security.transfer_snapshot_complete(ws,attrs->0) THEN
  RETURN (attrs->0) || jsonb_build_object('status','frozen'); END IF;
 IF attrs IS NOT NULL OR b.id IS NOT NULL
  OR EXISTS(SELECT 1 FROM crm.opportunity_actual_attribution WHERE workspace_id=ws AND opportunity_id=p_id
   AND year=p_year AND quarter=p_quarter AND kind='recognized')
  OR EXISTS(SELECT 1 FROM crm.customer_actual WHERE workspace_id=ws AND opportunity_id=p_id AND kind='recognized'
   AND extract(year FROM occurred_on)=p_year AND extract(quarter FROM occurred_on)=p_quarter)
  OR EXISTS(SELECT 1 FROM crm.opportunity_period_actual_snapshot WHERE workspace_id=ws AND opportunity_id=p_id
   AND year=p_year AND quarter=p_quarter AND kind='recognized')
  OR EXISTS(SELECT 1 FROM crm.opportunity_owner_transfer_event WHERE workspace_id=ws AND opportunity_id=p_id
   AND make_date(p_year,(p_quarter-1)*3+1,1)<date_trunc('quarter',created_at)::date) THEN
  RETURN jsonb_build_object('status','unresolved','owner_user_ref_id',NULL,'owner_team_id',NULL); END IF;
 SELECT * INTO parent FROM crm.opportunity WHERE id=p_id AND workspace_id=ws;
 RETURN jsonb_build_object('status',CASE WHEN parent.owner_user_ref_id IS NULL OR parent.owner_team_id IS NULL THEN 'unresolved' ELSE 'frozen' END,
  'owner_user_ref_id',parent.owner_user_ref_id,'owner_team_id',parent.owner_team_id);
END $$;
REVOKE ALL ON FUNCTION security.recognized_period_attribution(uuid,integer,integer) FROM PUBLIC;

CREATE FUNCTION crm.guard_actual_attribution() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c crm.customer%ROWTYPE;
BEGIN
 IF TG_OP='UPDATE' THEN NEW.attribution_snapshot:=OLD.attribution_snapshot;
 ELSIF NEW.kind='recognized' THEN
  IF NEW.opportunity_id IS NOT NULL THEN
   NEW.attribution_snapshot:=security.recognized_period_attribution(NEW.opportunity_id,
    extract(year FROM NEW.occurred_on)::integer,extract(quarter FROM NEW.occurred_on)::integer);
  ELSE
   SELECT * INTO c FROM crm.customer WHERE id=NEW.customer_id AND workspace_id=NEW.workspace_id;
   NEW.attribution_snapshot:=jsonb_build_object('status',CASE WHEN c.owner_user_ref_id IS NULL THEN 'unresolved' ELSE 'frozen' END,
    'owner_user_ref_id',c.owner_user_ref_id,'owner_team_id',c.owner_team_id);
  END IF;
 ELSE NEW.attribution_snapshot:=NULL;
 END IF; RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION crm.guard_actual_attribution() FROM PUBLIC;
CREATE TRIGGER actual_attribution_guard BEFORE INSERT OR UPDATE ON crm.customer_actual
 FOR EACH ROW EXECUTE FUNCTION crm.guard_actual_attribution();

CREATE FUNCTION security.opportunity_recognized_edit_scope(p_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_user_ref_id() IS NOT NULL AND security.workspace_is_active(common.current_workspace_id())
 AND security.authorization_opportunity('opportunity.update',p_id)
 AND security.authorization_opportunity('actual.read',p_id)
 AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=p_id AND o.workspace_id=common.current_workspace_id()
  AND o.deleted_at IS NULL AND security.authorization_customer('customer.read',o.customer_id)
  AND security.authorization_customer('actual.read',o.customer_id));
$$;
REVOKE ALL ON FUNCTION security.opportunity_recognized_edit_scope(uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION crm.guard_actual_confirmation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE parent crm.opportunity%ROWTYPE; line crm.opportunity_material%ROWTYPE;
BEGIN
 PERFORM security.finance_write_lock();
 SELECT * INTO parent FROM crm.opportunity WHERE id=NEW.opportunity_id AND workspace_id=NEW.workspace_id FOR NO KEY UPDATE;
 IF NOT FOUND OR NOT (security.finance_management_scope(NEW.opportunity_id,'actual.create')
  OR (NEW.kind='collection' AND NEW.status='confirmed' AND security.opportunity_collection_edit_scope(NEW.opportunity_id))
  OR (NEW.kind='recognized' AND NEW.status='confirmed' AND security.opportunity_recognized_edit_scope(NEW.opportunity_id))) THEN
  RAISE EXCEPTION '仅有对象范围授权的运营管理身份可确认实际' USING ERRCODE='42501'; END IF;
 IF NEW.confirmed_by_user_ref_id IS DISTINCT FROM common.current_user_ref_id() THEN
  RAISE EXCEPTION 'ACTUAL_CONFIRMATION_ACTOR_MISMATCH' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.material_line_id,NEW.year,NEW.quarter,NEW.kind,NEW.created_at)
   IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.opportunity_id,OLD.material_line_id,OLD.year,OLD.quarter,OLD.kind,OLD.created_at) THEN
   RAISE EXCEPTION 'ACTUAL_CONFIRMATION_IDENTITY_IMMUTABLE' USING ERRCODE='23514'; END IF;
  IF NEW.version_no<>OLD.version_no+1 THEN RAISE EXCEPTION 'ACTUAL_CONFIRMATION_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 ELSE
  IF NEW.version_no<>1 OR NEW.status<>'confirmed' THEN RAISE EXCEPTION 'ACTUAL_CONFIRMATION_INITIAL_STATE' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.status='withdrawn' THEN
  IF NOT security.finance_management_scope(NEW.opportunity_id,'actual.void') THEN
   RAISE EXCEPTION '当前账号没有撤回实际的权限' USING ERRCODE='42501'; END IF;
  NEW.material_snapshot:=OLD.material_snapshot; NEW.amount:=OLD.amount;
 ELSE
  IF parent.trade_relation IS NULL THEN RAISE EXCEPTION '请先明确商机交易关系' USING ERRCODE='23514'; END IF;
  IF NEW.kind='collection' AND parent.trade_relation='downstream' AND NEW.amount<>0 THEN
   RAISE EXCEPTION '下游商机不能重复登记我方回款，请核对合同关系与收款归属' USING ERRCODE='23514'; END IF;
  IF NEW.kind='recognized' THEN
   PERFORM security.guard_finance_graph(NEW.workspace_id,NEW.opportunity_id,NEW.year,NEW.quarter,false);
   SELECT * INTO line FROM crm.opportunity_material WHERE id=NEW.material_line_id
    AND opportunity_id=NEW.opportunity_id AND workspace_id=NEW.workspace_id;
   IF NOT FOUND THEN RAISE EXCEPTION '实际确收必须引用本商机物料行' USING ERRCODE='23514'; END IF;
   IF parent.trade_relation='distributor' AND line.usage='resale' THEN
    RAISE EXCEPTION '总代转售部分须在下游终端商机确认确收' USING ERRCODE='23514'; END IF;
  END IF;
  IF TG_OP='INSERT' OR (OLD.status='withdrawn' AND NEW.kind<>'recognized') THEN
   NEW.material_snapshot:=jsonb_build_object('customer_id',parent.customer_id,'owner_user_ref_id',parent.owner_user_ref_id,
    'owner_team_id',parent.owner_team_id,'trade_relation',parent.trade_relation,'upstream_opportunity_id',parent.upstream_opportunity_id,
    'material_line',CASE WHEN NEW.kind='recognized' THEN to_jsonb(line) ELSE NULL END);
   IF NEW.kind='recognized' THEN NEW.material_snapshot:=NEW.material_snapshot ||
    security.recognized_period_attribution(NEW.opportunity_id,NEW.year,NEW.quarter); END IF;
  ELSE NEW.material_snapshot:=OLD.material_snapshot;
  END IF;
 END IF;
 NEW.confirmed_at:=clock_timestamp(); RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION crm.guard_opportunity_actual_basis() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE parent crm.opportunity%ROWTYPE; facts jsonb;
BEGIN
 PERFORM security.finance_write_lock();
 SELECT * INTO parent FROM crm.opportunity WHERE id=NEW.opportunity_id AND workspace_id=NEW.workspace_id FOR NO KEY UPDATE;
 IF NOT FOUND OR NOT (security.finance_management_scope(NEW.opportunity_id,'actual.create')
  OR (NEW.kind='collection' AND NEW.source='operations' AND security.opportunity_collection_edit_scope(NEW.opportunity_id))
  OR (NEW.kind='recognized' AND NEW.source='operations' AND NOT NEW.empty_confirmation
    AND security.opportunity_recognized_edit_scope(NEW.opportunity_id)))
  OR NEW.decided_by_user_ref_id IS DISTINCT FROM common.current_user_ref_id() THEN
  RAISE EXCEPTION 'ACTUAL_BASIS_ACTOR_MISMATCH' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.year,NEW.quarter,NEW.kind,NEW.created_at)
   IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.opportunity_id,OLD.year,OLD.quarter,OLD.kind,OLD.created_at) THEN
   RAISE EXCEPTION 'ACTUAL_BASIS_IDENTITY_IMMUTABLE' USING ERRCODE='23514'; END IF;
  IF NEW.version_no<>OLD.version_no+1 THEN RAISE EXCEPTION 'ACTUAL_BASIS_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
  IF OLD.empty_confirmation AND NOT NEW.empty_confirmation AND NEW.source='operations'
   AND NOT security.finance_management_scope(NEW.opportunity_id,'actual.void')
   AND NOT (NEW.kind='recognized' AND security.opportunity_recognized_edit_scope(NEW.opportunity_id)
     AND cardinality(NEW.expected_material_line_ids)>0) THEN
   RAISE EXCEPTION 'ACTUAL_EMPTY_WITHDRAW_PERMISSION' USING ERRCODE='42501'; END IF;
 ELSE
  IF NEW.version_no<>1 THEN RAISE EXCEPTION 'ACTUAL_BASIS_INITIAL_VERSION' USING ERRCODE='23514'; END IF;
  NEW.created_at:=clock_timestamp();
 END IF;
 IF NEW.kind='recognized' THEN
  NEW.attribution_snapshot:=CASE WHEN TG_OP='UPDATE' AND OLD.attribution_snapshot IS NOT NULL THEN OLD.attribution_snapshot
   ELSE security.recognized_period_attribution(NEW.opportunity_id,NEW.year,NEW.quarter) END;
 ELSE NEW.attribution_snapshot:=CASE WHEN TG_OP='UPDATE' THEN OLD.attribution_snapshot ELSE NULL END; END IF;
 IF NEW.source_fingerprint IS DISTINCT FROM security.finance_reference_fingerprint(
  NEW.workspace_id,NEW.opportunity_id,NEW.year,NEW.quarter,NEW.kind) THEN
  RAISE EXCEPTION 'ACTUAL_REFERENCE_CHANGED' USING ERRCODE='40001'; END IF;
 IF NEW.kind='recognized' THEN
  PERFORM security.guard_finance_graph(NEW.workspace_id,NEW.opportunity_id,NEW.year,NEW.quarter,NEW.source<>'operations');
  IF NEW.source<>'operations' THEN
   PERFORM security.guard_finance_graph(NEW.workspace_id,NEW.opportunity_id,NEW.year,NEW.quarter,false);
  END IF;
 END IF;
 IF NEW.source='operations' THEN
  IF parent.trade_relation IS NULL THEN RAISE EXCEPTION '请先明确商机交易关系' USING ERRCODE='23514'; END IF;
  IF NEW.kind='recognized' AND (NEW.expected_material_line_ids IS NULL OR
   cardinality(NEW.expected_material_line_ids)<>(SELECT count(DISTINCT x) FROM unnest(NEW.expected_material_line_ids) x)
   OR EXISTS(SELECT 1 FROM unnest(NEW.expected_material_line_ids) x WHERE NOT EXISTS(
    SELECT 1 FROM crm.opportunity_material m WHERE m.id=x AND m.workspace_id=NEW.workspace_id
     AND m.opportunity_id=NEW.opportunity_id AND (parent.trade_relation<>'distributor' OR m.usage='self_use')))) THEN
   RAISE EXCEPTION 'ACTUAL_EXPECTED_MATERIALS_INVALID' USING ERRCODE='23514'; END IF;
  IF NEW.kind='collection' AND (NEW.expected_material_line_ids IS NOT NULL OR NEW.empty_confirmation) THEN
   RAISE EXCEPTION 'COLLECTION_IS_ONE_OPPORTUNITY_FACT' USING ERRCODE='23514'; END IF;
  NEW.adopted_snapshot:=NULL;
  IF NEW.empty_confirmation THEN
   IF TG_OP='UPDATE' AND OLD.empty_confirmation AND OLD.operational_snapshot IS NOT NULL THEN
    NEW.operational_snapshot:=OLD.operational_snapshot;
   ELSE
    NEW.operational_snapshot:=jsonb_build_object('owner_user_ref_id',CASE WHEN NEW.kind='recognized' THEN (NEW.attribution_snapshot->>'owner_user_ref_id')::uuid ELSE parent.owner_user_ref_id END,
     'owner_team_id',CASE WHEN NEW.kind='recognized' THEN (NEW.attribution_snapshot->>'owner_team_id')::uuid ELSE parent.owner_team_id END,'trade_relation',parent.trade_relation,
     'upstream_opportunity_id',parent.upstream_opportunity_id,'captured_at',clock_timestamp());
   END IF;
  ELSE
   NEW.operational_snapshot:=CASE WHEN TG_OP='UPDATE' THEN OLD.operational_snapshot ELSE NULL END;
  END IF;
 ELSE
  NEW.expected_material_line_ids:=NULL; NEW.empty_confirmation:=false;
  IF NEW.source='entries' THEN
   SELECT jsonb_agg(jsonb_build_object('id',a.id,'amount',a.amount::text,'tax_basis',a.tax_basis,
    'occurred_on',a.occurred_on,'owner_user_ref_id',CASE WHEN NEW.kind='recognized' THEN (NEW.attribution_snapshot->>'owner_user_ref_id')::uuid ELSE parent.owner_user_ref_id END,'owner_team_id',CASE WHEN NEW.kind='recognized' THEN (NEW.attribution_snapshot->>'owner_team_id')::uuid ELSE parent.owner_team_id END,
    'confirmed_at',a.created_at,'source_ref',a.source_ref,'note',a.note) ORDER BY a.id) INTO facts
    FROM crm.customer_actual a WHERE a.workspace_id=NEW.workspace_id AND a.opportunity_id=NEW.opportunity_id
     AND a.kind=NEW.kind AND extract(year FROM a.occurred_on)=NEW.year AND extract(quarter FROM a.occurred_on)=NEW.quarter
     AND a.voided_at IS NULL;
  ELSE
   SELECT jsonb_build_array(jsonb_build_object('id',h.id,'amount',
    (h.raw_amount*CASE h.source_unit WHEN 'wan_cny' THEN 10000 ELSE 1 END)::text,'tax_basis',h.tax_basis,
    'occurred_on',NULL,'owner_user_ref_id',CASE WHEN NEW.kind='recognized' THEN (NEW.attribution_snapshot->>'owner_user_ref_id')::uuid ELSE parent.owner_user_ref_id END,'owner_team_id',CASE WHEN NEW.kind='recognized' THEN (NEW.attribution_snapshot->>'owner_team_id')::uuid ELSE parent.owner_team_id END,
    'confirmed_at',clock_timestamp(),'source_ref',h.source_record_id,'note',h.source_field)) INTO facts
    FROM crm.opportunity_period_actual_snapshot h WHERE h.id=NEW.selected_history_id AND h.workspace_id=NEW.workspace_id
     AND h.opportunity_id=NEW.opportunity_id AND h.kind=NEW.kind AND h.year=NEW.year AND h.quarter=NEW.quarter
     AND h.raw_amount IS NOT NULL AND h.source_unit IN ('cny','wan_cny');
  END IF;
  IF facts IS NULL THEN RAISE EXCEPTION 'ACTUAL_SOURCE_EMPTY_OR_INVALID' USING ERRCODE='23514'; END IF;
  IF NEW.kind='collection' AND parent.trade_relation='downstream' AND EXISTS(
   SELECT 1 FROM jsonb_array_elements(facts) f WHERE (f->>'amount')::numeric<>0) THEN
   RAISE EXCEPTION '下游商机不能采用非零我方回款，请核对合同关系与收款归属' USING ERRCODE='23514'; END IF;
  NEW.adopted_snapshot:=jsonb_build_object('facts',facts,'captured_at',clock_timestamp(),
   'trade_relation',parent.trade_relation,'upstream_opportunity_id',parent.upstream_opportunity_id);
 END IF;
 NEW.reason:=btrim(NEW.reason); NEW.updated_at:=clock_timestamp(); RETURN NEW;
END $$;



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
  CASE WHEN r.kind='recognized' AND (f.owner_user_ref_id IS NULL OR f.owner_team_id IS NULL) THEN NULL WHEN r.kind='recognized' OR r.source='operations' OR r.adopted_snapshot IS NOT NULL THEN f.owner_user_ref_id ELSE r.owner_user_ref_id END AS owner_user_ref_id,
  CASE WHEN r.kind='recognized' AND (f.owner_user_ref_id IS NULL OR f.owner_team_id IS NULL) THEN NULL WHEN r.kind='recognized' OR r.source='operations' OR r.adopted_snapshot IS NOT NULL THEN f.owner_team_id ELSE r.owner_team_id END AS owner_team_id,f.confirmed_at,f.source_ref,f.note,
  r.expected_material_count,CASE WHEN r.kind='recognized' THEN r.confirmed_count ELSE 0 END AS confirmed_material_count,r.missing_material_count,
  (NOT guard.current_conflict AND f.id IS NOT NULL AND r.missing_material_count=0 AND (r.kind='collection' OR f.tax_basis='exclusive')
   AND r.status NOT IN ('withdrawn','missing','needs_confirmation','stale_confirmation')) AS complete,
  r.reference_changed,r.source_fingerprint,r.version_no,
  CASE WHEN r.kind='recognized' AND (f.owner_user_ref_id IS NULL OR f.owner_team_id IS NULL) THEN 'unresolved' ELSE 'frozen' END AS attribution_status
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
  SELECT a.id::text,NULL::uuid,a.amount,a.tax_basis,a.occurred_on,(a.attribution_snapshot->>'owner_user_ref_id')::uuid,(a.attribution_snapshot->>'owner_team_id')::uuid,a.created_at,a.source_ref,a.note
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
  (date_trunc('quarter',a.occurred_on)+interval '3 months'-interval '1 day')::date,'day',CASE WHEN a.kind='recognized' THEN CASE WHEN a.attribution_snapshot->>'owner_user_ref_id' IS NOT NULL AND a.attribution_snapshot->>'owner_team_id' IS NOT NULL THEN (a.attribution_snapshot->>'owner_user_ref_id')::uuid END ELSE c.owner_user_ref_id END,
  CASE WHEN a.kind='recognized' THEN CASE WHEN a.attribution_snapshot->>'owner_user_ref_id' IS NOT NULL AND a.attribution_snapshot->>'owner_team_id' IS NOT NULL THEN (a.attribution_snapshot->>'owner_team_id')::uuid END ELSE c.owner_team_id END,
  a.created_at,a.source_ref,a.note,0,0::bigint,0::bigint,(a.kind='collection' OR a.tax_basis='exclusive'),false,NULL::text,0,CASE WHEN a.kind='recognized' AND (a.attribution_snapshot->>'owner_user_ref_id' IS NULL OR a.attribution_snapshot->>'owner_team_id' IS NULL) THEN 'unresolved' ELSE 'frozen' END
 FROM crm.customer_actual a JOIN crm.customer c ON c.id=a.customer_id AND c.workspace_id=a.workspace_id
 WHERE a.opportunity_id IS NULL AND a.voided_at IS NULL AND c.deleted_at IS NULL;

CREATE OR REPLACE FUNCTION security.transfer_preserve_attribution(p_workspace uuid,p_opportunity uuid,p_source text,p_fact text,
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
 ELSIF p_kind='recognized' THEN
  RETURN result; -- guarded V442 period attribution; never guess after transfer
 ELSE
  SELECT max(created_at) INTO transfer_at FROM crm.opportunity_owner_transfer_event WHERE workspace_id=p_workspace AND opportunity_id=p_opportunity;
  IF transfer_at IS NOT NULL AND (make_date(p_year,(p_quarter-1)*3+1,1)<date_trunc('quarter',transfer_at)::date
    OR (p_snapshot->>'occurred_on' IS NOT NULL AND (p_snapshot->>'occurred_on')::date<transfer_at::date))
  THEN RAISE EXCEPTION 'TRANSFER_ATTRIBUTION_UNRESOLVED' USING ERRCODE='40001'; END IF;
 END IF;
 RETURN result;
END $$;

-- Read projection is scoped before any definer read. Existing totals remain
-- totals: absent material evidence is never fabricated or apportioned.
CREATE FUNCTION security.opportunity_recognized_actual(p_id uuid,p_year integer,p_quarter integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); parent crm.opportunity%ROWTYPE;
 facts jsonb; result jsonb; items jsonb; editable boolean; reason text;
BEGIN
 IF p_year IS NULL OR p_quarter IS NULL OR p_year NOT BETWEEN 2000 AND 2100 OR p_quarter NOT BETWEEN 1 AND 4 THEN
  RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_PERIOD_INVALID' USING ERRCODE='22023'; END IF;
 SELECT * INTO parent FROM crm.opportunity WHERE id=p_id AND workspace_id=ws AND deleted_at IS NULL;
 IF NOT FOUND OR NOT security.workspace_is_active(ws)
  OR NOT security.authorization_opportunity('opportunity.read',p_id)
  OR NOT security.authorization_opportunity('actual.read',p_id)
  OR NOT security.authorization_customer('customer.read',parent.customer_id)
  OR NOT security.authorization_customer('actual.read',parent.customer_id) THEN
  RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_FORBIDDEN' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object(
  'reference',security.finance_reference_fingerprint(ws,p_id,p_year,p_quarter,'recognized'),
  'relation',parent.trade_relation,'upstream',parent.upstream_opportunity_id,
  'basis',(SELECT to_jsonb(b) FROM crm.opportunity_actual_basis b WHERE b.workspace_id=ws AND b.opportunity_id=p_id
    AND b.year=p_year AND b.quarter=p_quarter AND b.kind='recognized'),
  'confirmations',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM crm.opportunity_actual_confirmation a
    WHERE a.workspace_id=ws AND a.opportunity_id=p_id AND a.year=p_year AND a.quarter=p_quarter AND a.kind='recognized'),
  'materials',(SELECT jsonb_agg(to_jsonb(m) ORDER BY m.id) FROM crm.opportunity_material m
    WHERE m.workspace_id=ws AND m.opportunity_id=p_id)) INTO facts;
 SELECT jsonb_build_object('amount',CASE WHEN bool_and(e.complete) THEN sum(e.amount)::text ELSE NULL END,
  'known_subtotal',sum(e.amount)::text,'status',min(e.status),'source',min(e.source),
  'missing_material_count',COALESCE(max(e.missing_material_count),0),
  'attribution_status',CASE WHEN count(*)=0 THEN 'not_registered'
    WHEN bool_or(e.attribution_status='unresolved') THEN 'unresolved' ELSE 'frozen' END) INTO result
 FROM crm.effective_actual e WHERE e.workspace_id=ws AND e.opportunity_id=p_id
  AND e.year=p_year AND e.quarter=p_quarter AND e.kind='recognized';
 SELECT COALESCE(jsonb_agg(jsonb_build_object('material_line_id',m.id,
  'material_code',COALESCE(m.catalog_snapshot->>'code',''),
  'material_name',COALESCE(m.catalog_snapshot->>'name','物料'),
  'is_active',m.is_active,'eligible',(parent.trade_relation IS NOT NULL
   AND (parent.trade_relation<>'distributor' OR m.usage='self_use')),
  'amount',CASE WHEN a.status='confirmed' THEN a.amount::text ELSE NULL END,
  'status',COALESCE(a.status,'missing'),'source_ref',COALESCE(a.source_ref,''),'note',COALESCE(a.note,''))
  ORDER BY m.line_no,m.id),'[]') INTO items
 FROM crm.opportunity_material m LEFT JOIN crm.opportunity_actual_confirmation a
  ON a.workspace_id=m.workspace_id AND a.opportunity_id=p_id AND a.material_line_id=m.id
   AND a.year=p_year AND a.quarter=p_quarter AND a.kind='recognized'
 WHERE m.workspace_id=ws AND m.opportunity_id=p_id AND (m.is_active OR a.id IS NOT NULL);
 editable:=security.opportunity_recognized_edit_scope(p_id);
 IF NOT editable THEN reason:='OPPORTUNITY_RECOGNIZED_FORBIDDEN';
 ELSIF parent.trade_relation IS NULL THEN editable:=false;reason:='OPPORTUNITY_TRADE_RELATION_REQUIRED';
 ELSIF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(items) i WHERE (i->>'eligible')::boolean) THEN
  editable:=false;reason:='OPPORTUNITY_RECOGNIZED_MATERIAL_REQUIRED'; END IF;
 RETURN jsonb_build_object('opportunity_id',p_id,'period',jsonb_build_object('year',p_year,'quarter',p_quarter),
  'can_edit',editable,'editable_reason',reason,'items',items,'actual',result||jsonb_build_object(
   'status',COALESCE(result->>'status','missing'),
   'revision',encode(sha256(convert_to(facts::text,'UTF8')),'hex'),
   'requires_source_replacement',(result->>'source' IS DISTINCT FROM 'operations') AND EXISTS(
    SELECT 1 FROM crm.finance_period_resolution p WHERE p.workspace_id=ws AND p.opportunity_id=p_id
     AND p.year=p_year AND p.quarter=p_quarter AND p.kind='recognized'
     AND (p.entry_count>0 OR p.history_count>0 OR p.basis_id IS NOT NULL))));
END $$;

CREATE FUNCTION security.save_opportunity_recognized_actual(p_id uuid,p_year integer,p_quarter integer,
 p_entries jsonb,p_revision text,p_replace_source boolean DEFAULT false) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); uid uuid:=common.current_user_ref_id(); before jsonb; e jsonb;
 a crm.opportunity_actual_confirmation%ROWTYPE; b crm.opportunity_actual_basis%ROWTYPE;
 expected uuid[]; reference text; changed boolean:=false; line_id uuid; amount_value numeric;
BEGIN
 PERFORM security.finance_write_lock();
 IF NOT security.opportunity_recognized_edit_scope(p_id) THEN
  RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_FORBIDDEN' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM crm.opportunity WHERE id=p_id AND workspace_id=ws FOR NO KEY UPDATE;
 before:=security.opportunity_recognized_actual(p_id,p_year,p_quarter);
 IF p_revision IS NULL OR p_revision IS DISTINCT FROM before->'actual'->>'revision' THEN
  RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 IF NOT (before->>'can_edit')::boolean THEN
  IF before->>'editable_reason'='OPPORTUNITY_TRADE_RELATION_REQUIRED' THEN
   RAISE EXCEPTION 'OPPORTUNITY_TRADE_RELATION_REQUIRED' USING ERRCODE='23514'; END IF;
  RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_MATERIAL_REQUIRED' USING ERRCODE='22023'; END IF;
 IF p_entries IS NULL OR jsonb_typeof(p_entries)<>'array' OR jsonb_array_length(p_entries) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 FOR e IN SELECT value FROM jsonb_array_elements(p_entries) LOOP
  IF jsonb_typeof(e)<>'object' OR NOT(e ?& ARRAY['material_line_id','amount','source_ref'])
   OR EXISTS(SELECT 1 FROM jsonb_object_keys(e) k WHERE k NOT IN ('material_line_id','amount','source_ref','note'))
   OR COALESCE(e->>'material_line_id','') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
   OR COALESCE(e->>'amount','') !~ '^[0-9]{1,16}(\.[0-9]{1,2})?$'
   OR jsonb_typeof(e->'source_ref')<>'string' OR length(btrim(e->>'source_ref')) NOT BETWEEN 1 AND 160
   OR (e ? 'note' AND (jsonb_typeof(e->'note')<>'string' OR length(e->>'note')>1000)) THEN
   RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_INPUT_INVALID' USING ERRCODE='22023'; END IF;
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(before->'items') i
   WHERE i->>'material_line_id'=e->>'material_line_id' AND (i->>'eligible')::boolean) THEN
   RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_MATERIAL_FORBIDDEN' USING ERRCODE='22023'; END IF;
 END LOOP;
 IF jsonb_array_length(p_entries)<>(SELECT count(DISTINCT (v->>'material_line_id')::uuid)
  FROM jsonb_array_elements(p_entries) v) THEN
  RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_INPUT_INVALID' USING ERRCODE='22023'; END IF;
 IF (before->'actual'->>'requires_source_replacement')::boolean AND NOT COALESCE(p_replace_source,false) THEN
  RAISE EXCEPTION 'OPPORTUNITY_RECOGNIZED_SOURCE_CONFIRMATION_REQUIRED' USING ERRCODE='40001'; END IF;
 SELECT * INTO b FROM crm.opportunity_actual_basis WHERE workspace_id=ws AND opportunity_id=p_id
  AND year=p_year AND quarter=p_quarter AND kind='recognized' FOR UPDATE;
 SELECT array_agg(DISTINCT id ORDER BY id) INTO expected FROM (
  SELECT (i->>'material_line_id')::uuid id FROM jsonb_array_elements(before->'items') i WHERE (i->>'eligible')::boolean
  UNION SELECT unnest(b.expected_material_line_ids)
 ) ids;
 changed:=b.id IS NULL OR b.source<>'operations' OR b.empty_confirmation
  OR b.expected_material_line_ids IS DISTINCT FROM expected;
 FOR e IN SELECT value FROM jsonb_array_elements(p_entries) LOOP
  SELECT * INTO a FROM crm.opportunity_actual_confirmation WHERE workspace_id=ws AND opportunity_id=p_id
   AND year=p_year AND quarter=p_quarter AND kind='recognized' AND material_line_id=(e->>'material_line_id')::uuid;
  changed:=changed OR a.id IS NULL OR a.status<>'confirmed' OR a.amount<>(e->>'amount')::numeric
   OR a.source_ref<>btrim(e->>'source_ref') OR a.note<>btrim(COALESCE(e->>'note',''));
 END LOOP;
 IF NOT changed THEN RETURN false; END IF;
 SET CONSTRAINTS crm.actual_final_state,crm.basis_final_state DEFERRED;
 reference:=security.finance_reference_fingerprint(ws,p_id,p_year,p_quarter,'recognized');
 -- Freeze the period before adding its first/new material, under the same lock.
 IF b.id IS NULL THEN
  INSERT INTO crm.opportunity_actual_basis(workspace_id,opportunity_id,year,quarter,kind,source,source_fingerprint,
   reason,decided_by_user_ref_id,expected_material_line_ids)
  VALUES(ws,p_id,p_year,p_quarter,'recognized','operations',reference,'商机实际确收修改',uid,expected);
 ELSE
  UPDATE crm.opportunity_actual_basis SET source='operations',selected_history_id=NULL,source_fingerprint=reference,
   reason='商机实际确收修改',decided_by_user_ref_id=uid,expected_material_line_ids=expected,empty_confirmation=false,
   version_no=version_no+1 WHERE id=b.id;
 END IF;
 FOR e IN SELECT value FROM jsonb_array_elements(p_entries) LOOP
  line_id:=(e->>'material_line_id')::uuid; amount_value:=(e->>'amount')::numeric;
  SELECT * INTO a FROM crm.opportunity_actual_confirmation WHERE workspace_id=ws AND opportunity_id=p_id
   AND year=p_year AND quarter=p_quarter AND kind='recognized' AND material_line_id=line_id FOR UPDATE;
  IF a.id IS NULL THEN
   INSERT INTO crm.opportunity_actual_confirmation(workspace_id,opportunity_id,material_line_id,year,quarter,kind,
    amount,tax_basis,source_ref,note,reason,material_snapshot,confirmed_by_user_ref_id)
   VALUES(ws,p_id,line_id,p_year,p_quarter,'recognized',amount_value,'exclusive',btrim(e->>'source_ref'),
    btrim(COALESCE(e->>'note','')),'商机实际确收修改','{}',uid);
  ELSIF a.status<>'confirmed' OR a.amount<>amount_value OR a.source_ref<>btrim(e->>'source_ref')
   OR a.note<>btrim(COALESCE(e->>'note','')) THEN
   UPDATE crm.opportunity_actual_confirmation SET amount=amount_value,status='confirmed',source_ref=btrim(e->>'source_ref'),
    note=btrim(COALESCE(e->>'note','')),reason='商机实际确收修改',confirmed_by_user_ref_id=uid,version_no=version_no+1 WHERE id=a.id;
  END IF;
 END LOOP;
 SET CONSTRAINTS crm.actual_final_state,crm.basis_final_state IMMEDIATE;
 RETURN true;
END $$;

DO $$ DECLARE sig text; grantee record; BEGIN
 FOREACH sig IN ARRAY ARRAY['security.recognized_period_attribution(uuid,integer,integer)',
  'crm.guard_actual_attribution()','security.opportunity_recognized_edit_scope(uuid)',
  'security.opportunity_recognized_actual(uuid,integer,integer)',
  'security.save_opportunity_recognized_actual(uuid,integer,integer,jsonb,text,boolean)'] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',sig);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
   JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=to_regprocedure(sig) AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',sig,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V442','Scoped material quarterly net-recognition edits with preserved attribution');
COMMIT;
