import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from sales_backend.config import get_settings
from sales_backend.domain.company_rules import policy_snapshot
from sales_backend.integrations.supreme_fde import FdeError, FdeResult, RunIds
from sales_backend.services import connectivity as module
from sales_backend.services.connectivity import ConnectivityService, present
from sales_backend.services.model_api import ModelApiError
from tests.test_model_api import ACTOR, FakeDB


def service():
    s = ConnectivityService(FakeDB(), get_settings())
    s.repo = SimpleNamespace(
        lock=AsyncMock(), get=AsyncMock(return_value=None), count_recent=AsyncMock(return_value=0), append=AsyncMock(),
        enqueue=AsyncMock(),
    )
    return s


@pytest.mark.asyncio
async def test_duplicate_probe_reuses_receipt_and_never_calls_provider():
    s = service()
    saved = {"id": str(uuid4()), "kind": "agent", "target": "customer_advice", "status": "passed"}
    s.repo.get.return_value = {"actor_user_ref_id": ACTOR.user_id, "after_snapshot": saved, "stale": False}
    s.agent = AsyncMock()
    assert await s.run(ACTOR, "agent", "customer_advice", saved["id"]) == saved
    s.agent.assert_not_called()
    with pytest.raises(ModelApiError):
        await s.run(ACTOR, "direct", "text", saved["id"])
    s.repo.get.return_value["actor_user_ref_id"] = str(uuid4())
    with pytest.raises(ModelApiError):
        await s.get(ACTOR, saved["id"])


@pytest.mark.asyncio
async def test_rate_limit_unknown_target_and_external_error_do_not_leak_secrets():
    s = service()
    with pytest.raises(ModelApiError):
        await s.run(ACTOR, "agent", "invented", uuid4())
    s.repo.count_recent.return_value = 5
    with pytest.raises(ModelApiError):
        await s.run(ACTOR, "direct", "text", uuid4())
    s.repo.count_recent.return_value = 0
    s.direct = AsyncMock(side_effect=ValueError("synthetic-secret-must-not-leak"))
    result = await s.run(ACTOR, "direct", "text", uuid4())
    assert result["status"] == "failed" and not result["fallback_used"]
    assert "synthetic-secret" not in json.dumps(result)
    assert s.repo.append.await_count == 2


