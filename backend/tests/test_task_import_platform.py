"""Task import platform routing and draft-only retry, with synthetic HTTP and data."""

import copy
import json
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.config import get_settings
from sales_backend.contracts.task_imports import TaskImportPreview, TaskImportReparse
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.task_imports import candidate_lines, source_hash
from sales_backend.domain.tasks import TaskConflict
from sales_backend.repositories.task_imports import TaskImportRepository
from sales_backend.services import task_imports as service_module
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.inference import InferenceService, binding_for
from sales_backend.services.agent_platform.pilot import task_import_pilot_policy
from sales_backend.services.task_imports import TaskImportService, editable
from tests.test_fde_facts_runtime import ACTOR, AGENT, KEY, SNAPSHOT, Chunks, frame, setup

PAYLOAD = {"items": [{"source_line": 2, "description": "核对测试方案后发送报价"}]}
REQUEST = TaskImportPreview(
    source_text="张三：\n核对测试方案后发送报价", meeting_date=date(2026, 9, 28), batch_name="测试周会"
)
PEOPLE = [{"id": str(uuid4()), "name": "张三", "account_code": "ZHANGSAN"}]


def configured(*, pilot=True, direct_key=""):
    return replace(
        get_settings(),
        senseaudio_api_key=direct_key,
        agent_fde_base_url="https://platform.invalid/v1",
        agent_fde_task_import_id=AGENT,
        agent_fde_task_import_api_key=KEY,
        agent_platform_bindings_json=json.dumps(
            {
                ACTOR.workspace_id: {
                    "task_import": {
                        "enabled": True,
                        "execution_mode": "filtered_facts",
                        "agent_id": AGENT,
                        "expected_snapshot_id": SNAPSHOT,
                    }
                }
            }
        ),
        agent_fde_pilot_path="",
        agent_fde_pilot_json=json.dumps(
            {ACTOR.workspace_id: {"capabilities": {"task_import": {"enabled": pilot, "rollout": "production"}}}}
        ),
    )


