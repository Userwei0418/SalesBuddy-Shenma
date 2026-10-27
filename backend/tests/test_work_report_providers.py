"""Offline transport/accounting doubles, never live model or RLS evidence."""
import asyncio
import json
import socket
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest

from sales_backend.config import Settings
from sales_backend.contracts.work_report import GenerationInput
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.services import work_report_providers as subject
from sales_backend.services.agent_platform.routing import ProviderUnavailable, WriteState
from sales_backend.services.runtime_config import RuntimeConfiguration
from sales_backend.services.work_report_generation.engine import GenerationStateUnavailable
from tests.test_work_report_contract import U1, U3, request_data
from tests.test_work_report_generation import answer


ACTOR = ActorContext(workspace_id=U1, user_id=U1, role='sales', data_scope='self', team_ids=())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('network/database forbidden in synthetic work-report tests')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(asyncpg, 'connect', blocked)
    monkeypatch.setattr(asyncpg, 'create_pool', blocked)
    monkeypatch.setattr(httpx.AsyncClient, 'send', blocked)


def configured(*, bound=True):
    binding = {U1: {'work_report': dict(enabled=True, agent_id='work-report-test', expected_snapshot_id=U3,
                                        execution_mode='filtered_facts')}} if bound else {}
    return Settings(app_env='test', database_url='postgresql://invalid.invalid/never',
                    senseaudio_base_url='https://invalid.invalid', senseaudio_api_key='synthetic',
                    asr_model='synthetic', tts_model='synthetic', llm_model='synthetic', timeout_seconds=1,
                    max_retries=0, access_token_secret='synthetic', access_token_issuer='synthetic',
                    access_token_audience='synthetic', access_token_minutes=1, refresh_token_days=1,
                    auth_mode='test', demo_workspace='synthetic', database_min_pool_size=1,
                    database_max_pool_size=1, worker_poll_seconds=1, worker_lock_seconds=30,
                    worker_id='synthetic', agent_platform_bindings_json=json.dumps(binding))


class Client:
    def __init__(self, result=None):
        self.envelopes = []
        self.result = result
        self.close = AsyncMock()

    async def chat_json(self, *, messages, temperature):
        envelope = json.loads(messages[1].content)
        assert messages[0].content == envelope['system_prompt'] and temperature == .1
        self.envelopes.append(envelope)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result if self.result is not None else answer(envelope)


class Runtime:
    def __init__(self):
        self.requests, self.operations = [], []
        self.observer_factory = lambda *a, **kw: SimpleNamespace(start=AsyncMock(), prepare_dispatch=AsyncMock(), finish=AsyncMock())

    def operation(self, request):
        self.requests.append(request)
        operation = SimpleNamespace(invoke=AsyncMock(return_value={'synthetic': 'platform'}),
                                     seal=AsyncMock(return_value=WriteState.NONE))
        self.operations.append(operation)
        return operation


def envelope():
    return dict(capability='work_report', contract_version='work_report.v1', system_prompt='private instructions',
                prompt_version='work_report.v1', input={'sources': [{'text': '中文 material'}]},
                generation={'phase': 'batch', 'batch_index': 1, 'batches_total': 1}, output_contract={})


@pytest.mark.asyncio
async def test_routes_preserve_same_complete_envelope_and_separate_batch_receipts():
    client, runtime, active = Client(result={'synthetic': 'direct'}), Runtime(), AsyncMock()
    observer_args = []
    def observer(*args, **kwargs):
        observer_args.append(kwargs)
        return SimpleNamespace()
    adapter = subject.ReportTransports(None, ACTOR, configured(), assert_active=active, runtime=runtime,
                                       client_factory=lambda *a, **kw: client, observer_factory=observer)
    original = envelope()
    before = deepcopy(original)
    await adapter.platform(original)
    assert await adapter.seal() is WriteState.NONE
    await adapter.platform(original)
    await adapter.seal()
    await adapter.direct(original)
    assert original == before
    assert client.envelopes[0] == runtime.requests[0].input == {**before, 'mode': 'work_report'}
    assert runtime.requests[0].operation_id != runtime.requests[1].operation_id
    assert runtime.requests[0].capability == 'work_report' and runtime.requests[0].run_id is None
    assert runtime.requests[0].actor == ACTOR and 'run_id' not in observer_args[0]
    assert active.await_count == 6
    client.close.assert_awaited_once()


