"""Synthetic HTTP/SSE fixtures only; no platform credentials or model requests."""

import asyncio
import json

import httpx
import pytest

from sales_backend.integrations import supreme_fde as fde

SYNTHETIC_KEY = "app-synthetic-fixture"
IDS = {"task_id": "task-1", "message_id": "message-1", "conversation_id": "conversation-1"}
SSE_HEADERS = {"content-type": "text/event-stream; charset=utf-8"}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts, *, stall=False, failure=None):
        self.parts, self.stall, self.failure = parts, stall, failure
        self.closed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for part in self.parts:
            yield part
        if self.failure:
            raise self.failure
        if self.stall:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def frame(kind, *, answer=None, newline="\n", **extra):
    event = {"event": kind, **IDS, **extra}
    if answer is not None:
        event["answer"] = answer
    return ("data: " + json.dumps(event, ensure_ascii=False) + newline * 2).encode()


def config(protocol="text_deltas", **kwargs):
    return fde.FdeConfig("https://platform.invalid/v1", SYNTHETIC_KEY, protocol, **kwargs)


def client_for(stream, *, protocol="text_deltas", status=200, headers=None, timeout=1):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(status, headers=SSE_HEADERS if headers is None else headers, stream=stream)

    client = fde.FdeClient(config(protocol, timeout_seconds=timeout), transport=httpx.MockTransport(handler))
    return client, requests


async def chat(client, **kwargs):
    return await client.chat(query="合成测试", user="crm:synthetic-user", inputs={}, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("chunk_size", [1, 2, 7, 100000])
async def test_sse_boundaries_and_utf8_are_independent_of_network_chunks(newline, chunk_size):
    wire = (
        b"\xef\xbb\xbf: keepalive\r\n\r\n"
        + frame("message", answer='{"回答":"中', newline=newline)
        + frame("message", answer='文"}', newline=newline)
        + frame("message_end", newline=newline)
    )
    stream = Chunks([wire[i : i + chunk_size] for i in range(0, len(wire), chunk_size)])
    client, requests = client_for(stream)
    async with client:
        result = await chat(client)
    assert result.json_object() == {"回答": "中文"}
    assert result.ids == fde.RunIds(**IDS)
    assert result.input_tokens is result.output_tokens is None
    assert stream.closed and len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://platform.invalid/v1/chat-messages"
    assert request.headers["Authorization"] == f"Bearer {SYNTHETIC_KEY}"
    assert json.loads(request.content) == {
        "query": "合成测试",
        "user": "crm:synthetic-user",
        "inputs": {},
        "conversation_id": "",
        "response_mode": "streaming",
        "auto_generate_name": False,
    }
    assert SYNTHETIC_KEY not in request.content.decode()


@pytest.mark.asyncio
async def test_agent_final_is_authoritative_and_not_appended_to_deltas():
    stream = Chunks(
        [
            frame("agent_message", answer='{"answer":"中文"}'),
            frame("agent_thought", answer="DO NOT EXPOSE", tool_input="private", observation="private"),
            frame("message", answer='{"answer":"中文"}'),
            frame("message_end", metadata={"usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
        ]
    )
    client, _ = client_for(stream, protocol="agent_final")
    async with client:
        result = await chat(client)
    assert result.json_object() == {"answer": "中文"}
    assert (result.input_tokens, result.output_tokens) == (5, 3)
    assert "中文" not in repr(result)  # No response text in default diagnostics.


@pytest.mark.asyncio
async def test_documented_text_deltas_profile_accepts_both_text_event_names():
    stream = Chunks(
        [
            frame("message", answer='{"answer":'),
            frame("agent_message", answer='"synthetic"}'),
            frame("message_end"),
        ]
    )
    client, _ = client_for(stream)
    async with client:
        assert (await chat(client)).json_object() == {"answer": "synthetic"}


@pytest.mark.asyncio
async def test_multiline_data_comments_event_ids_and_ping_do_not_enter_answer():
    data = b'data: {"event":"message",\r\ndata: "answer":"{}"}\r\n\r\n'
    stream = Chunks(
        [
            b": comment\nretry: 1\nid: ignored\nevent: ping\n\n",
            frame("ping"),
            data,
            frame("message_end"),
        ]
    )
    client, _ = client_for(stream)
    async with client:
        assert (await chat(client)).json_object() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,parts,code",
    [
        ("agent_final", [frame("agent_message", answer="{}"), frame("message_end")], "missing_final_answer"),
        ("agent_final", [frame("message", answer="{}"), frame("message", answer="again")], "text_after_final"),
        ("agent_final", [frame("message", answer="{}"), frame("agent_message", answer="late")], "text_after_final"),
        ("text_deltas", [frame("message", answer="{}")], "incomplete_stream"),
        ("text_deltas", [frame("message", answer="{}"), frame("message_end")[:-1]], "incomplete_stream"),
        ("text_deltas", [frame("message", answer="{}"), frame("error", message="SECRET")], "stream_error"),
        ("text_deltas", [b'data: {"event":"message_end"}\n\n'], "missing_run_ids"),
        ("text_deltas", [frame("message_end")], "empty_answer"),
        ("text_deltas", [frame("message", answer=123)], "invalid_answer"),
        ("text_deltas", [b"data: [1,2]\n\n"], "invalid_event"),
        ("text_deltas", [b"data: not-json SECRET\n\n"], "invalid_json"),
        ("text_deltas", [b"data: \xff\n\n"], "invalid_utf8"),
        ("text_deltas", [b"data: \xe4"], "invalid_utf8"),
        (
            "text_deltas",
            [frame("message", answer="{}"), frame("message_end", message_id="foreign")],
            "run_identity_changed",
        ),
        ("text_deltas", [frame("message", answer="{}", task_id="invalid/SECRET")], "invalid_run_id"),
    ],
)
async def test_stream_failures_never_return_partial_success_or_retry(protocol, parts, code):
    stream = Chunks(parts)
    client, requests = client_for(stream, protocol=protocol)
    async with client:
        with pytest.raises(fde.FdeError) as caught:
            await chat(client)
    assert caught.value.code == code and caught.value.dispatch_started
    assert "SECRET" not in str(caught.value)
    assert stream.closed and len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 307, 400, 401, 403, 404, 429, 500])
