"""Read-only task scope inventory. Does not call models or create application sessions."""

import argparse
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from sales_backend.config import get_settings
from sales_backend.db import Database, _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.agent_run.today_scope import TodayTasksScope, source_fingerprint


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def run(args):
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    connection = await asyncpg.connect(
        host="/tmp", database="sales_saas", user="postgres", command_timeout=10,  # noqa: S108
    )
    try:
        await _initialize_connection(connection)
        async with connection.transaction(readonly=True):
            await connection.execute("SET LOCAL ROLE " + args.role)
            assert not await connection.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user",
            )
            record = await IdentityRepository().find_actor_by_account(
                connection, workspace_external_id="demo-sales-workspace", account_code="XS001",
            )
            actor = record.context
            assert actor.role.value == "sales"
            await set_request_context(connection, actor)

            class Pool:
                @asynccontextmanager
                async def acquire(self):
                    yield connection

            database = Database(get_settings(), pool=Pool())
            run = RunInput("read-only-scope", "none", "整理今日待办", "today_tasks", None, actor)
            facts = await AgentFactsLoader(database).load(run)
            # All still-unmaterialized sources are needed: repeated invocations
            # can expose an older eligible visit after the newest gets a task.
            pending = await connection.fetch(
                """
                SELECT v.id::text AS source_id, 'visit_follow_up' AS source_type,
                       v.customer_id::text, v.opportunity_id::text, c.name AS customer_name,
                       v.interaction_at, v.created_at, v.follow_up_record, v.next_action
                  FROM activity.visit v
                  LEFT JOIN crm.customer c ON c.id=v.customer_id
                 WHERE v.deleted_at IS NULL AND v.recorder_user_ref_id=$1::uuid
                   AND v.status IN ('confirmed','archived')
                   AND NULLIF(btrim(v.next_action),'') IS NOT NULL
                   AND v.interaction_at >= clock_timestamp() - interval '90 days'
                   AND NOT EXISTS (
                     SELECT 1 FROM workflow.task t WHERE t.source_visit_id=v.id AND t.deleted_at IS NULL
                   )
                 ORDER BY v.interaction_at DESC, v.created_at DESC, v.id
                """, actor.user_id,
            )
            task_rows = await connection.fetch(
                """
                SELECT t.* FROM workflow.task t
                JOIN workflow.task_assignee a ON a.task_id=t.id
                WHERE a.assignee_user_ref_id=$1::uuid AND a.responsibility='owner'
                ORDER BY t.id
                """, actor.user_id,
            )
            remaining, rounds = list(pending), []
            for _ in range(args.rounds):
                chosen, customers = [], set()
                for item in remaining:
                    if item["customer_id"] not in customers and len(chosen) < 30:
                        chosen.append(item["source_id"])
                        customers.add(item["customer_id"])
                rounds.append(chosen)
                remaining = [item for item in remaining if item["source_id"] not in chosen]
            current_ids = sorted(item["source_id"] for item in facts["follow_up_candidates"])
            assert sorted(rounds[0]) == current_ids
            state = {
                "approval_status": "pending",
                "preflight_only": True,
                "real_business_verified": False,
                "model_called": False,
                "application_sessions_created": 0,
                "business_writes": 0,
                "account": "XS001",
                "actor_id": actor.user_id,
                "workspace_id": actor.workspace_id,
                "data_as_of": facts["data_as_of"],
                "active_task_count": len(facts["active_tasks"]),
                "active_management_task_count": sum(
                    row["source_type"] == "management_task" for row in facts["active_tasks"]
                ),
                "current_candidate_count": len(current_ids),
                "current_candidate_source_ids": current_ids,
                "all_remaining_source_count": len(pending),
                "hypothetical_successive_round_source_ids": rounds,
                "hypothetical_new_tasks_in_rounds": sum(map(len, rounds)),
                "new_notifications_by_existing_materializer": 0,
                "own_task_count": len(task_rows),
                "own_tasks_fingerprint": fingerprint([dict(row) for row in task_rows]),
                "input_bytes": len(json.dumps(facts, default=str, ensure_ascii=False).encode()),
                "sources": [{
                    "source_id": row["source_id"], "customer_id": row["customer_id"],
                    "customer_name": row["customer_name"], "interaction_at": str(row["interaction_at"]),
                    "synthetic_customer_marker": any(
                        marker in (row["customer_name"] or "") for marker in ("演示", "验收", "测试", "联调")
                    ),
                    "next_action_chars": len(row["next_action"]),
                    "next_action_sha256": fingerprint(row["next_action"]),
                } for row in pending],
                "limitations": [
                    "Successive rounds may surface older eligible visits beyond today's first candidate list.",
                    "Date and priority depend on provider output and the existing future_due normalization.",
                    "Inventory can change with user activity; repeat and compare before any authorized live run.",
                ],
            }
            if args.propose_demo_cases:
                selected, customers = [], set()
                for row in pending:
                    if row["customer_id"] not in customers and any(
                        marker in (row["customer_name"] or "") for marker in ("演示", "验收", "测试", "联调")
                    ):
                        selected.append(dict(row))
                        customers.add(row["customer_id"])
                    if len(selected) == 4:
                        break
                if len(selected) != 4:
                    raise ValueError("Four distinct demo sources required; no scope is activated")
                proposals = []
                for case, row in zip(("normal", "off", "blocked", "production"), selected, strict=True):
                    digest = source_fingerprint(row)
                    # Read-only rehearsal of the narrowing query. This object
                    # is never installed into a worker or used for persistence.
                    narrow = TodayTasksScope(actor.workspace_id, actor.user_id,
                                             datetime.now(UTC) + timedelta(minutes=5), ((row["source_id"], digest),))
                    narrowed = await AgentFactsLoader(database).load(run, today_tasks_scope=narrow)
                    assert [item["source_id"] for item in narrowed["follow_up_candidates"]] == [row["source_id"]]
                    proposals.append({
                        "scenario": case, "customer_name": row["customer_name"],
                        "expected_candidate_count": 1, "new_task_limit_if_auto_materialized": 1,
                        "scope_file_content": {actor.workspace_id: {actor.user_id: {
                            "approval_status": "pending", "expires_at": None,
                            "sources": {row["source_id"]: digest},
                        }}},
                    })
                state["proposed_demo_cases"] = proposals
                state["proposed_demo_new_task_limit_if_auto_materialized"] = 4
                state["proposal_activated"] = False
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: value for key, value in state.items() if key not in {
            "sources", "current_candidate_source_ids", "hypothetical_successive_round_source_ids",
            "proposed_demo_cases",
        }}, ensure_ascii=False))
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rounds", type=int, choices=range(1, 8), default=4)
    parser.add_argument("--role", choices=("sales_app", "sales_runtime"), default="sales_app")
    parser.add_argument("--propose-demo-cases", action="store_true")
    asyncio.run(run(parser.parse_args()))
