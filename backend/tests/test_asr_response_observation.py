"""ASR semantic validation belongs to the observed HTTP attempt."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sales_backend.config import Settings
from sales_backend.integrations.circuit_breaker import GatewayCircuitBreaker
from sales_backend.integrations.senseaudio import (
    EmptyTranscriptionError,
    InvalidTranscriptionError,
    SenseAudioClient,
)

pytestmark = pytest.mark.asyncio


def asr_settings():
    return Settings(
        app_env='test', database_url='postgresql://test', senseaudio_base_url='https://test.invalid',
        senseaudio_api_key='synthetic-key', asr_model='asr', tts_model='tts', llm_model='llm',
        timeout_seconds=1, max_retries=2,
        access_token_secret='synthetic-secret-at-least-thirty-two-characters',
        access_token_issuer='test', access_token_audience='test', access_token_minutes=30,
        refresh_token_days=1, auth_mode='demo', demo_workspace='test',
        database_min_pool_size=1, database_max_pool_size=2, worker_poll_seconds=.01,
        worker_lock_seconds=90, worker_id='test',
    )


@pytest.mark.parametrize('custom', [False, True])
@pytest.mark.parametrize('body,error_class', [
    ({'text': ''}, EmptyTranscriptionError), ({'text': ' \n\t'}, EmptyTranscriptionError),
    ({}, InvalidTranscriptionError), ({'text': None}, InvalidTranscriptionError),
    ({'text': 123}, InvalidTranscriptionError), ([], InvalidTranscriptionError),
    ({'text': 'bad\x00text'}, InvalidTranscriptionError),
    ({'text': '\u200b\ufeff'}, EmptyTranscriptionError),
])
async def test_http_200_empty_or_invalid_is_observed_failed_without_retry(body, error_class, custom):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    settings = asr_settings()
    if custom:
        settings = replace(settings, model_api_endpoint='https://test.invalid/asr',
                           model_api_metadata={'connection_purpose': 'asr', 'connection_mode': 'custom'})
    observer = SimpleNamespace(start=AsyncMock(return_value='synthetic'), finish=AsyncMock())
    async with httpx.AsyncClient(base_url='https://test.invalid', transport=httpx.MockTransport(handler)) as http:
        client = SenseAudioClient(settings, client=http, observer=observer,
                                  breaker=GatewayCircuitBreaker(failure_threshold=3, recovery_seconds=60))
        with pytest.raises(error_class):
            await client.transcribe(filename='x.wav', content=b'PCM', mime_type='audio/wav')
    assert len(requests) == 1
    response, error = observer.finish.await_args.args[1:]
    assert response.status_code == 200 and isinstance(error, error_class)


async def test_valid_result_is_observed_success_with_original_trace():
    observer = SimpleNamespace(start=AsyncMock(return_value='synthetic'), finish=AsyncMock())
    async with httpx.AsyncClient(base_url='https://test.invalid', transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={'text': ' 正常文字 ', 'duration': 3.2}, headers={'x-trace-id': 'trace'})
    )) as http:
        client = SenseAudioClient(asr_settings(), client=http, observer=observer)
        result = await client.transcribe(filename='x.wav', content=b'PCM', mime_type='audio/wav')
    assert result.text == '正常文字' and result.trace_id == 'trace' and result.duration_seconds == 3.2
    assert observer.finish.await_args.args[2] is None


async def test_non_json_response_is_recorded_as_invalid_not_success():
    observer = SimpleNamespace(start=AsyncMock(return_value='synthetic'), finish=AsyncMock())
    async with httpx.AsyncClient(base_url='https://test.invalid', transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b'<html>invalid</html>')
    )) as http:
        client = SenseAudioClient(asr_settings(), client=http, observer=observer)
        with pytest.raises(InvalidTranscriptionError):
            await client.transcribe(filename='x.wav', content=b'PCM', mime_type='audio/wav')
    assert isinstance(observer.finish.await_args.args[2], InvalidTranscriptionError)
    assert observer.finish.await_count == 1


@pytest.mark.parametrize('failure', ['timeout', 'busy'])
async def test_transient_provider_failure_retries_with_separate_observed_outcomes(failure):
    calls = []
    observer = SimpleNamespace(start=AsyncMock(side_effect=['first', 'second']), finish=AsyncMock())

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            if failure == 'timeout':
                raise httpx.ReadTimeout('synthetic', request=request)
            return httpx.Response(503, json={'message': 'busy'})
        return httpx.Response(200, json={'text': 'recovered'})

    async with httpx.AsyncClient(base_url='https://test.invalid', transport=httpx.MockTransport(handler)) as http:
        client = SenseAudioClient(asr_settings(), client=http, observer=observer,
                                  breaker=GatewayCircuitBreaker(failure_threshold=3, recovery_seconds=60))
        result = await client.transcribe(filename='x.wav', content=b'PCM', mime_type='audio/wav')
    assert len(calls) == 2 and result.text == 'recovered'
    assert observer.finish.await_args_list[0].args[2].retryable
    assert observer.finish.await_args_list[1].args[2] is None
