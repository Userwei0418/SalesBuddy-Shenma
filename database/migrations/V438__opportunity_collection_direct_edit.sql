BEGIN;
-- V438 is intentionally independent of reserved V436/V437. No business rows,
-- roles, general actual.create grants or approval workflow are introduced.
CREATE FUNCTION security.opportunity_collection_edit_scope(p_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_user_ref_id() IS NOT NULL
 AND security.workspace_is_active(common.current_workspace_id())
 AND security.authorization_opportunity('opportunity.update',p_id)
 AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=p_id
   AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL);
$$;

-- Only the effective collection amount and revision are exposed, never raw
-- finance evidence or recognized revenue. Caller scope is checked before data.
CREATE FUNCTION security.opportunity_collection_actual(p_id uuid,p_year integer,p_quarter integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); parent crm.opportunity%ROWTYPE;
 facts jsonb; result jsonb; current_row crm.opportunity_actual_confirmation%ROWTYPE;
BEGIN
 IF p_year NOT BETWEEN 2000 AND 2100 OR p_quarter NOT BETWEEN 1 AND 4 THEN
  RAISE EXCEPTION '回款季度无效' USING ERRCODE='22023'; END IF;
 IF NOT security.workspace_is_active(ws) OR NOT security.authorization_opportunity('opportunity.read',p_id) THEN
  RAISE EXCEPTION '商机不存在或不在当前权限范围内' USING ERRCODE='P0002'; END IF;
 SELECT * INTO parent FROM crm.opportunity WHERE id=p_id AND workspace_id=ws AND deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION '商机不存在或不在当前权限范围内' USING ERRCODE='P0002'; END IF;
 SELECT jsonb_build_object(
  'reference',security.finance_reference_fingerprint(ws,p_id,p_year,p_quarter,'collection'),
  'basis',(SELECT to_jsonb(b) FROM crm.opportunity_actual_basis b WHERE b.workspace_id=ws AND b.opportunity_id=p_id
    AND b.year=p_year AND b.quarter=p_quarter AND b.kind='collection'),
  'confirmations',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM crm.opportunity_actual_confirmation a
    WHERE a.workspace_id=ws AND a.opportunity_id=p_id AND a.year=p_year AND a.quarter=p_quarter AND a.kind='collection')) INTO facts;
 SELECT jsonb_build_object('amount',CASE WHEN bool_and(e.complete) THEN sum(e.amount)::text ELSE NULL END,
  'status',min(e.status),'source',min(e.source)) INTO result
  FROM crm.effective_actual e WHERE e.workspace_id=ws AND e.opportunity_id=p_id
   AND e.year=p_year AND e.quarter=p_quarter AND e.kind='collection';
 SELECT * INTO current_row FROM crm.opportunity_actual_confirmation a WHERE a.workspace_id=ws AND a.opportunity_id=p_id
  AND a.year=p_year AND a.quarter=p_quarter AND a.kind='collection' AND a.status='confirmed';
 RETURN result || jsonb_build_object('status',COALESCE(result->>'status','missing'),
  'revision',encode(sha256(convert_to(facts::text,'UTF8')),'hex'),
  'source_ref',CASE WHEN result->>'source'='operations' THEN COALESCE(current_row.source_ref,'') ELSE '' END,
  'note',CASE WHEN result->>'source'='operations' THEN COALESCE(current_row.note,'') ELSE '' END,
  'requires_source_replacement',(result->>'source' IS DISTINCT FROM 'operations') AND EXISTS(
    SELECT 1 FROM crm.finance_period_resolution p WHERE p.workspace_id=ws AND p.opportunity_id=p_id
     AND p.year=p_year AND p.quarter=p_quarter AND p.kind='collection'
     AND (p.entry_count>0 OR p.history_count>0 OR p.basis_id IS NOT NULL)));
END $$;

-- General tables retain their existing RLS and API rights. The only new public
-- application entry point accepts collection fields, never kind/source/actor.
CREATE FUNCTION security.save_opportunity_collection_actual(p_id uuid,p_year integer,p_quarter integer,p_amount numeric,
 p_source_ref text,p_note text,p_revision text,p_replace_source boolean DEFAULT false) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); uid uuid:=common.current_user_ref_id(); before jsonb;
 a crm.opportunity_actual_confirmation%ROWTYPE; b crm.opportunity_actual_basis%ROWTYPE; reference text;
