"""Work-report transports use the existing company text route and quota receipts.

The generation package owns AgentRouter and output validation. This adapter owns
credentials, transport lifetime and repeated worker authority checks. It never
substitutes the older operating-report Agent or creates a queue/connection pool.
"""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from sales_backend.domain.agent import ChatMessage
from sales_backend.domain.work_report import WorkReportError, validate_generation_result
from sales_backend.integrations.senseaudio import SenseAudioClient, SenseAudioError
from sales_backend.services.agent_platform.audit import InferenceAudit
from sales_backend.services.agent_platform.authorization_snapshot import PermissionCheckedObserver
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import PlatformRequest, binding_for
from sales_backend.services.agent_platform.routing import ProviderUnavailable, RoutingPolicy, WriteState
from sales_backend.services.model_calls import DatabaseModelObserver
from sales_backend.services.runtime_config import load_runtime_configuration
from sales_backend.services.work_report_generation import GenerationProviders, generate_work_report
from sales_backend.services.work_report_generation.engine import GenerationStateUnavailable


def _report_settings(settings):
    # apply_execution_policy has already mapped an explicit company override to
    # agent_inference_*. Preserve it; otherwise use the report's multi-batch budget.
    if (settings.agent_execution_policy or {}).get('definition', {}).get('override_budget') is True:
        return settings
    # Scope these legacy transport fields to this report only. The platform HTTP
    # runtime and router must use the same budget without changing shared settings.
    return replace(settings, agent_inference_total_seconds=settings.work_report_inference_total_seconds,
                   agent_inference_platform_seconds=settings.work_report_inference_platform_seconds)


class ReportTransports:
    """One instance per report attempt; every platform batch has a fresh receipt."""
    def __init__(self, database, actor, settings, *, assert_active, runtime=None,
                 client_factory=None, observer_factory=None):
        settings = _report_settings(settings)
        self.database, self.actor, self.settings = database, actor, settings
        self.check = assert_active
        self.client_factory = client_factory or SenseAudioClient
        self.observer_factory = observer_factory or DatabaseModelObserver
        self.binding = binding_for(settings.agent_platform_bindings_json, actor.workspace_id, 'work_report')
        strategy = (settings.agent_execution_policy or {}).get('definition', {}).get('strategy')
        self.reason = None
        if strategy == 'direct_only':
            self.binding, self.reason = None, 'platform_policy_direct_only'
        elif self.binding is None:
            self.reason = 'platform_unconfigured'
        self.runtime = runtime
        if self.binding is not None and runtime is None:
            self.reason = 'platform_transport_unavailable'
        self.operation = None
        self.authority_failure = None
        # The existing filtered-facts runtime creates a quota observer for each
        # dispatch. Wrap that per-instance factory, never a global/shared runtime.
        if runtime is not None and hasattr(runtime, 'observer_factory'):
            factory = runtime.observer_factory
            runtime.observer_factory = lambda *a, **kw: PermissionCheckedObserver(factory(*a, **kw), self.active)

    async def active(self):
        try:
            await self.check()
        except (TimeoutError, ProviderUnavailable) as exc:
            self.authority_failure = GenerationStateUnavailable('work-report authority could not be checked')
            raise self.authority_failure from exc
        except Exception as exc:
            self.authority_failure = exc
            raise

    @property
    def providers(self):
        enabled = self.binding is not None and self.runtime is not None
        return GenerationProviders(direct=self.direct, platform=self.platform if enabled else None,
                                   seal_platform=self.seal if enabled else None)

    @property
    def policy(self):
        return RoutingPolicy(platform_enabled=self.binding is not None and self.runtime is not None,
                             total_seconds=self.settings.agent_inference_total_seconds,
                             platform_seconds=self.settings.agent_inference_platform_seconds)

    async def direct(self, envelope):
        await self.active()
        outgoing = {**deepcopy(envelope), 'mode': 'work_report'}
        observer = PermissionCheckedObserver(
            self.observer_factory(self.database, self.actor, 'work_report', operation_id=str(uuid4())), self.active,
        )
        # run_id is intentionally absent: the legacy model receipt FK targets
        # agent.run, not the independent work-report run table. ops.job links it.
        client = self.client_factory(self.settings, observer=observer)
        failed = False
        try:
            result = await client.chat_json(messages=[
                ChatMessage(role='system', content=outgoing['system_prompt']),
                ChatMessage(role='user', content=json.dumps(outgoing, ensure_ascii=False, allow_nan=False)),
            ], temperature=0.1)
            await self.active()
            return result
        except BaseException as exc:
            failed = True
            if isinstance(exc, SenseAudioError):
                raise ProviderUnavailable('work-report company text provider unavailable') from None
            raise
        finally:
            try:
                async with asyncio.timeout(1):
                    await client.close()
            except (Exception, asyncio.CancelledError):
                # Cleanup timeout must not replace revoked authority or a lost
                # lease with an exception the router treats as fallback-safe.
                if not failed:
                    raise

    async def platform(self, envelope):
        await self.active()
        if self.operation is not None:
            raise RuntimeError('previous report platform operation was not sealed')
        self.authority_failure = None
        self.operation = self.runtime.operation(PlatformRequest(
            str(uuid4()), 'work_report', self.binding, self.actor.model_copy(deep=True),
            {**deepcopy(envelope), 'mode': 'work_report'},
        ))
        try:
            result = await self.operation.invoke()
            await self.active()
            return result
        except Exception:
            # Some legacy transport accounting wraps observer failures. An
            # authority failure must never become a fallback/model retry signal.
            if self.authority_failure is not None:
                raise self.authority_failure from None
            raise

    async def seal(self):
        if self.operation is None:
            return WriteState.NONE
        state = await self.operation.seal()
        if state is WriteState.NONE:
            self.operation = None
        return state


