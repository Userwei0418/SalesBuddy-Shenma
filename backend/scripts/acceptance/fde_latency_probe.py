"""One synthetic FDE transport probe with no retry and no business API writes.

Use a new synthetic identity and customer, saving the synthetic request before
dispatch so a timeout can be correlated upstream. Never record the API key.
This is latency diagnosis, not actual business-integration acceptance.
"""

import argparse
import asyncio
import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

from sales_backend.integrations.supreme_fde import FdeClient, FdeConfig
from sales_backend.services.agent_platform.fde_invocation import FdeJsonInvocation
from sales_backend.services.agent_platform.routing import InvalidAgentResult, ProviderUnavailable


async def probe(args):
    key = Path(args.key_file).read_text().strip()
    output = Path(args.output)
    now = datetime.now(UTC).isoformat()
    synthetic_user = "sales-diagnostic:" + uuid4().hex
    customer_id = str(uuid4())
    facts = {
        "scope": {"type": "customer", "customer_id": customer_id},
        "customer": {
            "id": customer_id,
            "name": "合成客户-单客户耗时诊断",
            "level_code": "Tier-2",
            "industry_code": "software",
            "owner_name": "合成销售",
            "team_name": "合成团队",
        },
        "opportunities": [],
        "visits": [],
        "risks": [],
        "tasks": [
            {"id": str(uuid4()), "title": f"合成跟进任务{i}", "status": "pending_execution", "due_at": now}
            for i in range(7)
        ],
        "detail_coverage": {
            "opportunities": {"total": 0, "returned": 0, "truncated": False},
            "tasks": {"total": 7, "returned": 7, "truncated": False},
            "risks": {"total": 0, "returned": 0, "truncated": False},
            "visits": {"total": 0, "returned": 0, "truncated": False},
        },
        "summary": {
            "customers": 1,
            "open_opportunities": 0,
            "open_pipeline_amount_cny": 0,
            "open_tasks": 7,
            "completed_tasks": 0,
            "open_risks": 0,
            "visits": 0,
            "recent_visits": 0,
        },
        "data_as_of": now,
    }
    envelope = {
        "mode": "customer_chatbi",
        "role": "sales",
        "current_time": now,
        "facts": facts,
        "user_text": "当前客户有多少未完成任务？只返回一个metrics，label为未完成任务，value为数量，rows为空。",
    }
    query = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    state = {
        "purpose": "synthetic transport diagnosis; not business acceptance",
        "started_at": now,
        "input_bytes": len(query.encode()),
        "input_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "synthetic_user": synthetic_user,
        "synthetic_input": envelope,
        "attempts": 1,
        "budget_seconds": 12,
        "retry_performed": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to replace earlier evidence. All persisted inputs are generated
    # above, never customer data or credentials; save them before the network.
    with output.open("x") as evidence:
        evidence.write(json.dumps({**state, "outcome": "pending"}, ensure_ascii=False, indent=2) + "\n")
    started = monotonic()
    async with FdeClient(FdeConfig(args.base_url, key, "agent_final", timeout_seconds=12)) as client:
        invocation = FdeJsonInvocation(client, query, synthetic_user)
        try:
            answer = await invocation()
            metrics = answer.get("metrics", [])
            value = str(metrics[0].get("value", "")).replace(" ", "") if len(metrics) == 1 else ""
            state["expected_metric_matched"] = bool(re.fullmatch(r"7(?:\.0+)?(?:个|项|条)?", value))
            state["structured_answer_returned"] = True
        except (ProviderUnavailable, InvalidAgentResult) as exc:
            state["error_type"] = type(exc).__name__
            state["structured_answer_returned"] = False
        finally:
            state["elapsed_ms"] = round((monotonic() - started) * 1000)
            state["transport"] = asdict(invocation.transport)
            state["ids"] = asdict(invocation.ids)
            state["error_code"] = invocation.error_code
            if invocation.result:
                state["usage"] = {
                    "input_tokens": invocation.result.input_tokens,
                    "output_tokens": invocation.result.output_tokens,
                }
            elif invocation.ids.task_id:
                try:
                    async with asyncio.timeout(1):
                        await invocation.stop()
                    state["stop"] = "acknowledged; not proof of remote completion or no writes"
                except Exception:
                    state["stop"] = "unconfirmed"
            else:
                state["stop"] = "no task id; remote outcome unknown"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://123.207.235.181/v1")
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(probe(parser.parse_args()))