def test_configuration_and_company_scope(monkeypatch):
    monkeypatch.setenv("AGENT_FDE_TASK_IMPORT_ID", AGENT)
    monkeypatch.setenv("AGENT_FDE_TASK_IMPORT_API_KEY", KEY)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.agent_fde_task_import_id == AGENT and settings.agent_fde_task_import_api_key == KEY
        assert KEY not in repr(settings)
    finally:
        get_settings.cache_clear()
    settings = configured()
    assert task_import_pilot_policy(settings, ACTOR) is not None
    assert task_import_pilot_policy(settings, ACTOR.model_copy(update={"workspace_id": str(uuid4())})) is None
    assert task_import_pilot_policy(configured(pilot=False), ACTOR) is None
    assert binding_for(settings.agent_platform_bindings_json, ACTOR.workspace_id, "task_import")
    adapter = filtered_facts_runtime(None, settings, capability="task_import")
    assert adapter.agent_id == AGENT and adapter.capability == "task_import"
    assert (
        filtered_facts_runtime(None, replace(settings, agent_fde_task_import_api_key=""), capability="task_import")
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "platform",
        "platform_failure",
        "no_platform",
        "invalid_contract",
        "all_failed",
        "all_failed_reparse",
        "quota",
    ],
)
async def test_extract_routes_and_validates_without_dispatch(monkeypatch, scenario):
    payload = copy.deepcopy(PAYLOAD)
    if scenario == "invalid_contract":
        payload["items"][0]["due_at"] = "2099-01-01"  # Agent cannot author dates.
    transport = setup(
        stream=Chunks([frame("message", answer=json.dumps(payload)), frame("message_end")]),
        status=503 if scenario in {"platform_failure", "all_failed", "all_failed_reparse"} else 200,
    )
    transport.runtime.capability = "task_import"
    transport.direct.chat_json.return_value = copy.deepcopy(PAYLOAD)
    if scenario in {"all_failed", "all_failed_reparse"}:
        transport.direct.chat_json.side_effect = ValueError("synthetic provider failure")
    if scenario == "quota":
        from sales_backend.services.model_calls import CallQuotaExceeded

        observer = SimpleNamespace(start=AsyncMock(side_effect=CallQuotaExceeded()), finish=AsyncMock())
        transport.runtime.observer_factory = lambda *a, **kw: observer
    settings = configured(
        pilot=scenario != "no_platform", direct_key="" if scenario == "platform" else "synthetic-direct"
    )
    monkeypatch.setattr(
        service_module,
        "load_runtime_configuration",
        AsyncMock(return_value=SimpleNamespace(settings=settings, prompt_overrides={})),
    )
    monkeypatch.setattr(service_module, "filtered_facts_runtime", lambda *a, **k: transport.runtime)

    def inference(database, routed_settings, *, platform):
        instance = InferenceService(
            None, routed_settings, platform=platform, direct_factory=lambda *a, **k: transport.direct
        )
        instance.assert_actor_current = AsyncMock()
        return instance

    monkeypatch.setattr(service_module, "InferenceService", inference)
    dispatch = AsyncMock(side_effect=AssertionError("Extraction must not dispatch tasks"))
    monkeypatch.setattr(service_module.TaskService, "create", dispatch)
    if scenario == "all_failed_reparse":
        with pytest.raises(TaskConflict, match="原草稿保持不变"):
            await TaskImportService(None, settings)._extract(ACTOR, REQUEST, PEOPLE, require_ai=True)
        transport.direct.chat_json.assert_awaited_once()
        dispatch.assert_not_called()
        return
    items, method, warnings = await TaskImportService(None, settings)._extract(ACTOR, REQUEST, PEOPLE)
    assert items and method == ("rules" if scenario in {"all_failed", "quota"} else "ai")
    assert bool(warnings) == (scenario in {"all_failed", "quota"})
    dispatch.assert_not_called()
    if scenario == "quota":
        # Quota rejection never becomes a paid direct-provider retry.
        assert not transport.requests
        transport.direct.chat_json.assert_not_called()
        return
    if scenario == "platform":
        assert not settings.senseaudio_api_key  # Platform does not need a direct-provider key.
        transport.direct.chat_json.assert_not_called()
        envelope = json.loads(transport.requests[0].content)["query"]
        envelope = json.loads(envelope)
        assert "不能创建或派发" in envelope["backend_prompt"]
        assert envelope["mode"] == "task_import" and envelope["facts"]["candidates"]
    else:
        transport.direct.chat_json.assert_awaited_once()
    if scenario == "no_platform":
        assert not transport.requests


