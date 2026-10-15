-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V212__materials_and_operational_actuals.sql.
-- Original SHA-256: 459d1ff242acaaa1b9e750831646683e0d7a4aef1e5aa4f41509b9437bbe658f. See shenma_whole_121_manifest.json.
BEGIN;

-- Additive finance model. Applied migrations and original monetary evidence are
-- immutable. No catalog samples, historical amount backfill or outbox refresh.
INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('material.read','查看物料目录','物料与实绩',ARRAY['workspace'],false),
 ('material.manage','维护物料与产品线','物料与实绩',ARRAY['workspace'],true);
INSERT INTO control.permission_module(permission_code,module_code) VALUES
 ('material.read','core'),('material.manage','core');
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
 SELECT workspace_id,id,'material.read','workspace' FROM config.permission_role WHERE builtin_role_code IS NOT NULL;
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
 SELECT workspace_id,id,'material.manage','workspace' FROM config.permission_role
 WHERE builtin_role_code IN ('operations','administrator');
DELETE FROM config.permission_role_grant g USING config.permission_role r
 WHERE g.role_id=r.id AND r.builtin_role_code IN ('sales','supervisor','manager','fde','fde_lead')
 AND g.permission_code IN ('actual.create','actual.void');
UPDATE config.permission_role_default d SET permissions=(
 SELECT COALESCE(jsonb_agg(p),'[]') FROM jsonb_array_elements(d.permissions) p
 WHERE d.role_code IN ('operations','administrator') OR p->>'permission' NOT IN ('actual.create','actual.void'))
 ||jsonb_build_array(jsonb_build_object('permission','material.read','scope','workspace','team_ids','[]'::jsonb))
 ||CASE WHEN d.role_code IN ('operations','administrator') THEN
 jsonb_build_array(jsonb_build_object('permission','material.manage','scope','workspace','team_ids','[]'::jsonb)) ELSE '[]'::jsonb END;
UPDATE config.permission_role SET version_no=version_no+1,updated_at=clock_timestamp() WHERE builtin_role_code IS NOT NULL;
UPDATE config.authorization_scope_revision SET revision=revision+1;

ALTER TABLE crm.opportunity ADD COLUMN trade_relation text CHECK(trade_relation IN ('direct','distributor','downstream'));
ALTER TABLE crm.opportunity ADD COLUMN upstream_opportunity_id uuid;
ALTER TABLE crm.opportunity ADD CONSTRAINT opportunity_upstream_same_workspace
 FOREIGN KEY(upstream_opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id);
ALTER TABLE crm.opportunity ADD CONSTRAINT opportunity_relation_shape CHECK(
 (COALESCE(trade_relation,'')='downstream' AND upstream_opportunity_id IS NOT NULL AND upstream_opportunity_id<>id)
 OR (COALESCE(trade_relation,'')<>'downstream' AND upstream_opportunity_id IS NULL));

CREATE TABLE crm.material (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 code text NOT NULL CHECK(length(btrim(code)) BETWEEN 1 AND 100),
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 200), product_line_id uuid NOT NULL REFERENCES config.dictionary_item(id),
 unit text NOT NULL CHECK(length(btrim(unit)) BETWEEN 1 AND 50),
 catalog_price numeric(18,2) NOT NULL CHECK(catalog_price>=0 AND catalog_price<10000000000000000),
 currency text NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
 tax_rate numeric(7,6) NOT NULL CHECK(tax_rate>=0 AND tax_rate<=1),
 recognition_rule text NOT NULL CHECK(recognition_rule IN ('manual','delivery','monthly','consumption')),
 rule_parameters jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(rule_parameters)='object'),
 is_active boolean NOT NULL DEFAULT true, version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),
 updated_by_user_ref_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id), UNIQUE(workspace_id,code),
 FOREIGN KEY(updated_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);

CREATE TABLE crm.opportunity_material (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 opportunity_id uuid NOT NULL, material_id uuid NOT NULL,
 line_no integer NOT NULL CHECK(line_no>0), quantity numeric(16,4) NOT NULL CHECK(quantity>0 AND quantity<1000000000000),
 unit_price numeric(18,2) NOT NULL CHECK(unit_price>=0 AND unit_price<10000000000000000),
 line_amount numeric(18,2) GENERATED ALWAYS AS (round(quantity*unit_price,2)) STORED,
 usage text NOT NULL CHECK(usage IN ('self_use','resale')),
 expected_delivery_on date, expected_start_on date, expected_oa_on date,
 service_months integer CHECK(service_months BETWEEN 1 AND 120),
 expected_account_count integer CHECK(expected_account_count>=0),
 expected_consumption_amount numeric(18,2) CHECK(expected_consumption_amount>=0 AND expected_consumption_amount<10000000000000000),
 expected_consumption_year integer CHECK(expected_consumption_year BETWEEN 2000 AND 2100),
 expected_consumption_quarter integer CHECK(expected_consumption_quarter BETWEEN 1 AND 4),
 note text NOT NULL DEFAULT '' CHECK(length(note)<=1000), catalog_snapshot jsonb NOT NULL,
 is_active boolean NOT NULL DEFAULT true, version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 updated_by_user_ref_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id), UNIQUE(id,opportunity_id,workspace_id),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(material_id,workspace_id) REFERENCES crm.material(id,workspace_id),
 FOREIGN KEY(updated_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 CHECK((expected_consumption_year IS NULL)=(expected_consumption_quarter IS NULL))
);
CREATE INDEX opportunity_material_parent ON crm.opportunity_material(workspace_id,opportunity_id,line_no,id);

CREATE TABLE crm.opportunity_actual_confirmation (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 opportunity_id uuid NOT NULL, material_line_id uuid,
 year integer NOT NULL CHECK(year BETWEEN 2000 AND 2100), quarter integer NOT NULL CHECK(quarter BETWEEN 1 AND 4),
 kind text NOT NULL CHECK(kind IN ('recognized','collection')),
 amount numeric(18,2) NOT NULL CHECK(amount>=0 AND amount<10000000000000000),
 currency text NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
 tax_basis text NOT NULL CHECK((kind='recognized' AND tax_basis='exclusive') OR (kind='collection' AND tax_basis='not_applicable')),
 status text NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed','withdrawn')),
 source_ref text NOT NULL CHECK(length(btrim(source_ref)) BETWEEN 1 AND 160),
 note text NOT NULL DEFAULT '' CHECK(length(note)<=1000), reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),
 material_snapshot jsonb NOT NULL, confirmed_by_user_ref_id uuid NOT NULL,
 confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 UNIQUE(id,workspace_id),
 FOREIGN KEY(opportunity_id,workspace_id) REFERENCES crm.opportunity(id,workspace_id),
 FOREIGN KEY(material_line_id,opportunity_id,workspace_id) REFERENCES crm.opportunity_material(id,opportunity_id,workspace_id),
 FOREIGN KEY(confirmed_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 CHECK((kind='recognized')=(material_line_id IS NOT NULL))
);
CREATE UNIQUE INDEX actual_confirmation_recognized_unique ON crm.opportunity_actual_confirmation
 (workspace_id,opportunity_id,material_line_id,year,quarter) WHERE kind='recognized';
CREATE UNIQUE INDEX actual_confirmation_collection_unique ON crm.opportunity_actual_confirmation
 (workspace_id,opportunity_id,year,quarter) WHERE kind='collection';

ALTER TABLE crm.opportunity_actual_basis DROP CONSTRAINT opportunity_actual_basis_source_check;
ALTER TABLE crm.opportunity_actual_basis DROP CONSTRAINT opportunity_actual_basis_check;
ALTER TABLE crm.opportunity_actual_basis ADD CONSTRAINT opportunity_actual_basis_source_check
 CHECK(source IN ('entries','historical','operations'));
ALTER TABLE crm.opportunity_actual_basis ADD CONSTRAINT opportunity_actual_basis_check
 CHECK((source IN ('entries','operations') AND selected_history_id IS NULL) OR (source='historical' AND selected_history_id IS NOT NULL));
ALTER TABLE crm.opportunity_actual_basis ADD COLUMN adopted_snapshot jsonb;
ALTER TABLE crm.opportunity_actual_basis ADD COLUMN operational_snapshot jsonb;
ALTER TABLE crm.opportunity_actual_basis ADD COLUMN expected_material_line_ids uuid[];
ALTER TABLE crm.opportunity_actual_basis ADD COLUMN empty_confirmation boolean NOT NULL DEFAULT false;
ALTER TABLE crm.opportunity_actual_basis ADD CONSTRAINT basis_empty_confirmation_shape
 CHECK(NOT empty_confirmation OR (source='operations' AND kind='recognized' AND cardinality(expected_material_line_ids)=0));
COMMENT ON COLUMN crm.opportunity_actual_basis.adopted_snapshot IS 'Explicit source adoption snapshot from V212 forward; legacy decisions are not backfilled.';
COMMENT ON COLUMN crm.opportunity_actual_basis.expected_material_line_ids IS 'Operationally reviewed material population for this specific period; not a dynamic list of all active materials.';

CREATE FUNCTION security.finance_management_scope(p_opportunity uuid,p_permission text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT security.management_actor() AND security.authorization_has('access.console')
 AND security.authorization_opportunity(p_permission,p_opportunity)
 AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=p_opportunity
  AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
  AND security.authorization_customer(p_permission,o.customer_id));
$$;

DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['material','opportunity_material','opportunity_actual_confirmation'] LOOP
  EXECUTE format('ALTER TABLE crm.%I ENABLE ROW LEVEL SECURITY',name);
  EXECUTE format('ALTER TABLE crm.%I FORCE ROW LEVEL SECURITY',name);
  EXECUTE format('CREATE POLICY active_tenant_guard ON crm.%I AS RESTRICTIVE FOR ALL USING
   (workspace_id=common.current_workspace_id() AND security.workspace_is_active(common.current_workspace_id()))
   WITH CHECK(workspace_id=common.current_workspace_id() AND security.workspace_is_active(common.current_workspace_id()))',name);
  EXECUTE format('CREATE TRIGGER finance_audit AFTER INSERT OR UPDATE OR DELETE ON crm.%I
   FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row()',name);
 END LOOP;
END $$;
CREATE POLICY material_read ON crm.material FOR SELECT USING(security.authorization_has('material.read') OR security.authorization_has('material.manage'));
CREATE POLICY material_insert ON crm.material FOR INSERT WITH CHECK(security.management_actor()
 AND security.authorization_has('access.console') AND security.authorization_has('material.manage'));
CREATE POLICY material_update ON crm.material FOR UPDATE USING(security.management_actor()
 AND security.authorization_has('access.console') AND security.authorization_has('material.manage'))
 WITH CHECK(security.management_actor() AND security.authorization_has('access.console') AND security.authorization_has('material.manage'));
CREATE POLICY material_line_read ON crm.opportunity_material FOR SELECT USING(
 security.authorization_opportunity(security.authorization_read_feature('opportunity'),opportunity_id)
 OR security.authorization_opportunity(security.authorization_read_feature('actual'),opportunity_id));
CREATE POLICY material_line_insert ON crm.opportunity_material FOR INSERT WITH CHECK(
 security.authorization_opportunity('opportunity.update',opportunity_id) OR EXISTS(
 SELECT 1 FROM crm.opportunity o WHERE o.id=opportunity_material.opportunity_id AND o.workspace_id=opportunity_material.workspace_id
 AND o.created_by_user_ref_id=common.current_user_ref_id() AND o.created_at>=transaction_timestamp()
 AND security.authorization_opportunity('opportunity.create',o.id)));
CREATE POLICY material_line_update ON crm.opportunity_material FOR UPDATE
 USING(security.authorization_opportunity('opportunity.update',opportunity_id))
 WITH CHECK(security.authorization_opportunity('opportunity.update',opportunity_id));
CREATE POLICY actual_confirmation_read ON crm.opportunity_actual_confirmation FOR SELECT USING(
 security.authorization_opportunity(security.authorization_read_feature('actual'),opportunity_id) AND EXISTS(SELECT 1 FROM crm.opportunity o
 WHERE o.id=opportunity_actual_confirmation.opportunity_id AND o.workspace_id=opportunity_actual_confirmation.workspace_id
 AND security.authorization_customer(security.authorization_read_feature('actual'),o.customer_id)));
CREATE POLICY actual_confirmation_insert ON crm.opportunity_actual_confirmation FOR INSERT
 WITH CHECK(security.finance_management_scope(opportunity_id,'actual.create'));
CREATE POLICY actual_confirmation_update ON crm.opportunity_actual_confirmation FOR UPDATE
 USING(security.finance_management_scope(opportunity_id,'actual.create'))
 WITH CHECK(security.finance_management_scope(opportunity_id,'actual.create'));

-- Restrictive policies apply even to old permissive grants or custom sales
-- permissions. They do not rewrite old facts or their existing source metadata.
-- API takes this workspace lock BEFORE opportunity row locks. Trigger guards
-- take the same lock for direct SQL paths. This deliberately serializes finance
-- mutations in one tenant, avoiding parent/child source-switch write skew.
CREATE FUNCTION security.finance_write_lock() RETURNS void LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog AS $$
BEGIN
 IF common.current_workspace_id() IS NULL THEN RAISE EXCEPTION 'FINANCE_WORKSPACE_REQUIRED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('finance:'||common.current_workspace_id()::text,212));
END $$;
-- Private trigger helper sees the complete tenant graph even when the caller
-- cannot read sibling evidence. It returns no business data and has no runtime
-- EXECUTE grant. Object permissions remain enforced by the invoking table RLS.
CREATE FUNCTION security.guard_finance_graph(p_workspace uuid,p_opportunity uuid,p_year integer,p_quarter integer,p_legacy boolean)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE upstream uuid;
BEGIN
 IF p_workspace IS DISTINCT FROM common.current_workspace_id() THEN
  RAISE EXCEPTION 'FINANCE_WORKSPACE_MISMATCH' USING ERRCODE='42501'; END IF;
 PERFORM security.finance_write_lock();
 SELECT upstream_opportunity_id INTO upstream FROM crm.opportunity
  WHERE id=p_opportunity AND workspace_id=p_workspace AND trade_relation='downstream';
 IF p_legacy AND EXISTS(SELECT 1 FROM crm.opportunity child
  JOIN crm.effective_actual e ON e.workspace_id=child.workspace_id AND e.opportunity_id=child.id
    AND e.kind='recognized' AND e.year=p_year AND e.quarter=p_quarter
  WHERE child.workspace_id=p_workspace AND child.upstream_opportunity_id=p_opportunity
   AND child.deleted_at IS NULL AND e.raw_amount IS NOT NULL) THEN
  RAISE EXCEPTION 'FINANCE_DOWNSTREAM_CONFIRMED: 请先显式调整下游本期间确收，不能恢复上游旧总额' USING ERRCODE='40001';
 END IF;
 -- Both operational and newly adopted/repaired legacy child facts require
 -- an explicitly classified parent. A pinned old snapshot survives raw voids.
 IF upstream IS NOT NULL AND EXISTS(SELECT 1 FROM crm.finance_period_resolution p
  WHERE p.workspace_id=p_workspace AND p.opportunity_id=upstream AND p.kind='recognized'
   AND p.year=p_year AND p.quarter=p_quarter AND COALESCE(p.decided_source,'')<>'operations'
   AND (p.entry_count>0 OR p.history_count>0 OR p.adopted_snapshot IS NOT NULL)) THEN
  RAISE EXCEPTION 'FINANCE_UPSTREAM_UNCLASSIFIED: 请先核对上游本期间自用与转售份额并切换运营来源' USING ERRCODE='40001';
 END IF;
END $$;
CREATE POLICY actual_operational_insert ON crm.customer_actual AS RESTRICTIVE FOR INSERT
 WITH CHECK(security.management_actor() AND security.authorization_has('access.console'));
CREATE POLICY actual_operational_update ON crm.customer_actual AS RESTRICTIVE FOR UPDATE
 USING(security.management_actor() AND security.authorization_has('access.console'))
 WITH CHECK(security.management_actor() AND security.authorization_has('access.console'));
CREATE FUNCTION crm.guard_legacy_actual_source() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM security.finance_write_lock();
 IF NEW.opportunity_id IS NOT NULL THEN
  IF NEW.voided_at IS NULL THEN
   IF NEW.kind='recognized' THEN PERFORM security.guard_finance_graph(NEW.workspace_id,NEW.opportunity_id,
    extract(year FROM NEW.occurred_on)::integer,extract(quarter FROM NEW.occurred_on)::integer,true); END IF;
   IF NEW.kind='collection' AND NEW.amount<>0 AND EXISTS(SELECT 1 FROM crm.opportunity o
    WHERE o.id=NEW.opportunity_id AND o.workspace_id=NEW.workspace_id AND o.trade_relation='downstream') THEN
    RAISE EXCEPTION '下游商机不能重复登记我方回款，请核对合同关系与收款归属' USING ERRCODE='23514'; END IF;
  END IF;
  PERFORM 1 FROM crm.opportunity WHERE id=NEW.opportunity_id AND workspace_id=NEW.workspace_id FOR NO KEY UPDATE;
  IF EXISTS(SELECT 1 FROM crm.opportunity_actual_basis b WHERE b.workspace_id=NEW.workspace_id
   AND b.opportunity_id=NEW.opportunity_id AND b.kind=NEW.kind AND b.year=extract(year FROM NEW.occurred_on)
   AND b.quarter=extract(quarter FROM NEW.occurred_on) AND b.source='operations') THEN
   RAISE EXCEPTION '请在运营期间确认中更正；此期间不能再写旧逐笔实绩' USING ERRCODE='40001';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER actual_source_guard BEFORE INSERT OR UPDATE ON crm.customer_actual
 FOR EACH ROW EXECUTE FUNCTION crm.guard_legacy_actual_source();

CREATE FUNCTION crm.guard_material() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.workspace_id,NEW.code,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.code,OLD.created_at)
   THEN RAISE EXCEPTION 'MATERIAL_IDENTITY_IMMUTABLE' USING ERRCODE='23514'; END IF;
  IF NEW.version_no<>OLD.version_no+1 THEN RAISE EXCEPTION 'MATERIAL_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 ELSIF NEW.version_no<>1 THEN RAISE EXCEPTION 'MATERIAL_INITIAL_VERSION' USING ERRCODE='23514'; END IF;
 IF NEW.updated_by_user_ref_id IS DISTINCT FROM common.current_user_ref_id() THEN
  RAISE EXCEPTION 'MATERIAL_ACTOR_MISMATCH' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM config.dictionary_item i JOIN config.dictionary d ON d.id=i.dictionary_id
  WHERE i.id=NEW.product_line_id AND d.workspace_id=NEW.workspace_id AND d.code='material_product_line'
   AND (NOT NEW.is_active OR (i.is_active AND d.status='active'))) THEN
  RAISE EXCEPTION '请选择本公司有效产品线' USING ERRCODE='23514'; END IF;
 NEW.updated_at:=clock_timestamp(); RETURN NEW;
END $$;
CREATE TRIGGER material_guard BEFORE INSERT OR UPDATE ON crm.material FOR EACH ROW EXECUTE FUNCTION crm.guard_material();