def test_stale_probe_is_unknown_not_success_or_automatic_retry():
    result = present(
        {"actor_user_ref_id": ACTOR.user_id, "stale": True, "after_snapshot": {"status": "running"}}, ACTOR
    )
    assert result["status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "bad_json", "auth", "timeout", "required_input"])
async def test_agent_connectivity_distinguishes_auth_json_and_business_acceptance(monkeypatch, failure):
    s = service()
    agent_id, snapshot = str(uuid4()), str(uuid4())
    s.settings = replace(
        get_settings(),
        agent_fde_base_url="https://middle.example/v1",
        agent_fde_customer_advice_id=agent_id,
        agent_fde_customer_advice_api_key="app-synthetic-test",
        agent_platform_bindings_json=json.dumps(
            {
                ACTOR.workspace_id: {
                    "customer_advice": {
                        "enabled": True,
                        "agent_id": agent_id,
                        "expected_snapshot_id": snapshot,
                        "execution_mode": "filtered_facts",
                    }
                }
            }
        ),
    )
    monkeypatch.setattr(
        module.CompanyRulesRepository,
        "active",
        AsyncMock(return_value=policy_snapshot("agent_execution.customer_advice", None)),
    )
    params = {"user_input_form": [{"text-input": {"required": True}}] if failure == "required_input" else []}
    client = SimpleNamespace(parameters=AsyncMock(return_value=params), chat=AsyncMock(), close=AsyncMock())
    if failure == "auth":
        client.parameters.side_effect = FdeError("http_error", status=401)
    if failure == "timeout":
        client.chat.side_effect = httpx.ReadTimeout("synthetic-secret")
    else:
        client.chat.return_value = FdeResult(
            "not json" if failure == "bad_json" else '{"ok":true}', RunIds(), None, None
        )
    monkeypatch.setattr(module, "FdeClient", lambda *_: client)
    observer = SimpleNamespace(start=AsyncMock(return_value=str(uuid4())), finish=AsyncMock())
    monkeypatch.setattr(module, "DatabaseModelObserver", lambda *_, **__: observer)
    result = await s.run(ACTOR, "agent", "customer_advice", uuid4())
    assert result["status"] == "running" and result["execution_process"] == "worker"
    s.repo.enqueue.assert_awaited_once()
    client.parameters.assert_not_called()
    result = await s.execute(ACTOR, "agent", "customer_advice", result)
    assert not result["business_acceptance"] and not result["runtime_snapshot_verified"]
    assert result["agent_id"] == agent_id and result["expected_snapshot_id"] == snapshot
    assert not result["fallback_used"] and not result["business_writes"]
    assert "synthetic-secret" not in json.dumps(result)
    if failure == "auth":
        assert result["connection_status"] == "failed" and result["http_status"] == 401
        client.chat.assert_not_called()
    elif failure == "required_input":
        assert result["connection_status"] == "passed" and result["inference_status"] == "not_tested"
        client.chat.assert_not_called()
    else:
        assert result["connection_status"] == "passed"
        assert result["status"] == ("passed" if failure is None else "failed")
        assert client.chat.await_count == 1
        sent = json.loads(client.chat.call_args.kwargs["query"])
        assert sent["facts"] == {} and sent["connectivity_test"] is True
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_current_direct_test_loads_inherited_configuration_without_publishing(monkeypatch):
    s = service()
    monkeypatch.setattr(module.ModelApiRepository, "current", AsyncMock(return_value=None))
    cfg = {
        "mode": "inherit",
        "provider_name": "Fixture",
        "endpoint_url": "https://model.example/v1/chat/completions",
        "model": "fixture",
        "timeout_seconds": 10,
        "max_retries": 0,
    }
    monkeypatch.setattr(module.ModelApiService, "_legacy", AsyncMock(return_value=(cfg, "synthetic-key")))
    probe = AsyncMock()
    monkeypatch.setattr(module.ModelApiService, "_probe", probe)
    monkeypatch.setattr(module.ModelApiService, "publish", AsyncMock(side_effect=AssertionError("must not publish")))
    result = await s.run(ACTOR, "direct", "text", uuid4())
    assert result["status"] == "passed" and result["mode"] == "inherit" and result["version"] == 0
    assert probe.call_args.args[1].model_api_endpoint == cfg["endpoint_url"]
    assert "synthetic-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_disabled_direct_test_does_not_dispatch(monkeypatch):
    s = service()
    monkeypatch.setattr(
        module.ModelApiRepository,
        "current",
        AsyncMock(return_value={"config_snapshot": {"mode": "disabled"}, "version_no": 2}),
    )
    probe = AsyncMock()
    monkeypatch.setattr(module.ModelApiService, "_probe", probe)
    result = await s.run(ACTOR, "direct", "text", uuid4())
    assert result["status"] == "unavailable" and result["inference_status"] == "not_tested"
    probe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["revoked", "stale", "dispatched", "complete"])
async def test_worker_never_repeats_dispatch_or_uses_removed_administrator(monkeypatch, condition):
    s = service()
    saved = {"id": str(uuid4()), "kind": "agent", "target": "customer_advice",
             "status": "passed" if condition == "complete" else "running",
             "dispatch_started": condition == "dispatched"}
    s.repo.get.return_value = {"actor_user_ref_id": ACTOR.user_id, "after_snapshot": saved,
                               "stale": condition == "stale"}
    monkeypatch.setattr(module.IdentityRepository, "find_actor_by_id",
                        AsyncMock(return_value=None if condition == "revoked" else object()))
    s.agent = AsyncMock()
    result = await s.handle(ACTOR, saved["id"])
    assert result["status"] == ("passed" if condition == "complete" else "unavailable")
    s.agent.assert_not_called()
