"""CAS publishing, secret-free administration and resumable credential rotation."""
from __future__ import annotations

from sales_backend.repositories.admin import AgentRuntimeConfigRepository
from sales_backend.security.runtime_credentials import FORMAT, CredentialCipher, decrypt_credential
from sales_backend.services.idempotency import execute_mutation

AGENT_LABELS = {
    "chatbi": "经营问数",
    "visit_entry": "拜访结构化与质量审核",
    "opportunity_draft": "商机创建/更新判断",
    "today_tasks": "今日待办",
    "personal_risks": "个人风险",
    "operating_report": "即时总结",
    "customer_create": "客户建档",
    "management_task": "管理任务",
    "competency_review": "销售六维能力复盘",
    "customer_advice": "客户经营建议",
    "opportunity_advice": "商机经营建议",
    "visit_advice": "单次拜访建议",
    "battle_map_review": "客户作战地图重评",
}
SNAPSHOT_FIELDS = ("provider_base_url", "llm_model", "asr_model", "tts_model", "prompt_overrides", "enabled")
CREDENTIAL_FIELDS = ("api_key_ciphertext", "cipher_format", "encryption_key_id", "api_key_tail")


class ConfigVersionConflict(RuntimeError):
    def __init__(self):
        super().__init__("CONFIG_VERSION_CONFLICT")


class RuntimeConfigService:
    def __init__(self, settings, repository=None):
        self.settings = settings
        self.repository = repository or AgentRuntimeConfigRepository()

    async def get(self, connection, actor):
        row = await self.repository.current(connection, actor, include_ciphertext=False) or {}
        defaults = self.settings
        has_key = row.get("has_api_key", False)
        tail = row.get("api_key_tail")
        return {
            "revision_no": row.get("revision_no", 0),
            "provider_base_url": row.get("provider_base_url", defaults.senseaudio_base_url),
            "api_key_masked": (f"••••••••{tail}" if tail else "已配置（历史凭据）") if has_key
                else ("环境变量已配置" if defaults.senseaudio_api_key else "未配置"),
            "credential_source": "database" if has_key else "environment",
            "cipher_format": row.get("cipher_format"), "encryption_key_id": row.get("encryption_key_id"),
            "encryption_revision": row.get("encryption_revision", 0),
            "llm_model": row.get("llm_model", defaults.llm_model),
            "asr_model": row.get("asr_model", defaults.asr_model),
            "tts_model": row.get("tts_model", defaults.tts_model),
            "enabled": row.get("enabled", True), "updated_at": row.get("updated_at"),
            "updated_by": row.get("updated_by") or "环境变量",
            "agents": [{"code": code, "label": label, "prompt": (row.get("prompt_overrides") or {}).get(code, "")}
                       for code, label in AGENT_LABELS.items()],
        }

    @staticmethod
    def validate(snapshot):
        unknown = set(snapshot["prompt_overrides"]) - set(AGENT_LABELS)
        if unknown:
            raise ValueError("未知 Agent：" + ", ".join(sorted(unknown)))

    @staticmethod
    def check_version(row, expected_version):
        if (row["revision_no"] if row else 0) != expected_version:
            raise ConfigVersionConflict()

    async def save(self, connection, actor, data, *, key=None):
        snapshot = {name: data[name] for name in SNAPSHOT_FIELDS}
        self.validate(snapshot)

        async def execute():
            row = await self.repository.lock(connection, actor)
            self.check_version(row, data["expected_version"])
            credential = {name: (row or {}).get(name) for name in CREDENTIAL_FIELDS}
            encryption_revision = (row or {}).get("encryption_revision", 0)
            if data.get("api_key") is not None:
                cipher = CredentialCipher.from_file(self.settings.config_credential_keyring_file,
                                                    self.settings.config_credential_key_id)
                credential = cipher.encrypt(actor.workspace_id, data["api_key"])
                encryption_revision += 1
            await self.repository.publish(connection, actor, snapshot, revision=data["expected_version"] + 1,
                                          credential=credential, encryption_revision=encryption_revision, operation="save")
            return await self.get(connection, actor)
        # execute_mutation persists only request digest + masked response, never data/API key.
        return await execute_mutation(connection, actor, key, "admin.agent_config.save", data, execute)

    async def rollback(self, connection, actor, version, expected_version, *, key=None):
        async def execute():
            row = await self.repository.lock(connection, actor)
            self.check_version(row, expected_version)
            snapshot = await self.repository.release(connection, actor, version)
            if snapshot is None:
                raise LookupError("RELEASE_NOT_FOUND")
            snapshot = {name: snapshot[name] for name in SNAPSHOT_FIELDS}
            self.validate(snapshot)
            await self.repository.publish(connection, actor, snapshot, revision=expected_version + 1,
                credential={name: (row or {}).get(name) for name in CREDENTIAL_FIELDS},
                encryption_revision=(row or {}).get("encryption_revision", 0), operation="rollback",
                restored_from_version=version)
            return await self.get(connection, actor)
        return await execute_mutation(connection, actor, key, "admin.agent_config.rollback",
                                      {"version": version, "expected_version": expected_version}, execute)

    async def reencrypt(self, connection, actor):
        row = await self.repository.lock(connection, actor)
        if not row or row["api_key_ciphertext"] is None:
            return {"status": "no_database_credential"}
        cipher = CredentialCipher.from_file(self.settings.config_credential_keyring_file,
                                            self.settings.config_credential_key_id)
        plaintext = await decrypt_credential(connection, row, self.settings)
        if row["cipher_format"] == FORMAT and row["encryption_key_id"] == cipher.key_id:
            return {"status": "already_current", "encryption_revision": row["encryption_revision"]}
        credential = cipher.encrypt(actor.workspace_id, plaintext)
        if cipher.decrypt(actor.workspace_id, cipher.key_id, credential["api_key_ciphertext"]) != plaintext:
            raise RuntimeError("CREDENTIAL_VERIFICATION_FAILED")
        await self.repository.replace_credential(connection, actor, credential, row["encryption_revision"] + 1)
        return {"status": "rotated", "encryption_revision": row["encryption_revision"] + 1}
