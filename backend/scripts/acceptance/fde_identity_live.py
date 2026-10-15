"""Opt-in FDE acceptance against the approved current demo system, through HTTPS only.

Reads one password from stdin. Tokens stay in memory. Evidence contains identifiers
and assertions only. Creates labelled demo opportunities/tasks; never deletes data.
Run once after provisioning the four requested FDE accounts. This is not a seed.
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx


async def main(args):
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        raise ValueError("An existing demo account password is required on stdin")
    state = (
        json.loads(args.output.read_text())
        if args.output.exists()
        else {"source": "current_database_via_https", "ids": {}}
    )
    if not args.finish_handover:
        state["checks"] = []
    clients = {}
    now = datetime.now(UTC)
    prefix = state.get("prefix") or "【FDE演示0913】" + uuid4().hex[:6]
    state["prefix"] = prefix

    def check(name, passed, **facts):
        state["checks"].append({"name": name, "passed": bool(passed), **facts})
        args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        print(json.dumps(state["checks"][-1], ensure_ascii=False), flush=True)
        assert passed, name

    async def request(code, method, path, expected=200, **kwargs):
        result = await clients[code].request(method, path, **kwargs)
        if result.status_code != expected:
            # Do not echo response bodies: an auth/config failure must not leak credentials.
            raise RuntimeError(f"{code} {method} {path}: expected {expected}, got {result.status_code}")
        return result.json() if result.content else None

    def key():
        return {"Idempotency-Key": str(uuid4())}

    async def members(code, ids, expected=200, version=None, headers=None):
        if version is None:
            row = await request("XS001", "GET", f"/opportunities/{oid}/detail")
            version = row["opportunities"][0]["version_no"]
        return await request(
            code,
            "PUT",
            f"/opportunities/{oid}/fde-members",
            expected,
            json={"member_ids": ids, "version_no": version},
            headers=headers or key(),
        )

    async def event(code, task, kind, expected=200, **data):
        return await request(
            code,
            "POST",
            f"/tasks/{task['id']}/events",
            expected,
            json={"event_type": kind, "version_no": task["version_no"], **data},
            headers=key(),
        )

    try:
        roles = {
            "XS001": "sales",
            "ZJL001": "manager",
            "FDEL001": "fde_lead",
            "FDE001": "fde",
            "FDE002": "fde",
            "FDE003": "fde",
        }
        actors = {}
        for code, role in roles.items():
            clients[code] = httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=150)
            auth = await request(
                code,
                "POST",
                "/auth/password/login",
                json={"account_code": code, "password": password, "workspace": "demo-sales-workspace", "role": role},
            )
            clients[code].headers["Authorization"] = "Bearer " + auth["access_token"]
            actors[code] = auth["actor"]
            assert not auth["must_change_password"], "Complete initial password setup before acceptance"
            check(code + "真实密码登录", actors[code]["role"] == role)
        password = ""
        ids = [actors[c]["user_id"] for c in ("FDE001", "FDE002")]
        state["actors"] = {code: {"id": a["user_id"], "name": a["display_name"]} for code, a in actors.items()}
        if args.finish_handover:
            cid, oid = state["ids"]["customer"], state["ids"]["opportunity"]
            current = await request("FDEL001", "GET", f"/tasks/{args.finish_handover}")
            transferred = await event(
                "FDEL001", current, "reassign", note="负责人交接给继续参与的FDE", assignee_account_code="FDE002"
            )
            accepted = await event("FDE002", transferred, "accept")
            await event("FDE002", accepted, "complete", note="已接收并完成试点材料核对")
            await members("FDEL001", ids)
            check("同一待交接任务转交后接受完成且恢复演示名单", True, task_id=args.finish_handover)
            notes = await request("FDE002", "GET", "/notifications")
            check(
                "真实转交通知入库",
                any(
                    n["template_code"] == "task_reassigned" and n.get("object_id") == args.finish_handover
                    for n in notes["items"]
                ),
            )
            check("原账号及FDE业务闭环", True)
            return
        for code in ("FDE001", "FDEL001"):
            me = await request(code, "GET", "/auth/me")
            check(
                code + "权限与默认关闭录入",
                not me["capabilities"]["visit.create"]
                and not me["capabilities"]["opportunity.edit"]
                and bool(me["permission_version"]),
            )
            for path in (
                "/assistant/home",
                "/customer-assets/map",
                "/opportunities",
                "/fde/dashboard",
                "/fde/activity",
                "/notifications",
                "/tasks",
            ):
                await request(code, "GET", path)
            check(code + "五页所需真实读取", True)
        choices = (await request("XS001", "GET", "/opportunities?page_size=100"))["items"]
        assert choices, "The original sales account needs an existing owned customer"
        chosen = next((o for o in choices if "演示" in o.get("customer_name", "")), choices[0])
        cid = chosen["customer_id"]
        state["ids"]["customer"] = cid
        payload = {
            "name": prefix + "知识检索试点",
            "probability": 30,
            "status": "open",
            "amount": 128000,
            "expected_close_date": (now + timedelta(days=35)).date().isoformat(),
            "quarterly_forecasts": [
                {"year": now.year, "quarter": 4, "recognized_amount": 128000, "collection_amount": 128000}
            ],
        }
        primary = next((o for o in choices if o["name"] == payload["name"]), None)
        if primary is None:
            primary = await request(
                "XS001", "POST", f"/customers/{cid}/opportunities", 201, json=payload, headers=key()
            )
        oid = primary["id"]
        state["ids"]["opportunity"] = oid
        other = next((o for o in choices if o["name"] == prefix + "另一部门试点"), None)
        other = other or await request(
            "XS001",
            "POST",
            f"/customers/{cid}/opportunities",
            201,
            json={**payload, "name": prefix + "另一部门试点", "amount": 64000},
            headers=key(),
        )
        state["ids"]["other_opportunity"] = other["id"]
        if not primary.get("fde_members"):
            await request("FDE001", "GET", f"/opportunities/{oid}/detail", 404)
        change = await members("XS001", ids)
        check("销售关联FDE真实保存", {p["id"] for p in change["fde_members"]} == set(ids))
        idem = key()
        same = await members("XS001", ids, version=change["version_no"], headers=idem)
        replay = await members("XS001", ids, version=change["version_no"], headers=idem)
        check("未变化及请求重放不生成新版本", not same["changed"] and replay == same)
        await members("XS001", ids[:1], 409, version=change["version_no"] - 1)
        await members("FDE001", ids, 403)
        updated = await request(
            "XS001",
            "POST",
            f"/customers/{cid}/opportunities",
            201,
            json={
                **payload,
                "action": "update",
                "opportunity_id": oid,
                "version_no": change["version_no"],
                "probability": 50,
            },
            headers=key(),
        )
        check("销售更新阶段保留FDE关联", updated["id"] == oid)
        await request("FDE001", "POST", f"/customers/{cid}/opportunities", 403, json=payload, headers=key())
        await request("FDE001", "POST", "/conversations", 403, json={"mode": "visit_entry", "customer_id": cid})
        await request("FDE001", "POST", "/visit-imports", 403, files={"file": ("demo.md", b"demo")})
        await request("FDE001", "GET", "/fde/dashboard?scope=team", 403)
        check("商业写入与自主录入及越权团队范围被拦截", True)
        await request("FDE001", "GET", f"/opportunities/{other['id']}/detail")
        personal = await request("FDE001", "GET", "/fde/dashboard")
        team = await request("FDEL001", "GET", "/fde/dashboard?scope=team")
        check(
            "全客户可读但仅协助项目计数且团队去重",
            personal["summary"]["opportunities"] == 1
            and team["summary"]["opportunities"] == 1
            and float(team["summary"]["open_acv"]) == 128000,
        )
        for kind, amount in (("recognized", 12000), ("collection", 5000)):
            if state["ids"].get(kind):
                continue
            body = {
                "customer_id": cid,
                "opportunity_id": oid,
                "kind": kind,
                "amount": amount,
                "occurred_on": now.date().isoformat(),
                "source_ref": prefix + kind,
                "note": "FDE项目金额口径演示，无个人业绩分摊",
                "request_id": str(uuid4()),
                "confirmed": True,
            }
            headers = key()
            actual = await request("ZJL001", "POST", "/customer-assets", 201, json=body, headers=headers)
            again = await request("ZJL001", "POST", "/customer-assets", 201, json=body, headers=headers)
            state["ids"][kind] = actual["id"]
            check(kind + "实绩单笔入账且重试回放", actual == again)
        team = await request("FDEL001", "GET", "/fde/dashboard?scope=team")
        check(
            "FDE看板使用账本且两人不放大金额",
            float(team["summary"]["recognized_amount"]) == 12000
            and float(team["summary"]["collection_amount"]) == 5000,
        )
        task_body = {
            "description": prefix + "核对试点技术方案",
            "target_position": "fde",
            "customer_id": cid,
            "opportunity_id": oid,
            "priority_code": "high",
            "due_at": (now + timedelta(days=3)).isoformat(),
        }
        headers = key()
        task = await request("XS001", "POST", "/tasks", 201, json=task_body, headers=headers)
        task_again = await request("XS001", "POST", "/tasks", 201, json=task_body, headers=headers)
        state["ids"]["task"] = task["id"]
        check("岗位任务创建重试不重复", task["id"] == task_again["id"])
        accepted = await event("FDE001", task, "accept")
        await event("FDE002", task, "accept", 409)
        done = await event("FDE001", accepted, "complete", note="已确认脱敏样本与技术验收标准")
        check("同岗位单人领取及完成", done["status"] == "completed")
        reject = await request(
            "XS001", "POST", "/tasks", 201, json={**task_body, "description": prefix + "验证拒绝与取消"}, headers=key()
        )
        await event("FDE001", reject, "reject", note="当前任务排期冲突")
        current = await request("FDEL001", "GET", f"/tasks/{reject['id']}")
        cancelled = await event("FDEL001", current, "cancel", note="演示验证结束，由负责人取消")
        check("拒绝与负责人取消留痕", cancelled["status"] == "cancelled")
        handover = await request(
            "XS001", "POST", "/tasks", 201, json={**task_body, "description": prefix + "移出后任务交接"}, headers=key()
        )
        state["ids"]["handover"] = handover["id"]
        accepted = await event("FDE001", handover, "accept")
        await members("XS001", ids[1:])
        await request("FDE001", "GET", f"/opportunities/{oid}/detail", 404)
        current = await request("FDEL001", "GET", f"/tasks/{handover['id']}")
        check("移出撤销资料读取并显式待交接", current["handover_required"])
        transferred = await event(
            "FDEL001", current, "reassign", note="负责人交接给继续参与的FDE", assignee_account_code="FDE002"
        )
        accepted = await event("FDE002", transferred, "accept")
        await event("FDE002", accepted, "complete", note="已接收并完成试点材料核对")
        await members("FDEL001", ids)
        check("负责人交接及名单管理", True)
        notes = await request("FDE001", "GET", "/notifications")
        relevant = [
            n
            for n in notes["items"]
            if n.get("object_id") in {oid, task["id"]} or n.get("payload", {}).get("membership_opportunity_id") == oid
        ]
        check("加入移出及任务业务提醒真实入库", bool(relevant), notifications=len(relevant))
        state["actors"] = {code: {"id": a["user_id"], "name": a["display_name"]} for code, a in actors.items()}
        check("原账号及FDE业务闭环", True)
    finally:
        args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        for client in clients.values():
            if "Authorization" in client.headers:
                try:
                    await client.post("/auth/logout")
                except httpx.HTTPError:
                    pass
            await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--finish-handover", help="Resume the preserved failed handover by its real task ID")
    asyncio.run(main(parser.parse_args()))