@pytest.mark.parametrize('bound,runtime,policy,reason', [
    (False, False, {}, 'platform_unconfigured'),
    (True, False, {}, 'platform_transport_unavailable'),
    (True, True, {'definition': {'strategy': 'direct_only'}}, 'platform_policy_direct_only'),
])
def test_absent_or_disabled_work_report_platform_never_aliases_another_agent(bound, runtime, policy, reason):
    settings = replace(configured(bound=bound), agent_execution_policy=policy)
    adapter = subject.ReportTransports(None, ACTOR, settings, assert_active=AsyncMock(), runtime=Runtime() if runtime else None)
    assert adapter.reason == reason and adapter.providers.platform is None and not adapter.policy.platform_enabled


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [SenseAudioError('private response body'), PermissionError('revoked'), asyncio.CancelledError()])
async def test_direct_transport_closes_and_only_maps_transport_failures(error):
    client = Client(error)
    adapter = subject.ReportTransports(None, ACTOR, configured(), assert_active=AsyncMock(),
                                       client_factory=lambda *a, **kw: client, observer_factory=lambda *a, **kw: object())
    expected = ProviderUnavailable if isinstance(error, SenseAudioError) else type(error)
    with pytest.raises(expected) as captured:
        await adapter.direct(envelope())
    if isinstance(error, SenseAudioError):
        assert 'private' not in str(captured.value)
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_internal_direct_retries_recheck_authority_before_dispatch():
    active = AsyncMock(side_effect=[None, None, WorkReportError('WORK_REPORT_RUN_TERMINAL', 'cancelled')])
    started = AsyncMock(return_value='receipt')
    observer = SimpleNamespace(start=started, prepare_dispatch=AsyncMock(), finish=AsyncMock())
    client = Client({'ok': True})
    async def chat_json(**kwargs):
        await client.observer.start('endpoint', 'model', {}, 1)
        await client.observer.prepare_dispatch('receipt')
        raise AssertionError('must never dispatch after cancelled')
    client.chat_json = chat_json
    def factory(*args, **kwargs):
        client.observer = kwargs['observer']
        return client
    adapter = subject.ReportTransports(None, ACTOR, configured(), assert_active=active,
                                       client_factory=factory, observer_factory=lambda *a, **kw: observer)
    with pytest.raises(WorkReportError):
        await adapter.direct(envelope())
    started.assert_awaited_once()
    observer.prepare_dispatch.assert_not_awaited()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_timeout_preserves_authority_error_instead_of_allowing_fallback():
    client = Client(PermissionError('revoked'))
    client.close.side_effect = TimeoutError('cleanup failed')
    adapter = subject.ReportTransports(None, ACTOR, configured(), assert_active=AsyncMock(),
                                       client_factory=lambda *a, **kw: client, observer_factory=lambda *a, **kw: object())
    with pytest.raises(PermissionError, match='revoked'):
        await adapter.direct(envelope())


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [PermissionError('revoked'), TimeoutError('private database address')])
async def test_platform_accounting_cannot_relabel_authority_failure_as_fallback(error):
    runtime = Runtime()
    active = AsyncMock(side_effect=[None, error])
    adapter = subject.ReportTransports(None, ACTOR, configured(), assert_active=active, runtime=runtime)
    original = runtime.operation
    def operation(request):
        op = original(request)
        async def invoke():
            observer = runtime.observer_factory(None)
            try:
                await observer.start('endpoint', 'model', {}, 1)
            except Exception:
                raise ProviderUnavailable('legacy accounting wrapper') from None
        op.invoke = invoke
        return op
    runtime.operation = operation
    with pytest.raises(GenerationStateUnavailable if isinstance(error, TimeoutError) else PermissionError):
        await adapter.platform(envelope())
    await adapter.seal()
    assert len(runtime.operations) == 1