CREATE FUNCTION crm.finance_relation_has_actuals(p_opportunity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.opportunity_actual_confirmation a WHERE a.opportunity_id=p_opportunity AND a.status='confirmed')
 OR EXISTS(SELECT 1 FROM crm.opportunity_actual_basis b WHERE b.opportunity_id=p_opportunity AND b.source='operations' AND b.empty_confirmation)
 OR EXISTS(SELECT 1 FROM crm.customer_actual a WHERE a.opportunity_id=p_opportunity AND a.voided_at IS NULL
  AND NOT EXISTS(SELECT 1 FROM crm.opportunity_actual_basis b WHERE b.opportunity_id=a.opportunity_id
  AND b.kind=a.kind AND b.year=extract(year FROM a.occurred_on) AND b.quarter=extract(quarter FROM a.occurred_on) AND b.source='operations'))
 OR EXISTS(SELECT 1 FROM crm.opportunity_period_actual_snapshot h WHERE h.opportunity_id=p_opportunity
  AND NOT EXISTS(SELECT 1 FROM crm.opportunity_actual_basis b WHERE b.opportunity_id=h.opportunity_id
  AND b.kind=h.kind AND b.year=h.year AND b.quarter=h.quarter AND b.source='operations'));
$$;
CREATE FUNCTION crm.guard_finance_relation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM security.finance_write_lock();
 IF TG_OP='UPDATE' AND OLD.trade_relation IS NOT NULL
  AND (NEW.trade_relation,NEW.upstream_opportunity_id) IS DISTINCT FROM (OLD.trade_relation,OLD.upstream_opportunity_id)
  AND crm.finance_relation_has_actuals(OLD.id) THEN
  RAISE EXCEPTION 'FINANCE_RELATION_LOCKED: 请先由运营核对并撤回受影响期间的实际，再修改交易关系' USING ERRCODE='40001';
 END IF;
 IF NEW.trade_relation='downstream' AND NOT EXISTS(SELECT 1 FROM crm.opportunity parent
  WHERE parent.id=NEW.upstream_opportunity_id AND parent.workspace_id=NEW.workspace_id
   AND parent.customer_id<>NEW.customer_id AND parent.trade_relation='distributor' AND parent.deleted_at IS NULL
   AND security.authorization_opportunity('opportunity.read',parent.id)) THEN
  RAISE EXCEPTION '下游商机须关联本公司有效总代商机，并使用不同的终端客户' USING ERRCODE='23514';
 END IF;
 IF COALESCE(NEW.trade_relation,'')<>'distributor' AND EXISTS(SELECT 1 FROM crm.opportunity child
  WHERE child.upstream_opportunity_id=NEW.id AND child.deleted_at IS NULL) THEN
  RAISE EXCEPTION '仍有下游商机关联此总代交易' USING ERRCODE='40001';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER finance_relation_guard BEFORE INSERT OR UPDATE ON crm.opportunity FOR EACH ROW EXECUTE FUNCTION crm.guard_finance_relation();

CREATE FUNCTION crm.guard_opportunity_material() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE snapshot jsonb;
BEGIN
 PERFORM security.finance_write_lock();
 PERFORM 1 FROM crm.opportunity WHERE id=NEW.opportunity_id AND workspace_id=NEW.workspace_id FOR NO KEY UPDATE;
 IF NEW.updated_by_user_ref_id IS DISTINCT FROM common.current_user_ref_id() THEN
  RAISE EXCEPTION 'MATERIAL_ACTOR_MISMATCH' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.workspace_id,NEW.opportunity_id,NEW.material_id,NEW.created_at,NEW.catalog_snapshot)
   IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.opportunity_id,OLD.material_id,OLD.created_at,OLD.catalog_snapshot) THEN
   RAISE EXCEPTION 'MATERIAL_LINE_IDENTITY_IMMUTABLE' USING ERRCODE='23514'; END IF;
  IF NEW.version_no<>OLD.version_no+1 THEN RAISE EXCEPTION 'MATERIAL_LINE_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
  IF NEW.usage IS DISTINCT FROM OLD.usage AND crm.finance_relation_has_actuals(OLD.opportunity_id) THEN
   RAISE EXCEPTION 'FINANCE_RELATION_LOCKED: 请先核对已确认实际再修改用途' USING ERRCODE='40001'; END IF;
 ELSE
  IF NEW.version_no<>1 THEN RAISE EXCEPTION 'MATERIAL_LINE_INITIAL_VERSION' USING ERRCODE='23514'; END IF;
  SELECT to_jsonb(m)-ARRAY['workspace_id','reason','created_at','updated_at','updated_by_user_ref_id']
   ||jsonb_build_object('product_line_name',i.item_label,'catalog_price',m.catalog_price::text,'tax_rate',m.tax_rate::text) INTO snapshot
   FROM crm.material m JOIN config.dictionary_item i ON i.id=m.product_line_id
   WHERE m.id=NEW.material_id AND m.workspace_id=NEW.workspace_id AND m.is_active AND i.is_active;
  IF snapshot IS NULL THEN RAISE EXCEPTION '物料不存在、已停用或不在本公司' USING ERRCODE='23514'; END IF;
  NEW.catalog_snapshot:=snapshot;
 END IF;
 NEW.updated_at:=clock_timestamp(); RETURN NEW;
END $$;
CREATE TRIGGER opportunity_material_guard BEFORE INSERT OR UPDATE ON crm.opportunity_material
 FOR EACH ROW EXECUTE FUNCTION crm.guard_opportunity_material();

CREATE FUNCTION crm.guard_actual_confirmation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE parent crm.opportunity%ROWTYPE; line crm.opportunity_material%ROWTYPE;
BEGIN
 PERFORM security.finance_write_lock();
 SELECT * INTO parent FROM crm.opportunity WHERE id=NEW.opportunity_id AND workspace_id=NEW.workspace_id FOR NO KEY UPDATE;
 IF NOT FOUND OR NOT security.finance_management_scope(NEW.opportunity_id,'actual.create') THEN
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
CREATE TRIGGER actual_confirmation_guard BEFORE INSERT OR UPDATE ON crm.opportunity_actual_confirmation
 FOR EACH ROW EXECUTE FUNCTION crm.guard_actual_confirmation();

-- Current financial projection follows the calling feature's object scope.
DROP POLICY permission_read ON crm.opportunity_actual_basis;
CREATE POLICY permission_read ON crm.opportunity_actual_basis FOR SELECT USING(
 workspace_id=common.current_workspace_id()
 AND security.authorization_opportunity(security.authorization_read_feature('actual'),opportunity_id)
 AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=opportunity_actual_basis.opportunity_id
 AND o.workspace_id=opportunity_actual_basis.workspace_id
 AND security.authorization_customer(security.authorization_read_feature('actual'),o.customer_id)));

-- Keep legacy fingerprint bytes compatible with domain.opportunity_finance:
-- sorted object keys, no separator whitespace, normalized numeric strings and
-- canonical UTC timestamps. No old decision is backfilled with guessed amounts.
CREATE FUNCTION security.finance_json_canonical(p_value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE result text;
BEGIN
 CASE jsonb_typeof(p_value)
 WHEN 'object' THEN
  SELECT '{'||COALESCE(string_agg(to_jsonb(key)::text||':'||security.finance_json_canonical(value),',' ORDER BY key COLLATE "C"),'')||'}'
   INTO result FROM jsonb_each(p_value);
 WHEN 'array' THEN
  SELECT '['||COALESCE(string_agg(security.finance_json_canonical(value),',' ORDER BY ord),'')||']'
   INTO result FROM jsonb_array_elements(p_value) WITH ORDINALITY t(value,ord);
 ELSE result:=p_value::text;
 END CASE;
 RETURN result;
END $$;
CREATE FUNCTION security.finance_decimal_text(p_value numeric) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT CASE WHEN p_value=0 THEN '0' WHEN strpos(p_value::text,'.')>0 THEN rtrim(rtrim(p_value::text,'0'),'.') ELSE p_value::text END;
$$;
CREATE FUNCTION security.finance_timestamp_text(p_value timestamptz) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT to_char(p_value AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')
 ||CASE WHEN extract(microseconds FROM p_value)::bigint%1000000=0 THEN '' ELSE to_char(p_value AT TIME ZONE 'UTC','.US') END||'+00:00';
$$;
CREATE FUNCTION security.finance_reference_fingerprint(p_workspace uuid,p_opportunity uuid,p_year integer,p_quarter integer,p_kind text)
RETURNS text LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT encode(sha256(convert_to(security.finance_json_canonical(jsonb_build_object(
  'rule_version',1,'workspace_id',p_workspace,'opportunity_id',p_opportunity,'year',p_year,'quarter',p_quarter,'kind',p_kind,
  'entries',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',a.id,'opportunity_id',a.opportunity_id,'kind',a.kind,
   'amount',security.finance_decimal_text(a.amount),'occurred_on',a.occurred_on,'voided_at',security.finance_timestamp_text(a.voided_at),
   'void_reason',a.void_reason,'source_ref',a.source_ref,'created_at',security.finance_timestamp_text(a.created_at),
   'updated_at',security.finance_timestamp_text(COALESCE(a.voided_at,a.created_at)))
   ||CASE WHEN a.tax_basis<>'unknown' THEN jsonb_build_object('tax_basis',a.tax_basis) ELSE '{}'::jsonb END ORDER BY a.id)
   FROM crm.customer_actual a WHERE a.workspace_id=p_workspace AND a.opportunity_id=p_opportunity AND a.kind=p_kind
    AND extract(year FROM a.occurred_on)=p_year AND extract(quarter FROM a.occurred_on)=p_quarter),'[]'::jsonb),
  'history',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',h.id,'opportunity_id',h.opportunity_id,'year',h.year,
   'quarter',h.quarter,'kind',h.kind,'raw_amount',security.finance_decimal_text(h.raw_amount),'source_unit',h.source_unit,
   'tax_basis',h.tax_basis,'source_field',h.source_field,'source_record_id',h.source_record_id,'import_batch_id',h.import_batch_id) ORDER BY h.id)
   FROM crm.opportunity_period_actual_snapshot h WHERE h.workspace_id=p_workspace AND h.opportunity_id=p_opportunity
    AND h.year=p_year AND h.quarter=p_quarter AND h.kind=p_kind),'[]'::jsonb))), 'UTF8')),'hex');
