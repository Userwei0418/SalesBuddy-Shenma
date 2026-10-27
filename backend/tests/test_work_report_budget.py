"""Offline regression for the report-only deadline; synthetic facts, no model I/O."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from sales_backend.config import get_settings
from sales_backend.contracts.work_report import GenerationInput
from sales_backend.domain.work_report import WorkReportError, validate_generation_result
from sales_backend.services import work_report_providers as subject
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.routing import RoutingPolicy
from sales_backend.services.runtime_config import RuntimeConfiguration, apply_execution_policy
from sales_backend.services.work_report_generation import GenerationProviders, engine, generate_work_report, service
from sales_backend.services.work_report_generation.engine import GenerationStateUnavailable
from tests.test_work_report_contract import request_data
from tests.test_work_report_generation import answer
from tests.test_work_report_providers import ACTOR, configured, generation as generation, offline as offline


def virtual_clock(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(service, 'BATCH_CHARACTERS', 1)
    monkeypatch.setattr(service, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(engine, 'monotonic', lambda: clock[0])
    return clock


def test_report_environment_budget_is_separate_from_generic_budget(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'test')
    monkeypatch.setenv('AUTH_MODE', 'password')
    monkeypatch.setenv('AGENT_INFERENCE_TOTAL_SECONDS', '45')
    monkeypatch.setenv('AGENT_INFERENCE_PLATFORM_SECONDS', '12')
    monkeypatch.delenv('WORK_REPORT_INFERENCE_TOTAL_SECONDS', raising=False)
    monkeypatch.delenv('WORK_REPORT_INFERENCE_PLATFORM_SECONDS', raising=False)
    defaults = get_settings.__wrapped__()
    assert (defaults.work_report_inference_total_seconds, defaults.work_report_inference_platform_seconds) == (600, 300)
    monkeypatch.setenv('WORK_REPORT_INFERENCE_TOTAL_SECONDS', '25')
    monkeypatch.setenv('WORK_REPORT_INFERENCE_PLATFORM_SECONDS', '3')
    explicit = get_settings.__wrapped__()
    scoped = subject._report_settings(explicit)
    assert (scoped.agent_inference_total_seconds, scoped.agent_inference_platform_seconds) == (25, 3)
    assert (explicit.agent_inference_total_seconds, explicit.agent_inference_platform_seconds) == (45, 12)


@pytest.mark.asyncio
async def test_same_multibatch_request_exceeds_generic_45_but_completes_with_report_600(monkeypatch):
    clock = virtual_clock(monkeypatch)
    active = AsyncMock()
    phases = []

    async def slow(envelope):
        clock[0] += 20
        phases.append(envelope['generation']['phase'])
        return answer(envelope)

    request = GenerationInput(**request_data())
    providers = GenerationProviders(direct=slow)
    with pytest.raises(GenerationStateUnavailable):
        await generate_work_report(request, providers=providers, assert_active=active, routing_policy=RoutingPolicy())
    assert clock[0] == 60
    clock[0], phases[:] = 0, []
    active.reset_mock()
    original = configured(bound=False)
    transport = subject.ReportTransports(None, ACTOR, original, assert_active=active)
    result = await generate_work_report(request, providers=providers, assert_active=active,
                                        routing_policy=transport.policy)
    validate_generation_result(result, request)
    assert result.status == 'generated' and result.trace.elapsed_ms == 60000
    assert result.trace.chunks_completed == result.trace.chunks_total == 3
    assert phases == ['summarize', 'summarize', 'combine']
    assert active.await_count > len(phases)
    assert (original.agent_inference_total_seconds, original.agent_inference_platform_seconds) == (45, 12)


@pytest.mark.asyncio
@pytest.mark.parametrize('override', [False, True])
async def test_report_entry_passes_effective_budget_to_actual_platform_runtime(generation, monkeypatch, override):
    settings, _, loader, _, _ = generation
    settings = replace(settings, agent_fde_base_url='https://invalid.invalid/v1',
                       agent_fde_work_report_id='synthetic', agent_fde_work_report_api_key='app-synthetic')
    if override:
        settings = apply_execution_policy(settings, {'code': 'agent_execution.work_report',
            'definition': {'override_budget': True, 'total_seconds': 25, 'platform_seconds': 3}})
    loader.return_value = RuntimeConfiguration(settings, {})
    observed = []

    def runtime(database, effective, **kwargs):
        actual = filtered_facts_runtime(database, effective, **kwargs)
        observed.append((effective.agent_inference_total_seconds, actual.config.timeout_seconds))
        # No platform network call: inspect the actual constructor then use the
        # unbound report's real direct route with the fixture's synthetic client.
        return actual

    monkeypatch.setattr(subject, 'filtered_facts_runtime', runtime)
    result = await subject.generate_report(GenerationInput(**request_data()), database=object(), actor=ACTOR,
                                          settings=settings, assert_active=AsyncMock())
    assert result.status == 'generated'
    assert observed == ([(25, 3)] if override else [(600, 300)])
    assert loader.await_args.args[2] is settings
    if not override:
        assert (settings.agent_inference_total_seconds, settings.agent_inference_platform_seconds) == (45, 12)


@pytest.mark.asyncio
@pytest.mark.parametrize('budget_source', ['report_configuration', 'published_policy'])
async def test_explicit_low_budget_still_discards_overdue_report(generation, monkeypatch, budget_source):
    settings, _, loader, client, audit = generation
    clock = virtual_clock(monkeypatch)
    if budget_source == 'published_policy':
        settings = apply_execution_policy(settings, {'code': 'agent_execution.work_report',
            'definition': {'override_budget': True, 'total_seconds': 25, 'platform_seconds': 3}})
    else:
        settings = replace(settings, work_report_inference_total_seconds=25,
                           work_report_inference_platform_seconds=3)
    loader.return_value = RuntimeConfiguration(settings, {})
    original = client.chat_json

    async def slow(**kwargs):
        clock[0] += 30
        return await original(**kwargs)

    client.chat_json = slow
    with pytest.raises(GenerationStateUnavailable):
        await subject.generate_report(GenerationInput(**request_data()), database=object(), actor=ACTOR,
                                      settings=settings, assert_active=AsyncMock())
    assert clock[0] == 30 and len(client.envelopes) == 1
    assert audit.finish.await_args.args == ('failed',)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [WorkReportError('WORK_REPORT_PERMISSION_CHANGED', 'revoked', status=403),
                                   asyncio.CancelledError()])
async def test_report_budget_does_not_admit_output_after_revocation_or_cancellation(generation, failure):
    settings, _, _, client, audit = generation
    finished = False
    original = client.chat_json

    async def returned(**kwargs):
        nonlocal finished
        result = await original(**kwargs)
        finished = True
        return result

    async def active():
        if finished:
            raise failure

    client.chat_json = returned
    with pytest.raises(type(failure)):
        await subject.generate_report(GenerationInput(**request_data()), database=object(), actor=ACTOR,
                                      settings=settings, assert_active=active)
    assert len(client.envelopes) == 1
    assert audit.finish.await_args.args == ('cancelled' if isinstance(failure, asyncio.CancelledError) else 'failed',)


@pytest.mark.parametrize('total,platform', [(0, 1), (float('inf'), 1), (20, 20)])
def test_invalid_report_budget_remains_rejected(total, platform):
    settings = replace(configured(), work_report_inference_total_seconds=total,
                       work_report_inference_platform_seconds=platform)
    transport = subject.ReportTransports(None, ACTOR, settings, assert_active=AsyncMock())
    with pytest.raises(ValueError):
        transport.policy