async def test_http_errors_are_not_followed_retried_or_body_logged(status):
    stream = Chunks([b"SECRET upstream diagnostic app-synthetic-fixture"])
    client, requests = client_for(stream, status=status, headers={"location": "https://foreign.invalid/key"})
    async with client:
        with pytest.raises(fde.FdeError) as caught:
            await chat(client)
    assert caught.value.status == status and caught.value.code == "http_error"
    assert "SECRET" not in str(caught.value) and SYNTHETIC_KEY not in str(caught.value)
    assert len(requests) == 1 and stream.closed


@pytest.mark.asyncio
async def test_http_200_html_is_not_a_success():
    stream = Chunks([b"private error"])
    client, _ = client_for(stream, headers={"content-type": "text/html"})
    async with client:
        with pytest.raises(fde.FdeError, match="unexpected_content_type"):
            await chat(client)
    assert stream.closed


@pytest.mark.asyncio
async def test_timeout_retains_ids_without_replaying_request():
    stream = Chunks([frame("message", answer="{}")], stall=True)
    client, requests = client_for(stream, timeout=0.02)
    ids = []
    async with client:
        with pytest.raises(fde.FdeError) as caught:
            await chat(client, on_ids=ids.append)
    assert caught.value.code == "timeout"
    assert caught.value.ids == fde.RunIds(**IDS) and caught.value.dispatch_started
    assert ids == [fde.RunIds(**IDS)]
    assert stream.closed and len(requests) == 1