class Database:
    def __init__(self):
        self.connection = SimpleNamespace()

    @asynccontextmanager
    async def transaction(self, *args, **kwargs):
        yield self.connection


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["platform", "direct_fallback", "rules_fallback"])
async def test_full_batch_uses_minimal_model_facts_and_keeps_original_mapping(monkeypatch, route):
    counts = [4, 6, 4, 4, 2, 1]
    people = [{"id": str(uuid4()), "name": f"测试人员{i}", "account_code": f"USER{i}"} for i in range(6)]
    lines = []
    for person, count in zip(people, counts, strict=True):
        lines.append(person["name"] + "：")
        for _ in range(count):
            # Over 500 chars must reach the provider and saved draft unchanged.
            description = f"独立行动{len(lines):02d}核对方案：" + "完整保留验收条件；" * 70 + "（9.29）"
            lines.append("• " + description)
    request = TaskImportPreview(source_text="\n".join(lines), meeting_date=REQUEST.meeting_date, batch_name="完整周会")
    candidates = candidate_lines(request.source_text, people)
    minimal = [{"source_line": row["source_line"], "description": row["description"]} for row in candidates]
    payload = {"items": copy.deepcopy(minimal)}
    transport = setup(
        stream=Chunks([frame("message", answer=json.dumps(payload, ensure_ascii=False)), frame("message_end")]),
        status=200 if route == "platform" else 503,
    )
    transport.runtime.capability = "task_import"
    transport.direct.chat_json.return_value = payload
    if route == "rules_fallback":
        transport.direct.chat_json.side_effect = ValueError("synthetic direct failure")
    settings = configured()
    monkeypatch.setattr(
        service_module,
        "load_runtime_configuration",
        AsyncMock(
            return_value=SimpleNamespace(settings=settings, prompt_overrides={}),
        ),
    )
    monkeypatch.setattr(service_module, "filtered_facts_runtime", lambda *a, **k: transport.runtime)

    def inference(database, routed_settings, *, platform):
        instance = InferenceService(
            None,
            routed_settings,
            platform=platform,
            direct_factory=lambda *a, **k: transport.direct,
        )
        instance.assert_actor_current = AsyncMock()
        return instance

    monkeypatch.setattr(service_module, "InferenceService", inference)
    monkeypatch.setattr(service_module.TaskTargetRepository, "recipients", AsyncMock(return_value=people))
    monkeypatch.setattr(service_module, "annotate_duplicates", AsyncMock(side_effect=lambda conn, values: values))
    monkeypatch.setattr(service_module, "require_capability", AsyncMock())
    dispatch = AsyncMock(side_effect=AssertionError("Preview must never dispatch tasks"))
    monkeypatch.setattr(service_module.TaskService, "create", dispatch)
    service = TaskImportService(Database(), settings)
    service.assert_current = AsyncMock()
    service.repo.matching = AsyncMock(return_value=None)
    service.repo.insert = AsyncMock(return_value={"id": str(uuid4())})
    service.detail = AsyncMock(return_value={"id": "saved-draft"})
    await service.preview(ACTOR, request)

    envelope = json.loads(json.loads(transport.requests[0].content)["query"])
    expected_facts = {"meeting_date": request.meeting_date.isoformat(), "candidates": minimal}
    assert envelope["facts"] == expected_facts
    assert envelope["user_text"] == "请逐条整理给定候选事项，保留原文的全部事实与行动。"
    encoded = json.dumps(envelope, ensure_ascii=False)
    assert all(encoded.count(row["description"]) == 1 for row in minimal)
    assert len(minimal) == 21 and all(set(row) == {"source_line", "description"} for row in minimal)
    if route == "platform":
        transport.direct.chat_json.assert_not_called()
    else:
        transport.direct.chat_json.assert_awaited_once()
        messages = transport.direct.chat_json.call_args.kwargs["messages"]
        assert json.loads(messages[1].content) == expected_facts
        assert all("\n".join(message.content for message in messages).count(row["description"]) == 1 for row in minimal)

    _, saved_actor, saved_request, fingerprint, method, _, items, _ = service.repo.insert.call_args.args
    assert saved_actor == ACTOR and saved_request == request
    assert saved_request.source_text == "\n".join(lines) and fingerprint == source_hash(request.source_text)
    assert method == ("rules" if route == "rules_fallback" else "ai")
    assert len(items) == 21
    assert Counter(item["assignee_account_code"] for item in items) == {
        person["account_code"]: count for person, count in zip(people, counts, strict=True)
    }
    for item, original in zip(items, candidates, strict=True):
        assert item["source_text"] == original["source_text"]
        assert item["assignee_hint"] == original["assignee_hint"]
        assert item["description"] == original["description"] and len(item["description"]) > 500
        assert item["due_at"] == "2026-09-29T23:59:00+08:00"
    dispatch.assert_not_called()


