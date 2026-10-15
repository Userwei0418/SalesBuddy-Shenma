"""Opt-in real API -> queue -> Worker acceptance; tokens and answers stay in memory.

The operator selects normal/off/blocked with the backend pilot file separately.
This script does not change routing or retry an ambiguously submitted message.
Only ChatBI conversations, messages and audit records are created, never CRM data.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

import httpx


async def run(args):
    output = Path(args.output)
    state = {"started_at": datetime.now(UTC).isoformat(), "scenario": args.scenario, "checks": [], "ids": {}}
    clients = {}

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    def check(name, passed, **metadata):
        state["checks"].append({"name": name, "passed": bool(passed), **metadata})
        save()
        print(json.dumps(state["checks"][-1], ensure_ascii=False), flush=True)
        if not passed:
            raise AssertionError(name)

    async def request(account, method, path, expected=200, **kwargs):
        response = await clients[account].request(method, path, **kwargs)
        # Never put response bodies, tokens or business values in diagnostics.
        check(f"{account} {method} {path}", response.status_code == expected, http_status=response.status_code)
        return response.json() if response.content else None

    try:
        for account in (args.account, args.other_account):
            clients[account] = httpx.AsyncClient(base_url=args.base_url, timeout=15)
            session = await request(
                account,
                "POST",
                "/auth/session",
                json={
                    "account_code": account,
                    "workspace": args.workspace,
                    "client": {"purpose": "chatbi-business-acceptance", "label": args.scenario},
                },
            )
            clients[account].headers["Authorization"] = "Bearer " + session["access_token"]
            check(
                account + " sales/self identity",
                session["actor"]["role"] == "sales" and session["actor"]["data_scope"] == "self",
            )
        customer_id = None
        if args.customer:
            customers = await request(args.account, "GET", "/customers", params={"scope": "mine", "page_size": 1})
            check("owned customer exists", bool(customers["items"]))
            customer_id = customers["items"][0]["id"]
        mode = "customer_chatbi" if customer_id else "chatbi"
        conversation = await request(
            args.account, "POST", "/conversations", 201, json={"mode": mode, "customer_id": customer_id}
        )
        cid = state["ids"]["conversation_id"] = conversation["id"]
        state["mode"] = mode
        save()
        body = {
            "text": "【后端受控联调】请根据已提供的 summary.open_tasks，回答当前权限范围内"
            + ("这个客户的" if customer_id else "的")
            + "未完成任务数量。只返回一个 metrics，label 为未完成任务，value 为准确整数；"
            "summary 简短说明，rows 为空。不创建或修改任何业务数据。",
            "client_message_id": "fde-acceptance-" + uuid4().hex,
            "input_source": "text",
        }
        started = monotonic()
        accepted = await request(args.account, "POST", f"/conversations/{cid}/messages", 202, json=body)
        rid = state["ids"]["run_id"] = accepted["run_id"]
        save()
        print(json.dumps({"scenario": args.scenario, "run_id": rid}), flush=True)
        # Deliberate replay uses the acknowledged message ID, never a new task.
        replay = await request(args.account, "POST", f"/conversations/{cid}/messages", 202, json=body)
        check("acknowledged replay returns same run", replay["run_id"] == rid)
        await request(
            args.account,
            "POST",
            f"/conversations/{cid}/messages",
            409,
            json={**body, "text": body["text"] + "不同内容"},
        )
        deadline = monotonic() + 75
        while monotonic() < deadline:
            response = await clients[args.account].get(f"/agent/runs/{rid}")
            if response.status_code != 200:
                check("run polling HTTP", False, http_status=response.status_code)
            result = response.json()
            if result["status"] in {"succeeded", "failed", "cancelled", "waiting_human"}:
                break
            await asyncio.sleep(1)
        else:
            check("run reaches bounded terminal state", False)
        state["status"] = result["status"]
        state["elapsed_ms"] = round((monotonic() - started) * 1000)
        check("real business run succeeded", result["status"] == "succeeded")
        answer = result["result"]
        check(
            "public result contract",
            isinstance(answer.get("summary"), str)
            and bool(answer["summary"])
            and isinstance(answer.get("metrics"), list)
            and isinstance(answer.get("rows"), list),
        )
        check(
            "route metadata stays private",
            all(
                k not in answer for k in ("provider", "inference_route", "fallback_reason", "platform_run", "agent_id")
            ),
        )
        messages = await request(args.account, "GET", f"/conversations/{cid}/messages")
        check(
            "one user and one final assistant message",
            len(messages) == 2 and sum(m["sender_type"] == "assistant" for m in messages) == 1,
        )
        await request(args.other_account, "GET", f"/agent/runs/{rid}", 404)
        hidden = await request(args.other_account, "GET", f"/conversations/{cid}/messages")
        check("other sales user cannot read messages", hidden == [])
        state["api_verified"] = True
        state["provider_audit"] = "pending server-side run audit; API success alone does not identify provider"
    except Exception as exc:
        state["error_type"] = type(exc).__name__
        raise
    finally:
        for account, client in clients.items():
            if "Authorization" in client.headers:
                try:
                    response = await client.post("/auth/logout")
                    state.setdefault("test_sessions_revoked", {})[account] = response.status_code == 204
                    check(account + " test session revoked", response.status_code == 204)
                except Exception as exc:
                    state.setdefault("cleanup_errors", {})[account] = type(exc).__name__
            await client.aclose()
        state["finished_at"] = datetime.now(UTC).isoformat()
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    parser.add_argument("--workspace", default="demo-sales-workspace")
    parser.add_argument("--account", default="XS001")
    parser.add_argument("--other-account", default="XS002")
    parser.add_argument("--scenario", required=True, choices=("normal", "off", "blocked"))
    parser.add_argument("--customer", action="store_true")
    parser.add_argument("--output", required=True)
    asyncio.run(run(parser.parse_args()))