BEGIN
 PERFORM security.finance_write_lock();
 IF NOT security.opportunity_collection_edit_scope(p_id) THEN
  RAISE EXCEPTION '当前账号无权修改此商机的实际回款' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM crm.opportunity WHERE id=p_id AND workspace_id=ws FOR NO KEY UPDATE;
 before:=security.opportunity_collection_actual(p_id,p_year,p_quarter);
 IF p_revision IS NULL OR p_revision IS DISTINCT FROM before->>'revision' THEN
  RAISE EXCEPTION '实际回款或来源已变化，请刷新后重新核对' USING ERRCODE='40001'; END IF;
 IF p_amount IS NULL OR p_amount<0 OR p_amount>=10000000000000000 OR p_amount<>round(p_amount,2)
  OR p_source_ref IS NULL OR length(btrim(p_source_ref)) NOT BETWEEN 1 AND 160
  OR p_note IS NULL OR length(p_note)>1000 THEN
  RAISE EXCEPTION '请核对实际回款金额、登记依据和备注' USING ERRCODE='22023'; END IF;
 IF (before->>'requires_source_replacement')::boolean AND NOT COALESCE(p_replace_source,false) THEN
  RAISE EXCEPTION '请确认将本季度现有来源改为本次人工登记，原始依据仍会保留' USING ERRCODE='40001'; END IF;
 SELECT * INTO a FROM crm.opportunity_actual_confirmation WHERE workspace_id=ws AND opportunity_id=p_id
  AND year=p_year AND quarter=p_quarter AND kind='collection' FOR UPDATE;
 SELECT * INTO b FROM crm.opportunity_actual_basis WHERE workspace_id=ws AND opportunity_id=p_id
  AND year=p_year AND quarter=p_quarter AND kind='collection' FOR UPDATE;
 IF a.id IS NOT NULL AND a.status='confirmed' AND b.source='operations'
  AND a.amount=p_amount AND a.source_ref=btrim(p_source_ref) AND a.note=btrim(p_note) THEN RETURN false; END IF;
 SET CONSTRAINTS crm.actual_final_state,crm.basis_final_state DEFERRED;
 reference:=security.finance_reference_fingerprint(ws,p_id,p_year,p_quarter,'collection');
 IF a.id IS NULL THEN
  INSERT INTO crm.opportunity_actual_confirmation(workspace_id,opportunity_id,year,quarter,kind,amount,tax_basis,
   source_ref,note,reason,material_snapshot,confirmed_by_user_ref_id)
  VALUES(ws,p_id,p_year,p_quarter,'collection',p_amount,'not_applicable',btrim(p_source_ref),btrim(p_note),
   '商机回款修改','{}',uid);
 ELSE
  UPDATE crm.opportunity_actual_confirmation SET amount=p_amount,status='confirmed',source_ref=btrim(p_source_ref),
   note=btrim(p_note),reason='商机回款修改',confirmed_by_user_ref_id=uid,version_no=version_no+1 WHERE id=a.id;
 END IF;
 IF b.id IS NULL THEN
  INSERT INTO crm.opportunity_actual_basis(workspace_id,opportunity_id,year,quarter,kind,source,source_fingerprint,
   reason,decided_by_user_ref_id) VALUES(ws,p_id,p_year,p_quarter,'collection','operations',reference,'商机回款修改',uid);
 ELSE
  UPDATE crm.opportunity_actual_basis SET source='operations',selected_history_id=NULL,source_fingerprint=reference,
   reason='商机回款修改',decided_by_user_ref_id=uid,expected_material_line_ids=NULL,empty_confirmation=false,
   version_no=version_no+1 WHERE id=b.id;
 END IF;
 SET CONSTRAINTS crm.actual_final_state,crm.basis_final_state IMMEDIATE;
 RETURN true;
END $$;