$$;

CREATE VIEW crm.finance_period_resolution WITH (security_invoker=true) AS
 WITH buckets AS (
  SELECT workspace_id,opportunity_id,extract(year FROM occurred_on)::integer AS year,extract(quarter FROM occurred_on)::integer AS quarter,kind
   FROM crm.customer_actual WHERE opportunity_id IS NOT NULL
  UNION SELECT workspace_id,opportunity_id,year,quarter,kind FROM crm.opportunity_period_actual_snapshot
  UNION SELECT workspace_id,opportunity_id,year,quarter,kind FROM crm.opportunity_actual_basis
  UNION SELECT workspace_id,opportunity_id,year,quarter,kind FROM crm.opportunity_actual_confirmation
 ), evidence AS (
  SELECT p.*,o.customer_id,o.owner_user_ref_id,o.owner_team_id,o.trade_relation,o.upstream_opportunity_id,b.source AS decided_source,b.selected_history_id,
   b.id AS basis_id,b.adopted_snapshot,b.operational_snapshot,b.expected_material_line_ids,b.empty_confirmation,COALESCE(b.version_no,0) AS version_no,
   b.source_fingerprint AS adopted_reference_fingerprint,
   security.finance_reference_fingerprint(p.workspace_id,p.opportunity_id,p.year,p.quarter,p.kind) AS source_fingerprint,
   (SELECT count(*) FROM crm.customer_actual a WHERE a.workspace_id=p.workspace_id AND a.opportunity_id=p.opportunity_id
    AND a.kind=p.kind AND extract(year FROM a.occurred_on)=p.year AND extract(quarter FROM a.occurred_on)=p.quarter
    AND a.voided_at IS NULL) AS entry_count,
   (SELECT count(*) FROM crm.opportunity_period_actual_snapshot h WHERE h.workspace_id=p.workspace_id
    AND h.opportunity_id=p.opportunity_id AND h.kind=p.kind AND h.year=p.year AND h.quarter=p.quarter) AS history_count,
   (SELECT count(*) FROM crm.opportunity_actual_confirmation a WHERE a.workspace_id=p.workspace_id
    AND a.opportunity_id=p.opportunity_id AND a.kind=p.kind AND a.year=p.year AND a.quarter=p.quarter AND a.status='confirmed') AS confirmed_count
  FROM buckets p JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id AND o.deleted_at IS NULL
  LEFT JOIN crm.opportunity_actual_basis b ON b.workspace_id=p.workspace_id AND b.opportunity_id=p.opportunity_id
    AND b.year=p.year AND b.quarter=p.quarter AND b.kind=p.kind
 ), resolved AS (
  SELECT e.*,CASE
   WHEN decided_source='operations' THEN 'operations'
   WHEN adopted_snapshot IS NOT NULL THEN decided_source
   WHEN decided_source IS NOT NULL AND adopted_reference_fingerprint=source_fingerprint THEN decided_source
   WHEN decided_source IS NOT NULL THEN NULL
   WHEN entry_count>0 AND history_count=0 THEN 'entries'
   WHEN entry_count=0 AND history_count=1 THEN 'historical' END AS source,
  CASE
   WHEN decided_source='operations' THEN CASE WHEN empty_confirmation THEN 'confirmed' WHEN confirmed_count=0 THEN 'withdrawn'
     WHEN kind='recognized' AND confirmed_count<COALESCE(cardinality(expected_material_line_ids),0) THEN 'partial' ELSE 'confirmed' END
   WHEN adopted_snapshot IS NOT NULL THEN 'confirmed'
   WHEN decided_source IS NOT NULL AND adopted_reference_fingerprint<>source_fingerprint THEN 'stale_confirmation'
   WHEN decided_source IS NOT NULL THEN 'confirmed'
   WHEN (entry_count>0 AND history_count>0) OR history_count>1 THEN 'needs_confirmation'
   WHEN entry_count>0 OR history_count=1 THEN 'single_source' ELSE 'missing' END AS status
  FROM evidence e
 ) SELECT r.*,COALESCE(cardinality(expected_material_line_ids),0) AS expected_material_count,
  CASE WHEN source='operations' AND kind='recognized' THEN
   greatest(COALESCE(cardinality(expected_material_line_ids),0)-confirmed_count,0) ELSE 0 END AS missing_material_count,
  (adopted_reference_fingerprint IS DISTINCT FROM source_fingerprint AND
    (source='operations' OR adopted_snapshot IS NOT NULL)) AS reference_changed
 FROM resolved r;

-- Leaf rows are independent: collection appears exactly once per opportunity
-- period and is never joined across material lines or FDE participants. Empty /
-- conflicted periods retain a null placeholder so totals cannot become false 0.
CREATE VIEW crm.effective_actual WITH (security_invoker=true) AS
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

COMMENT ON VIEW crm.effective_actual IS 'One selected source per opportunity/period/kind; selected leaf facts only. Preserve null conflicts and quarter precision, never fabricate a payment day.';

-- Catalog entry points, dependent SQL reads and exact runtime grants follow.
CREATE FUNCTION security.finance_window_totals(p_opportunities uuid[],p_start date,p_end date,p_quarters integer[] DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 WITH facts AS MATERIALIZED (
  SELECT a.*,(CASE WHEN a.occurred_on IS NOT NULL THEN a.occurred_on BETWEEN p_start AND p_end
   ELSE a.period_start>=p_start AND a.period_end<=p_end END) AS included
  FROM crm.effective_actual a WHERE a.workspace_id=common.current_workspace_id()
   AND a.opportunity_id=ANY(p_opportunities) AND (p_quarters IS NULL OR a.quarter=ANY(p_quarters))
   AND ((a.occurred_on IS NOT NULL AND a.occurred_on BETWEEN p_start AND p_end
    AND a.occurred_on<=timezone('Asia/Shanghai',clock_timestamp())::date)
    OR (a.occurred_on IS NULL AND a.period_start<=p_end AND a.period_end>=p_start))
 ), periods AS (
  SELECT opportunity_id,year,quarter,kind,included,bool_and(complete) complete,sum(amount) subtotal,
   count(amount) valid_count,max(missing_material_count) missing_count,bool_or(tax_unresolved) tax_unresolved,
   bool_or(status IN ('needs_confirmation','stale_confirmation')) conflicted
  FROM facts GROUP BY opportunity_id,year,quarter,kind,included
 ), kinds AS (SELECT unnest(ARRAY['recognized','collection']) kind), totals AS (
  SELECT k.kind,jsonb_build_object(
   k.kind||'_amount',CASE WHEN bool_and(p.complete) FILTER(WHERE included) THEN (sum(p.subtotal) FILTER(WHERE included))::text END,
   k.kind||'_confirmed_subtotal',(sum(p.subtotal) FILTER(WHERE included))::text,
   k.kind||'_complete',COALESCE(bool_and(p.complete) FILTER(WHERE included),false),
   k.kind||'_count',COALESCE(sum(p.valid_count) FILTER(WHERE included),0),
   k.kind||'_period_count',count(*) FILTER(WHERE included),
   k.kind||'_incomplete_period_count',count(*) FILTER(WHERE included AND NOT p.complete),
   k.kind||'_missing_material_count',COALESCE(sum(p.missing_count) FILTER(WHERE included),0),
   k.kind||'_tax_unknown_period_count',count(*) FILTER(WHERE included AND p.tax_unresolved),
   k.kind||'_conflict_period_count',count(*) FILTER(WHERE included AND p.conflicted),
   'period_only_'||k.kind||'_amount',(sum(p.subtotal) FILTER(WHERE NOT included))::text) AS result
  FROM kinds k LEFT JOIN periods p ON p.kind=k.kind GROUP BY k.kind
 ) SELECT (SELECT result FROM totals WHERE kind='recognized')||(SELECT result FROM totals WHERE kind='collection')
  ||jsonb_build_object('unallocated_period_count',(SELECT count(*) FROM periods WHERE NOT included),
    'period_precision_warning',CASE WHEN EXISTS(SELECT 1 FROM periods WHERE NOT included) THEN 'not_allocated_to_days' END,
    'complete',COALESCE((SELECT bool_and(complete) FROM periods WHERE included),false)
      AND (SELECT count(DISTINCT kind)=2 FROM periods WHERE included),
    'conflict_period_count',(SELECT count(*) FROM periods WHERE included AND conflicted));
$$;
CREATE FUNCTION security.material_product_lines(p_all boolean DEFAULT false) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF NOT security.authorization_has('material.read') OR (p_all AND NOT security.authorization_has('material.manage')) THEN
  RAISE EXCEPTION 'MATERIAL_DIRECTORY_PERMISSION' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('version_no',d.version_no,'items',COALESCE((SELECT jsonb_agg(jsonb_build_object(
  'id',i.id,'code',i.item_code,'name',i.item_label,'is_active',i.is_active,'sort_order',i.sort_order) ORDER BY i.sort_order,i.id)
  FROM config.dictionary_item i WHERE i.dictionary_id=d.id AND (p_all OR i.is_active)),'[]'::jsonb)) INTO result
 FROM config.dictionary d WHERE d.workspace_id=common.current_workspace_id() AND d.code='material_product_line';
 RETURN COALESCE(result,'{"version_no":0,"items":[]}'::jsonb);
END $$;
CREATE FUNCTION security.save_material_product_lines(p_version integer,p_reason text,p_items jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE dictionary config.dictionary%ROWTYPE; item jsonb; item_id uuid; old_item config.dictionary_item%ROWTYPE;
BEGIN
 IF NOT security.workspace_is_active(common.current_workspace_id()) OR NOT security.management_actor()
  OR NOT security.authorization_has('access.console') OR NOT security.authorization_has('material.manage')
  OR NOT security.authorization_has('material.read') THEN
  RAISE EXCEPTION 'MATERIAL_MANAGEMENT_PERMISSION' USING ERRCODE='42501'; END IF;
 IF length(btrim(p_reason)) NOT BETWEEN 1 AND 1000 OR jsonb_typeof(p_items)<>'array' OR jsonb_array_length(p_items)>200 THEN
  RAISE EXCEPTION 'MATERIAL_PRODUCT_LINE_INPUT_INVALID' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('material_catalog:'||common.current_workspace_id()::text,212));
 SELECT * INTO dictionary FROM config.dictionary WHERE workspace_id=common.current_workspace_id()
  AND code='material_product_line' FOR UPDATE;
 IF COALESCE(dictionary.version_no,0)<>p_version THEN RAISE EXCEPTION 'PRODUCT_LINE_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 IF dictionary.id IS NULL THEN
  INSERT INTO config.dictionary(workspace_id,code,name,version_no,status,attributes)
   VALUES(common.current_workspace_id(),'material_product_line','物料产品线',1,'active',jsonb_build_object('reason',p_reason)) RETURNING * INTO dictionary;
 ELSE
  IF EXISTS(SELECT 1 FROM config.dictionary_item i WHERE i.dictionary_id=dictionary.id
    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_items) x WHERE x->>'id'=i.id::text)) THEN
   RAISE EXCEPTION '产品线不能删除，请保留旧条目并停用' USING ERRCODE='23514'; END IF;
  UPDATE config.dictionary SET version_no=version_no+1,updated_at=clock_timestamp(),attributes=attributes||jsonb_build_object('reason',p_reason)
   WHERE id=dictionary.id;
 END IF;
 IF (SELECT count(*)<>count(DISTINCT x->>'code') FROM jsonb_array_elements(p_items) x) THEN
  RAISE EXCEPTION 'PRODUCT_LINE_DUPLICATE_CODE' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(p_items) LOOP
  IF length(btrim(item->>'code')) NOT BETWEEN 1 AND 100 OR length(btrim(item->>'name')) NOT BETWEEN 1 AND 200 THEN
   RAISE EXCEPTION 'PRODUCT_LINE_NAME_REQUIRED' USING ERRCODE='23514'; END IF;
  item_id:=NULLIF(item->>'id','')::uuid;
  IF item_id IS NULL THEN
   INSERT INTO config.dictionary_item(dictionary_id,item_code,item_label,sort_order,is_active)
    VALUES(dictionary.id,item->>'code',item->>'name',(item->>'sort_order')::integer,(item->>'is_active')::boolean);
  ELSE
   SELECT * INTO old_item FROM config.dictionary_item WHERE id=item_id AND dictionary_id=dictionary.id;
   IF NOT FOUND OR old_item.item_code<>item->>'code' THEN
    RAISE EXCEPTION 'PRODUCT_LINE_IDENTITY_IMMUTABLE' USING ERRCODE='23514'; END IF;
   UPDATE config.dictionary_item SET item_label=item->>'name',sort_order=(item->>'sort_order')::integer,
    is_active=(item->>'is_active')::boolean,updated_at=clock_timestamp() WHERE id=item_id;
  END IF;
 END LOOP;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,action_code,object_type,object_id,after_snapshot)
  VALUES(common.current_workspace_id(),common.current_user_ref_id(),'material_product_lines.updated','dictionary',dictionary.id,
   jsonb_build_object('reason',p_reason,'version_before',p_version,'items',p_items));
 RETURN security.material_product_lines(true);
