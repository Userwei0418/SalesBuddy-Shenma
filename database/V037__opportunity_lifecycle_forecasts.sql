BEGIN;
SELECT pg_advisory_xact_lock(2026091004);
ALTER TABLE crm.opportunity ADD COLUMN IF NOT EXISTS partner_name text;
ALTER TABLE crm.opportunity ADD COLUMN IF NOT EXISTS product_line text;
ALTER TABLE crm.opportunity ADD COLUMN IF NOT EXISTS closed_at timestamptz;
ALTER TABLE activity.visit ADD COLUMN IF NOT EXISTS opportunity_mutation_hash text;
-- Historical close timestamps are unknown: never infer them from updated_at.
CREATE TABLE IF NOT EXISTS crm.opportunity_forecast (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 opportunity_id uuid NOT NULL REFERENCES crm.opportunity(id),
 year integer NOT NULL CHECK(year BETWEEN 2000 AND 2100),
 quarter integer NOT NULL CHECK(quarter BETWEEN 1 AND 4),
 recognized_amount numeric(18,2) CHECK(recognized_amount >= 0),
 collection_amount numeric(18,2) CHECK(collection_amount >= 0),
 updated_by_user_ref_id uuid REFERENCES platform.user_ref(id),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(opportunity_id,year,quarter)
);
COMMENT ON TABLE crm.opportunity_forecast IS '人工确认的季度预测含税确收/回款，非实际收入；NULL未填写，0明确为零';
CREATE TABLE IF NOT EXISTS crm.business_change (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 customer_id uuid NOT NULL REFERENCES crm.customer(id),
 opportunity_id uuid REFERENCES crm.opportunity(id),
 actor_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 version_no integer NOT NULL,
 changes jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE crm.opportunity_forecast ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.opportunity_forecast FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS forecast_customer_scope ON crm.opportunity_forecast;
CREATE POLICY forecast_customer_scope ON crm.opportunity_forecast
 USING(workspace_id=common.current_workspace_id() AND EXISTS(
 SELECT 1 FROM crm.opportunity o WHERE o.id=opportunity_id AND o.workspace_id=crm.opportunity_forecast.workspace_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND EXISTS(
 SELECT 1 FROM crm.opportunity o WHERE o.id=opportunity_id AND o.workspace_id=crm.opportunity_forecast.workspace_id));
ALTER TABLE crm.business_change ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.business_change FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS business_change_scope ON crm.business_change;
CREATE POLICY business_change_scope ON crm.business_change
 USING(workspace_id=common.current_workspace_id() AND security.has_customer_access(customer_id))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.has_customer_access(customer_id));
-- A review may enrich only the originating actor's authorized change event.
CREATE OR REPLACE FUNCTION workflow.complete_business_review(p_event uuid,p_review jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_workspace uuid;
BEGIN
 SELECT workspace_id INTO v_workspace FROM crm.business_change
 WHERE id=p_event AND workspace_id=common.current_workspace_id()
 AND actor_user_ref_id=common.current_user_ref_id() AND security.has_customer_access(customer_id);
 IF v_workspace IS NULL THEN RETURN; END IF;
 UPDATE workflow.notification SET payload=payload || jsonb_build_object('ai_review',p_review)
 WHERE workspace_id=v_workspace AND template_code='business_changed' AND payload->>'event_id'=p_event::text;
END $$;
REVOKE ALL ON FUNCTION workflow.complete_business_review(uuid,jsonb) FROM PUBLIC;
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='crm' AND table_name='opportunity' AND privilege_type='INSERT'
 LOOP
 EXECUTE format('GRANT SELECT,INSERT,UPDATE ON crm.opportunity_forecast TO %I',r.grantee);
 EXECUTE format('GRANT SELECT,INSERT ON crm.business_change TO %I',r.grantee);
 EXECUTE format('GRANT EXECUTE ON FUNCTION workflow.complete_business_review(uuid,jsonb) TO %I',r.grantee);
 END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description)
VALUES('V037','商机人工生命周期、固定季度预测及变化记录') ON CONFLICT DO NOTHING;
COMMIT;
