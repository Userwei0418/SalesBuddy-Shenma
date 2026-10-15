"""Two real original-provider calls with synthetic facts and no database writes."""
import asyncio
import json
from time import monotonic

# Python puts this script's directory on sys.path. This sibling module resolves
# backend/src and scripts/acceptance from its own repository location.
import probe_platform_synthetic as inputs
from agent_provider_recheck import EvidenceObserver
from sales_backend.integrations.senseaudio import SenseAudioClient


async def main(args):
    settings = inputs.get_settings()
    database = inputs.ReadOnlyDatabase(settings)
    await database.connect()
    outputs = []
    try:
        async with database.connection() as connection:
            record = await connection.fetchrow(
                "SELECT * FROM security.resolve_account_actor($1,$2,'sales')",
                args.workspace_external_id, args.account_code,
            )
            assert record, "Existing active sales identity required"
            actor = inputs.IdentityRepository._actor(record).context
        runtime = await inputs.load_runtime_configuration(database, actor, settings, capability="personal_risks")
        binding = inputs.binding_for(runtime.settings.agent_platform_bindings_json, actor.workspace_id, "personal_risks")
        # Exercise a conservative remaining fallback budget, after the platform
        # consumes its whole configured allowance, rather than a fresh full budget.
        remaining = runtime.settings.agent_inference_total_seconds - runtime.settings.agent_inference_platform_seconds
        assert remaining > 0
        for name, facts in inputs.cases():
            messages = [inputs.ChatMessage(**item) for item in inputs.contract.customer_risk_messages(facts)]
            messages[0] = inputs.ChatMessage(role="system", content=inputs.supplementary_prompt(
                messages[0].content, runtime.prompt_overrides.get("personal_risks")))
            prepared, messages = inputs.prepare_business_rules(runtime, facts, messages)
            messages[0] = inputs.ChatMessage(role="system", content=messages[0].content + "\n"
                + inputs.contract.customer_risk_output_checklist(facts))
            EvidenceObserver.rows = []
            client = SenseAudioClient(runtime.settings, observer=EvidenceObserver(database, actor, "personal_risks"))
            output = {"case": name, "surface": "customer_risk", "provider": "original_api",
                      "model": runtime.settings.llm_model, "business_writes": False,
                      "source": "synthetic_only", "input_facts": prepared,
                      "contract_version": inputs.contract.CUSTOMER_RISK_OUTPUT_CONTRACT["version"],
                      "binding_snapshot_id": binding.snapshot_id if binding else None,
                      "runtime_snapshot_verified": False, "actual_version_receipt": None,
                      "total_budget_seconds": runtime.settings.agent_inference_total_seconds,
                      "remaining_fallback_budget_seconds": remaining,
                      "full_queue_lifecycle_exercised": False}
            started = monotonic()
            try:
                async with asyncio.timeout(remaining):
                    result = await client.chat_json(messages=messages, temperature=0.1)
                output["result"] = result
                validated = inputs.contract.validate_customer_risk_result(result, facts)
                expected = "risk_found" if name == "budget-risk" else "no_risk_identified"
                output.update(contract_passed=True, outcome=validated["outcome"],
                              expected_outcome_passed=validated["outcome"] == expected)
                if name == "budget-risk":
                    output["budget_evidence_identified"] = any(
                        item["risk_type"] == "budget_risk" and item["source_visit_id"] == "SYN-SALES-01"
                        for item in validated["risks"])
            except Exception as error:
                output.update(contract_passed=False, error_type=type(error).__name__,
                              http_status=getattr(error, "status_code", None))
                if hasattr(error, "errors"):
                    output["validation_errors"] = [{"loc": item["loc"], "type": item["type"]}
                        for item in error.errors(include_input=False, include_url=False)]
            finally:
                output["elapsed_ms"] = round((monotonic() - started) * 1000)
                output["attempts"] = [{key: row.get(key) for key in (
                    "provider", "model", "attempt", "status", "http_status", "error_type")}
                    for row in EvidenceObserver.rows]
                await client.close()
            outputs.append(output)
            print(json.dumps({k: v for k, v in output.items() if k not in {"input_facts", "result"}},
                             ensure_ascii=False), flush=True)
    finally:
        await database.close()
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(outputs, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main(inputs.arguments(__doc__)))
