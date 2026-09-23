"""Individually integrated Agents with backend-filtered facts and no tool grants.

The backend validates and persists each result. This adapter cannot execute
business writes and does not claim a pinned runtime version.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict

import httpx

from sales_backend.integrations.supreme_fde import FdeClient, FdeConfig
from sales_backend.services.agent_platform.fde_invocation import FdeJsonInvocation
from sales_backend.services.agent_platform.inference import PlatformOperation, PlatformRequest
from sales_backend.services.agent_platform.routing import ControlledPlatformBlock, ProviderUnavailable, WriteState
from sales_backend.services.model_calls import DatabaseModelObserver

logger = logging.getLogger(__name__)
FACT_MODES = {
    "chatbi": {"chatbi", "customer_chatbi"},
    "battle_map_review": {"battle_map_review"},
    "opportunity_draft": {"opportunity_draft"},
    "personal_risks": {"personal_risks"},
    "visit_entry": {"visit_entry"},
    "visit_quality": {"visit_quality"},
    "today_tasks": {"today_tasks"},
    "operating_report": {"operating_report"},
    "customer_advice": {"customer_advice"},
    "opportunity_advice": {"opportunity_advice"},
    "visit_advice": {"visit_advice"},
    "competency_review": {"competency_review"},
}


def filtered_facts_runtime(database, settings, *, block_requests=False, capability="chatbi"):
    """No I/O, credential issuance or platform calls while constructing a worker."""
    if capability == "chatbi":
        agent_id, api_key = settings.agent_fde_chatbi_id, settings.agent_fde_chatbi_api_key
    elif capability == "battle_map_review":
        agent_id, api_key = settings.agent_fde_battle_map_id, settings.agent_fde_battle_map_api_key
    elif capability == "opportunity_draft":
        agent_id, api_key = settings.agent_fde_opportunity_id, settings.agent_fde_opportunity_api_key
    elif capability == "personal_risks":
        agent_id, api_key = settings.agent_fde_personal_risks_id, settings.agent_fde_personal_risks_api_key
    elif capability == "visit_entry":
        agent_id, api_key = settings.agent_fde_visit_entry_id, settings.agent_fde_visit_entry_api_key
    elif capability == "visit_quality":
        agent_id, api_key = settings.agent_fde_visit_quality_id, settings.agent_fde_visit_quality_api_key
    elif capability == "today_tasks":
        agent_id, api_key = settings.agent_fde_today_tasks_id, settings.agent_fde_today_tasks_api_key
    elif capability == "operating_report":
        agent_id, api_key = settings.agent_fde_operating_report_id, settings.agent_fde_operating_report_api_key
    elif capability in {"customer_advice", "opportunity_advice", "visit_advice", "competency_review"}:
        agent_id = getattr(settings, "agent_fde_" + capability + "_id")
        api_key = getattr(settings, "agent_fde_" + capability + "_api_key")
    else:
        return None
    if not all((settings.agent_fde_base_url, agent_id, api_key)):
        return None
    try:
        config = FdeConfig(
            settings.agent_fde_base_url,
            api_key,
            "agent_final",
            timeout_seconds=settings.agent_inference_platform_seconds,
        )
    except (TypeError, ValueError):
        logger.warning("FDE filtered-facts configuration is invalid; keeping direct AI")
        return None
    return FdeFactsRuntime(database, config, agent_id=agent_id, capability=capability, block_requests=block_requests)


class FdeFactsRuntime:
    def __init__(
        self,
        database,
        config,
        *,
        agent_id,
        client_factory=FdeClient,
        observer_factory=DatabaseModelObserver,
        block_requests=False,
        capability="chatbi",
    ):
        self.database, self.config, self.agent_id = database, config, agent_id
        self.client_factory, self.observer_factory = client_factory, observer_factory
        self.block_requests = block_requests
        self.capability = capability

    def operation(self, request: PlatformRequest) -> PlatformOperation:
        if (
            request.capability != self.capability
            or request.binding.execution_mode != "filtered_facts"
            or request.binding.agent_id != self.agent_id
            or request.input.get("mode") not in FACT_MODES.get(self.capability, set())
        ):
            raise ProviderUnavailable("FDE runtime is restricted to its configured capability and Agent")
        return _FactsOperation(self, request).callbacks()


class _FactsOperation:
    def __init__(self, runtime, request):
        self.runtime, self.request = runtime, request
        self.client = self.invocation = None
        self.sealed = self.started = False
        self.stop_state = "not_requested"

    def callbacks(self):
        return PlatformOperation(self.invoke, self.seal, self.diagnostics)

    async def invoke(self):
        if self.sealed or self.started:
            raise ProviderUnavailable("FDE operation is closed or already dispatched")
        self.started = True
        request = self.request
        # Only the trusted envelope prepared after AgentFactsLoader enters the
        # model. Auth/session tokens and tool credentials are never minted here.
        # Compact structural whitespace only; retain every field, null, row and
        # original string so detail questions and fallback share the same facts.
        query = json.dumps(request.input, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        identity = request.actor.model_dump(mode="json")
        identity["team_ids"] = sorted(identity.get("team_ids", []))
        identity_json = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        user = "sales:" + hashlib.sha256(identity_json.encode()).hexdigest()
        self.client = self.runtime.client_factory(self.runtime.config)
        self.invocation = FdeJsonInvocation(self.client, query, user)
        observer = self.runtime.observer_factory(
            self.runtime.database,
            request.actor,
            request.capability,
            operation_id=request.operation_id,
            run_id=request.run_id,
            provider="agent_platform",
        )
        try:
            invocation_id = await observer.start(
                "agent-chat-messages",
                f"agent:{request.binding.agent_id}",
                {
                    "execution_mode": "filtered_facts",
                    "agent_id": request.binding.agent_id,
                    "expected_snapshot_id": request.binding.snapshot_id,
                    "runtime_snapshot_verified": False,
                    "tool_authority_issued": False,
                    "input_bytes": len(query.encode()),
                    "input_sha256": hashlib.sha256(query.encode()).hexdigest(),
                    "record_semantics": "platform_api_attempt_not_downstream_model_count",
                    "test_fault_injected": self.runtime.block_requests,
                    "network_dispatch_suppressed": self.runtime.block_requests,
                },
                1,
            )
        except Exception:
            raise ProviderUnavailable("FDE attempt accounting unavailable") from None
        error = None
        try:
            if self.sealed:
                raise ProviderUnavailable("FDE operation closed before dispatch")
            if self.runtime.block_requests:
                raise ControlledPlatformBlock("operator-scoped request block before network dispatch")
            result = await self.invocation()
            if self.sealed:
                raise ProviderUnavailable("FDE operation closed before result acceptance")
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            result = self.invocation.result
            status = 200 if result is not None else self.invocation.http_status
            # Adapt only observed status and provider-reported usage to the shared
            # observer. No raw body, prompt, key or fabricated downstream calls.
            response = (
                None
                if status is None
                else httpx.Response(
                    status,
                    json={
                        "usage": {
                            "prompt_tokens": result.input_tokens if result else None,
                            "completion_tokens": result.output_tokens if result else None,
                        }
                    },
                    headers={"x-trace-id": self.invocation.ids.task_id or ""},
                )
            )
            try:
                async with asyncio.timeout(1):
                    await observer.finish(
                        invocation_id,
                        response,
                        error,
                        response_metadata={"transport": asdict(self.invocation.transport)},
                    )
            except (Exception, asyncio.CancelledError):
                logger.warning("FDE attempt completion remains unknown operation=%s", request.operation_id)

    async def seal(self):
        if self.sealed:
            return WriteState.NONE
        self.sealed = True  # Synchronous before cleanup: late output cannot be accepted.
        if self.invocation and self.invocation.result is None and self.invocation.ids.task_id:
            try:
                async with asyncio.timeout(0.4):
                    await self.invocation.stop()
                self.stop_state = "acknowledged"
            except Exception:
                self.stop_state = "unconfirmed"
        if self.client:
            try:
                async with asyncio.timeout(0.4):
                    await self.client.close()
            except (Exception, asyncio.CancelledError):
                logger.warning("FDE client cleanup incomplete operation=%s", self.request.operation_id)
        # This is our own authority boundary: this operation never issues query
        # or write credentials and cannot dispatch our business gateway. It is
        # not a conclusion drawn from remote stop/history/model output.
        return WriteState.NONE

    def diagnostics(self):
        return {
            "execution_mode": "filtered_facts",
            "expected_snapshot_id": self.request.binding.snapshot_id,
            "actual_snapshot_id": None,
            "runtime_snapshot_verified": False,
            "tool_authority_issued": False,
            "ids": asdict(self.invocation.ids) if self.invocation else {},
            "error_code": self.invocation.error_code if self.invocation else None,
            "stop_state": self.stop_state,
            "test_fault_injected": self.runtime.block_requests,
            "network_dispatch_suppressed": self.runtime.block_requests,
            "transport": asdict(self.invocation.transport) if self.invocation else {},
        }
