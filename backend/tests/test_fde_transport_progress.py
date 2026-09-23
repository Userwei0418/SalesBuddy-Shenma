"""Distinguish waiting for headers, SSE bytes and answer text without logging content."""

import asyncio
from dataclasses import asdict

import httpx
import pytest

from sales_backend.integrations.supreme_fde import FdeClient, FdeConfig, FdeError
from sales_backend.services.agent_platform.fde_invocation import FdeJsonInvocation
from sales_backend.services.agent_platform.routing import ProviderUnavailable
from tests.test_supreme_fde import SSE_HEADERS, Chunks, frame


def client(stream=None, *, handler=None):
    async def respond(request):
        return httpx.Response(200, headers=SSE_HEADERS, stream=stream)

    return FdeClient(
        FdeConfig("https://platform.invalid/v1", "app-synthetic-fixture", "agent_final", timeout_seconds=0.03),
        transport=httpx.MockTransport(handler or respond),
    )


@pytest.mark.asyncio
async def test_no_headers_is_distinct_from_http_200_with_no_body():
    async def never_headers(request):
        await asyncio.Event().wait()

    async with client(handler=never_headers) as wire:
        invocation = FdeJsonInvocation(wire, "synthetic", "synthetic-user")
        with pytest.raises(ProviderUnavailable):
            await invocation()
    assert invocation.transport.request_started_ms is not None
    assert invocation.transport.headers_received_ms is None
    assert invocation.http_status is None
    assert invocation.error_code == "timeout"

    stream = Chunks([], stall=True)
    async with client(stream) as wire:
        invocation = FdeJsonInvocation(wire, "synthetic", "synthetic-user")
        with pytest.raises(ProviderUnavailable):
            await invocation()
    assert invocation.transport.headers_received_ms is not None
    assert invocation.transport.first_body_byte_ms is None
    assert invocation.http_status == 200  # Header status survives the later timeout.
    assert invocation.result is None and stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix,event_observed",
    [
        (b": keepalive\n\n", False),
        (frame("ping"), True),
        (frame("agent_thought", answer="PRIVATE-CONTENT", observation="PRIVATE-CONTENT"), True),
    ],
)
async def test_keepalive_or_thought_is_not_answer_progress(prefix, event_observed):
    stream = Chunks([prefix], stall=True)
    async with client(stream) as wire:
        invocation = FdeJsonInvocation(wire, "synthetic", "synthetic-user")
        task = asyncio.create_task(invocation())
        await stream.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    telemetry = asdict(invocation.transport)
    assert telemetry["http_status"] == 200
    assert telemetry["first_body_byte_ms"] is not None
    assert (telemetry["first_data_event_ms"] is not None) == event_observed
    assert telemetry["first_text_ms"] is telemetry["stream_completed_ms"] is None
    assert "PRIVATE-CONTENT" not in str(telemetry)
    assert stream.closed


@pytest.mark.asyncio
async def test_complete_stream_has_ordered_milestones_without_answer_or_identity():
    stream = Chunks(
        [
            frame("agent_message", answer=""),
            frame("agent_thought", answer="PRIVATE-CONTENT"),
            frame("agent_message", answer='{"ok":'),
            frame("agent_message", answer="true}"),
            frame("message", answer='{"ok":true}'),
            frame("message_end"),
        ]
    )
    events = []
    async with client(stream) as wire:
        result = await wire.chat(query="PRIVATE-CONTENT", user="private-user", inputs={}, on_progress=events.append)
    assert result.json_object() == {"ok": True}
    final = asdict(events[-1])
    timestamps = [value for key, value in final.items() if key.endswith("_ms")]
    assert all(type(value) is int and value >= 0 for value in timestamps)
    assert timestamps == sorted(timestamps)
    assert events[0].headers_received_ms is None  # Previously emitted snapshots remain immutable.
    assert "private-user" not in str(final) and "PRIVATE-CONTENT" not in str(final)
    assert set(final) == {
        "http_status",
        "request_started_ms",
        "headers_received_ms",
        "first_body_byte_ms",
        "first_data_event_ms",
        "first_text_ms",
        "stream_completed_ms",
    }


@pytest.mark.asyncio
async def test_invalid_stream_can_have_http_200_without_completion():
    stream = Chunks([b"data: \xff\n\n"])
    events = []
    async with client(stream) as wire:
        with pytest.raises(FdeError):
            await wire.chat(query="synthetic", user="synthetic-user", inputs={}, on_progress=events.append)
    assert events[-1].http_status == 200
    assert events[-1].first_body_byte_ms is not None
    assert events[-1].first_data_event_ms is None
    assert events[-1].stream_completed_ms is None
