BEGIN;

-- Only a material manager may attach an external document to the exact active
-- retrievable SalesBuddy version. Runtime API calls this definer function; the
-- provider table itself remains SELECT-only for the API role.
CREATE FUNCTION security.knowledge_bind_provider(
 p_material uuid,p_version uuid,p_dataset text,p_document text
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE m knowledge.material; v knowledge.version; observed timestamptz;
BEGIN
 IF p_dataset IS NULL OR length(p_dataset) NOT BETWEEN 1 AND 300
    OR p_document IS NULL OR length(p_document) NOT BETWEEN 1 AND 300 THEN
  RAISE EXCEPTION 'KNOWLEDGE_PROVIDER_BINDING_INVALID' USING ERRCODE='22023';
 END IF;
 SELECT * INTO m FROM knowledge.material
  WHERE workspace_id=common.current_workspace_id() AND id=p_material AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND OR NOT security.knowledge_can(p_material,'read') THEN
  RAISE EXCEPTION 'KNOWLEDGE_MATERIAL_NOT_FOUND' USING ERRCODE='P0002';
 END IF;
 IF NOT security.knowledge_can(p_material,'manage') THEN
  RAISE EXCEPTION 'KNOWLEDGE_ACCESS_DENIED' USING ERRCODE='42501';
 END IF;
 SELECT * INTO v FROM knowledge.version
  WHERE workspace_id=m.workspace_id AND material_id=p_material AND id=p_version
    AND state='retrievable' AND m.active_version_id=p_version;
 IF NOT FOUND THEN
  RAISE EXCEPTION 'KNOWLEDGE_VERSION_UNAVAILABLE' USING ERRCODE='P0002';
 END IF;
 observed:=clock_timestamp();
 BEGIN
  INSERT INTO knowledge.provider_binding(
   workspace_id,material_id,version_id,provider_dataset_id,provider_document_id,state,observed_at
  ) VALUES(m.workspace_id,p_material,p_version,p_dataset,p_document,'active',observed)
  ON CONFLICT(workspace_id,material_id,version_id) DO UPDATE SET
   provider_dataset_id=EXCLUDED.provider_dataset_id,
   provider_document_id=EXCLUDED.provider_document_id,
   state='active',observed_at=EXCLUDED.observed_at;
 EXCEPTION WHEN unique_violation THEN
  RAISE EXCEPTION 'KNOWLEDGE_PROVIDER_BINDING_CONFLICT' USING ERRCODE='40001';
 END;
 INSERT INTO knowledge.audit(workspace_id,actor_id,material_id,operation,detail)
 VALUES(m.workspace_id,common.current_user_ref_id(),p_material,'provider.binding',jsonb_build_object(
  'version_id',p_version,'provider_dataset_id',p_dataset,'provider_document_id',p_document,'observed_at',observed));
 RETURN jsonb_build_object('material_id',p_material,'version_id',p_version,
  'provider_dataset_id',p_dataset,'provider_document_id',p_document,'state','active','observed_at',observed);
END $$;
REVOKE ALL ON FUNCTION security.knowledge_bind_provider(uuid,uuid,text,text)
 FROM PUBLIC,salegent_feishu_worker;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V413','Knowledge provider binding management for active versions');
COMMIT;
