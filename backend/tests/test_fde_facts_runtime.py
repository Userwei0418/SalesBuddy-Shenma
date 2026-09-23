"""Real FDE parser/routing with synthetic HTTP, no live secrets or database."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sales_backend.config import get_settings
from sales_backend.domain.agent import ActorContext
from sales_backend.integrations.supreme_fde import FdeClient, FdeConfig, FdeResult, RunIds
from sales_backend.services.agent_platform.fde_facts_runtime import FdeFactsRuntime, filtered_facts_runtime
from sales_backend.services.agent_platform.inference import AgentBinding, InferenceService, PlatformRequest, binding_for
from sales_backend.services.agent_platform.result_contracts import validate_run_result
from sales_backend.services.agent_platform.routing import ProviderUnavailable, WriteState

ACTOR = ActorContext(
    workspace_id="11111111-1111-4111-8111-111111111111", user_id="22222222-2222-4222-8222-222222222222",
    role="sales", data_scope="self",
)
SNAPSHOT = "33333333-3333-4333-8333-333333333333"
AGENT = "synthetic-fde-agent"
KEY = "app-synthetic-fde-key"
ANSWER = {"summary": "合成数据：3个任务", "metrics": [{"label": "任务", "value": "3个"}], "rows": []}
FACTS = {"summary": {"open_tasks": 3}, "data_as_of": "2026-09-11T00:00:00Z"}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts, *, stall=False):
        self.parts, self.stall, self.closed = parts, stall, False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for part in self.parts:
            yield part
        if self.stall:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def frame(kind, *, answer=None):
    data = {"event": kind, "task_id": "task-1", "message_id": "message-1", "conversation_id": "conversation-1"}
    if answer is not None:
        data["answer"] = answer
    return ("data: " + json.dumps(data) + "\n\n").encode()


def configured(**kwargs):
    binding = {ACTOR.workspace_id: {"chatbi": {
        "enabled": True, "execution_mode": "filtered_facts", "agent_id": AGENT,
        "expected_snapshot_id": SNAPSHOT,
    }}}
    return replace(get_settings(), **{
        "agent_fde_base_url": "https://platform.invalid/v1", "agent_fde_chatbi_id": AGENT,
        "agent_fde_chatbi_api_key": KEY, "senseaudio_api_key": "", "database_url": "",
        "agent_platform_bindings_json": json.dumps(binding), **kwargs,
    })


def request(actor=ACTOR, **kwargs):
    return PlatformRequest(
        "44444444-4444-4444-8444-444444444444", "chatbi", AgentBinding(AGENT, SNAPSHOT, "filtered_facts"),
        actor, {"mode": "chatbi", "facts": FACTS, "user_text": "合成问题"}, **kwargs,
    )


def setup(stream=None, *, status=200, stop_status=200, timeout=1):
    requests, clients, observations = [], [], []
    stream = stream if stream is not None else Chunks([
        frame("message", answer=json.dumps(ANSWER)), frame("message_end"),
    ])

    async def handler(req):
        requests.append(req)
        if req.url.path.endswith("/stop"):
            return httpx.Response(stop_status, json={"result": "success"})
        return httpx.Response(status, headers={"content-type": "text/event-stream"}, stream=stream)

    def client_factory(config):
        client = FdeClient(config, transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    def observer_factory(*args, **kwargs):
        observer = SimpleNamespace(start=AsyncMock(return_value="attempt-1"), finish=AsyncMock(), context=kwargs)
        observations.append(observer)
        return observer

    runtime = FdeFactsRuntime(
        None, FdeConfig("https://platform.invalid/v1", KEY, "agent_final", timeout_seconds=timeout),
        agent_id=AGENT, client_factory=client_factory, observer_factory=observer_factory,
    )
    direct = SimpleNamespace(chat_json=AsyncMock(return_value=ANSWER.copy()), close=AsyncMock())
    inference = InferenceService(
        None, configured(), platform=runtime, direct_factory=lambda *a, **k: direct,
    )
    inference.assert_actor_current = AsyncMock()
    return SimpleNamespace(
        runtime=runtime, inference=inference, direct=direct, requests=requests, clients=clients,
        observations=observations, stream=stream,
    )


async def evaluate(test, *, actor=ACTOR, mode="chatbi"):
    run = SimpleNamespace(actor=actor, mode=mode)
    return await test.inference.evaluate(
        actor=actor, mode=mode, facts=FACTS, messages=[], user_text="合成问题",
        validate=lambda value: validate_run_result(run, value, FACTS),
        run_id="55555555-5555-4555-8555-555555555555",
    )


@pytest.mark.asyncio
async def test_controlled_request_block_uses_fallback_without_claiming_remote_http_failure():
    test = setup()
    test.runtime.block_requests = True
    result = await evaluate(test)
    assert result.trace["provider"] == "senseaudio"
    assert result.trace["fallback_reason"] == "ControlledPlatformBlock"
    assert result.trace["platform_run"]["network_dispatch_suppressed"] is True
    assert not test.requests
    test.direct.chat_json.assert_awaited_once()
    observer = test.observations[0]
    assert observer.start.call_args.args[2]["test_fault_injected"] is True
    assert observer.finish.call_args.args[1] is None
    assert type(observer.finish.call_args.args[2]).__name__ == "ControlledPlatformBlock"


@pytest.mark.parametrize("field", ["agent_fde_base_url", "agent_fde_chatbi_id", "agent_fde_chatbi_api_key"])
def test_missing_configuration_disables_runtime_without_io(field):
    settings = configured(**{field: ""})
    assert filtered_facts_runtime(None, settings) is None


@pytest.mark.parametrize("url", ["http://platform.invalid/v1", "bad-url", "https://user:secret@platform.invalid/v1"])
def test_bad_fde_configuration_keeps_existing_worker_usable(url):
    assert filtered_facts_runtime(None, configured(agent_fde_base_url=url)) is None


def test_runtime_uses_per_run_settings_and_explicit_test_fault():
    db = SimpleNamespace(settings=configured())
    normal = filtered_facts_runtime(db, db.settings)
    blocked = filtered_facts_runtime(db, db.settings, block_requests=True)
    assert isinstance(normal, FdeFactsRuntime)
    assert normal.block_requests is False
    assert blocked.block_requests is True
    assert KEY not in repr(db.settings)


@pytest.mark.parametrize("mode", ["customer_profile", "management_task"])
def test_filtered_facts_cannot_relax_other_capabilities(mode):
    data = json.loads(configured().agent_platform_bindings_json)
    data[ACTOR.workspace_id][mode] = data[ACTOR.workspace_id].pop("chatbi")
    assert binding_for(json.dumps(data), ACTOR.workspace_id, mode) is None


@pytest.mark.parametrize("change", [
    {"capability": "battle_map_review"},
    {"binding": AgentBinding("different-agent", SNAPSHOT, "filtered_facts")},
    {"binding": AgentBinding(AGENT, SNAPSHOT)},
    {"input": {"mode": "visit_entry"}},
])
def test_runtime_rejects_wrong_agent_mode_or_pinned_claim_before_io(change):
    test = setup()
    with pytest.raises(ProviderUnavailable):
        test.runtime.operation(replace(request(), **change))
    assert not test.clients and not test.observations


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chatbi", "customer_chatbi"])
async def test_filtered_facts_uses_real_parser_and_common_business_contract(mode):
    test = setup()
    result = await evaluate(test, mode=mode)
    assert result.payload == {**ANSWER, "scope": "本人", "data_as_of": FACTS["data_as_of"]}
    assert result.trace["provider"] == "agent_platform"
    assert result.trace["model_ref"] == f"agent-platform:{AGENT}"
    trace = result.trace["platform_run"]
    assert trace["expected_snapshot_id"] == SNAPSHOT and trace["actual_snapshot_id"] is None
    assert trace["runtime_snapshot_verified"] is trace["tool_authority_issued"] is False
    assert trace["ids"]["task_id"] == "task-1"
    test.direct.chat_json.assert_not_called()
    assert test.stream.closed and len(test.requests) == 1
    test.inference.assert_actor_current.assert_awaited()
    wire = json.loads(test.requests[0].content)
    assert wire["inputs"] == {} and wire["conversation_id"] == ""
    query = json.loads(wire["query"])
    assert query["facts"] == FACTS and query["mode"] == mode
    assert KEY not in wire["query"] and "snapshot" not in wire["query"]
    assert wire["user"].startswith("sales:") and ACTOR.user_id not in wire["user"]
    observer = test.observations[0]
    assert observer.context["provider"] == "agent_platform"
    assert observer.context["run_id"] == "55555555-5555-4555-8555-555555555555"
    observer.start.assert_awaited_once()
    observer.finish.assert_awaited_once()
    metadata = observer.start.await_args.args[2]
    assert metadata["record_semantics"] == "platform_api_attempt_not_downstream_model_count"
    assert KEY not in json.dumps(metadata) and "合成问题" not in json.dumps(metadata)
    response = observer.finish.await_args.args[1]
    assert response.json()["usage"] == {"prompt_tokens": None, "completion_tokens": None}


@pytest.mark.asyncio
async def test_opaque_platform_identity_includes_workspace_role_scope_and_teams():
    actors = [ACTOR, ACTOR.model_copy(update={"workspace_id": SNAPSHOT}),
              ActorContext(**{**ACTOR.model_dump(), "role": "manager", "data_scope": "workspace"}),
              ACTOR.model_copy(update={"team_ids": (SNAPSHOT,)})]
    users = []
    for actor in actors:
        test = setup()
        op = test.runtime.operation(request(actor))
        await op.invoke()
        await op.seal()
        users.append(json.loads(test.requests[0].content)["user"])
    assert len(set(users)) == len(actors)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "json", "schema", "timeout", "stop_failure"])
async def test_platform_failures_use_original_direct_flow_without_replay(failure):
    stream = None
    if failure in {"json", "schema"}:
        stream = Chunks([frame("message", answer="not JSON" if failure == "json" else '{}'), frame("message_end")])
    elif failure in {"timeout", "stop_failure"}:
        stream = Chunks([frame("agent_message", answer="partial")], stall=True)
    test = setup(
        stream, status=503 if failure == "http" else 200,
        stop_status=500 if failure == "stop_failure" else 200, timeout=.03,
    )
    result = await evaluate(test)
    assert result.trace["provider"] == "senseaudio" and result.payload["summary"] == ANSWER["summary"]
    test.direct.chat_json.assert_awaited_once()
    test.direct.close.assert_awaited_once()
    assert sum(r.url.path == "/v1/chat-messages" for r in test.requests) == 1
    assert test.stream.closed and len(test.observations) == 1
    test.observations[0].finish.assert_awaited_once()
    if failure == "stop_failure":
        assert result.trace["platform_run"]["stop_state"] == "unconfirmed"


@pytest.mark.asyncio
async def test_observer_start_failure_prevents_dispatch_and_falls_back():
    test = setup()
    observer = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("db unavailable")), finish=AsyncMock())
    test.runtime.observer_factory = lambda *a, **k: observer
    result = await evaluate(test)
    assert result.trace["provider"] == "senseaudio" and not test.requests
    observer.finish.assert_not_called()


@pytest.mark.asyncio
async def test_late_remote_answer_after_timeout_cannot_replace_direct_result():
    release, completed = asyncio.Event(), asyncio.Event()
    test = setup()

    class StubbornClient:
        async def chat(self, **kwargs):
            kwargs["on_ids"](RunIds("task-1", "message-1", "conversation-1"))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                completed.set()
                return FdeResult(json.dumps(ANSWER), RunIds("task-1", "message-1", "conversation-1"), None, None)

        async def stop(self, **kwargs):
            raise RuntimeError("remote stop cannot be confirmed")

        async def close(self):
            pass

    test.runtime.client_factory = lambda *a: StubbornClient()
    test.inference.settings = replace(test.inference.settings, agent_inference_platform_seconds=.02)
    try:
        result = await asyncio.wait_for(evaluate(test), .5)
        assert result.trace["provider"] == "senseaudio" and not completed.is_set()
    finally:
        release.set()
        await asyncio.wait_for(completed.wait(), .5)
        await asyncio.sleep(0)
    test.direct.chat_json.assert_awaited_once()
    assert result.trace["provider"] == "senseaudio"
    assert isinstance(test.observations[0].finish.await_args.args[2], ProviderUnavailable)


@pytest.mark.asyncio
async def test_cancelled_business_run_does_not_start_direct_inference():
    test = setup(Chunks([frame("agent_message", answer="partial")], stall=True))
    task = asyncio.create_task(evaluate(test))
    await asyncio.wait_for(test.stream.waiting.wait(), .5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    test.direct.chat_json.assert_not_called()
    assert test.stream.closed


@pytest.mark.asyncio
async def test_seal_is_idempotent_and_prevents_dispatch_even_before_start():
    test = setup()
    operation = test.runtime.operation(request())
    assert await operation.seal() is await operation.seal() is WriteState.NONE
    with pytest.raises(ProviderUnavailable):
        await operation.invoke()
    assert not test.requests and not test.observations


def test_new_diagnostics_cannot_become_user_visible_output():
    private = {key: "forged" for key in (
        "execution_mode", "expected_snapshot_id", "actual_snapshot_id", "runtime_snapshot_verified",
        "platform_run", "operation_id", "tool_authority_issued",
    )}
    result = validate_run_result(SimpleNamespace(mode="chatbi", actor=ACTOR), {**ANSWER, **private}, FACTS)
    assert not private.keys() & result.keys()
