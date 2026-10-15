"""Read-only live-provider recheck, using the deployed business facts/contracts.

Run through an isolated service loading the existing application's environment.
Never supply credentials on the command line. This does not submit API jobs or
persist business/model-call rows: observers write sanitized evidence instead.
Each capability is checked sequentially: normal budget, disabled, blocked, then
an optional bounded platform-only diagnosis with the identical facts envelope.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

import asyncpg
from pydantic_core import to_jsonable_python

from sales_backend.config import get_settings
from sales_backend.db import Database, _initialize_connection, normalize_database_url
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services import battle_map_reviews
from sales_backend.services.agent_platform import inference
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.fde_invocation import FdeJsonInvocation
from sales_backend.services.agent_platform.result_contracts import validate_run_result
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.agent_run.prompts import AgentPromptBuilder
from sales_backend.services.runtime_config import load_runtime_configuration

CAPABILITIES = [
    "battle_map_review", "opportunity_draft", "personal_risks", "visit_entry",
    "today_tasks", "operating_report", "chatbi",
]
CUSTOMER = "1b127c42-c436-470f-a6dc-876de6586d53"
MAP_CUSTOMER = "8d5fc876-957e-486a-bad8-711a0e8b3847"
TEXT = {
    "opportunity_draft": "客户沟通了新的客服知识库试点需求，预算约50万元，需要先演示并确定决策人。请判断新建、更新还是不关联，只生成待确认候选。",
    "personal_risks": "请根据本人历史拜访和现有风险，识别仍需处理的风险，给出证据和建议。",
    "visit_entry": "2026年9月12日上午10点，我在线上拜访了【演示】字段验收-422815的业务经理林悦。客户每天约60条工单需人工核对，当前规则分散；我们演示了5条样本，4条分类正确，1条缺少退货规则。客户同意小范围试点，但尚未确认采购，预算约50万元。下一步由我在2026年9月15日17点前提交试点计划和验收清单，客户在2026年9月15日12点前提供20条脱敏工单。",
    "today_tasks": "请根据本人管理任务和拜访下一步行动整理待执行计划，不要虚构来源。",
    "operating_report": "请生成当前权限范围的经营即时总结，包括健康客户、待处理客户和行动建议。",
    "chatbi": "我当前有多少客户、未完成任务和在途商机？请按给定指标口径回答。",
}


def packed(value):
    return json.dumps(to_jsonable_python(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def shape(value):
    """Counts/types and hashes only; business prose never enters evidence files."""
    data = packed(value).encode()
    return {
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        "fields": {str(k): {"type": type(v).__name__, **({"count": len(v)} if isinstance(v, (list, dict)) else {}),
                            **({"display_item_types": [
                                {"item_type": type(item).__name__, **({
                                    field: {"type": type(item.get(field)).__name__,
                                            "nonempty_text": isinstance(item.get(field), str) and bool(item[field].strip())}
                                    for field in ("title", "detail")
                                } if isinstance(item, dict) else {})} for item in v]
                               } if isinstance(v, list) and k in {
                                   "safe_customers", "attention_customers", "action_plan", "team_comparison",
                                   "personal_safe_customers", "personal_attention_customers",
                                   "team_safe_customers", "team_attention_customers"} else {})}
                   for k, v in value.items()} if isinstance(value, dict) else {},
    }


class ReadOnlyDatabase(Database):
    async def connect(self):
        self.pool = await asyncpg.create_pool(
            dsn=normalize_database_url(self.settings.database_url), min_size=1, max_size=2,
            command_timeout=30, init=_initialize_connection,
            server_settings={"application_name": "salegent-provider-recheck-readonly",
                             "default_transaction_read_only": "on"},
        )
        async with self.connection() as connection:
            role = await connection.fetchrow("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert role and not role["rolsuper"] and not role["rolbypassrls"], "RLS application role required"

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False):
        async with super().transaction(actor, readonly=True) as connection:
            yield connection


class EvidenceObserver:
    rows = []

    def __init__(self, database, actor, operation, *, operation_id=None, run_id=None, provider="senseaudio"):
        self.provider, self.operation_id = provider, operation_id

    async def start(self, endpoint, model, metadata, attempt):
        item = {"invocation_id": str(uuid4()), "provider": self.provider,
                "operation_id": self.operation_id, "model": model, "attempt": attempt,
                "status": "running", "started_at": datetime.now(UTC).isoformat()}
        if self.provider == "agent_platform":
            item["metadata"] = metadata
        self.rows.append(item)
        return item["invocation_id"]

    async def finish(self, invocation_id, response, error, *, response_metadata=None):
        item = next(row for row in self.rows if row["invocation_id"] == invocation_id)
        item.update(status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed" if error else "succeeded",
                    error_type=type(error).__name__ if error else None,
                    http_status=response.status_code if response is not None else None,
                    finished_at=datetime.now(UTC).isoformat())
        if response_metadata:
            item["response_metadata"] = response_metadata


class CaptureRuntime:
    def __init__(self, runtime):
        self.runtime, self.request, self.last_operation = runtime, None, None

    def operation(self, request):
        self.request = request
        self.last_operation = self.runtime.operation(request)
        return self.last_operation


async def prepare(database, settings, actor, capability):
    runtime = await load_runtime_configuration(database, actor, settings)
    if capability == "battle_map_review":
        captured = {}

        class CaptureInference:
            def __init__(self, *args, **kwargs):
                pass

            async def evaluate(self, **kwargs):
                captured.update(kwargs)
                return inference.InferenceResult({}, {})

        pilot = {actor.workspace_id: {"capabilities": {capability: {"enabled": True, "rollout": "production"}}}}
        prepared = replace(runtime.settings, agent_fde_pilot_path="", agent_fde_pilot_json=json.dumps(pilot))
        handler = battle_map_reviews.BattleMapReviewHandler(database, prepared)
        facts = await handler._load_facts(MAP_CUSTOMER, actor)
        with patch.object(battle_map_reviews, "InferenceService", CaptureInference):
            await handler._evaluate(actor, facts)
        assert captured, "map evaluation arguments were not captured"
        return runtime.settings, captured
    run = RunInput(str(uuid4()), str(uuid4()), TEXT[capability], capability,
                   CUSTOMER if capability == "opportunity_draft" else None, actor)
    facts = await AgentFactsLoader(database).load(run)
    messages = AgentPromptBuilder().build(run, facts, runtime.prompt_overrides.get(run.mode))
    return runtime.settings, dict(actor=actor, mode=run.mode, facts=facts, messages=messages,
                                 user_text=run.text, validate=lambda result: validate_run_result(run, result, facts))


async def routed_case(database, settings, kwargs, capability, case):
    start = monotonic()
    EvidenceObserver.rows = []
    binding = json.loads(settings.agent_platform_bindings_json)
    binding[kwargs["actor"].workspace_id][capability]["enabled"] = case != "off"
    settings = replace(settings, agent_platform_bindings_json=json.dumps(binding))
    runtime = filtered_facts_runtime(database, settings, capability=capability, block_requests=case == "blocked")
    assert runtime is not None, "dedicated runtime configuration unavailable"
    runtime.observer_factory = EvidenceObserver
    capture = CaptureRuntime(runtime)
    validation = []

    def validate(value):
        item = {"input": shape(value)}
        validation.append(item)
        try:
            accepted = kwargs["validate"](value)
            item.update(accepted=True, output=shape(accepted))
            return accepted
        except Exception as error:
            item.update(accepted=False, error_type=type(error).__name__, error=str(error)[:240])
            raise

    record = {"capability": capability, "case": case, "started_at": datetime.now(UTC).isoformat(),
              "test_level": "real_providers_with_current_business_facts_and_contracts_no_persistence",
              "platform_seconds": settings.agent_inference_platform_seconds,
              "total_seconds": settings.agent_inference_total_seconds, "facts": shape(kwargs["facts"])}
    with patch.object(inference, "DatabaseModelObserver", EvidenceObserver):
        try:
            result = await inference.InferenceService(database, settings, platform=capture).evaluate(
                **{**kwargs, "validate": validate})
            record.update(accepted=True, trace=result.trace, result=shape(result.payload))
        except Exception as error:
            record.update(accepted=False, error_type=type(error).__name__)
    record.update(elapsed_ms=round((monotonic() - start) * 1000), validation=validation,
                  attempts=EvidenceObserver.rows)
    if capture.last_operation:
        record["platform"] = capture.last_operation.diagnostics()
    return record, capture


async def diagnose(capture, validate, seconds):
    """One additional call, identical query, no fallback; production budget stays unchanged."""
    request = capture.request
    query = packed(request.input)
    identity = request.actor.model_dump(mode="json")
    identity["team_ids"] = sorted(identity.get("team_ids", []))
    user = "sales:" + hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    config = replace(capture.runtime.config, timeout_seconds=seconds)
    record = {"capability": request.capability, "case": "platform_only_diagnostic",
              "diagnostic_seconds": seconds, "started_at": datetime.now(UTC).isoformat(),
              "same_input_sha256": hashlib.sha256(query.encode()).hexdigest(),
              "business_writes": False, "production_settings_changed": False}
    start = monotonic()
    client = FdeClient(config)
    invocation = FdeJsonInvocation(client, query, user)
    try:
        async with asyncio.timeout(seconds):
            value = await invocation()
            record["raw_result"] = shape(value)
            accepted = validate(value)
            record.update(accepted=True, result=shape(accepted))
    except Exception as error:
        record.update(accepted=False, error_type=type(error).__name__, error=str(error)[:240])
    finally:
        record.update(elapsed_ms=round((monotonic() - start) * 1000), ids=asdict(invocation.ids),
                      transport=asdict(invocation.transport), error_code=invocation.error_code,
                      answer_chars=len(invocation.result.answer) if invocation.result else 0)
        if invocation.result is None and invocation.ids.task_id:
            try:
                async with asyncio.timeout(2):
                    await invocation.stop()
                record["stop_state"] = "acknowledged"
            except Exception:
                record["stop_state"] = "unconfirmed"
        await client.close()
    return record


async def main(args):
    settings = get_settings()
    database = ReadOnlyDatabase(settings)
    await database.connect()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    try:
        async with database.connection() as connection:
            record = await IdentityRepository().find_actor_by_account(
                connection, workspace_external_id="demo-sales-workspace", account_code=args.account)
        assert record is not None, "test actor unavailable"
        actor = record.context
        if args.history_source:
            identity = actor.model_dump(mode="json")
            identity["team_ids"] = sorted(identity.get("team_ids", []))
            user = "sales:" + hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            history = []
            for path in sorted(Path(args.history_source).glob("*.json")):
                saved = json.loads(path.read_text())
                capability = saved.get("capability")
                if capability not in CAPABILITIES:
                    continue
                ids = saved.get("ids") or saved.get("platform", {}).get("ids", {})
                if not ids.get("conversation_id") or not ids.get("message_id"):
                    continue
                runtime = filtered_facts_runtime(database, settings, capability=capability)
                assert runtime is not None
                item = {"source_file": path.name, "capability": capability, "ids": ids}
                try:
                    async with FdeClient(runtime.config) as client:
                        page = await client.history_page(conversation_id=ids["conversation_id"], user=user)
                        matches = [message for message in page.messages if message.ids.message_id == ids["message_id"]]
                        item["matched"] = len(matches) == 1
                        if matches:
                            message = matches[0]
                            item.update(status=message.status, error_present=message.error_present,
                                        answer_chars=len(message.answer),
                                        answer_sha256=hashlib.sha256(message.answer.encode()).hexdigest())
                            try:
                                item["parsed_answer"] = shape(message.json_object())
                            except Exception as error:
                                item["parse_error"] = getattr(error, "code", type(error).__name__)
                except Exception as error:
                    item["error"] = getattr(error, "code", type(error).__name__)
                history.append(item)
            (output / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(history, ensure_ascii=False), flush=True)
            return
        for capability in args.capabilities.split(","):
            configured, kwargs = await prepare(database, settings, actor, capability)
            normal = capture = None
            for case in args.cases.split(","):
                result, runtime_capture = await routed_case(database, configured, kwargs, capability, case)
                if case == "normal":
                    normal, capture = result, runtime_capture
                (output / f"{capability}-{case}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps({"capability": capability, "case": case, "accepted": result["accepted"],
                                  "elapsed_ms": result["elapsed_ms"], "trace": result.get("trace"),
                                  "validation_errors": [v.get("error") for v in result["validation"] if not v["accepted"]]},
                                 ensure_ascii=False), flush=True)
            if args.diagnostic_seconds and capture and capture.request and (
                not normal["accepted"] or normal.get("trace", {}).get("provider") != "agent_platform"
            ):
                result = await diagnose(capture, kwargs["validate"], args.diagnostic_seconds)
                (output / f"{capability}-diagnostic.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--account", default="XS001", choices=["XS001", "ZJ001", "ZJL001"])
    parser.add_argument("--capabilities", default=",".join(CAPABILITIES))
    parser.add_argument("--cases", default="normal,off,blocked", help="Subset of normal,off,blocked, in order")
    parser.add_argument("--history-source", help="Read histories for saved IDs only; issues no inference requests")
    parser.add_argument("--diagnostic-seconds", type=float, default=60)
    args = parser.parse_args()
    if (not set(args.capabilities.split(",")) <= set(CAPABILITIES)
            or not set(args.cases.split(",")) <= {"normal", "off", "blocked"}
            or not 0 <= args.diagnostic_seconds <= 60):
        parser.error("unsupported capability or diagnostic budget")
    asyncio.run(main(args))
