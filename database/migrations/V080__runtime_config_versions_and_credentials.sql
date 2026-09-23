BEGIN;
-- Only metadata is migrated. No historical signing secret is read or guessed.
ALTER TABLE config.agent_runtime_config
 ADD COLUMN revision_no integer NOT NULL DEFAULT 0,
 ADD COLUMN cipher_format text,
 ADD COLUMN encryption_key_id text,
 ADD COLUMN encryption_revision integer NOT NULL DEFAULT 0,
 ADD COLUMN api_key_tail text;
ALTER TABLE config.agent_runtime_release
 ADD COLUMN operation text NOT NULL DEFAULT 'save',
 ADD COLUMN restored_from_version integer;
UPDATE config.agent_runtime_config SET cipher_format='pgp-v1',
 encryption_key_id='legacy-pgp',encryption_revision=1 WHERE api_key_ciphertext IS NOT NULL;
-- Old rollback changed current without a release. Publish that effective snapshot
-- once; a matching head is reused. History without current needs operator repair.
DO $$ DECLARE cfg record; head record; snapshot jsonb; next_version integer; BEGIN
 IF EXISTS(SELECT 1 FROM config.agent_runtime_release r WHERE NOT EXISTS
   (SELECT 1 FROM config.agent_runtime_config c WHERE c.workspace_id=r.workspace_id)) THEN
  RAISE EXCEPTION 'Runtime release history has no current configuration; restore current explicitly before V080';
 END IF;
 FOR cfg IN SELECT * FROM config.agent_runtime_config FOR UPDATE LOOP
  snapshot=jsonb_build_object('provider_base_url',cfg.provider_base_url,'llm_model',cfg.llm_model,
   'asr_model',cfg.asr_model,'tts_model',cfg.tts_model,'prompt_overrides',cfg.prompt_overrides,'enabled',cfg.enabled);
  SELECT version_no,config_snapshot INTO head FROM config.agent_runtime_release
   WHERE workspace_id=cfg.workspace_id ORDER BY version_no DESC LIMIT 1;
  IF head.version_no IS NULL OR head.config_snapshot IS DISTINCT FROM snapshot THEN
   next_version=COALESCE(head.version_no,0)+1;
   INSERT INTO config.agent_runtime_release(workspace_id,version_no,config_snapshot,created_by_user_ref_id,operation)
    VALUES(cfg.workspace_id,next_version,snapshot,cfg.updated_by_user_ref_id,'migration_baseline');
  ELSE next_version=head.version_no; END IF;
  UPDATE config.agent_runtime_config SET revision_no=next_version WHERE workspace_id=cfg.workspace_id;
 END LOOP;
END $$;
ALTER TABLE config.agent_runtime_config
 ADD CONSTRAINT runtime_revision_positive CHECK(revision_no>0),
 ADD CONSTRAINT runtime_encryption_metadata CHECK (
  (api_key_ciphertext IS NULL AND cipher_format IS NULL AND encryption_key_id IS NULL AND api_key_tail IS NULL AND encryption_revision=0)
  OR (api_key_ciphertext IS NOT NULL AND encryption_revision>0 AND cipher_format IS NOT NULL AND encryption_key_id IS NOT NULL AND
   ((cipher_format='pgp-v1' AND encryption_key_id='legacy-pgp') OR
    (cipher_format='aes256gcm-v1' AND encryption_key_id IS NOT NULL AND encryption_key_id<>'legacy-pgp'
     AND length(encryption_key_id) BETWEEN 1 AND 100 AND octet_length(api_key_ciphertext)>=29 AND api_key_tail IS NOT NULL AND length(api_key_tail)=4))));
ALTER TABLE config.agent_runtime_release ADD CONSTRAINT runtime_release_operation CHECK(
 operation IN ('save','rollback','migration_baseline') AND
 ((operation='rollback' AND restored_from_version IS NOT NULL AND restored_from_version>0 AND restored_from_version<version_no)
 OR (operation<>'rollback' AND restored_from_version IS NULL)));
ALTER TABLE config.agent_runtime_config ADD CONSTRAINT runtime_current_release
 FOREIGN KEY(workspace_id,revision_no) REFERENCES config.agent_runtime_release(workspace_id,version_no)
 DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE config.agent_runtime_release ADD CONSTRAINT runtime_restored_release
 FOREIGN KEY(workspace_id,restored_from_version) REFERENCES config.agent_runtime_release(workspace_id,version_no);
CREATE TRIGGER runtime_release_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
 ON config.agent_runtime_release FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
-- Existing permissive workspace policies remain for runtime reads. Restrictive
-- write policies cannot be bypassed by their OR composition with old policies.
CREATE POLICY runtime_config_admin_insert ON config.agent_runtime_config AS RESTRICTIVE FOR INSERT
 WITH CHECK(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator' AND security.has_active_role('administrator'));
CREATE POLICY runtime_config_admin_update ON config.agent_runtime_config AS RESTRICTIVE FOR UPDATE
 USING(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator' AND security.has_active_role('administrator'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator' AND security.has_active_role('administrator'));
CREATE POLICY runtime_config_admin_delete ON config.agent_runtime_config AS RESTRICTIVE FOR DELETE USING(false);
CREATE POLICY runtime_release_admin_insert ON config.agent_runtime_release AS RESTRICTIVE FOR INSERT
 WITH CHECK(workspace_id=common.current_workspace_id() AND common.current_role_code()='administrator' AND security.has_active_role('administrator'));
COMMENT ON COLUMN config.agent_runtime_config.revision_no IS '当前生效发布版本；保存和回滚均CAS发布新版本；纯换钥不改变';
COMMENT ON COLUMN config.agent_runtime_config.encryption_revision IS '只跟随凭据替换或重加密增长；不作为业务输入指纹';
COMMENT ON COLUMN config.agent_runtime_config.encryption_key_id IS '外部独立keyring的非秘密ID；legacy-pgp仅使用明确提供的历史解密密钥';
COMMIT;
