"""Exactly two synthetic platform calls; no sales data or business writes."""
import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND / "scripts" / "acceptance"))
from agent_provider_recheck import ReadOnlyDatabase
from sales_backend.config import get_settings
from sales_backend.integrations.supreme_fde import FdeClient
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_business_rules import prepare_business_rules, supplementary_prompt
from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
from sales_backend.services.agent_platform.fde_invocation import FdeJsonInvocation
from sales_backend.services.agent_platform.inference import binding_for
from sales_backend.services.runtime_config import load_runtime_configuration
from sales_backend.domain.agent import ChatMessage
from sales_backend.domain import customer_risk as contract


def arguments(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--workspace-external-id", required=True)
    parser.add_argument("--account-code", required=True, help="Existing active sales identity; no password is needed")
    parser.add_argument("--output", type=Path, required=True, help="New evidence JSON path; existing files are refused")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite existing evidence")
    if not args.output.parent.is_dir():
        parser.error("Output parent directory must exist")
    return args


def cases():
    base = {"contract_version": "customer-risk.v1", "data_as_of": datetime.now(UTC).isoformat(),
            "customer": {"id": "SYN-CUSTOMER-ONLY", "name": "纯合成客户", "version_no": 1},
            "coverage": {"complete": True}, "opportunities": [], "contacts": []}
    yield "budget-risk", {**base, "visits": [
        {"id": "SYN-SALES-01", "customer_id": "SYN-CUSTOMER-ONLY", "version_no": 1,
         "follow_up_record": "客户财务负责人明确表示本季度项目预算尚未获批，采购审批因此暂停。",
         "next_action": "销售将在2026年9月18日与客户财务负责人核实预算审批进展。"},
        {"id": "SYN-FDE-01", "customer_id": "SYN-CUSTOMER-ONLY", "version_no": 1,
         "follow_up_record": "FDE与客户技术负责人完成全部约定测试，技术指标全部通过，没有未处理的技术问题。",
         "next_action": "FDE将在2026年9月18日向销售提供测试结果。"},
    ]}
    yield "explicit-healthy", {**base, "visits": [
        {"id": "SYN-SALES-02", "customer_id": "SYN-CUSTOMER-ONLY", "version_no": 1,
         "follow_up_record": "客户决策者、财务负责人和采购负责人共同确认：预算已批准，合同已签署，款项已收齐，全部商务审批已完成，进度按期；没有竞争变更、关系异议或未解决的经营风险。",
         "next_action": "销售将在2026年9月18日向客户发送本次确认纪要。"},
        {"id": "SYN-FDE-02", "customer_id": "SYN-CUSTOMER-ONLY", "version_no": 1,
         "follow_up_record": "客户技术负责人已签字确认全部技术指标、验收标准与交付成果通过，双方预期一致，没有待处理问题、延期或技术障碍。",
         "next_action": "FDE将在2026年9月18日归档验收记录。"},
    ]}


async def main(args):
    settings = get_settings()
    database = ReadOnlyDatabase(settings)
    await database.connect()
    outputs = []
    try:
        async with database.connection() as connection:
            record = await connection.fetchrow(
                "SELECT * FROM security.resolve_account_actor($1,$2,'sales')",
                args.workspace_external_id, args.account_code,
            )
            assert record, "Existing active sales identity required"
            actor = IdentityRepository._actor(record).context
            assert await connection.fetchval("SHOW default_transaction_read_only") == "on"
        runtime = await load_runtime_configuration(database, actor, settings, capability="personal_risks")
        transport = filtered_facts_runtime(database, runtime.settings, capability="personal_risks")
        binding = binding_for(runtime.settings.agent_platform_bindings_json, actor.workspace_id, "personal_risks")
        assert transport and binding and transport.agent_id == binding.agent_id
        for name, facts in cases():
            messages = [ChatMessage(**item) for item in contract.customer_risk_messages(facts)]
            messages[0] = ChatMessage(role="system", content=supplementary_prompt(
                messages[0].content, runtime.prompt_overrides.get("personal_risks")))
            prepared, messages = prepare_business_rules(runtime, facts, messages)
            messages[0] = ChatMessage(role="system", content=messages[0].content + "\n"
                + contract.customer_risk_output_checklist(facts))
            payload = {"mode": "personal_risks", "surface": "customer_risk", "role": "sales",
                       "user_text": "评估指定客户的风险，并明确返回事实覆盖与评估结论。",
                       "current_time": facts["data_as_of"], "facts": prepared,
                       "backend_prompt": messages[0].content,
                       "output_contract": contract.CUSTOMER_RISK_OUTPUT_CONTRACT}
            client = FdeClient(transport.config)
            invocation = FdeJsonInvocation(client, json.dumps(payload, ensure_ascii=False),
                                           "sales:synthetic-customer-risk-20260916-" + name)
            output = {"case": name, "surface": "customer_risk", "agent_id": binding.agent_id,
                      "binding_snapshot_id": binding.snapshot_id, "runtime_snapshot_verified": False,
                      "actual_version_receipt": None, "contract_version": contract.CUSTOMER_RISK_OUTPUT_CONTRACT["version"],
                      "business_writes": False, "source": "synthetic_only", "input": payload,
                      "platform_budget_seconds": runtime.settings.agent_inference_platform_seconds}
            started = monotonic()
            try:
                async with asyncio.timeout(runtime.settings.agent_inference_platform_seconds):
                    result = await invocation()
                output["result"] = result
                validated = contract.validate_customer_risk_result(result, facts)
                output.update(contract_passed=True, outcome=validated["outcome"],
                              expected_outcome_passed=validated["outcome"] == (
                                  "risk_found" if name == "budget-risk" else "no_risk_identified"))
            except Exception as error:
                output.update(contract_passed=False, error_type=type(error).__name__,
                              error_code=invocation.error_code)
                if hasattr(error, "errors"):
                    output["validation_errors"] = [
                        {"loc": item["loc"], "type": item["type"]}
                        for item in error.errors(include_input=False, include_url=False)
                    ]
            finally:
                if invocation.result is None and invocation.ids.task_id:
                    try:
                        async with asyncio.timeout(1):
                            await invocation.stop()
                    except Exception:
                        pass
                output.update(ids=asdict(invocation.ids), http_status=invocation.http_status,
                              elapsed_ms=round((monotonic() - started) * 1000))
                if invocation.result:
                    output["usage"] = {"input_tokens": invocation.result.input_tokens,
                                       "output_tokens": invocation.result.output_tokens}
                await client.close()
            outputs.append(output)
            print(json.dumps({k: v for k, v in output.items() if k not in {"input", "result"}},
                             ensure_ascii=False), flush=True)
    finally:
        await database.close()
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(outputs, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main(arguments(__doc__)))
