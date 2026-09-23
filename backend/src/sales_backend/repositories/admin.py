"""Runtime configuration persistence. No settings, HTTP models or cryptography."""
from __future__ import annotations


class AgentRuntimeConfigRepository:
    async def lock(self, connection, actor):
        if not connection.is_in_transaction():
            raise RuntimeError("Runtime configuration mutations require an explicit transaction")
        # Also serializes first creation (there may be no row to FOR UPDATE yet).
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                                 "runtime-config:" + actor.workspace_id)
        return await self.current(connection, actor, for_update=True)

    async def current(self, connection, actor, *, for_update=False, include_ciphertext=True):
        credential = "cfg.api_key_ciphertext" if include_ciphertext else "(cfg.api_key_ciphertext IS NOT NULL) AS has_api_key"
        row = await connection.fetchrow(
            f"""SELECT cfg.workspace_id,cfg.provider_base_url,cfg.llm_model,cfg.asr_model,cfg.tts_model,
                cfg.prompt_overrides,cfg.enabled,cfg.updated_at,cfg.revision_no,cfg.cipher_format,
                cfg.encryption_key_id,cfg.encryption_revision,cfg.api_key_tail,{credential},
                updater.display_name AS updated_by FROM config.agent_runtime_config cfg
                LEFT JOIN platform.user_ref updater ON updater.id=cfg.updated_by_user_ref_id
                WHERE cfg.workspace_id=$1::uuid""" + (" FOR UPDATE OF cfg" if for_update else ""), actor.workspace_id)
        return dict(row) if row else None

    async def releases(self, connection, actor, *, limit=50):
        rows = await connection.fetch(
            """SELECT r.version_no,r.config_snapshot,r.created_at,r.operation,r.restored_from_version,
               u.display_name AS created_by FROM config.agent_runtime_release r
               LEFT JOIN platform.user_ref u ON u.id=r.created_by_user_ref_id
               WHERE r.workspace_id=$1::uuid ORDER BY r.version_no DESC LIMIT $2::int""", actor.workspace_id, limit)
        return [dict(row) for row in rows]

    async def release(self, connection, actor, version):
        return await connection.fetchval("""SELECT config_snapshot FROM config.agent_runtime_release
          WHERE workspace_id=$1::uuid AND version_no=$2::int""", actor.workspace_id, version)

    async def publish(self, connection, actor, snapshot, *, revision, credential, encryption_revision,
                      operation, restored_from_version=None):
        await connection.execute(
            """INSERT INTO config.agent_runtime_config(workspace_id,provider_base_url,llm_model,asr_model,tts_model,
             prompt_overrides,enabled,updated_by_user_ref_id,revision_no,api_key_ciphertext,cipher_format,
             encryption_key_id,encryption_revision,api_key_tail)
             VALUES($1::uuid,$2,$3,$4,$5,$6::jsonb,$7,$8::uuid,$9,$10,$11,$12,$13,$14)
             ON CONFLICT(workspace_id) DO UPDATE SET provider_base_url=EXCLUDED.provider_base_url,
             llm_model=EXCLUDED.llm_model,asr_model=EXCLUDED.asr_model,tts_model=EXCLUDED.tts_model,
             prompt_overrides=EXCLUDED.prompt_overrides,enabled=EXCLUDED.enabled,
             updated_by_user_ref_id=EXCLUDED.updated_by_user_ref_id,updated_at=clock_timestamp(),
             revision_no=EXCLUDED.revision_no,api_key_ciphertext=EXCLUDED.api_key_ciphertext,
             cipher_format=EXCLUDED.cipher_format,encryption_key_id=EXCLUDED.encryption_key_id,
             encryption_revision=EXCLUDED.encryption_revision,api_key_tail=EXCLUDED.api_key_tail""",
            actor.workspace_id, snapshot["provider_base_url"], snapshot["llm_model"], snapshot["asr_model"],
            snapshot["tts_model"], snapshot["prompt_overrides"], snapshot["enabled"], actor.user_id, revision,
            credential.get("api_key_ciphertext"), credential.get("cipher_format"), credential.get("encryption_key_id"),
            encryption_revision, credential.get("api_key_tail"))
        await connection.execute("""INSERT INTO config.agent_runtime_release(workspace_id,version_no,config_snapshot,
          created_by_user_ref_id,operation,restored_from_version) VALUES($1::uuid,$2,$3::jsonb,$4::uuid,$5,$6)""",
          actor.workspace_id, revision, snapshot, actor.user_id, operation, restored_from_version)
        await self.audit(connection, actor, operation, {"version": revision,
            "restored_from_version": restored_from_version, "prompt_agent_codes": sorted(snapshot["prompt_overrides"])})

    async def replace_credential(self, connection, actor, credential, encryption_revision):
        # The caller owns the same lock as publish. No business timestamps change.
        await connection.execute("""UPDATE config.agent_runtime_config SET api_key_ciphertext=$2,
         cipher_format=$3,encryption_key_id=$4,api_key_tail=$5,encryption_revision=$6
         WHERE workspace_id=$1::uuid""", actor.workspace_id, credential["api_key_ciphertext"],
         credential["cipher_format"], credential["encryption_key_id"], credential["api_key_tail"], encryption_revision)
        await self.audit(connection, actor, "reencrypt", {"encryption_revision": encryption_revision,
                                                       "encryption_key_id": credential["encryption_key_id"]})

    async def audit(self, connection, actor, operation, metadata):
        await connection.execute("""INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,
          action_code,object_type,after_snapshot,result_code,sensitivity)
          VALUES($1::uuid,$2::uuid,$3,$4,'agent_runtime_config',$5::jsonb,'success','restricted')""",
          actor.workspace_id, actor.user_id, actor.role.value, "admin.agent_config." + operation, metadata)
