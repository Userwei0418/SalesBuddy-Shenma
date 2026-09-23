BEGIN;
SELECT pg_advisory_xact_lock(2026091005);
CREATE TABLE crm.customer_actual (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 customer_id uuid NOT NULL REFERENCES crm.customer(id),
 opportunity_id uuid REFERENCES crm.opportunity(id),
 kind text NOT NULL CHECK(kind IN ('recognized','collection')),
 amount numeric(18,2) NOT NULL CHECK(amount >= 0),
 occurred_on date NOT NULL,
 source_ref text NOT NULL CHECK(length(btrim(source_ref)) BETWEEN 1 AND 160),
 note text NOT NULL DEFAULT '',
 request_id uuid NOT NULL,
 confirmed_by_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 voided_at timestamptz,
 voided_by_user_ref_id uuid REFERENCES platform.user_ref(id),
 void_reason text,
 UNIQUE(workspace_id, request_id),
 UNIQUE(workspace_id, customer_id, kind, source_ref),
 CHECK((voided_at IS NULL AND voided_by_user_ref_id IS NULL AND void_reason IS NULL)
 OR (voided_at IS NOT NULL AND voided_by_user_ref_id IS NOT NULL AND length(btrim(void_reason)) > 0))
);
COMMENT ON TABLE crm.customer_actual IS '人工确认的确收/回款实绩，元；独立于ACV及季度预测。历史缺少商机关联可空，不按ACV分摊。作废保留原始记录。';
CREATE INDEX ON crm.customer_actual(workspace_id,customer_id,occurred_on DESC) WHERE voided_at IS NULL;
ALTER TABLE crm.customer_actual ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.customer_actual FORCE ROW LEVEL SECURITY;
CREATE POLICY actual_read ON crm.customer_actual FOR SELECT USING(
 workspace_id=common.current_workspace_id() AND security.has_customer_access(customer_id));
CREATE POLICY actual_insert ON crm.customer_actual FOR INSERT WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.has_customer_access(customer_id)
 AND common.current_role_code() IN ('manager','supervisor')
 AND confirmed_by_user_ref_id=common.current_user_ref_id()
 AND (opportunity_id IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity o
 WHERE o.id=opportunity_id AND o.customer_id=customer_actual.customer_id AND o.workspace_id=customer_actual.workspace_id)));
CREATE POLICY actual_update ON crm.customer_actual FOR UPDATE USING(
 workspace_id=common.current_workspace_id() AND security.has_customer_access(customer_id)
 AND common.current_role_code() IN ('manager','supervisor')) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.has_customer_access(customer_id)
 AND common.current_role_code() IN ('manager','supervisor'));
-- Financial facts are append-only. Corrections void the old fact and create a new confirmed fact.
CREATE FUNCTION crm.guard_actual_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (to_jsonb(NEW)-ARRAY['voided_at','voided_by_user_ref_id','void_reason']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['voided_at','voided_by_user_ref_id','void_reason'])
 OR OLD.voided_at IS NOT NULL OR NEW.voided_at IS NULL THEN
 RAISE EXCEPTION 'actual records are immutable; void then create a correction';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER actual_immutable BEFORE UPDATE ON crm.customer_actual
 FOR EACH ROW EXECUTE FUNCTION crm.guard_actual_update();
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='crm' AND table_name='opportunity' AND privilege_type='INSERT'
 LOOP EXECUTE format('GRANT SELECT,INSERT,UPDATE ON crm.customer_actual TO %I',r.grantee); END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V038','客户确收回款实绩、溯源与作废留痕');
COMMIT;