END $$;
CREATE OR REPLACE FUNCTION crm.guard_opportunity_actual_basis() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE parent crm.opportunity%ROWTYPE; facts jsonb;
BEGIN
 PERFORM security.finance_write_lock();
 SELECT * INTO parent FROM crm.opportunity WHERE id=NEW.opportunity_id AND workspace_id=NEW.workspace_id FOR NO KEY UPDATE;
 IF NOT FOUND OR NOT security.finance_management_scope(NEW.opportunity_id,'actual.create')
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

-- Check the final transaction state, allowing entries + period basis to be
-- changed atomically in either order without accepting orphan confirmed lines.
CREATE FUNCTION crm.guard_actual_final_state() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b crm.opportunity_actual_basis%ROWTYPE;
BEGIN
 SELECT * INTO b FROM crm.opportunity_actual_basis WHERE workspace_id=NEW.workspace_id AND opportunity_id=NEW.opportunity_id
  AND year=NEW.year AND quarter=NEW.quarter AND kind=NEW.kind;
 IF NOT FOUND THEN RAISE EXCEPTION 'ACTUAL_PERIOD_BASIS_REQUIRED' USING ERRCODE='23514'; END IF;
 IF b.source='operations' AND b.kind='recognized' AND EXISTS(
  SELECT 1 FROM crm.opportunity_actual_confirmation a WHERE a.workspace_id=b.workspace_id AND a.opportunity_id=b.opportunity_id
   AND a.year=b.year AND a.quarter=b.quarter AND a.kind=b.kind AND a.status='confirmed'
   AND NOT(a.material_line_id=ANY(b.expected_material_line_ids))) THEN
  RAISE EXCEPTION 'ACTUAL_CONFIRMED_LINE_OUTSIDE_EXPECTED_SET' USING ERRCODE='23514'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER actual_final_state AFTER INSERT OR UPDATE ON crm.opportunity_actual_confirmation
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION crm.guard_actual_final_state();
CREATE CONSTRAINT TRIGGER basis_final_state AFTER INSERT OR UPDATE ON crm.opportunity_actual_basis
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION crm.guard_actual_final_state();

CREATE FUNCTION crm.guard_historical_finance_graph() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM security.finance_write_lock();
 IF NOT EXISTS(SELECT 1 FROM crm.opportunity_actual_basis b
  WHERE b.workspace_id=NEW.workspace_id AND b.opportunity_id=NEW.opportunity_id AND b.kind=NEW.kind
   AND b.year=NEW.year AND b.quarter=NEW.quarter AND b.source='operations') THEN
  IF NEW.kind='recognized' THEN
   PERFORM security.guard_finance_graph(NEW.workspace_id,NEW.opportunity_id,NEW.year,NEW.quarter,true);
  ELSIF NEW.kind='collection' AND NEW.raw_amount IS DISTINCT FROM 0 AND EXISTS(
   SELECT 1 FROM crm.opportunity o WHERE o.id=NEW.opportunity_id AND o.workspace_id=NEW.workspace_id
    AND o.trade_relation='downstream') THEN
   RAISE EXCEPTION '下游旧回款须保留为参考证据，不能自动采用非零公司回款' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER historical_finance_graph BEFORE INSERT OR UPDATE ON crm.opportunity_period_actual_snapshot
 FOR EACH ROW EXECUTE FUNCTION crm.guard_historical_finance_graph();