@pytest.mark.asyncio
async def test_external_cancellation_closes_stream_and_preserves_tracked_ids():
    stream = Chunks([frame("message", answer="{}")], stall=True)
    client, requests = client_for(stream)
    ids = []
    async with client:
        task = asyncio.create_task(chat(client, on_ids=ids.append))
        await asyncio.wait_for(stream.waiting.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert ids == [fde.RunIds(**IDS)] and stream.closed and len(requests) == 1


@pytest.mark.asyncio
async def test_network_error_keeps_ids_and_does_not_include_request_details():
    stream = Chunks([frame("message", answer="{}")], failure=httpx.ReadError("SECRET"))
    client, requests = client_for(stream)
    async with client:
        with pytest.raises(fde.FdeError) as caught:
            await chat(client)
    assert caught.value.code == "transport_error" and caught.value.ids == fde.RunIds(**IDS)
    assert "SECRET" not in str(caught.value) and stream.closed and len(requests) == 1


@pytest.mark.asyncio
async def test_continuation_must_keep_the_server_bound_conversation():
    stream = Chunks([frame("message", answer="{}")])
    client, requests = client_for(stream)
    async with client:
        with pytest.raises(fde.FdeError, match="run_identity_changed"):
            await chat(client, conversation_id="different-conversation")
    assert json.loads(requests[0].content)["conversation_id"] == "different-conversation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit,value,wire,code",
    [
        ("MAX_STREAM_BYTES", 1000, b"x" * 1001, "stream_too_large"),
        ("MAX_FRAME_CHARS", 10, b"x" * 11, "frame_too_large"),
        ("MAX_ANSWER_CHARS", 1, frame("message", answer="{}"), "answer_too_large"),
        ("MAX_EVENTS", 1, frame("ping") * 2, "too_many_events"),
    ],
)
async def test_stream_limits_are_enforced_before_unbounded_buffering(monkeypatch, limit, value, wire, code):
    monkeypatch.setattr(fde, limit, value)
    stream = Chunks([wire])
    client, _ = client_for(stream)
    async with client:
        with pytest.raises(fde.FdeError, match=code):
            await chat(client)
    assert stream.closed


@pytest.mark.asyncio
async def test_parameters_uses_same_app_key_and_preserves_declared_schema():
    payload = {"user_input_form": [{"text-input": {"variable": "customer_name", "required": True}}]}
    stream = Chunks([json.dumps(payload).encode()])
    client, requests = client_for(stream, headers={"content-type": "application/json"})
    async with client:
        assert await client.parameters() == payload
    assert requests[0].method == "GET" and requests[0].url.path == "/v1/parameters"
    assert requests[0].headers["Authorization"] == f"Bearer {SYNTHETIC_KEY}"
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"[]", b"{}", b'{"user_input_form":{}}', b"SECRET", b"\xff"])
async def test_parameters_rejects_malformed_configuration(body):
    client, _ = client_for(Chunks([body]), headers={"content-type": "application/json"})
    async with client:
        with pytest.raises(fde.FdeError):
            await client.parameters()


@pytest.mark.parametrize("answer", [
    "[]", "NaN", '{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}',
    '```json\n{"x":1,"x":2}\n```', '```json\n{"x":NaN}\n```',
    '```json\n[]\n```', '```python\n{}\n```', '```json\n{}',
    '说明\n```json\n{}\n```', '```json\n{}\n```\n解释',
    '```json\n{}\n```\n```json\n{}\n```', '{}\n{}',
])
def test_business_json_requires_an_unambiguous_finite_object(answer):
    result = fde.FdeResult(answer, fde.RunIds(**IDS), None, None)
    with pytest.raises(fde.FdeError) as caught:
        result.json_object()
    assert caught.value.ids == fde.RunIds(**IDS)


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "```\n{}\n```", " \n```JSON\r\n{}\r\n```\n"])
def test_business_json_accepts_one_complete_fence_without_changing_values(wrapper):
    payload = {"text": "客户原文\n有换行、空格  和```标记", "score": 55, "passed": False}
    answer = wrapper.format(json.dumps(payload, ensure_ascii=False))
    result = fde.FdeResult(answer, fde.RunIds(**IDS), None, None)
    assert result.json_object() == payload


