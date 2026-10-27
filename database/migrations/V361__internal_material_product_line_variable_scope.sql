-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V213__material_product_line_variable_scope.sql.
-- Original SHA-256: 90d9e57005cf36b1d377e239037a0088b26c73598689d228c37e32a55f1f7412. See shenma_whole_121_manifest.json.
BEGIN;

-- FINANCE-SQL-R03: preserve applied V212; disambiguate the local record from
-- the target table name in UPDATE config.dictionary ... WHERE id=... .
-- CREATE OR REPLACE preserves the existing function identity, owner and ACL.
CREATE OR REPLACE FUNCTION security.save_material_product_lines(p_version integer,p_reason text,p_items jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_dictionary config.dictionary%ROWTYPE; item jsonb; item_id uuid; old_item config.dictionary_item%ROWTYPE;
BEGIN
 IF NOT security.workspace_is_active(common.current_workspace_id()) OR NOT security.management_actor()
  OR NOT security.authorization_has('access.console') OR NOT security.authorization_has('material.manage')
  OR NOT security.authorization_has('material.read') THEN
  RAISE EXCEPTION 'MATERIAL_MANAGEMENT_PERMISSION' USING ERRCODE='42501'; END IF;
 IF length(btrim(p_reason)) NOT BETWEEN 1 AND 1000 OR jsonb_typeof(p_items)<>'array' OR jsonb_array_length(p_items)>200 THEN
  RAISE EXCEPTION 'MATERIAL_PRODUCT_LINE_INPUT_INVALID' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('material_catalog:'||common.current_workspace_id()::text,212));
 SELECT * INTO v_dictionary FROM config.dictionary WHERE workspace_id=common.current_workspace_id()
  AND code='material_product_line' FOR UPDATE;
 IF COALESCE(v_dictionary.version_no,0)<>p_version THEN RAISE EXCEPTION 'PRODUCT_LINE_VERSION_CONFLICT' USING ERRCODE='40001'; END IF;
 IF v_dictionary.id IS NULL THEN
  INSERT INTO config.dictionary(workspace_id,code,name,version_no,status,attributes)
   VALUES(common.current_workspace_id(),'material_product_line','物料产品线',1,'active',jsonb_build_object('reason',p_reason)) RETURNING * INTO v_dictionary;
 ELSE
  IF EXISTS(SELECT 1 FROM config.dictionary_item i WHERE i.dictionary_id=v_dictionary.id
    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_items) x WHERE x->>'id'=i.id::text)) THEN
   RAISE EXCEPTION '产品线不能删除，请保留旧条目并停用' USING ERRCODE='23514'; END IF;
  UPDATE config.dictionary SET version_no=version_no+1,updated_at=clock_timestamp(),attributes=attributes||jsonb_build_object('reason',p_reason)
   WHERE id=v_dictionary.id;
 END IF;
 IF (SELECT count(*)<>count(DISTINCT x->>'code') FROM jsonb_array_elements(p_items) x) THEN
  RAISE EXCEPTION 'PRODUCT_LINE_DUPLICATE_CODE' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(p_items) LOOP
  IF length(btrim(item->>'code')) NOT BETWEEN 1 AND 100 OR length(btrim(item->>'name')) NOT BETWEEN 1 AND 200 THEN
   RAISE EXCEPTION 'PRODUCT_LINE_NAME_REQUIRED' USING ERRCODE='23514'; END IF;
  item_id:=NULLIF(item->>'id','')::uuid;
  IF item_id IS NULL THEN
   INSERT INTO config.dictionary_item(dictionary_id,item_code,item_label,sort_order,is_active)
    VALUES(v_dictionary.id,item->>'code',item->>'name',(item->>'sort_order')::integer,(item->>'is_active')::boolean);
  ELSE
   SELECT * INTO old_item FROM config.dictionary_item WHERE id=item_id AND dictionary_id=v_dictionary.id;
   IF NOT FOUND OR old_item.item_code<>item->>'code' THEN
    RAISE EXCEPTION 'PRODUCT_LINE_IDENTITY_IMMUTABLE' USING ERRCODE='23514'; END IF;
   UPDATE config.dictionary_item SET item_label=item->>'name',sort_order=(item->>'sort_order')::integer,
    is_active=(item->>'is_active')::boolean,updated_at=clock_timestamp() WHERE id=item_id;
  END IF;
 END LOOP;
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,action_code,object_type,object_id,after_snapshot)
  VALUES(common.current_workspace_id(),common.current_user_ref_id(),'material_product_lines.updated','dictionary',v_dictionary.id,
   jsonb_build_object('reason',p_reason,'version_before',p_version,'items',p_items));
 RETURN security.material_product_lines(true);
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V361','Disambiguate material product line function record from target table');
COMMIT;
