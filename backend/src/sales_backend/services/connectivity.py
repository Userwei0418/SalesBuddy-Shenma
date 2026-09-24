"""Explicit current-binding probes, fixed samples, no fallback or business writes."""

import asyncio
import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from time import monotonic

import httpx

from sales_backend.domain.company_rules import TECHNICAL_CAPABILITIES
from sales_backend.domain.model_api import PURPOSES
from sales_backend.integrations.supreme_fde import FdeClient, FdeError
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.repositories.connectivity import ConnectivityRepository
from sales_backend.repositories.model_api import ModelApiRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services.authorization import require_permission
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.security.runtime_credentials import decrypt_credential
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.fde_invocation import FdeJsonInvocation
from sales_backend.services.agent_platform.inference import binding_for
from sales_backend.services.model_api import ModelApiError, ModelApiService, connection_settings, safe_test_error
from sales_backend.services.model_calls import DatabaseModelObserver
from sales_backend.services.runtime_config import apply_execution_policy


def present(row, actor):
    if not row or row["actor_user_ref_id"] != actor.user_id:
        raise ModelApiError("找不到当前账号的测试回执", 404)
    result = dict(row["after_snapshot"])
    if result["status"] == "running" and row["stale"]:
        result.update(status="unknown", message="未取得完成回执，请重新测试；上次结果不作为成功依据")
    return result