-- FINANCE V212 FDE CURRENT READS
CREATE OR REPLACE FUNCTION security.fde_peer_rankings(p_start date, p_end date, p_quarters integer[] DEFAULT NULL::integer[])
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE answer jsonb;
BEGIN
 IF NOT security.authorization_has('dashboard.ranking') THEN RAISE insufficient_privilege; END IF;
 IF p_start IS NULL OR p_end IS NULL OR p_start>p_end OR
   (p_quarters IS NOT NULL AND EXISTS(SELECT 1 FROM unnest(p_quarters) q WHERE q NOT BETWEEN 1 AND 4))
 THEN RAISE EXCEPTION 'INVALID_FDE_RANKING_RANGE' USING ERRCODE='22023'; END IF;
 WITH members AS MATERIALIZED (
   SELECT DISTINCT ON(u.id) u.id,u.display_name AS name,t.name AS team_name,'fde'::text AS role
   FROM platform.user_ref u JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
   JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
   WHERE u.workspace_id=common.current_workspace_id() AND b.role_code='fde' AND tm.membership_role='fde'
     AND security.fde_user_is_active(u.id,'fde',tm.team_id)
     AND security.authorization_subject('dashboard.ranking','person',u.id,NULL)
     AND (b.team_id IS NULL OR b.team_id=tm.team_id)
     AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   ORDER BY u.id,tm.is_primary DESC,tm.valid_from DESC
 ), visits AS (
   SELECT v.recorder_user_ref_id AS user_id,count(*) AS followup_count,
     count(DISTINCT v.opportunity_id) AS opportunity_count
   FROM activity.visit v JOIN members m ON m.id=v.recorder_user_ref_id
   WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND v.status='archived'
     AND security.authorization_allows('dashboard.ranking',v.workspace_id,v.recorder_user_ref_id,ARRAY[v.recording_team_id_snapshot],false)
     AND v.recording_role_code_snapshot='fde' AND v.created_by_user_ref_id=v.recorder_user_ref_id
     AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
     AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',v.interaction_at))::int=ANY(p_quarters))
   GROUP BY v.recorder_user_ref_id
 ), relations AS MATERIALIZED (
   SELECT DISTINCT p.user_ref_id AS user_id,o.id AS opportunity_id
   FROM crm.opportunity_participant p JOIN members m ON m.id=p.user_ref_id
   JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id AND o.deleted_at IS NULL
   WHERE p.workspace_id=common.current_workspace_id() AND p.participant_role='fde' AND security.authorization_opportunity_direct('dashboard.ranking',o.id)
     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 ), project_counts AS (SELECT user_id,count(*) AS opportunity_count FROM relations GROUP BY user_id),
 ledger AS (
   SELECT r.user_id,security.finance_window_totals(array_agg(DISTINCT r.opportunity_id),p_start,p_end,p_quarters) AS actuals FROM relations r GROUP BY r.user_id
 ), demos AS (
   SELECT d.created_by AS user_id,count(*) AS demo_scene_count
   FROM crm.opportunity_demo_scenes d JOIN members m ON m.id=d.created_by
   WHERE d.workspace_id=common.current_workspace_id() AND d.deleted_at IS NULL
     AND timezone('Asia/Shanghai',d.created_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',d.created_at))::int=ANY(p_quarters))
   GROUP BY d.created_by
 ), facts AS (
   SELECT m.id::text AS user_id,m.name,m.team_name,m.role,COALESCE(v.followup_count,0) AS followup_count,
     l.actuals->'recognized_amount' AS recognized_amount,l.actuals->'recognized_confirmed_subtotal' AS recognized_confirmed_subtotal,COALESCE(l.actuals->'recognized_complete','false'::jsonb) AS recognized_complete,COALESCE(l.actuals->'recognized_count','0'::jsonb) AS recognized_count,COALESCE(l.actuals->'recognized_period_count','0'::jsonb) AS recognized_period_count,COALESCE(l.actuals->'recognized_incomplete_period_count','0'::jsonb) AS recognized_incomplete_period_count,COALESCE(l.actuals->'recognized_missing_material_count','0'::jsonb) AS recognized_missing_material_count,COALESCE(l.actuals->'recognized_tax_unknown_period_count','0'::jsonb) AS recognized_tax_unknown_period_count,COALESCE(l.actuals->'recognized_conflict_period_count','0'::jsonb) AS recognized_conflict_period_count,l.actuals->'collection_amount' AS collection_amount,l.actuals->'collection_confirmed_subtotal' AS collection_confirmed_subtotal,COALESCE(l.actuals->'collection_complete','false'::jsonb) AS collection_complete,COALESCE(l.actuals->'collection_count','0'::jsonb) AS collection_count,COALESCE(l.actuals->'collection_period_count','0'::jsonb) AS collection_period_count,COALESCE(l.actuals->'collection_incomplete_period_count','0'::jsonb) AS collection_incomplete_period_count,COALESCE(l.actuals->'collection_missing_material_count','0'::jsonb) AS collection_missing_material_count,COALESCE(l.actuals->'collection_tax_unknown_period_count','0'::jsonb) AS collection_tax_unknown_period_count,COALESCE(l.actuals->'collection_conflict_period_count','0'::jsonb) AS collection_conflict_period_count,COALESCE(l.actuals->'unallocated_period_count','0'::jsonb) AS unallocated_period_count,l.actuals->'period_only_recognized_amount' AS period_only_recognized_amount,l.actuals->'period_only_collection_amount' AS period_only_collection_amount,l.actuals->'period_precision_warning' AS period_precision_warning,COALESCE(d.demo_scene_count,0) AS demo_scene_count,
     COALESCE(v.opportunity_count,0) AS opportunity_count,COALESCE(p.opportunity_count,0) AS current_opportunity_count
   FROM members m LEFT JOIN visits v ON v.user_id=m.id LEFT JOIN ledger l ON l.user_id=m.id
   LEFT JOIN demos d ON d.user_id=m.id LEFT JOIN project_counts p ON p.user_id=m.id
 ) SELECT jsonb_build_object('data_source','database','scope','all_fde','complete',true,
   'start',p_start,'end',p_end,'total',count(*),'items',COALESCE(jsonb_agg(to_jsonb(f) ORDER BY f.name,f.user_id),'[]'::jsonb),
   'amount_basis','参与项目已确认确收；同项目可展示在多名协助人的个人行，不可累加为公司业绩') INTO answer FROM facts f;
 RETURN answer;
END $function$;
CREATE OR REPLACE FUNCTION security.fde_dashboard_rankings(p_start date, p_end date, p_quarters integer[] DEFAULT NULL::integer[], p_member uuid DEFAULT NULL::uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE answer jsonb; subject_role text;
BEGIN
 IF NOT security.authorization_has('dashboard.ranking') THEN RAISE insufficient_privilege; END IF;
 IF p_start IS NULL OR p_end IS NULL OR p_start>p_end OR
   (p_quarters IS NOT NULL AND EXISTS(SELECT 1 FROM unnest(p_quarters) q WHERE q NOT BETWEEN 1 AND 4))
 THEN RAISE EXCEPTION 'INVALID_FDE_RANKING_RANGE' USING ERRCODE='22023'; END IF;

 IF p_member IS NULL AND NOT EXISTS(SELECT 1 FROM platform.team t WHERE t.workspace_id=common.current_workspace_id() AND security.authorization_subject('profile.fde_read','team',NULL,t.id)) THEN RAISE insufficient_privilege; END IF;
 IF p_member IS NOT NULL AND NOT security.authorization_subject('profile.fde_read','person',p_member,NULL)
   AND NOT EXISTS(SELECT 1 FROM security.fde_recorded_visit_history() h WHERE h.user_ref_id=p_member)
 THEN RAISE insufficient_privilege; END IF;
 SELECT b.role_code INTO subject_role FROM platform.role_binding b
 WHERE b.workspace_id=common.current_workspace_id() AND b.user_ref_id=p_member
   AND b.role_code IN ('fde','fde_lead') AND security.fde_user_is_active(p_member,b.role_code)
   AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
 ORDER BY CASE b.role_code WHEN 'fde_lead' THEN 0 ELSE 1 END,b.id LIMIT 1;
 WITH members AS MATERIALIZED (
   SELECT DISTINCT ON(u.id) u.id,u.display_name AS name,t.id AS team_id,t.name AS team_name,b.role_code AS role
   FROM platform.user_ref u JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
   JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
   WHERE u.workspace_id=common.current_workspace_id() AND b.role_code IN ('fde','fde_lead') AND tm.membership_role=b.role_code
     AND security.fde_user_is_active(u.id,b.role_code,tm.team_id)
     AND security.authorization_subject('dashboard.ranking','person',u.id,NULL)
     AND (b.team_id IS NULL OR b.team_id=tm.team_id)
     AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   ORDER BY u.id,CASE b.role_code WHEN 'fde_lead' THEN 0 ELSE 1 END,tm.is_primary DESC,tm.valid_from DESC,tm.id
 ), recorded_visits AS MATERIALIZED (
   SELECT v.recorder_user_ref_id,v.recording_team_id_snapshot,v.opportunity_id
   FROM activity.visit v
   WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND v.status='archived'
     AND security.authorization_allows('dashboard.ranking',v.workspace_id,v.recorder_user_ref_id,ARRAY[v.recording_team_id_snapshot],false)
     AND v.recording_role_code_snapshot IN ('fde','fde_lead') AND v.created_by_user_ref_id=v.recorder_user_ref_id
     AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
     AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',v.interaction_at))::int=ANY(p_quarters))
 ), visits AS (
   SELECT v.recorder_user_ref_id AS user_id,count(*) AS followup_count,
     count(DISTINCT v.opportunity_id) AS opportunity_count
   FROM recorded_visits v JOIN members m ON m.id=v.recorder_user_ref_id
   GROUP BY v.recorder_user_ref_id
 ), team_visits AS (
   -- Historical team ownership is immutable, independent of the author's current membership/status.
   SELECT recording_team_id_snapshot AS team_id,count(*) AS followup_count,
     count(DISTINCT opportunity_id) AS opportunity_count FROM recorded_visits
   WHERE recording_team_id_snapshot IS NOT NULL GROUP BY recording_team_id_snapshot
 ), relations AS MATERIALIZED (
   SELECT DISTINCT p.user_ref_id AS user_id,m.team_id,o.id AS opportunity_id
   FROM crm.opportunity_participant p JOIN members m ON m.id=p.user_ref_id
   JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id AND o.deleted_at IS NULL
   WHERE p.workspace_id=common.current_workspace_id() AND p.participant_role='fde' AND security.authorization_opportunity_direct('dashboard.ranking',o.id)
     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 ), project_counts AS (SELECT user_id,count(*) AS opportunity_count FROM relations GROUP BY user_id),
 ledger AS (
   SELECT r.user_id,security.finance_window_totals(array_agg(DISTINCT r.opportunity_id),p_start,p_end,p_quarters) AS actuals FROM relations r GROUP BY r.user_id
 ), demos AS (
   SELECT d.created_by AS user_id,count(*) AS demo_scene_count
   FROM crm.opportunity_demo_scenes d JOIN members m ON m.id=d.created_by
   WHERE d.workspace_id=common.current_workspace_id() AND d.deleted_at IS NULL
     AND timezone('Asia/Shanghai',d.created_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',d.created_at))::int=ANY(p_quarters))
   GROUP BY d.created_by
 ), facts AS (
   SELECT m.id::text AS user_id,m.team_id::text AS team_id,m.name,m.team_name,m.role,COALESCE(v.followup_count,0) AS followup_count,
     l.actuals->'recognized_amount' AS recognized_amount,l.actuals->'recognized_confirmed_subtotal' AS recognized_confirmed_subtotal,COALESCE(l.actuals->'recognized_complete','false'::jsonb) AS recognized_complete,COALESCE(l.actuals->'recognized_count','0'::jsonb) AS recognized_count,COALESCE(l.actuals->'recognized_period_count','0'::jsonb) AS recognized_period_count,COALESCE(l.actuals->'recognized_incomplete_period_count','0'::jsonb) AS recognized_incomplete_period_count,COALESCE(l.actuals->'recognized_missing_material_count','0'::jsonb) AS recognized_missing_material_count,COALESCE(l.actuals->'recognized_tax_unknown_period_count','0'::jsonb) AS recognized_tax_unknown_period_count,COALESCE(l.actuals->'recognized_conflict_period_count','0'::jsonb) AS recognized_conflict_period_count,l.actuals->'collection_amount' AS collection_amount,l.actuals->'collection_confirmed_subtotal' AS collection_confirmed_subtotal,COALESCE(l.actuals->'collection_complete','false'::jsonb) AS collection_complete,COALESCE(l.actuals->'collection_count','0'::jsonb) AS collection_count,COALESCE(l.actuals->'collection_period_count','0'::jsonb) AS collection_period_count,COALESCE(l.actuals->'collection_incomplete_period_count','0'::jsonb) AS collection_incomplete_period_count,COALESCE(l.actuals->'collection_missing_material_count','0'::jsonb) AS collection_missing_material_count,COALESCE(l.actuals->'collection_tax_unknown_period_count','0'::jsonb) AS collection_tax_unknown_period_count,COALESCE(l.actuals->'collection_conflict_period_count','0'::jsonb) AS collection_conflict_period_count,COALESCE(l.actuals->'unallocated_period_count','0'::jsonb) AS unallocated_period_count,l.actuals->'period_only_recognized_amount' AS period_only_recognized_amount,l.actuals->'period_only_collection_amount' AS period_only_collection_amount,l.actuals->'period_precision_warning' AS period_precision_warning,COALESCE(d.demo_scene_count,0) AS demo_scene_count,
     COALESCE(v.opportunity_count,0) AS opportunity_count,COALESCE(p.opportunity_count,0) AS current_opportunity_count
   FROM members m LEFT JOIN visits v ON v.user_id=m.id LEFT JOIN ledger l ON l.user_id=m.id
   LEFT JOIN demos d ON d.user_id=m.id LEFT JOIN project_counts p ON p.user_id=m.id
 ), team_members AS (
   SELECT DISTINCT tm.team_id,m.id AS user_id FROM platform.team_membership tm JOIN members m ON m.id=tm.user_ref_id
   WHERE tm.workspace_id=common.current_workspace_id() AND tm.membership_role IN ('fde','fde_lead')
     AND security.fde_user_is_active(m.id,NULL,tm.team_id)
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 ), team_relations AS (
   SELECT DISTINCT tm.team_id,r.opportunity_id FROM team_members tm JOIN relations r ON r.user_id=tm.user_id
 ), team_ledger AS (
   SELECT r.team_id,security.finance_window_totals(array_agg(DISTINCT r.opportunity_id),p_start,p_end,p_quarters) AS actuals FROM team_relations r GROUP BY r.team_id
 ), ranked_teams AS (
   SELECT team_id FROM team_members UNION SELECT team_id FROM team_visits
 ), teams AS (
   SELECT t.id::text AS user_id,t.name,t.name AS team_name,'fde_team'::text AS role,
     COALESCE(max(v.followup_count),0) AS followup_count,COALESCE(sum(f.demo_scene_count),0) AS demo_scene_count,
     (jsonb_agg(l.actuals)->0)->'recognized_amount' AS recognized_amount,(jsonb_agg(l.actuals)->0)->'recognized_confirmed_subtotal' AS recognized_confirmed_subtotal,COALESCE((jsonb_agg(l.actuals)->0)->'recognized_complete','false'::jsonb) AS recognized_complete,COALESCE((jsonb_agg(l.actuals)->0)->'recognized_count','0'::jsonb) AS recognized_count,COALESCE((jsonb_agg(l.actuals)->0)->'recognized_period_count','0'::jsonb) AS recognized_period_count,COALESCE((jsonb_agg(l.actuals)->0)->'recognized_incomplete_period_count','0'::jsonb) AS recognized_incomplete_period_count,COALESCE((jsonb_agg(l.actuals)->0)->'recognized_missing_material_count','0'::jsonb) AS recognized_missing_material_count,COALESCE((jsonb_agg(l.actuals)->0)->'recognized_tax_unknown_period_count','0'::jsonb) AS recognized_tax_unknown_period_count,COALESCE((jsonb_agg(l.actuals)->0)->'recognized_conflict_period_count','0'::jsonb) AS recognized_conflict_period_count,(jsonb_agg(l.actuals)->0)->'collection_amount' AS collection_amount,(jsonb_agg(l.actuals)->0)->'collection_confirmed_subtotal' AS collection_confirmed_subtotal,COALESCE((jsonb_agg(l.actuals)->0)->'collection_complete','false'::jsonb) AS collection_complete,COALESCE((jsonb_agg(l.actuals)->0)->'collection_count','0'::jsonb) AS collection_count,COALESCE((jsonb_agg(l.actuals)->0)->'collection_period_count','0'::jsonb) AS collection_period_count,COALESCE((jsonb_agg(l.actuals)->0)->'collection_incomplete_period_count','0'::jsonb) AS collection_incomplete_period_count,COALESCE((jsonb_agg(l.actuals)->0)->'collection_missing_material_count','0'::jsonb) AS collection_missing_material_count,COALESCE((jsonb_agg(l.actuals)->0)->'collection_tax_unknown_period_count','0'::jsonb) AS collection_tax_unknown_period_count,COALESCE((jsonb_agg(l.actuals)->0)->'collection_conflict_period_count','0'::jsonb) AS collection_conflict_period_count,COALESCE((jsonb_agg(l.actuals)->0)->'unallocated_period_count','0'::jsonb) AS unallocated_period_count,(jsonb_agg(l.actuals)->0)->'period_only_recognized_amount' AS period_only_recognized_amount,(jsonb_agg(l.actuals)->0)->'period_only_collection_amount' AS period_only_collection_amount,(jsonb_agg(l.actuals)->0)->'period_precision_warning' AS period_precision_warning,
     COALESCE(max(v.opportunity_count),0) AS opportunity_count
   FROM platform.team t JOIN ranked_teams rt ON rt.team_id=t.id
   LEFT JOIN team_members tm ON tm.team_id=t.id
   LEFT JOIN facts f ON f.user_id=tm.user_id::text LEFT JOIN team_ledger l ON l.team_id=t.id
   LEFT JOIN team_visits v ON v.team_id=t.id
   WHERE t.workspace_id=common.current_workspace_id() AND security.authorization_subject('dashboard.ranking','team',NULL,t.id) AND t.deleted_at IS NULL AND t.status='active'
   GROUP BY t.id,t.name
 ), selected_teams AS (
   SELECT DISTINCT tm.team_id::text AS id FROM platform.team_membership tm
   WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=common.current_user_ref_id()
     AND tm.membership_role='fde_lead' AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',tm.team_id)
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 ), result AS (
   SELECT to_jsonb(f) AS item FROM facts f WHERE p_member IS NOT NULL AND f.role=subject_role
   UNION ALL SELECT to_jsonb(t) FROM teams t WHERE p_member IS NULL
 ) SELECT jsonb_build_object('contract_version',3,'data_source','database',
   'scope',CASE WHEN p_member IS NULL THEN 'company_fde_teams' WHEN subject_role='fde_lead' THEN 'all_fde_leads' ELSE 'all_fde' END,
   'complete',true,'start',p_start,'end',p_end,'total',count(*),
   'selection',jsonb_build_object('personal',p_member IS NOT NULL,'member_id',p_member,'cohort_role',subject_role,
     'team_ids',COALESCE((SELECT jsonb_agg(id) FROM selected_teams),'[]'::jsonb)),
   'items',COALESCE(jsonb_agg(item ORDER BY item->>'name',item->>'user_id'),'[]'::jsonb),
   'amount_basis','个人按参与项目计确收；团队按参与项目并集计确收，同一项目在同一团队仅计一次') INTO answer FROM result;
 RETURN answer;
END $function$;


-- Exact portable export catalog: current projection and separately named evidence.
CREATE OR REPLACE FUNCTION security._closed_export_catalog() RETURNS jsonb
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
      "expected_close_quarter",
      "trade_relation",
      "upstream_opportunity_id"
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
  },
  "crm.opportunity_material": {
    "section": "opportunities",
    "columns": [
      "id",
      "workspace_id",
      "opportunity_id",
      "material_id",
      "line_no",
      "quantity",
      "unit_price",
      "line_amount",
      "usage",
      "expected_delivery_on",
      "expected_start_on",
      "expected_oa_on",
      "service_months",
      "expected_account_count",
      "expected_consumption_amount",
      "expected_consumption_year",
      "expected_consumption_quarter",
      "note",
      "catalog_snapshot",
      "is_active",
      "version_no",
      "updated_by_user_ref_id",
      "created_at",
      "updated_at"
    ],
    "predicate": "opportunity_id IN (SELECT id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_opportunity(''opportunity.read'',opportunity_id)"
  },
  "crm.effective_actual": {
    "section": "actuals",
    "columns": [
      "id",
      "workspace_id",
      "customer_id",
      "opportunity_id",
      "material_line_id",
      "year",
      "quarter",
      "kind",
      "amount",
      "raw_amount",
      "tax_basis",
      "tax_unresolved",
      "source",
      "status",
      "occurred_on",
      "period_start",
      "period_end",
      "period_precision",
      "owner_user_ref_id",
      "owner_team_id",
      "expected_material_count",
      "confirmed_material_count",
      "missing_material_count",
      "complete",
      "reference_changed",
      "source_fingerprint",
      "version_no"
    ],
    "predicate": "true",
    "guard": "CASE WHEN opportunity_id IS NULL THEN security.authorization_customer(''actual.read'',customer_id) ELSE security.authorization_opportunity(''actual.read'',opportunity_id) END"
  },
  "crm.opportunity_actual_confirmation": {
    "section": "actuals",
    "columns": [
      "id",
      "workspace_id",
      "opportunity_id",
      "material_line_id",
      "year",
      "quarter",
      "kind",
      "amount",
      "currency",
      "tax_basis",
      "status",
      "source_ref",
      "note",
      "reason",
      "material_snapshot",
      "confirmed_by_user_ref_id",
      "confirmed_at",
      "created_at",
      "version_no"
    ],
    "predicate": "opportunity_id IN (SELECT id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_opportunity(''actual.read'',opportunity_id)"
  },
  "crm.opportunity_actual_basis": {
    "section": "actuals",
    "columns": [
      "id",
      "workspace_id",
      "opportunity_id",
      "year",
      "quarter",
      "kind",
      "source",
      "selected_history_id",
      "source_fingerprint",
      "reason",
      "decided_by_user_ref_id",
      "created_at",
      "updated_at",
      "version_no",
      "adopted_snapshot",
      "operational_snapshot",
      "expected_material_line_ids",
      "empty_confirmation"
    ],
    "predicate": "opportunity_id IN (SELECT id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_opportunity(''actual.read'',opportunity_id)"
  },
  "crm.opportunity_period_actual_snapshot": {
    "section": "actuals",
    "columns": [
      "id",
      "workspace_id",
      "opportunity_id",
      "year",
      "quarter",
      "kind",
      "raw_amount",
      "source_unit",
      "tax_basis",
      "source_field",
      "source_record_id",
      "import_batch_id"
    ],
    "predicate": "opportunity_id IN (SELECT id FROM crm.opportunity WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL)",
    "guard": "security.authorization_opportunity(''actual.read'',opportunity_id)"
  },
  "crm.material": {
    "section": "materials",
    "columns": [
      "id",
      "workspace_id",
      "code",
      "name",
      "product_line_id",
      "unit",
      "catalog_price",
      "currency",
      "tax_rate",
      "recognition_rule",
      "rule_parameters",
      "is_active",
      "version_no",
      "reason",
      "updated_by_user_ref_id",
      "created_at",
      "updated_at"
    ],
    "predicate": "true",
    "guard": "security.authorization_has(''material.read'')"
  }
}'::jsonb; $catalog$;
CREATE OR REPLACE FUNCTION security._closed_export_sections() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $sections$ SELECT '{
  "organization": [
    "organization.read"
  ],
  "customers": [
    "customer.read",
    "customer.export"
  ],
  "opportunities": [
    "opportunity.read",
    "opportunity.export"
  ],
  "visits": [
    "visit.read"
  ],
  "tasks": [
    "task.read"
  ],
  "actuals": [
    "actual.read"
  ],
  "materials": [
    "material.read"
  ],
  "targets": [
    "target.read"
  ],
  "partners": [
    "partner.read"
  ]
}'::jsonb; $sections$;
-- Keep existing source entry point OID, owner and ACL. Its established tenant,
-- connection and feature checks remain the first operation in the wrapper.
DO $$ DECLARE original_owner name; BEGIN
 SELECT pg_get_userbyid(proowner) INTO original_owner FROM pg_proc WHERE oid='ops.feishu_source(uuid,text,uuid)'::regprocedure;
 EXECUTE replace(pg_get_functiondef('ops.feishu_source(uuid,text,uuid)'::regprocedure),
  'CREATE OR REPLACE FUNCTION ops.feishu_source(','CREATE OR REPLACE FUNCTION ops.feishu_source_before_operational_finance(');
 EXECUTE format('ALTER FUNCTION ops.feishu_source_before_operational_finance(uuid,text,uuid) OWNER TO %I',original_owner);