def reparse_fixture(monkeypatch):
    from sales_backend.domain.task_imports import candidate_lines, materialize_items

    candidates = candidate_lines(REQUEST.source_text, PEOPLE)
    items = materialize_items(candidates, candidates, PEOPLE, REQUEST.meeting_date)
    row = {
        "id": str(uuid4()),
        "status": "draft",
        "version_no": 1,
        "items": items,
        "editable_items": editable(items),
        "source_text": REQUEST.source_text,
        "meeting_date": REQUEST.meeting_date,
        "batch_name": REQUEST.batch_name,
    }
    service = TaskImportService(Database(), configured())
    service.assert_current = AsyncMock()
    service.repo.own = AsyncMock(return_value=row)
    service.repo.replace_parse = AsyncMock()
    service._extract = AsyncMock(return_value=(items, "ai", []))
    service.detail = AsyncMock(return_value={"id": row["id"], "version_no": 2})
    monkeypatch.setattr(service_module.TaskTargetRepository, "recipients", AsyncMock(return_value=PEOPLE))
    monkeypatch.setattr(service_module, "annotate_duplicates", AsyncMock(side_effect=lambda conn, values: values))
    monkeypatch.setattr(service_module, "require_capability", AsyncMock())
    return service, row


@pytest.mark.asyncio
async def test_reparse_uses_immutable_saved_source_and_version_without_creating_tasks(monkeypatch):
    service, row = reparse_fixture(monkeypatch)
    dispatch = AsyncMock(side_effect=AssertionError("Only confirm dispatches"))
    monkeypatch.setattr(service_module.TaskService, "create", dispatch)
    result = await service.reparse(ACTOR, row["id"], TaskImportReparse(version_no=1))
    assert result["version_no"] == 2
    request = service._extract.call_args.args[1]
    assert request.source_text == row["source_text"] and request.meeting_date == row["meeting_date"]
    assert service._extract.call_args.kwargs == {"require_ai": True}
    service.repo.replace_parse.assert_awaited_once()
    dispatch.assert_not_called()
    with pytest.raises(ValidationError):
        TaskImportReparse(version_no=1, source_text="different source")


def test_unchanged_saved_date_uses_instant_equality(monkeypatch):
    service, row = reparse_fixture(monkeypatch)
    row["items"][0]["due_at"] = "2026-09-29T17:00:00+08:00"
    row["editable_items"][0]["due_at"] = "2026-09-29T09:00:00Z"
    service._require_reparseable(row, 1)
    row["editable_items"][0]["due_at"] = "2026-09-29T10:00:00Z"
    with pytest.raises(TaskConflict, match="人工编辑"):
        service._require_reparseable(row, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["confirmed", "edited", "stale", "concurrent", "ai_failure"])
async def test_reparse_does_not_overwrite_saved_draft_on_failure(monkeypatch, kind):
    service, row = reparse_fixture(monkeypatch)
    if kind == "confirmed":
        row["status"] = "confirmed"
    if kind == "edited":
        row["editable_items"][0]["description"] = "人工修改应保留"
    if kind == "stale":
        row["version_no"] = 2
    if kind == "concurrent":
        service.repo.own.side_effect = [copy.deepcopy(row), {**row, "version_no": 2}]
    if kind == "ai_failure":
        service._extract.side_effect = TaskConflict("AI失败原稿保留")
    before = copy.deepcopy(row)
    with pytest.raises((TaskConflict, VersionConflict)):
        await service.reparse(ACTOR, row["id"], TaskImportReparse(version_no=1))
    assert row == before
    service.repo.replace_parse.assert_not_called()
    if kind in {"confirmed", "edited", "stale"}:
        service._extract.assert_not_called()


@pytest.mark.asyncio
async def test_reparse_repository_never_changes_source_fields():
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=str(uuid4())))
    await TaskImportRepository().replace_parse(connection, str(uuid4()), 3, "ai", [], [], [])
    sql = connection.fetchval.call_args.args[0]
    assert "security.reparse_task_import_batch" in sql
    assert connection.fetchval.call_args.args[2] == 3
    assert all(name not in sql for name in ("source_text", "source_hash", "meeting_date", "INSERT INTO workflow.task"))