-- Existing actor, source graph, snapshot, version, withdrawal and final-state
-- checks are preserved byte for byte except for this collection-only admission.
CREATE OR REPLACE FUNCTION crm.guard_actual_confirmation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE parent crm.opportunity%ROWTYPE; line crm.opportunity_material%ROWTYPE;
BEGIN
 PERFORM security.finance_write_lock();
 SELECT * INTO parent FROM crm.opportunity WHERE id=NEW.opportunity_id AND workspace_id=NEW.workspace_id FOR NO KEY UPDATE;
 IF NOT FOUND OR NOT (security.finance_management_scope(NEW.opportunity_id,'actual.create')
  OR (NEW.kind='collection' AND NEW.status='confirmed' AND security.opportunity_collection_edit_scope(NEW.opportunity_id))) THEN
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
  IF TG_OP='INSERT' OR OLD.status='withdrawn' THEN
   NEW.material_snapshot:=jsonb_build_object('customer_id',parent.customer_id,'owner_user_ref_id',parent.owner_user_ref_id,
    'owner_team_id',parent.owner_team_id,'trade_relation',parent.trade_relation,'upstream_opportunity_id',parent.upstream_opportunity_id,
    'material_line',CASE WHEN NEW.kind='recognized' THEN to_jsonb(line) ELSE NULL END);
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
  OR (NEW.kind='collection' AND NEW.source='operations' AND security.opportunity_collection_edit_scope(NEW.opportunity_id)))
  OR NEW.decided_by_user_ref_id IS DISTINCT FROM common.current_user_ref_id() THEN
  RAISE EXCEPTION 'ACTUAL_BASIS_ACTOR_MISMATCH' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.year,NEW.quarter,NEW.kind,NEW.created_at)
   IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.opportunity_id,OLD.year,OLD.quarter,OLD.kind,OLD.created_at) THEN
   RAISE EXCEPTION 'ACTUAL_BASIS_IDENTITY_IMMUTABLE' USING ERRCODE='23514'; END IF;
  IF NEW.version_no<>OLD.version_no+1 THEN RAISE EXCEPTION 'ACTUAL_BASIS_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
  IF OLD.empty_confirmation AND NOT NEW.empty_confirmation AND NEW.source='operations'
   AND NOT security.finance_management_scope(NEW.opportunity_id,'actual.void') THEN
   RAISE EXCEPTION 'ACTUAL_EMPTY_WITHDRAW_PERMISSION' USING ERRCODE='42501'; END IF;
 ELSE
  IF NEW.version_no<>1 THEN RAISE EXCEPTION 'ACTUAL_BASIS_INITIAL_VERSION' USING ERRCODE='23514'; END IF;
  NEW.created_at:=clock_timestamp();
 END IF;
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
    NEW.operational_snapshot:=jsonb_build_object('owner_user_ref_id',parent.owner_user_ref_id,
     'owner_team_id',parent.owner_team_id,'trade_relation',parent.trade_relation,
     'upstream_opportunity_id',parent.upstream_opportunity_id,'captured_at',clock_timestamp());
   END IF;
  ELSE
   NEW.operational_snapshot:=CASE WHEN TG_OP='UPDATE' THEN OLD.operational_snapshot ELSE NULL END;
  END IF;
 ELSE
  NEW.expected_material_line_ids:=NULL; NEW.empty_confirmation:=false;
  IF NEW.source='entries' THEN
   SELECT jsonb_agg(jsonb_build_object('id',a.id,'amount',a.amount::text,'tax_basis',a.tax_basis,
    'occurred_on',a.occurred_on,'owner_user_ref_id',parent.owner_user_ref_id,'owner_team_id',parent.owner_team_id,
    'confirmed_at',a.created_at,'source_ref',a.source_ref,'note',a.note) ORDER BY a.id) INTO facts
    FROM crm.customer_actual a WHERE a.workspace_id=NEW.workspace_id AND a.opportunity_id=NEW.opportunity_id
     AND a.kind=NEW.kind AND extract(year FROM a.occurred_on)=NEW.year AND extract(quarter FROM a.occurred_on)=NEW.quarter
     AND a.voided_at IS NULL;
  ELSE
   SELECT jsonb_build_array(jsonb_build_object('id',h.id,'amount',
    (h.raw_amount*CASE h.source_unit WHEN 'wan_cny' THEN 10000 ELSE 1 END)::text,'tax_basis',h.tax_basis,
    'occurred_on',NULL,'owner_user_ref_id',parent.owner_user_ref_id,'owner_team_id',parent.owner_team_id,
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


-- Revoke hostile default ACLs as well as PUBLIC. Runtime policy grants only the
-- projection + one narrow save entry point to API; trigger helpers stay private.
DO $$ DECLARE sig text; grantee record; BEGIN
 FOREACH sig IN ARRAY ARRAY['security.opportunity_collection_edit_scope(uuid)',
  'security.opportunity_collection_actual(uuid,integer,integer)',
  'security.save_opportunity_collection_actual(uuid,integer,integer,numeric,text,text,text,boolean)'] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',sig);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a
    JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=to_regprocedure(sig) AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',sig,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V438','Scoped direct cumulative collection edit with opportunity save');
COMMIT;