END $$;
CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; extra jsonb; ws uuid;
BEGIN
 result:=ops.feishu_source_before_operational_finance(p_connection,p_kind,p_id);
 IF p_kind<>'opportunity' OR result->>'excluded'='true' OR result->>'deleted'='true' THEN RETURN result; END IF;
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 SELECT jsonb_build_object('version_no',o.version_no,
  'finance_effective',COALESCE((SELECT jsonb_agg(to_jsonb(e)||jsonb_build_object('amount',e.amount::text,'raw_amount',e.raw_amount::text)
   ORDER BY e.year,e.quarter,e.kind,e.id) FROM crm.effective_actual e WHERE e.workspace_id=ws AND e.opportunity_id=p_id),'[]'::jsonb),
  'finance_decisions',COALESCE((SELECT jsonb_agg(to_jsonb(b) ORDER BY b.id) FROM crm.opportunity_actual_basis b
   WHERE b.workspace_id=ws AND b.opportunity_id=p_id),'[]'::jsonb),
  'finance_confirmations',COALESCE((SELECT jsonb_agg(to_jsonb(a)||jsonb_build_object('amount',a.amount::text) ORDER BY a.id)
   FROM crm.opportunity_actual_confirmation a WHERE a.workspace_id=ws AND a.opportunity_id=p_id),'[]'::jsonb)) INTO extra
 FROM crm.opportunity o WHERE o.workspace_id=ws AND o.id=p_id;
 RETURN result||COALESCE(extra,'{}'::jsonb);