@pytest.mark.parametrize(
    "base",
    [
        "http://host/v1",
        "https://user:SECRET@host/v1",
        "https://host/v1?key=SECRET",
        "https://host/v1#SECRET",
        "https://host/other",
        "https://host\\evil/v1",
        "https://host:bad/v1",
    ],
)
def test_unsafe_endpoints_rejected_without_echoing_them(base):
    with pytest.raises(ValueError) as caught:
        fde.FdeConfig(base, SYNTHETIC_KEY, "text_deltas")
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("key", ["", "fde_cli_synthetic", "model-key", "app-key\r\nX: secret"])
def test_management_and_model_keys_are_not_runtime_credentials(key):
    with pytest.raises(ValueError, match="application runtime key"):
        fde.FdeConfig("https://platform.invalid/v1", key, "text_deltas")
    assert SYNTHETIC_KEY not in repr(config())


@pytest.mark.asyncio
async def test_invalid_request_makes_no_http_attempt():
    client, requests = client_for(Chunks([]))
    async with client:
        for inputs in ({"bad": float("nan")}, {"bad": object()}):
            with pytest.raises(ValueError, match="finite JSON"):
                await client.chat(query="测试", user="crm:user", inputs=inputs)
        with pytest.raises(ValueError):
            await client.chat(query="测试", user="model supplied / user", inputs={})
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [None, True, -1, "3", 2.5])
async def test_invalid_or_missing_usage_stays_unknown(count):
    client, _ = client_for(
        Chunks(
            [
                frame("message", answer="{}"),
                frame("message_end", metadata={"usage": {"prompt_tokens": count, "completion_tokens": count}}),
            ]
        )
    )
    async with client:
        result = await chat(client)
    assert result.input_tokens is result.output_tokens is None


@pytest.mark.asyncio
async def test_stop_sends_original_identity_once_without_claiming_no_writes():
    client, requests = client_for(Chunks([b'{"result":"success"}']), headers={"content-type": "application/json"})
    async with client:
        ack = await client.stop(task_id="task-1", user="crm:synthetic-user")
    assert ack.acknowledged and ack.task_id == "task-1"
    assert not hasattr(ack, "write_state")
    assert len(requests) == 1 and requests[0].method == "POST"
    assert requests[0].url.path == "/v1/chat-messages/task-1/stop"
    assert json.loads(requests[0].content) == {"user": "crm:synthetic-user"}


@pytest.mark.asyncio
@pytest.mark.parametrize("task_id", ["..", ".", "task/other", "task?key=wrong", ""])
async def test_stop_rejects_path_injection_before_dispatch(task_id):
    client, requests = client_for(Chunks([]))
    async with client:
        with pytest.raises(ValueError):
            await client.stop(task_id=task_id, user="crm:user")
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"{}", b'{"result":false}', b'{"result":"not-found"}'])
async def test_stop_http_success_without_success_body_is_not_acknowledgment(payload):
    client, requests = client_for(Chunks([payload]), headers={"content-type": "application/json"})
    async with client:
        with pytest.raises(fde.FdeError, match="stop_not_acknowledged"):
            await client.stop(task_id="task-1", user="crm:user")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_stop_timeout_is_bounded_and_never_retried():
    stream = Chunks([], stall=True)
    client, requests = client_for(stream, timeout=0.01, headers={"content-type": "application/json"})
    async with client:
        with pytest.raises(fde.FdeError, match="timeout"):
            await client.stop(task_id="task-1", user="crm:user")
    assert len(requests) == 1 and stream.closed
