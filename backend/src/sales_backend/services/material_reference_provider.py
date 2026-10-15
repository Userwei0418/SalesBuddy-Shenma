"""Optional platform explanation; never recalculates or persists finance facts."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from uuid import uuid4

from sales_backend.services.agent_platform.audit import InferenceAudit
from sales_backend.services.agent_platform.authorization_snapshot import PermissionCheckedObserver
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.finance_explanation import PreviewExplanation, validate_preview_explanation
from sales_backend.services.agent_platform.inference import PlatformRequest, binding_for
from sales_backend.services.agent_platform.routing import AgentUnavailable, ProviderUnavailable, WriteState
from sales_backend.services.material_reference_explanation import MaterialExplanationError
from sales_backend.services.ai_service_policy import AIServiceUnavailable
from sales_backend.services.model_calls import CallQuotaExceeded
from sales_backend.services.runtime_config import load_runtime_configuration

MAX_ENVELOPE_BYTES = 256_000


def finance_explanation_provider(database, actor, settings):
    # Construction is side-effect free. Authority and configuration are checked
    # only after the service has captured its single deterministic preview.
    async def explain(preview, *, user_text, assert_current):
        await assert_current()
        try:
            original = await load_runtime_configuration(database, actor, settings,
                                                        capability='finance_assistant', purpose='text')
        except AIServiceUnavailable:
            raise ProviderUnavailable('finance_assistant.disabled') from None
        configured = original.settings
        policy = (configured.agent_execution_policy or {}).get('definition', {})
        strategy = policy.get('strategy')
        binding = binding_for(configured.agent_platform_bindings_json, actor.workspace_id, 'finance_assistant')
        if strategy == 'direct_only' or binding is None:
            raise ProviderUnavailable('finance_assistant.platform_unavailable')
        budget = max(0.01, min(60, configured.agent_finance_explanation_seconds))
        if policy.get('override_budget'):
            budget = min(budget, configured.agent_inference_total_seconds, configured.agent_inference_platform_seconds)
        # Only this capability gets its explicit budget. Other Agent transports
        # and their global defaults remain governed by their existing settings.
        runtime = filtered_facts_runtime(database, replace(configured, agent_inference_platform_seconds=budget),
                                         capability='finance_assistant')
        if runtime is None:
            raise ProviderUnavailable('finance_assistant.transport_unavailable')
        authority_failure = None

        async def current():
            nonlocal authority_failure
            try:
                await assert_current()
                try:
                    live = await load_runtime_configuration(database, actor, settings,
                        capability='finance_assistant', purpose='text')
                except AIServiceUnavailable:
                    raise MaterialExplanationError('MATERIAL_EXPLANATION_CONFIGURATION_CHANGED',
                        status=409, detail='公司模型配置已变化，请重新获取解释') from None
                if live != original:
                    raise MaterialExplanationError('MATERIAL_EXPLANATION_CONFIGURATION_CHANGED',
                        status=409, detail='公司模型配置已变化，请重新获取解释')
            except MaterialExplanationError as exc:
                authority_failure = exc
                raise

        observer_factory = runtime.observer_factory
        runtime.observer_factory = lambda *a, **kw: PermissionCheckedObserver(observer_factory(*a, **kw), current)
        envelope = dict(mode='finance_assistant', contract_version='finance_assistant.v1',
            task='explain_preview', user_text=user_text, evidence_refs=[], preview=deepcopy(preview),
            output_contract=PreviewExplanation.model_json_schema())
        if len(json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()) > MAX_ENVELOPE_BYTES:
            # Keep the full deterministic preview. Never omit lines, months or
            # missing-state evidence just to make a model request fit.
            raise ProviderUnavailable('finance_assistant.input_too_large')
        operation_id = str(uuid4())
        audit = InferenceAudit(database, actor, operation_id, mode='finance_assistant', run_id=None,
            facts={'preview': preview}, config={'agent_id': binding.agent_id,
                'expected_snapshot_id': binding.snapshot_id, 'runtime_snapshot_verified': False,
                'execution_mode': 'filtered_facts', 'tool_authority_issued': False,
                'budget_seconds': budget,
                'user_text_sha256': hashlib.sha256(user_text.encode()).hexdigest()})
        await audit.start()
        operation, sealed, status, error = None, False, 'failed', None
        try:
            await current()
            operation = runtime.operation(PlatformRequest(operation_id, 'finance_assistant', binding,
                actor.model_copy(deep=True), envelope))
            async with asyncio.timeout(budget):
                raw = await operation.invoke()
            await current()
            result = validate_preview_explanation(raw, preview=preview)
            async with asyncio.timeout(1):
                state = await operation.seal()
            sealed = True
            if state is not WriteState.NONE:
                raise ProviderUnavailable('finance_assistant.operation_not_closed')
            await current()
            status = 'succeeded'
            return result
        except BaseException as exc:
            status = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
            error = type(exc).__name__
            # Legacy accounting may wrap observer failures; never convert a
            # changed authority/configuration into optional-model degradation.
            if authority_failure is not None:
                raise authority_failure from None
            if isinstance(exc, (TimeoutError, ProviderUnavailable, AgentUnavailable, ValueError, TypeError,
                                CallQuotaExceeded, AIServiceUnavailable)):
                # Optional degradation still returns authenticated business data.
                # A failed/timeout transport must not bypass the same live
                # configuration check required after a successful response.
                try:
                    await current()
                except BaseException as changed:
                    error = type(changed).__name__
                    raise
            if isinstance(exc, (CallQuotaExceeded, AIServiceUnavailable)):
                raise ProviderUnavailable('finance_assistant.admission_unavailable') from None
            raise
        finally:
            if operation is not None and not sealed:
                try:
                    async with asyncio.timeout(1):
                        await operation.seal()
                except (Exception, asyncio.CancelledError):
                    audit.event('cleanup_incomplete', 'agent_platform')
            await audit.finish(status, trace={'provider': 'agent_platform',
                'tool_authority_issued': False, 'runtime_snapshot_verified': False}, error_code=error)

    return explain