@pytest.fixture
def generation(monkeypatch):
    settings = configured(bound=False)
    runtime = RuntimeConfiguration(settings, {})
    loader = AsyncMock(return_value=runtime)
    monkeypatch.setattr(subject, 'load_runtime_configuration', loader)
    monkeypatch.setattr(subject, 'filtered_facts_runtime', lambda *a, **kw: None)
    client = Client()
    monkeypatch.setattr(subject, 'SenseAudioClient', lambda *a, **kw: client)
    monkeypatch.setattr(subject, 'DatabaseModelObserver', lambda *a, **kw: object())
    audit = SimpleNamespace(start=AsyncMock(), event=lambda *a, **kw: None, finish=AsyncMock())
    monkeypatch.setattr(subject, 'InferenceAudit', lambda *a, **kw: audit)
    return settings, runtime, loader, client, audit


@pytest.mark.asyncio
async def test_real_generation_engine_uses_company_text_and_preserves_no_binding_reason(generation):
    settings, _, loader, client, audit = generation
    result = await subject.generate_report(GenerationInput(**request_data()), database=object(), actor=ACTOR,
                                           settings=settings, assert_active=AsyncMock())
    assert result.status == 'generated' and result.provider == 'direct'
    assert result.trace.fallback_reason == 'platform_unconfigured'
    assert len(client.envelopes) == 1 and client.envelopes[0]['mode'] == 'work_report'
    assert all(call.kwargs == {'capability': 'work_report', 'purpose': 'text'} for call in loader.await_args_list)
    assert audit.finish.await_args.args == ('succeeded',)


@pytest.mark.asyncio
async def test_configuration_revocation_before_dispatch_is_not_basic_summary(generation):
    settings, runtime, loader, client, audit = generation
    loader.side_effect = [runtime, RuntimeConfiguration(replace(settings, llm_model='replacement'), {})]
    with pytest.raises(WorkReportError):
        await subject.generate_report(GenerationInput(**request_data()), database=object(), actor=ACTOR,
                                      settings=settings, assert_active=AsyncMock())
    assert client.envelopes == []
    assert audit.finish.await_args.args == ('failed',)


@pytest.mark.asyncio
async def test_empty_material_invokes_no_provider_and_records_truthful_status(generation):
    settings, _, loader, client, _ = generation
    loader.side_effect = RuntimeError('no configured model')
    data = request_data()
    data.update(sources=[], supplements=[], metrics=[])
    data['coverage'][0].update(total=0, included=0)
    result = await subject.generate_report(GenerationInput(**data), database=object(), actor=ACTOR,
                                           settings=settings, assert_active=AsyncMock())
    assert result.status == 'insufficient_data' and result.provider == 'none'
    assert client.envelopes == []
    loader.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_platform', [False, True])
async def test_generation_router_uses_work_report_binding_and_seals_before_fallback(generation, monkeypatch, fail_platform):
    _, _, loader, client, _ = generation
    settings = configured()
    loader.return_value = RuntimeConfiguration(settings, {})
    runtime = Runtime()
    original = runtime.operation
    def operation(request):
        op = original(request)
        if fail_platform:
            op.invoke.side_effect = ProviderUnavailable('synthetic failure')
        else:
            op.invoke.return_value = answer(request.input)
        return op
    runtime.operation = operation
    monkeypatch.setattr(subject, 'filtered_facts_runtime', lambda *a, **kw: runtime)
    result = await subject.generate_report(GenerationInput(**request_data()), database=object(), actor=ACTOR,
                                           settings=settings, assert_active=AsyncMock())
    assert result.provider == ('direct' if fail_platform else 'agent_platform')
    assert result.status == 'generated'
    runtime.operations[0].seal.assert_awaited_once()
    if fail_platform:
        assert client.envelopes[0] == runtime.requests[0].input
        assert result.trace.fallback_reason == 'provider_unavailable'
    else:
        assert client.envelopes == []