END $$;
DO $$ DECLARE definition text; old_text text:='(''customer_actual'',''opportunity_period_actual_snapshot'',''opportunity_actual_basis'')'; BEGIN
 definition:=pg_get_functiondef('ops.capture_opportunity_finance_dependency()'::regprocedure);
 IF strpos(definition,old_text)=0 THEN RAISE EXCEPTION 'FINANCE_DEPENDENCY_BASELINE_CHANGED'; END IF;
 EXECUTE replace(definition,old_text,'(''customer_actual'',''opportunity_period_actual_snapshot'',''opportunity_actual_basis'',''opportunity_actual_confirmation'')');
END $$;
CREATE TRIGGER feishu_finance_parent AFTER INSERT OR UPDATE OR DELETE ON crm.opportunity_actual_confirmation
 FOR EACH ROW EXECUTE FUNCTION ops.capture_opportunity_finance_dependency();

-- New objects get explicit least-privilege grants. Never call the historical
-- broad reconciler or grant a trigger/private graph validator to runtime roles.
DO $$ DECLARE obj text; signature text; grantee record; runtime record; trusted name;
 read_functions text:='security.finance_json_canonical(jsonb),security.finance_decimal_text(numeric),security.finance_timestamp_text(timestamp with time zone),security.finance_reference_fingerprint(uuid,uuid,integer,integer,text),security.finance_window_totals(uuid[],date,date,integer[])';
 api_functions text:='security.finance_management_scope(uuid,text),security.finance_write_lock(),security.material_product_lines(boolean),security.save_material_product_lines(integer,text,jsonb)';
BEGIN
 FOREACH signature IN ARRAY ARRAY[
  'security.finance_management_scope(uuid,text)','security.finance_write_lock()',
  'security.guard_finance_graph(uuid,uuid,integer,integer,boolean)',
  'security.finance_json_canonical(jsonb)','security.finance_decimal_text(numeric)','security.finance_timestamp_text(timestamp with time zone)',
  'security.finance_reference_fingerprint(uuid,uuid,integer,integer,text)','security.finance_window_totals(uuid[],date,date,integer[])',
  'security.material_product_lines(boolean)','security.save_material_product_lines(integer,text,jsonb)',
  'crm.guard_legacy_actual_source()','crm.guard_material()','crm.finance_relation_has_actuals(uuid)',
  'crm.guard_finance_relation()','crm.guard_opportunity_material()','crm.guard_actual_confirmation()',
  'crm.guard_actual_final_state()','crm.guard_historical_finance_graph()',
  'ops.feishu_source_before_operational_finance(uuid,text,uuid)'] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',signature);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a JOIN pg_roles r ON r.oid=a.grantee
   WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',signature,grantee.rolname);
  END LOOP;
 END LOOP;
 FOREACH obj IN ARRAY ARRAY['crm.material','crm.opportunity_material','crm.opportunity_actual_confirmation',
  'crm.finance_period_resolution','crm.effective_actual'] LOOP
  EXECUTE format('REVOKE ALL ON TABLE %s FROM PUBLIC',obj);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_class c
   CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a JOIN pg_roles r ON r.oid=a.grantee
   WHERE c.oid=obj::regclass AND a.grantee<>c.relowner LOOP
   EXECUTE format('REVOKE ALL ON TABLE %s FROM %I',obj,grantee.rolname);
  END LOOP;
 END LOOP;
 FOR runtime IN SELECT r.oid,r.rolname FROM pg_roles r WHERE NOT r.rolsuper AND NOT r.rolbypassrls
  AND left(r.rolname,3)<>'pg_' AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
  AND has_schema_privilege(r.oid,'security','USAGE') AND has_table_privilege(r.oid,'crm.customer_actual','SELECT')
  AND has_function_privilege(r.oid,'security.authorization_read_feature(text)','EXECUTE') LOOP
  EXECUTE format('GRANT SELECT ON crm.material,crm.opportunity_material,crm.opportunity_actual_confirmation,crm.finance_period_resolution,crm.effective_actual TO %I',runtime.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',read_functions,runtime.rolname);
  IF has_function_privilege(runtime.oid,'security.create_auth_session(uuid,uuid,uuid,text,timestamp with time zone,jsonb)','EXECUTE')
   AND has_table_privilege(runtime.oid,'crm.customer_actual','INSERT') THEN
   EXECUTE format('GRANT INSERT,UPDATE ON crm.material,crm.opportunity_material,crm.opportunity_actual_confirmation TO %I',runtime.rolname);
   EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',api_functions,runtime.rolname);
  END IF;
 END LOOP;
 -- Existing protected definer owners may be distinct from the migration owner.
 FOR trusted IN SELECT DISTINCT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=ANY(ARRAY[
   'ops.feishu_source(uuid,text,uuid)'::regprocedure,'security.closed_export_rows(text,text,integer,integer)'::regprocedure,
   'security.fde_peer_rankings(date,date,integer[])'::regprocedure,'security.fde_dashboard_rankings(date,date,integer[],uuid)'::regprocedure]) LOOP
  EXECUTE format('GRANT SELECT ON crm.material,crm.opportunity_material,crm.opportunity_actual_confirmation,crm.finance_period_resolution,crm.effective_actual TO %I',trusted);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',read_functions,trusted);
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V360','Material snapshots, independent operational actuals and one effective finance projection');
COMMIT;
