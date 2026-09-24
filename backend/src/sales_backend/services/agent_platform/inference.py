"""Application-side provider selection; platform wire protocol belongs to its adapter.

Runtime adapters declare their execution mode. Tool-capable adapters must use a
pinned published snapshot and revoke tool access/read backend-owned receipts
before fallback. The first-stage chatbi adapter has no business tool authority.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic_core import to_jsonable_python

from sales_backend.config import Settings
from sales_backend.domain.agent import ActorContext, ChatMessage
from sales_backend.domain.customer_risk import CUSTOMER_RISK_OUTPUT_CONTRACT
from sales_backend.integrations.senseaudio import SenseAudioClient, SenseAudioError
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_platform.audit import InferenceAudit
from sales_backend.services.agent_platform.routing import (
    AgentRouter,
    AgentUnavailable,
    Invoke,
    ProviderUnavailable,
    ReconciliationRequired,
    RoutingPolicy,
    Validate,
    WriteState,
)
from sales_backend.services.model_calls import DatabaseModelObserver

logger = logging.getLogger(__name__)
CAPABILITIES = frozenset(
    {
        "battle_map_review",
        "opportunity_draft",
        "personal_risks",
        "visit_entry",
        "visit_quality",
        "chatbi",
        "today_tasks",
        "operating_report",
        "customer_advice",
        "opportunity_advice",
        "visit_advice",
        "competency_review",
    }
)
REFERENCE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


@dataclass(frozen=True)
class AgentBinding:
    agent_id: str
    snapshot_id: str
    execution_mode: str = "snapshot_pinned"


def binding_for(raw: str, workspace_id: str, capability: str) -> AgentBinding | None:
    """Operator-owned settings only. Invalid/unapproved settings keep direct AI usable."""
    if capability not in CAPABILITIES or len(raw) > 64000:
        return None
    try:
        config = json.loads(raw)
        item = config.get(workspace_id, {}).get(capability, {})
        if item.get("enabled") is not True:
            return None
        mode = item.get("execution_mode", "snapshot_pinned")
        if mode not in {"snapshot_pinned", "filtered_facts"}:
            return None
        if mode == "filtered_facts" and capability not in {
            "chatbi",
            "battle_map_review",
            "opportunity_draft",
            "personal_risks",
            "visit_entry",
            "visit_quality",
            "today_tasks",
            "operating_report",
            "customer_advice",
            "opportunity_advice",
            "visit_advice",
            "competency_review",
        }:
            return None  # Only individually integrated capabilities; backend owns all result writes.
        agent_id = item["agent_id"]
        snapshot_id = item.get("expected_snapshot_id") if mode == "filtered_facts" else item.get("snapshot_id")
        if not isinstance(snapshot_id, str):
            return None
        # A CLI management revision is a concurrency hash, not a runtime ID.
        # Deliberately do not alias legacy revision/approved_revision settings.
        if str(UUID(snapshot_id)) != snapshot_id:
            return None
        if mode == "snapshot_pinned" and item.get("approved_snapshot_id") != snapshot_id:
            return None
        if not isinstance(agent_id, str) or not REFERENCE.fullmatch(agent_id):
            return None
        return AgentBinding(agent_id, snapshot_id, mode)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


@dataclass(frozen=True)
class PlatformRequest:
    operation_id: str
    capability: str
    binding: AgentBinding
    actor: ActorContext
    # Only this JSON envelope is model input. Identity, binding and operation IDs
    # above remain trusted orchestration arguments; never recover them from output.
    input: dict[str, Any]
    run_id: str | None = None


@dataclass(frozen=True)
class PlatformOperation:
    invoke: Invoke
    seal: Callable[[], Awaitable[WriteState]]
    diagnostics: Callable[[], dict[str, Any]] = field(default=lambda: {})


class PlatformRuntime(Protocol):
    def operation(self, request: PlatformRequest) -> PlatformOperation:
        """Construct callbacks without IO; invoke performs the actual network request.

        snapshot_pinned must execute the pinned snapshot and reject drift.
        The filtered_facts stage uses facts queried by our backend, issues no
        tool authority and makes no claim of runtime version proof. Only
        individually integrated capabilities can select this mode.
        Both modes isolate conversation memory by identity. seal is trusted backend
        state, never an Agent-provided flag. Credential minting belongs inside
        the operation lifetime and must be revoked on both success and failure.
        """
        ...


@dataclass(frozen=True)
class InferenceResult:
    payload: dict[str, Any]
    trace: dict[str, Any]


class InferenceService:
    def __init__(
        self, database, settings: Settings, *, platform: PlatformRuntime | None = None, direct_factory=SenseAudioClient
    ):
        self.database, self.settings = database, settings
        self.platform, self.direct_factory = platform, direct_factory

    async def assert_actor_current(self, actor, permission):
        async with self.database.transaction(actor, readonly=True) as connection:
            from sales_backend.services.authorization import require_permission
            await require_permission(connection, permission)
            current = await IdentityRepository().find_actor_by_id(
                connection,
                workspace_id=actor.workspace_id,
                user_id=actor.user_id,
                role=actor.role.value,
            )
        if not current or current.context.model_copy(update={"team_ids": tuple(sorted(current.context.team_ids))}) != (
            actor.model_copy(update={"team_ids": tuple(sorted(actor.team_ids))})
        ):
            raise PermissionError("当前数据权限已变化，请重新发起分析")

    async def evaluate(
        self,
        *,
        actor: ActorContext,
        mode: str,
        facts: dict[str, Any],
        messages: list[ChatMessage],
        user_text: str,
        validate: Validate,
        run_id: str | None = None,
        surface: str | None = None,
        rule_fallback: Callable[[], dict[str, Any]] | None = None,
    ) -> InferenceResult:
        if surface == "customer_risk":
            if mode != "personal_risks":
                raise PermissionError("客户风险评估须使用 personal_risks 能力")
            if not messages:
                raise ValueError("客户风险评估必须提供明确的后端提示词")
        elif surface is not None and (
            surface != "fde_profile"
            or mode != "operating_report"
            or facts.get("coaching_inputs", {}).get("contract_version") != "fde.coaching.v1"
        ):
            raise PermissionError("推理场景来源不合法")
        if rule_fallback is not None and mode != "opportunity_change":
            raise ValueError("rule fallback is restricted to opportunity change assessment")
        capability = {"customer_chatbi": "chatbi", "opportunity_change": "opportunity_draft"}.get(mode, mode)
        business_policy = facts.get("agent_business_policy")
        if business_policy and business_policy.get("code") != "agent_business." + capability:
            raise ValueError("业务规则与当前能力不一致")
        from sales_backend.domain.route_permissions import AGENT_PERMISSIONS
        permission = ({"customer_risk": "risk.auto_review", "fde_profile": "profile.fde_review"}.get(surface)
                      or {"customer_advice": "advice.customer", "opportunity_advice": "advice.opportunity",
                          "visit_advice": "advice.visit", "visit_quality": "visit.quality_review",
                          "opportunity_change": "opportunity.update", "battle_map_review": "battle_map.read",
                          "competency_review": "profile.sales_review"}.get(mode)
                      or AGENT_PERMISSIONS.get(mode, ""))
        operation_id = str(uuid4())  # fresh per worker attempt; run_id remains the stable business reference
        if run_id:
            UUID(run_id)
        await self.assert_actor_current(actor, permission)
        binding = binding_for(self.settings.agent_platform_bindings_json, actor.workspace_id, capability)
        audit = InferenceAudit(
            self.database,
            actor,
            operation_id,
            mode=mode,
            run_id=run_id,
            facts=facts,
            config={
                "platform_enabled": binding is not None,
                "adapter_configured": self.platform is not None,
                "agent_capability": capability,
                "agent_id": binding.agent_id if binding else None,
                "expected_snapshot_id": binding.snapshot_id if binding else None,
                "execution_mode": binding.execution_mode if binding else None,
                "platform_seconds": self.settings.agent_inference_platform_seconds,
                "total_seconds": self.settings.agent_inference_total_seconds,
                "direct_model": "opportunity_change_rules_v1" if rule_fallback else self.settings.llm_model,
                "execution_policy": self.settings.agent_execution_policy,
            },
        )
        await audit.start()
        audit_status, audit_error, trace = "failed", None, {}
        operation = None
        started = monotonic()
        sealed = False
        seal_attempted = False

        async def platform_call():
            nonlocal operation
            if binding and self.platform:
                operation = self.platform.operation(
                    PlatformRequest(
                        operation_id,
                        capability,
                        binding,
                        actor.model_copy(deep=True),
                        {
                            "mode": capability if mode == "opportunity_change" else mode,
                            "role": actor.role.value,
                            "user_text": user_text,
                            "current_time": facts.get("data_as_of"),
                            "facts": to_jsonable_python(facts),
                            **(
                                {"backend_prompt": messages[0].content}
                                if mode == "opportunity_change" or capability
                                in {"customer_advice", "opportunity_advice", "visit_advice", "competency_review"}
                                else {}
                            ),
                            **(
                                {
                                    "surface": surface,
                                    "backend_prompt": messages[0].content,
                                    "output_contract": CUSTOMER_RISK_OUTPUT_CONTRACT,
                                }
                                if surface == "customer_risk"
                                else {}
                            ),
                            **(
                                {
                                    "surface": surface,
                                    "backend_prompt": messages[0].content,
                                    "output_contract": {
                                        "version": "fde.coaching.v1",
                                        "action_plan": {
                                            "min_items": 0,
                                            "max_items": 3,
                                            "required": ["title", "detail", "source_refs"],
                                            "source_refs": "only coaching_inputs.sources source_ref values",
                                        },
                                    },
                                }
                                if surface == "fde_profile"
                                else {}
                            ),
                        },
                        run_id=run_id,
                    )
                )
            if operation is None:
                raise ProviderUnavailable("platform runtime adapter is not configured")
            return await operation.invoke()

        async def seal():
            nonlocal sealed, seal_attempted
            seal_attempted = True
            # No operation means no network dispatch and no issued tool credential.
            state = await operation.seal() if operation else WriteState.NONE
            sealed = True
            return state

        async def direct():
            if rule_fallback is not None:
                return rule_fallback()
            client = self.direct_factory(
                self.settings,
                observer=DatabaseModelObserver(
                    self.database,
                    actor,
                    mode,
                    operation_id=operation_id,
                    run_id=run_id,
                ),
            )
            try:
                return await client.chat_json(messages=messages, temperature=0.1)
            except SenseAudioError as exc:
                raise ProviderUnavailable("original model request failed") from exc
            finally:
                await client.close()

        try:
            routed = await AgentRouter().evaluate(
                policy=RoutingPolicy(
                    platform_enabled=binding is not None,
                    total_seconds=self.settings.agent_inference_total_seconds,
                    platform_seconds=self.settings.agent_inference_platform_seconds,
                ),
                platform=platform_call,
                direct=direct,
                validate=validate,
                write_state=seal,
                observe=audit.event,
                fallback_provider="rules" if rule_fallback else "senseaudio",
            )
            # Revoke an otherwise successful operation before the shared persistence
            # boundary, so a late callback cannot outlive the accepted result.
            if operation and not sealed:
                try:
                    async with asyncio.timeout(2):
                        state = await seal()
                except Exception as exc:
                    raise ReconciliationRequired("accepted inference receipt is unavailable") from exc
                if state is not WriteState.NONE:
                    raise ReconciliationRequired(
                        "inference operation has business writes; reconcile before persistence"
                    )
            trace = {
                "operation_id": operation_id,
                "provider": routed.provider,
                "fallback_reason": routed.fallback_reason or (
                    "PlatformNotSelected" if routed.provider == "rules" else None
                ),
                "model_ref": (
                    (
                        f"agent-platform:{binding.agent_id}@{binding.snapshot_id}"
                        if binding.execution_mode == "snapshot_pinned"
                        else f"agent-platform:{binding.agent_id}"
                    )
                    if routed.provider == "agent_platform"
                    else "opportunity_change_rules_v1" if rule_fallback else self.settings.llm_model
                ),
                "elapsed_ms": round((monotonic() - started) * 1000),
            }
            if operation:
                trace["platform_run"] = operation.diagnostics()
            await self.assert_actor_current(actor, permission)
            logger.info(
                "inference completed capability=%s operation=%s provider=%s fallback=%s elapsed_ms=%s",
                capability,
                operation_id,
                trace["provider"],
                trace["fallback_reason"],
                trace["elapsed_ms"],
                extra={"system_event": True, "event_type": "inference_route", "actor": actor},
            )
            audit_status = "accepted"
            return InferenceResult(routed.payload, trace)
        except AgentUnavailable as exc:
            audit_error = type(exc).__name__
            # Both routes already consumed this run's bounded inference budget.
            # A queue retry would replay both billed calls and extend UI waiting.
            raise SenseAudioError("AI 暂时不可用，请稍后重试", retryable=False) from exc
        except ReconciliationRequired as exc:
            audit_status, audit_error = "reconciliation_required", type(exc).__name__
            raise ReconciliationRequired("操作结果需要核对，请刷新后查看，避免重复提交") from exc
        except BaseException as exc:
            audit_status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            audit_error = type(exc).__name__
            raise
        finally:
            if operation and not seal_attempted:
                try:
                    async with asyncio.timeout(2):
                        await seal()
                except Exception:
                    logger.warning(
                        "inference cleanup incomplete operation=%s",
                        operation_id,
                        extra={"actor": actor, "event_type": "inference_cleanup"},
                    )
            if operation:
                trace["platform_run"] = operation.diagnostics()
            trace["operation_id"] = operation_id
            trace["elapsed_ms"] = round((monotonic() - started) * 1000)
            await audit.finish(audit_status, trace=trace, error_code=audit_error)