async def generate_report(request, *, database, actor, settings, assert_active):
    await assert_active()
    overview = []
    if getattr(request, 'overview_facts', None) is not None:
        # Share the generator's target-section and rewrite admission rule.
        from sales_backend.services.work_report_generation.content import overview_material
        overview = overview_material(request)
    review_facts = getattr(request, 'review_facts', None)
    review_mode = getattr(request, 'review_mode', None)
    has_review = bool(review_facts and review_facts.evidence)
    has_material = (request.sources or overview or any(item.text.strip() for item in request.supplements)
                    or any(item.complete and item.value is not None and Decimal(item.value) != 0
                           for item in request.metrics))
    if not (has_review if review_mode == 'daily' else has_material or has_review):
        async def unused(envelope):
            raise RuntimeError('empty report material must not invoke a provider')
        # Empty material does not require model credentials/configuration. The
        # shared generator still validates it and rechecks authority before/after.
        return await generate_work_report(request, providers=GenerationProviders(direct=unused),
                                          assert_active=assert_active, routing_policy=RoutingPolicy())
    runtime_config = await load_runtime_configuration(database, actor, settings, capability='work_report', purpose='text')
    configured = _report_settings(runtime_config.settings)
    # An explicit company total remains a hard cap; ordinary queued reports
    # renew their bounded stage budget only after a fully accepted batch.
    renew_budget_on_progress = (configured.agent_execution_policy or {}).get("definition", {}).get("override_budget") is not True

    async def current():
        await assert_active()

    async def dispatch_current():
        await current()
        # A disabled/replaced company text configuration cannot be bypassed by a
        # queued request or a direct-client internal retry using old credentials.
        live = await load_runtime_configuration(database, actor, settings, capability='work_report', purpose='text')
        if live != runtime_config:
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '公司模型配置已变化，请重新生成', status=403)

    transport = ReportTransports(database, actor, configured, assert_active=dispatch_current,
                                runtime=filtered_facts_runtime(database, configured, capability='work_report'))
    audit = InferenceAudit(database, actor, str(uuid4()), mode='work_report', run_id=None,
                           facts=request.model_dump(mode='json'), config={
                               'capability': 'work_report', 'platform_enabled': transport.policy.platform_enabled,
                               'fallback_reason': transport.reason, 'direct_model': configured.llm_model,
                               'execution_policy': configured.agent_execution_policy,
                               'budget_mode': 'accepted_batch_progress' if renew_budget_on_progress else 'explicit_total',
                           })
    await audit.start()
    status, trace, error_code = 'failed', {}, None
    try:
        result = await generate_work_report(request, providers=transport.providers, assert_active=current,
                                            routing_policy=transport.policy, observer=audit.event,
                                            renew_budget_on_progress=renew_budget_on_progress)
        if result.provider == 'direct' and result.trace.fallback_reason is None and transport.reason:
            result = result.model_copy(update={'trace': result.trace.model_copy(update={'fallback_reason': transport.reason})})
        validate_generation_result(result, request)
        await current()
        status, trace = 'accepted', result.trace.model_dump(mode='json')
        return result
    except BaseException as exc:
        status = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
        error_code = type(exc).__name__
        raise
    finally:
        # Content-free receipt completion is separate from the final report
        # transaction. 'accepted' means a validated result, including a labelled
        # basic_summary fallback; its trace still distinguishes AI generation.
        await audit.finish(status, trace=trace, error_code=error_code)