class ConnectivityService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings
        self.repo = ConnectivityRepository()

    async def get(self, actor, request_id):
        async with self.database.transaction(actor, readonly=True) as conn:
            return present(await self.repo.get(conn, actor, request_id), actor)

    async def run(self, actor, kind, target, request_id):
        labels = PURPOSES if kind == "direct" else TECHNICAL_CAPABILITIES
        if kind not in {"direct", "agent"} or target not in labels:
            raise ModelApiError("不支持的测试对象", 422)
        label = PURPOSES[target]["label"] if kind == "direct" else labels[target]
        async with self.database.transaction(actor) as conn:
            await require_permission(conn, "ai.config_test")
            await self.repo.lock(conn, actor)
            prior = await self.repo.get(conn, actor, request_id)
            if prior:
                receipt = present(prior, actor)
                if receipt["kind"] != kind or receipt["target"] != target:
                    raise ModelApiError("此测试标识已用于其他对象，请重新发起")
                return receipt
            if await self.repo.count_recent(conn, actor) >= 5:
                raise ModelApiError("本公司测试过于频繁，请一分钟后重试", 429)
            result = {
                "id": str(request_id),
                "kind": kind,
                "target": target,
                "label": label,
                "status": "running",
                "started_at": datetime.now(UTC).isoformat(),
                "message": "正在测试当前生效配置",
                "connection_status": "not_tested",
                "inference_status": "not_tested",
                "business_acceptance": False,
                "fallback_used": False,
                "business_writes": False,
                "execution_process": "worker" if kind == "agent" else "api",
            }
            if kind == "agent":
                result["message"] = "已提交后台任务，等待智能体实际运行进程测试"
            await self.repo.append(conn, actor, result)
            if kind == "agent":
                await self.repo.enqueue(conn, actor, request_id)
                return result
        return await self.execute(actor, kind, target, result)

    async def handle(self, actor, request_id):
        async with self.database.transaction(actor) as conn:
            await self.repo.lock(conn, actor)
            row = await self.repo.get(conn, actor, request_id)
            result = present(row, actor)
            if result["kind"] != "agent" or result["target"] not in TECHNICAL_CAPABILITIES:
                raise ModelApiError("后台测试对象不匹配", 422)
            if result["status"] not in {"running", "unknown"}:
                return result
            current = await IdentityRepository().find_actor_by_id(
                conn, workspace_id=actor.workspace_id, user_id=actor.user_id, role=actor.role.value
            )
            allowed = (await AuthorizationRepository().effective(conn)).allows("ai.config_test")
            if not current or not allowed or row["stale"] or result.get("dispatch_started"):
                result.update(status="unavailable", message="测试已过期、权限已变更或执行回执不完整，请重新测试")
                await self.repo.append(conn, actor, result, finished=True)
                await record_job_effect(conn, actor.workspace_id)
                return result
            result.update(dispatch_started=True, message="正在后台任务进程测试智能体")
            await self.repo.append(conn, actor, result, executing=True)
        return await self.execute(actor, "agent", result["target"], result)

    async def execute(self, actor, kind, target, result):
        start = monotonic()
        try:
            async with asyncio.timeout(50):
                if kind == "direct":
                    await self.direct(actor, target, result)
                else:
                    await self.agent(actor, target, result)
        except Exception as error:
            result.update(status="failed", message=safe_test_error(error))
        result.update(elapsed_ms=round((monotonic() - start) * 1000), completed_at=datetime.now(UTC).isoformat())
        async with self.database.transaction(actor) as conn:
            await self.repo.append(conn, actor, result, finished=True)
            await record_job_effect(conn, actor.workspace_id)
        return result

    async def direct(self, actor, purpose, result):
        service = ModelApiService(self.database, self.settings)
        async with self.database.transaction(actor, readonly=True) as conn:
            await require_permission(conn, "ai.config_test")
            row = await ModelApiRepository().current(conn, actor, purpose)
            config = row["config_snapshot"] if row else {"mode": "inherit"}
            version = row["version_no"] if row else 0
            if config["mode"] == "disabled":
                result.update(status="unavailable", message="此用途已停用，未发送测试请求", version=version)
                return
            if config["mode"] == "inherit":
                config, key = await service._legacy(conn, actor, purpose)
            else:
                key = await decrypt_credential(conn, row, self.settings)
        result.update(
            version=version,
            provider=config["provider_name"],
            model=config["model"],
            endpoint_url=config["endpoint_url"],
            mode=config["mode"],
        )
        if purpose == "tts" and not config.get("voice_id"):
            result.update(status="unavailable", message="语音合成为预留能力，请先配置服务商支持的音色编号")
            return
        if not key:
            result.update(status="unavailable", message="此接口尚未配置密钥")
            return
        effective = connection_settings(self.settings, actor, purpose, {**config, "mode": "custom"}, key, version)
        effective = replace(
            effective,
            model_api_metadata={**effective.model_api_metadata, "connectivity_test": True, "synthetic_input": True},
        )
        observer = DatabaseModelObserver(self.database, actor, "connectivity_test", operation_id=result["id"])
        try:
            async with asyncio.timeout(min(config["timeout_seconds"], 45)):
                await service._probe(purpose, effective, config, observer=observer)
            result.update(
                status="passed",
                connection_status="passed",
                inference_status="passed",
                message="连接成功，固定样本返回合格；未执行业务验收",
            )
        except Exception as error:
            result.update(
                status="failed", message=safe_test_error(error), connection_status="failed", inference_status="failed"
            )
            status = getattr(error, "status_code", None)
            if status is not None:
                result["http_status"] = status

    async def agent(self, actor, capability, result):
        async with self.database.transaction(actor, readonly=True) as conn:
            await require_permission(conn, "ai.config_test")
            policy = await CompanyRulesRepository().active(conn, "agent_execution." + capability)
        settings = apply_execution_policy(self.settings, policy)
        binding = binding_for(settings.agent_platform_bindings_json, actor.workspace_id, capability)
        runtime = filtered_facts_runtime(self.database, settings, capability=capability)
        if (
            not binding
            or not runtime
            or binding.agent_id != runtime.agent_id
            or binding.execution_mode != "filtered_facts"
        ):
            result.update(status="unavailable", message="当前公司未加载一致的中台绑定与接口凭据，未发送请求")
            return
        result.update(
            agent_id=binding.agent_id,
            expected_snapshot_id=binding.snapshot_id,
            actual_snapshot_id=None,
            runtime_snapshot_verified=False,
            platform_seconds=settings.agent_inference_platform_seconds,
            policy_source=policy.get("source"),
            policy_version=policy.get("version"),
        )
        seconds = min(settings.agent_inference_platform_seconds, 45)
        client = FdeClient(replace(runtime.config, timeout_seconds=seconds))
        invocation = None
        try:
            async with asyncio.timeout(min(seconds, 5)):
                parameters = await client.parameters()
            result["connection_status"] = "passed"
            required = any(
                field.get("required")
                for entry in parameters["user_input_form"]
                if isinstance(entry, dict)
                for field in entry.values()
                if isinstance(field, dict)
            )
            if required:
                result.update(
                    status="unavailable", message="中台连接与鉴权成功；此智能体要求额外输入，未执行固定样本推理"
                )
                return
            query = json.dumps(
                {
                    "mode": capability,
                    "role": "sales",
                    "facts": {},
                    "user_text": "系统连通性测试，无真实客户数据。按已配置的返回契约处理空资料，不编造事实，不调用业务写入。",
                    "connectivity_test": True,
                },
                ensure_ascii=False,
            )
            user = "probe:" + hashlib.sha256((actor.workspace_id + actor.user_id + result["id"]).encode()).hexdigest()
            invocation = FdeJsonInvocation(client, query, user)
            observer = DatabaseModelObserver(
                self.database, actor, "connectivity_test", operation_id=result["id"], provider="agent_platform"
            )
            invocation_id = await observer.start(
                "agent-chat-messages",
                "agent:" + binding.agent_id,
                {
                    "agent_id": binding.agent_id,
                    "expected_snapshot_id": binding.snapshot_id,
                    "runtime_snapshot_verified": False,
                    "connectivity_test": True,
                    "test_fault_injected": False,
                    "synthetic_input": True,
                    "business_writes": False,
                },
                1,
            )
            error = None
            try:
                async with asyncio.timeout(seconds):
                    await invocation()
                result.update(
                    status="passed",
                    inference_status="passed",
                    message="中台连接成功，已收到完整 JSON 回执；业务字段与语义未验收",
                )
            except Exception as exc:
                error = exc
                result.update(
                    status="failed",
                    inference_status="failed",
                    message="中台连接成功，但固定样本推理超时"
                    if isinstance(exc, (TimeoutError, httpx.TimeoutException))
                    else "中台连接成功，但未取得合格 JSON 回执；请结合中台任务编号排查",
                )
            finally:
                status = invocation.http_status
                result.update(http_status=status, ids=asdict(invocation.ids))
                await observer.finish(
                    invocation_id,
                    httpx.Response(status) if status else None,
                    error,
                    response_metadata={"transport": asdict(invocation.transport), "connectivity_test": True},
                )
        except (FdeError, httpx.HTTPError, TimeoutError) as exc:
            result.update(
                status="failed", connection_status="failed", message="中台连接或鉴权失败，请检查接口、凭据和服务状态"
            )
            if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                result["message"] = "中台连接超时，请检查服务状态"
            if getattr(exc, "status", None):
                result["http_status"] = exc.status
        finally:
            if invocation and invocation.result is None and invocation.ids.task_id:
                try:
                    async with asyncio.timeout(1):
                        await invocation.stop()
                    result["stop_state"] = "acknowledged"
                except Exception:
                    result["stop_state"] = "unconfirmed"
            await client.close()
