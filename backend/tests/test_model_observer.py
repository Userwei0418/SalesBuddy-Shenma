from dataclasses import replace
import httpx
import pytest
from sales_backend.domain.agent import ChatMessage
from sales_backend.integrations.model_observer import response_usage
from sales_backend.integrations.senseaudio import SenseAudioClient, SenseAudioError
from tests.test_senseaudio import settings


class Observer:
    def __init__(self):
        self.started = []
        self.finished = []

    async def start(self, endpoint, model, metadata, attempt):
        self.started.append((endpoint, model, metadata, attempt))
        return str(len(self.started))

    async def finish(self, invocation_id, response, error):
        self.finished.append((invocation_id, response_usage(response), error))


@pytest.mark.asyncio
async def test_every_actual_retry_is_observed_and_keeps_its_returned_usage(monkeypatch):
    observer = Observer()
    attempt = 0

    async def handler(request):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(503, json={"error": "busy", "usage": {"prompt_tokens": 8, "completion_tokens": 0}})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            },
        )

    async def no_sleep(delay):
        pass

    monkeypatch.setattr("sales_backend.integrations.senseaudio.asyncio.sleep", no_sleep)
    http = httpx.AsyncClient(base_url="https://api.senseaudio.cn", transport=httpx.MockTransport(handler))
    client = SenseAudioClient(replace(settings(), max_retries=1), client=http, observer=observer)
    try:
        assert await client.chat_json(messages=[ChatMessage(role="user", content="private user text")]) == {"ok": True}
    finally:
        await http.aclose()
    assert len(observer.started) == len(observer.finished) == 2
    assert observer.started[0][2] == {"message_count": 1}
    assert [x[1]["input_tokens"] for x in observer.finished] == [8, 10]
    assert observer.finished[0][2] and observer.finished[1][2] is None


@pytest.mark.asyncio
async def test_auth_failure_is_one_attempt_and_unknown_usage_stays_unknown():
    observer = Observer()
    async with httpx.AsyncClient(
        base_url="https://api.senseaudio.cn",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "unauthorized"})),
    ) as http:
        client = SenseAudioClient(replace(settings(), max_retries=3), client=http, observer=observer)
        with pytest.raises(SenseAudioError):
            await client.chat_json(messages=[ChatMessage(role="user", content="check")])
    assert len(observer.started) == len(observer.finished) == 1
    assert observer.finished[0][1]["input_tokens"] is None
    assert observer.finished[0][1]["output_tokens"] is None


@pytest.mark.parametrize("value", [None, True, -1, "100", 1.5])
def test_usage_never_fabricates_provider_counters(value):
    usage = response_usage(httpx.Response(200, json={"usage": {"prompt_tokens": value}}))
    assert usage["input_tokens"] is None and usage["output_tokens"] is None
