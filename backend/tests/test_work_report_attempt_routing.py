"""Report-local routing and progress windows, using synthetic providers only."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.work_report import GenerationInput
from sales_backend.domain.work_report import validate_generation_result
from sales_backend.services import work_report_providers as adapter
from sales_backend.services.agent_platform.routing import (
    InvalidAgentResult, ProviderUnavailable, ReconciliationRequired, RoutingPolicy, WriteState,
)
from sales_backend.services.runtime_config import RuntimeConfiguration, apply_execution_policy
from sales_backend.services.work_report_generation import GenerationProviders, engine, generate_work_report, service
from sales_backend.services.work_report_generation.engine import GenerationStateUnavailable
from tests.test_work_report_contract import request_data
from tests.test_work_report_generation import answer
from tests.test_work_report_providers import ACTOR, generation, offline


def four_batches(monkeypatch):
    monkeypatch.setattr(service, 'BATCH_CHARACTERS', 1)
    data = request_data()
    original = data['sources'][0]
    data['sources'] = []
    data['supplements'] = []
    for i in range(10, 14):
        row = deepcopy(original)
        row.update(record_id=str(UUID(int=i)), source_id='visit:' + str(UUID(int=i)))
        data['sources'].append(row)
    data['coverage'][0].update(total=4, included=4)
    return GenerationInput(**data)


def virtual_clock(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(service, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(engine, 'monotonic', lambda: clock[0])
    return clock


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [TimeoutError, ProviderUnavailable])
async def test_safely_failed_platform_is_skipped_for_remaining_batches_and_combine(monkeypatch, failure):
    request, clock = four_batches(monkeypatch), virtual_clock(monkeypatch)
    phases = []

    async def platform(envelope):
        clock[0] += 60
        raise failure('synthetic')

    async def direct(envelope):
        clock[0] += 15
        phases.append(envelope['generation']['phase'])
        return answer(envelope)

    primary, fallback = AsyncMock(side_effect=platform), AsyncMock(side_effect=direct)
    seal = AsyncMock(return_value=WriteState.NONE)
    providers = GenerationProviders(fallback, primary, seal)
    policy = RoutingPolicy(platform_enabled=True, total_seconds=240, platform_seconds=60)
    result = await generate_work_report(request, providers=providers, assert_active=AsyncMock(), routing_policy=policy)
    validate_generation_result(result, request)
    assert result.status == 'generated' and result.trace.elapsed_ms == 135000
    assert result.trace.fallback_reason == 'batch_fallback'
    assert result.trace.chunks_completed == result.trace.chunks_total == 5
    assert phases == ['summarize'] * 4 + ['combine']
    assert primary.await_count == seal.await_count == 1 and fallback.await_count == 5
    # The caller's policy and a fresh attempt retain platform priority.
    assert policy.platform_enabled
    await generate_work_report(request, providers=providers, assert_active=AsyncMock(), routing_policy=policy)
    assert primary.await_count == seal.await_count == 2 and fallback.await_count == 10


@pytest.mark.asyncio
async def test_progressing_report_can_cross_240_while_default_caller_keeps_total_cap(monkeypatch):
    request, clock = four_batches(monkeypatch), virtual_clock(monkeypatch)

    async def slow(envelope):
        clock[0] += 70
        return answer(envelope)

    policy = RoutingPolicy(total_seconds=240, platform_seconds=60)
    providers = GenerationProviders(AsyncMock(side_effect=slow))
    with pytest.raises(GenerationStateUnavailable):
        await generate_work_report(request, providers=providers, assert_active=AsyncMock(), routing_policy=policy)
    clock[0] = 0
    result = await generate_work_report(request, providers=providers, assert_active=AsyncMock(), routing_policy=policy,
                                        renew_budget_on_progress=True)
    assert result.status == 'generated' and result.trace.elapsed_ms == 350000
    assert result.trace.chunks_completed == 5
    validate_generation_result(result, request)


@pytest.mark.asyncio
async def test_invalid_content_does_not_disable_platform_for_later_batches(monkeypatch):
    request = four_batches(monkeypatch)
    calls = 0

    async def platform(envelope):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InvalidAgentResult('content varies by batch')
        return answer(envelope)

    fallback, seal = AsyncMock(side_effect=answer), AsyncMock(return_value=WriteState.NONE)
    result = await generate_work_report(request, providers=GenerationProviders(fallback, platform, seal),
        assert_active=AsyncMock(), routing_policy=RoutingPolicy(platform_enabled=True), renew_budget_on_progress=True)
    assert calls == seal.await_count == 5 and fallback.await_count == 1
    assert result.provider == 'agent_platform' and result.trace.fallback_reason == 'batch_fallback'


@pytest.mark.asyncio
@pytest.mark.parametrize('state', [WriteState.UNKNOWN, WriteState.DISPATCHED, WriteState.COMMITTED])
async def test_platform_without_seal_none_never_enters_direct_or_later_batch(monkeypatch, state):
    request = four_batches(monkeypatch)
    platform = AsyncMock(side_effect=TimeoutError('synthetic'))
    direct = AsyncMock(side_effect=answer)
    with pytest.raises(ReconciliationRequired):
        await generate_work_report(request, providers=GenerationProviders(direct, platform, AsyncMock(return_value=state)),
            assert_active=AsyncMock(), routing_policy=RoutingPolicy(platform_enabled=True), renew_budget_on_progress=True)
    platform.assert_awaited_once()
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_seal_never_enables_direct_progress(monkeypatch):
    request = four_batches(monkeypatch)
    direct = AsyncMock(side_effect=answer)
    with pytest.raises(ReconciliationRequired):
        await generate_work_report(request, providers=GenerationProviders(direct, AsyncMock(side_effect=TimeoutError()),
            AsyncMock(side_effect=TimeoutError())), assert_active=AsyncMock(),
            routing_policy=RoutingPolicy(platform_enabled=True), renew_budget_on_progress=True)
    direct.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [PermissionError('revoked'), asyncio.CancelledError()])
async def test_latched_direct_route_still_checks_authority_before_next_batch(monkeypatch, failure):
    request = four_batches(monkeypatch)
    stopped = False

    async def active():
        if stopped:
            raise failure

    def observe(event, provider, **details):
        nonlocal stopped
        if event == 'report_chunk_accepted':
            stopped = True

    direct, platform = AsyncMock(side_effect=answer), AsyncMock(side_effect=TimeoutError())
    with pytest.raises(type(failure)):
        await generate_work_report(request, providers=GenerationProviders(direct, platform, AsyncMock(return_value=WriteState.NONE)),
            assert_active=active, routing_policy=RoutingPolicy(platform_enabled=True),
            renew_budget_on_progress=True, observer=observe)
    platform.assert_awaited_once()
    direct.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_progress_times_out_and_cannot_renew_itself(monkeypatch):
    request = four_batches(monkeypatch)
    cancelled = asyncio.Event()

    async def stuck(envelope):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(GenerationStateUnavailable):
        await asyncio.wait_for(generate_work_report(request, providers=GenerationProviders(stuck),
            assert_active=AsyncMock(), routing_policy=RoutingPolicy(total_seconds=.15, platform_seconds=.005, receipt_seconds=.001),
            renew_budget_on_progress=True), timeout=1)
    await asyncio.wait_for(cancelled.wait(), timeout=.2)


@pytest.mark.asyncio
async def test_late_platform_output_after_safe_fallback_is_discarded(monkeypatch):
    request = four_batches(monkeypatch)
    release, completed = asyncio.Event(), asyncio.Event()
    events = []

    async def late(envelope):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            return answer(envelope)
        finally:
            completed.set()

    direct, platform = AsyncMock(side_effect=answer), AsyncMock(side_effect=late)
    try:
        result = await generate_work_report(request,
            providers=GenerationProviders(direct, platform, AsyncMock(return_value=WriteState.NONE)),
            assert_active=AsyncMock(), routing_policy=RoutingPolicy(platform_enabled=True, total_seconds=1,
                platform_seconds=.04, receipt_seconds=.01), renew_budget_on_progress=True,
            observer=lambda event, provider, **kw: events.append((event, provider)))
        assert result.status == 'generated' and direct.await_count == 5
        platform.assert_awaited_once()
    finally:
        release.set()
        await asyncio.wait_for(completed.wait(), timeout=.2)
        await asyncio.sleep(0)
    assert ('contract_accepted', 'agent_platform') not in events


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit_company_cap', [False, True])
async def test_report_entry_opts_into_progress_except_explicit_company_cap(generation, monkeypatch, explicit_company_cap):
    request, clock = four_batches(monkeypatch), virtual_clock(monkeypatch)
    settings, _, loader, client, audit = generation
    if explicit_company_cap:
        settings = apply_execution_policy(settings, {'code': 'agent_execution.work_report',
            'definition': {'override_budget': True, 'total_seconds': 25, 'platform_seconds': 3}})
    loader.return_value = RuntimeConfiguration(settings, {})
    original = client.chat_json

    async def slow(**kwargs):
        clock[0] += 20 if explicit_company_cap else 70
        return await original(**kwargs)

    client.chat_json = slow
    if explicit_company_cap:
        with pytest.raises(GenerationStateUnavailable):
            await adapter.generate_report(request, database=object(), actor=ACTOR, settings=settings, assert_active=AsyncMock())
        assert clock[0] == 40 and audit.finish.await_args.args == ('failed',)
    else:
        result = await adapter.generate_report(request, database=object(), actor=ACTOR, settings=settings, assert_active=AsyncMock())
        assert result.status == 'generated' and result.trace.elapsed_ms == 350000
        assert result.trace.chunks_completed == 5


@pytest.mark.asyncio
async def test_accepted_output_after_old_stage_deadline_cannot_extend_progress(monkeypatch):
    request, clock = four_batches(monkeypatch), virtual_clock(monkeypatch)

    async def overdue(envelope):
        clock[0] += 241
        return answer(envelope)

    direct = AsyncMock(side_effect=overdue)
    with pytest.raises(GenerationStateUnavailable):
        await generate_work_report(request, providers=GenerationProviders(direct), assert_active=AsyncMock(),
            routing_policy=RoutingPolicy(total_seconds=240, platform_seconds=60), renew_budget_on_progress=True)
    direct.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize('mode, expected_calls', [('daily', 3), ('weekly', 5)])
@pytest.mark.parametrize('failure', [TimeoutError, ProviderUnavailable])
@pytest.mark.parametrize('renew', [False, True])
async def test_review_batches_keep_safe_direct_route_without_weakening_budget(
    monkeypatch, mode, expected_calls, failure, renew,
):
    from tests.test_work_report_review_generation import answer as review_answer
    from tests.test_work_report_review_generation import request as review_request

    monkeypatch.setattr(service, 'BATCH_CHARACTERS', 1)
    request, clock = review_request(mode), virtual_clock(monkeypatch)
    phases = []

    async def unavailable(envelope):
        clock[0] += 60
        raise failure('synthetic transport failure')

    async def direct(envelope):
        clock[0] += 80
        phases.append(envelope['generation']['phase'])
        return review_answer(envelope)

    platform, fallback = AsyncMock(side_effect=unavailable), AsyncMock(side_effect=direct)
    seal = AsyncMock(return_value=WriteState.NONE)
    policy = RoutingPolicy(platform_enabled=True, total_seconds=240, platform_seconds=60)
    invocation = generate_work_report(request, providers=GenerationProviders(fallback, platform, seal),
        assert_active=AsyncMock(), routing_policy=policy, renew_budget_on_progress=renew)
    if renew:
        result = await invocation
        validate_generation_result(result, request)
        assert result.status == 'generated' and result.provider == 'direct'
        assert result.trace.fallback_reason == 'batch_fallback'
        assert result.content.review_content is not None
        assert result.trace.chunks_completed == result.trace.chunks_total == expected_calls
        assert fallback.await_count == expected_calls
        assert phases == ['summarize'] * (expected_calls - 1) + ['combine']
        assert result.trace.elapsed_ms == (60 + 80 * expected_calls) * 1000
    else:
        # Explicit total caps reject an overdue accepted transport response,
        # including a final daily merge; no partial model result is published.
        with pytest.raises(GenerationStateUnavailable):
            await invocation
        assert fallback.await_count == 3 and clock[0] == 300
    platform.assert_awaited_once()
    seal.assert_awaited_once()
    assert policy.platform_enabled  # No cross-job circuit or configuration mutation.
