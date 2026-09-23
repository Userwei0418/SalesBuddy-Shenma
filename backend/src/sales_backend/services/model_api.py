"""Test an immutable connection candidate before atomically publishing its binding."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from sales_backend.domain.agent import ChatMessage
from sales_backend.domain.model_api import PURPOSES
from sales_backend.integrations.circuit_breaker import GatewayCircuitBreaker
from sales_backend.integrations.senseaudio import SenseAudioClient, SenseAudioError
from sales_backend.repositories.admin import AgentRuntimeConfigRepository
from sales_backend.repositories.model_api import ModelApiRepository
from sales_backend.security.runtime_credentials import CredentialCipher, decrypt_credential


class ModelApiError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status
        super().__init__(message)


def connection_settings(defaults, actor, purpose, snapshot, api_key, version):
    """Preserve business routing and prompts; replace only this direct API purpose."""
    metadata = {
        "connection_purpose": purpose,
        "connection_version": version,
        "connection_provider": snapshot["provider_name"],
        "connection_workspace": actor.workspace_id,
        "connection_mode": snapshot["mode"],
    }
    if snapshot["mode"] == "disabled":
        return replace(defaults, senseaudio_api_key="", model_api_metadata=metadata)
    return replace(
        defaults,
        senseaudio_api_key=api_key,
        model_api_endpoint=snapshot["endpoint_url"],
        model_api_metadata=metadata,
        timeout_seconds=snapshot["timeout_seconds"],
        max_retries=snapshot["max_retries"],
        **{{"text": "llm_model", "asr": "asr_model", "tts": "tts_model"}[purpose]: snapshot["model"]},
    )


def legacy_snapshot(settings, purpose):
    return {
        "mode": "inherit",
        "provider_name": "现有默认接口",
        "endpoint_url": settings.senseaudio_base_url.rstrip("/") + PURPOSES[purpose]["path"],
        "protocol": PURPOSES[purpose]["protocol"],
        "model": getattr(settings, {"text": "llm_model", "asr": "asr_model", "tts": "tts_model"}[purpose]),
        "timeout_seconds": settings.timeout_seconds,
        "max_retries": settings.max_retries,
        "voice_id": "",
    }


def fingerprint(snapshot, api_key):
    return hashlib.sha256(json.dumps([snapshot, api_key], sort_keys=True).encode()).hexdigest()


def receipt(row):
    # Explicit projection: never serialize a candidate's credential or request digest.
    result = {k: row[k] for k in ("id", "purpose", "expected_version", "status", "result", "expires_at")}
    if row["status"] == "running" and not row.get("unexpired", True):
        result.update(
            status="failed", result={"message": "测试已过期，请重新发起", "network_sent": None, "elapsed_ms": 0}
        )
    return result


def safe_test_error(exc):
    status = getattr(exc, "status_code", None)
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "接口响应超时，请检查地址、模型或服务商状态"
    if status in (401, 403):
        return "服务商拒绝鉴权，请检查密钥和模型权限"
    if status == 429:
        return "服务商限流或额度不足，请稍后重试"
    if status:
        return f"服务商返回错误（状态码 {status}），请核对协议和地址"
    if isinstance(exc, httpx.NetworkError):
        return "无法连接公网接口，请检查域名、证书和服务状态"
    return "接口未返回符合约定的结果，请核对模型、协议和服务状态"


class ModelApiService:
    def __init__(self, database, settings, *, probe=None):
        self.database, self.settings = database, settings
        self.repo = ModelApiRepository()
        self.probe = probe or self._probe

    async def _legacy(self, conn, actor, purpose):
        row = await AgentRuntimeConfigRepository().current(conn, actor)
        settings = self.settings
        if row and row["enabled"]:
            key = await decrypt_credential(conn, row, settings)
            settings = replace(
                settings,
                senseaudio_api_key=key or settings.senseaudio_api_key,
                senseaudio_base_url=row["provider_base_url"] or settings.senseaudio_base_url,
                llm_model=row["llm_model"] or settings.llm_model,
                asr_model=row["asr_model"] or settings.asr_model,
                tts_model=row["tts_model"] or settings.tts_model,
            )
        return legacy_snapshot(settings, purpose), settings.senseaudio_api_key

    async def list(self, actor):
        items = []
        async with self.database.transaction(actor, readonly=True) as conn:
            for purpose, info in PURPOSES.items():
                row = await self.repo.current(conn, actor, purpose)
                config = row["config_snapshot"] if row else {"mode": "inherit"}
                # Listing requires no decryption. A broken old key must not stop replacement.
                if config["mode"] == "inherit":
                    old = await AgentRuntimeConfigRepository().current(conn, actor, include_ciphertext=False)
                    defaults = self.settings
                    if old and old["enabled"]:
                        defaults = replace(
                            defaults,
                            senseaudio_base_url=old["provider_base_url"] or defaults.senseaudio_base_url,
                            llm_model=old["llm_model"] or defaults.llm_model,
                            asr_model=old["asr_model"] or defaults.asr_model,
                            tts_model=old["tts_model"] or defaults.tts_model,
                        )
                    config = legacy_snapshot(defaults, purpose)
                    has_key = bool((old and old["enabled"] and old["has_api_key"]) or defaults.senseaudio_api_key)
                    tail = old.get("api_key_tail") if old and old["enabled"] and old["has_api_key"] else ""
                else:
                    has_key, tail = bool(row.get("api_key_ciphertext")), row.get("api_key_tail")
                items.append(
                    {
                        "purpose": purpose,
                        **info,
                        "version": row["version_no"] if row else 0,
                        "configuration": config,
                        "has_api_key": has_key,
                        "key_hint": "已保存 · 尾号 " + tail if tail else ("已配置" if has_key else "未配置"),
                        "published_at": row["created_at"] if row else None,
                    }
                )
        return {"items": items}

    async def test(self, actor, purpose, body):
        config = body.configuration.model_dump()
        if config["protocol"] != PURPOSES[purpose]["protocol"]:
            raise ModelApiError("接口协议与用途不一致", 422)
        secret = body.api_key.get_secret_value() if body.api_key is not None else None
        digest = fingerprint(
            [str(body.request_id), purpose, body.expected_version, config, body.restored_from_version, actor.user_id],
            secret,
        )
        async with self.database.transaction(actor) as conn:
            # Serialize duplicate request IDs even across purposes, then serialize the binding.
            await self.repo.lock(conn, actor, str(body.request_id))
            prior = await self.repo.test(conn, actor, body.request_id)
            if prior:
                if prior["request_digest"] != digest or str(prior["actor_user_ref_id"]) != actor.user_id:
                    raise ModelApiError("该测试标识已用于其他配置，请重新发起测试")
                return receipt(prior)
            await self.repo.lock(conn, actor, purpose)
            if await self.repo.recent_tests(conn, actor, purpose) >= 5:
                raise ModelApiError("该用途测试过于频繁，请稍后重试", 429)
            current = await self.repo.current(conn, actor, purpose)
            if (current["version_no"] if current else 0) != body.expected_version:
                raise ModelApiError("配置已被其他管理员更新，请刷新后重试")
            if body.restored_from_version is not None:
                old = await self.repo.release_snapshot(conn, actor, purpose, body.restored_from_version)
                if old != config:
                    raise ModelApiError("恢复版本与测试配置不一致")
            credential, guard = {}, None
            if config["mode"] == "custom":
                if secret is None:
                    if current and current["config_snapshot"]["mode"] == "custom":
                        origin = current["config_snapshot"]
                        secret = await decrypt_credential(conn, current, self.settings)
                    else:
                        origin, secret = await self._legacy(conn, actor, purpose)
                        guard = fingerprint(origin, secret)
                    if (
                        urlsplit(origin["endpoint_url"]).netloc.lower()
                        != urlsplit(config["endpoint_url"]).netloc.lower()
                    ):
                        raise ModelApiError("更换服务商地址时，请重新填写该服务商的密钥", 422)
                if not secret:
                    raise ModelApiError("请填写接口密钥", 422)
                credential = CredentialCipher.from_file(
                    self.settings.config_credential_keyring_file, self.settings.config_credential_key_id
                ).encrypt(actor.workspace_id, secret)
            elif secret is not None:
                raise ModelApiError("继承或停用模式无需填写密钥", 422)
            if config["mode"] == "inherit":
                origin, key = await self._legacy(conn, actor, purpose)
                guard = fingerprint(origin, key)
            await self.repo.reserve(conn, actor, purpose, body, digest, credential, guard)
        # Do not hold a database connection while an external service runs.
        start, network_sent, status = time.monotonic(), config["mode"] == "custom", "passed"
        try:
            if network_sent:
                settings = connection_settings(self.settings, actor, purpose, config, secret, body.expected_version + 1)
                async with asyncio.timeout(min(config["timeout_seconds"], 45)):
                    await self.probe(purpose, settings, config)
                message = "接口连通且固定样本返回合格；未执行真实业务"
            else:
                message = (
                    "配置检查通过；未发送模型请求"
                    if config["mode"] == "inherit"
                    else "停用配置检查通过；未发送模型请求"
                )
        except Exception as exc:
            status, message = "failed", safe_test_error(exc)
        result = {
            "message": message,
            "elapsed_ms": round((time.monotonic() - start) * 1000),
            "network_sent": network_sent,
            "business_acceptance": False,
        }
        async with self.database.transaction(actor) as conn:
            await self.repo.finish(conn, actor, body.request_id, status, result)
            await self.repo.audit(
                conn, actor, "test", {"purpose": purpose, "test_id": str(body.request_id), "status": status}
            )
            row = await self.repo.test(conn, actor, body.request_id)
            if not row:
                raise ModelApiError("权限已变更，测试回执未保存", 403)
            return receipt(row)

    async def publish(self, actor, purpose, body):
        async with self.database.transaction(actor) as conn:
            await self.repo.lock(conn, actor, purpose)
            test = await self.repo.test(conn, actor, body.test_id)
            if not test or test["purpose"] != purpose or str(test["actor_user_ref_id"]) != actor.user_id:
                raise ModelApiError("找不到当前账号在本公司的测试回执", 404)
            prior = await self.repo.published_test(conn, actor, body.test_id)
            if prior is not None:
                if test["expected_version"] != body.expected_version:
                    raise ModelApiError("测试与发布版本不一致")
                return {"published_version": prior, "replayed": True}
            current = await self.repo.current(conn, actor, purpose)
            if (current["version_no"] if current else 0) != body.expected_version or test[
                "expected_version"
            ] != body.expected_version:
                raise ModelApiError("配置已更新，请刷新并重新测试")
            if test["status"] != "passed" or not test["unexpired"]:
                raise ModelApiError("请先完成一次成功测试；测试回执有效期为 15 分钟")
            if test["source_guard"]:
                origin, key = await self._legacy(conn, actor, purpose)
                if fingerprint(origin, key) != test["source_guard"]:
                    raise ModelApiError("默认接口已发生变化，请重新测试")
            version = await self.repo.publish(conn, actor, test)
            return {"published_version": version, "replayed": False}

    async def _probe(self, purpose, settings, config, *, observer=None):
        client = SenseAudioClient(replace(settings, max_retries=0), breaker=GatewayCircuitBreaker(), observer=observer)
        try:
            if purpose == "text":
                answer = await client.chat_json(
                    messages=[ChatMessage(role="user", content='接口连通测试。只返回 JSON：{"ok":true}')],
                    temperature=0,
                    max_tokens=64,
                )
                if answer.get("ok") is not True:
                    raise SenseAudioError("测试输出不符合约定")
            elif purpose == "asr":
                content = (Path(__file__).resolve().parents[1] / "assets/model-api-test.wav").read_bytes()
                await client.transcribe(filename="connection-test.wav", content=content, mime_type="audio/wav")
            else:
                await client.synthesize(text="语音接口连接测试。", voice_id=config["voice_id"])
        finally:
            await client.close()
