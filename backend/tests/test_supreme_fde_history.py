"""History observations cannot turn an empty/late response into accepted output."""

import asyncio
import json

import httpx
import pytest

from sales_backend.integrations import supreme_fde as fde
from sales_backend.services.agent_platform.fde_invocation import FdeJsonInvocation
from sales_backend.services.agent_platform.routing import ProviderUnavailable


def row(message_id="message-1", **changes):
    return {
        "id": message_id,
        "conversation_id": "conversation-1",
        "status": "normal",
        "error": None,
        "answer": '{"summary":"synthetic"}',
        "retriever_resources": [],
        "agent_thoughts": [{"thought": "DO NOT RETAIN"}],
        "inputs": {"secret": "DO NOT RETAIN"},
        **changes,
    }


def make_client(payload, *, status=200, timeout=1):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(status, json=payload)

    client = fde.FdeClient(
        fde.FdeConfig("https://platform.invalid/v1", "app-synthetic-history", "agent_final", timeout),
        transport=httpx.MockTransport(handler),
    )
    return client, requests


async def history(client, **changes):
    return await client.history_page(**{"conversation_id": "conversation-1", "user": "crm:user-1", **changes})


@pytest.mark.asyncio
async def test_history_matches_message_id_not_array_position_and_preserves_page_boundary():
    client, requests = make_client({"data": [row("message-other"), row()], "has_more": True, "limit": 20})
    async with client:
        page = await history(client, first_id="older-message")
    message = page.find("message-1")
    assert message is page.messages[1]
    assert message.json_object() == {"summary": "synthetic"}
    assert message.ids == fde.RunIds(message_id="message-1", conversation_id="conversation-1")
    assert page.has_more is True and page.find("missing") is None
    assert "synthetic" not in repr(page) and "DO NOT RETAIN" not in repr(page)
    assert set(vars(message)) == {"ids", "status", "answer", "error_present", "retriever_resources_count"}
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET" and request.url.path == "/v1/messages"
    assert dict(request.url.params) == {
        "conversation_id": "conversation-1", "user": "crm:user-1", "limit": "20", "first_id": "older-message"
    }
    assert request.headers["authorization"] == "Bearer app-synthetic-history"
    assert "app-synthetic-history" not in str(request.url) and not request.content


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"answer": ""}, {"answer": "  \n"}, {"error": "upstream private error"}, {"status": "stopped"}
])
async def test_normal_status_does_not_make_empty_or_errored_history_usable(changes):
    client, _ = make_client({"data": [row(**changes)], "has_more": False})
    async with client:
        message = (await history(client)).find("message-1")
    with pytest.raises(fde.FdeError, match="history_answer_unavailable") as caught:
        message.json_object()
    assert caught.value.ids.message_id == "message-1"
    assert "upstream private error" not in repr(message) + str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ['{"a":1,"a":2}', '{"a":NaN}', '[]', 'plain text'])
async def test_history_json_has_the_same_unambiguous_object_requirement(answer):
    client, _ = make_client({"data": [row(answer=answer)], "has_more": False})
    async with client:
        message = (await history(client)).find("message-1")
    with pytest.raises(fde.FdeError):
        message.json_object()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"data": [], "has_more": 1},
    {"data": {}, "has_more": False},
    {"data": [row(), row()], "has_more": False},
    {"data": [row(conversation_id="another-user-conversation")], "has_more": False},
    {"data": [row(retriever_resources=None)], "has_more": False},
    {"data": [row(answer=None)], "has_more": False},
    {"data": [row(status=None)], "has_more": False},
    {"data": [row(error={"secret": "not a string"})], "has_more": False},
    {"data": [row(id="bad/id")], "has_more": False},
    {"data": [None], "has_more": False},
])
async def test_malformed_or_mixed_identity_history_is_rejected(payload):
    client, _ = make_client(payload)
    async with client:
        with pytest.raises(fde.FdeError, match="invalid_history"):
            await history(client)


@pytest.mark.asyncio
async def test_response_cannot_exceed_requested_page_size():
    client, _ = make_client({"data": [row(), row("message-2")], "has_more": True})
    async with client:
        with pytest.raises(fde.FdeError, match="invalid_history"):
            await history(client, limit=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": "20"},
    {"user": "crm:user&admin=true"}, {"conversation_id": ""}, {"first_id": "a&user=other"}
])
async def test_invalid_lookup_is_rejected_before_network(changes):
    client, requests = make_client({"data": [], "has_more": False})
    async with client:
        with pytest.raises(ValueError):
            await history(client, **changes)
    assert requests == []


@pytest.mark.asyncio
async def test_cross_user_404_is_not_converted_into_empty_success():
    client, requests = make_client({"message": "private upstream body"}, status=404)
    async with client:
        with pytest.raises(fde.FdeError) as caught:
            await history(client)
    assert caught.value.status == 404 and len(requests) == 1
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_history_response_is_bounded_and_failure_is_not_retried():
    client, requests = make_client({"data": [row(answer="x" * fde.MAX_FRAME_CHARS)], "has_more": False})
    async with client:
        with pytest.raises(fde.FdeError, match="json_response_too_large"):
            await history(client)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_history_timeout_is_bounded_and_has_no_retry():
    requests = []

    async def handler(request):
        requests.append(request)
        await asyncio.Event().wait()

    client = fde.FdeClient(
        fde.FdeConfig("https://platform.invalid/v1", "app-synthetic-history", "agent_final", .01),
        transport=httpx.MockTransport(handler),
    )
    async with client:
        with pytest.raises(fde.FdeError, match="timeout"):
            await history(client)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_inspection_preserves_original_invocation_and_never_adopts_late_answer():
    requests = []
    ids = {"task_id": "task-1", "message_id": "message-1", "conversation_id": "conversation-1"}

    async def handler(request):
        requests.append(request)
        if request.method == "POST":
            event = {"event": "error", **ids, "message": "synthetic failure"}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=("data: " + json.dumps(event) + "\n\n").encode())
        return httpx.Response(200, json={"data": [row()], "has_more": False})

    client = fde.FdeClient(
        fde.FdeConfig("https://platform.invalid/v1", "app-synthetic-history", "agent_final"),
        transport=httpx.MockTransport(handler),
    )
    invocation = FdeJsonInvocation(client, "synthetic", "crm:original-user")
    async with client:
        assert await invocation.history() is None and requests == []
        with pytest.raises(ProviderUnavailable):
            await invocation()
        page = await invocation.history(first_id="older-message")
        assert page.find(invocation.ids.message_id).json_object() == {"summary": "synthetic"}
        assert invocation.result is None and invocation.error_code == "stream_error"
        with pytest.raises(RuntimeError, match="cannot be replayed"):
            await invocation()
    assert [r.method for r in requests] == ["POST", "GET"]
    assert requests[1].url.params["user"] == "crm:original-user"
    assert requests[1].url.params["conversation_id"] == "conversation-1"
