-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V174__model_setup_probe_scheduling.sql.
-- Original SHA-256: ef03d73b51da2ff4d395cdd38a6dedad43da749ee79b5285d15e298638420b38. See shenma_whole_121_manifest.json.
BEGIN;
-- Company administrators can validate their own connection before business AI
-- opens. Setup probes retain config-test RBAC, shared-source policy and quotas.
CREATE OR REPLACE FUNCTION security.workspace_job_enabled(p_workspace uuid,p_type text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.workspace_module_enabled(p_workspace,CASE WHEN p_type='visit.import' THEN 'visit'
 WHEN p_type IN ('agent.run','sales_competency.review','battle_map.review',
 'customer.risk.review','business.advice','opportunity.change.review') THEN 'ai' ELSE 'core' END);
$$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V322','Permit bounded model setup probes before business AI activation');
COMMIT;
