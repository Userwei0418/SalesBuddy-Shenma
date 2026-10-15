-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V172__tenant_configuration_status.sql.
-- Original SHA-256: 4550ed2bcd887b01c24b9a19cc91b23122e0206e36f1129a2570d3c829d0cacf. See shenma_whole_121_manifest.json.
BEGIN;
-- Platform diagnostics are an explicit metadata projection. Do not return
-- provider addresses, model names, mappings, credentials or business content.
CREATE FUNCTION control.configuration_status(p_session text,p_workspace uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w platform.workspace%ROWTYPE; result jsonb;
BEGIN
 PERFORM control.require_actor(p_session);
 SELECT * INTO w FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL;
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 SELECT jsonb_build_object(
  'workspace_id',w.id,'version_no',w.version_no,'sampled_at',statement_timestamp(),
  'identity_checks',control.readiness(w.id),
  'modules',(SELECT jsonb_agg(jsonb_build_object('code',m.code,
    'enabled',COALESCE(t.enabled,false),
    'effective',COALESCE(t.enabled AND (t.expires_at IS NULL OR t.expires_at>statement_timestamp()),false),
    'expires_at',t.expires_at) ORDER BY m.code)
   FROM control.module_catalog m LEFT JOIN control.tenant_module t ON t.workspace_id=w.id AND t.module_code=m.code
   WHERE m.code IN ('ai','feishu','visit')),
  'model_connections',(SELECT jsonb_agg(jsonb_build_object(
    'purpose',p.purpose,'mode',CASE WHEN r.config_snapshot->>'mode' IN ('custom','inherit','disabled')
       THEN r.config_snapshot->>'mode' WHEN r.version_no IS NULL THEN 'inherit' ELSE 'invalid' END,
    'version_no',r.version_no,
    'source',CASE WHEN r.config_snapshot->>'mode'='custom' THEN 'tenant_purpose'
       WHEN r.config_snapshot->>'mode'='disabled' THEN 'disabled'
       WHEN r.version_no IS NOT NULL AND r.config_snapshot->>'mode' IS DISTINCT FROM 'inherit' THEN 'invalid'
       WHEN l.enabled IS TRUE THEN 'tenant_legacy' ELSE 'instance_default' END,
    'legacy_version_no',CASE WHEN l.enabled IS TRUE THEN l.revision_no END,
    'fields_present',COALESCE(CASE WHEN r.config_snapshot->>'mode'='custom' THEN
       length(btrim(r.config_snapshot->>'provider_name'))>0
       AND length(btrim(r.config_snapshot->>'endpoint_url'))>0
       AND length(btrim(r.config_snapshot->>'model'))>0
       AND r.config_snapshot->>'protocol'=p.protocol
       WHEN (r.version_no IS NULL OR r.config_snapshot->>'mode'='inherit') AND l.enabled IS TRUE THEN
        length(btrim(l.provider_base_url))>0 AND length(btrim(CASE p.purpose
         WHEN 'text' THEN l.llm_model WHEN 'asr' THEN l.asr_model ELSE l.tts_model END))>0
       ELSE false END,false),
    'credential_saved',COALESCE(CASE WHEN r.config_snapshot->>'mode'='custom' THEN
       octet_length(r.api_key_ciphertext)>0 AND length(btrim(r.encryption_key_id))>0
       WHEN (r.version_no IS NULL OR r.config_snapshot->>'mode'='inherit') AND l.enabled IS TRUE THEN
        octet_length(l.api_key_ciphertext)>0 AND length(btrim(l.encryption_key_id))>0
       ELSE false END,false)) ORDER BY p.ordinal)
   FROM (VALUES ('text','chat_completions',1),('asr','audio_transcriptions',2),('tts','senseaudio_tts',3)) p(purpose,protocol,ordinal)
   LEFT JOIN config.model_api_current c ON c.workspace_id=w.id AND c.purpose=p.purpose
   LEFT JOIN config.model_api_release r ON r.workspace_id=c.workspace_id AND r.purpose=c.purpose AND r.version_no=c.version_no
   LEFT JOIN config.agent_runtime_config l ON l.workspace_id=w.id),
  'feishu',COALESCE((SELECT jsonb_build_object(
    'configured',true,'revision',f.revision,'enabled',f.enabled,
    'validated',COALESCE(f.validated_revision=f.revision,false),
    'validation_pending',f.validation_requested,
    'credential_saved',EXISTS(SELECT 1 FROM security.feishu_credential k
      WHERE k.workspace_id=w.id AND k.connection_id=f.id
        AND octet_length(k.ciphertext)>0 AND length(btrim(k.encryption_key_id))>0),
    'fields_present',COALESCE(length(btrim(f.settings->>'app_id'))>0
       AND length(btrim(f.settings->>'base_token'))>0
       AND length(btrim(f.settings->>'credential_ref'))>0,false),
    'direction_supported',COALESCE(f.settings->>'direction'='system_to_base',false),
    'enabled_mappings',(SELECT count(*) FROM jsonb_each(CASE WHEN jsonb_typeof(f.settings->'mappings')='object'
       THEN f.settings->'mappings' ELSE '{}'::jsonb END) m WHERE m.value->'enabled'='true'::jsonb)
   ) FROM config.feishu_connection f WHERE f.workspace_id=w.id),jsonb_build_object(
    'configured',false,'revision',NULL,'enabled',false,'validated',false,'validation_pending',false,
    'credential_saved',false,'fields_present',false,'direction_supported',false,'enabled_mappings',0))
 ) INTO result;
 PERFORM control.require_actor(p_session);
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION control.configuration_status(text,uuid) FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_configuration_status;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_before_configuration_status();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
 AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.configuration_status(text,uuid) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_configuration_status()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V320','Tenant configuration metadata without credentials or external requests');
COMMIT;
