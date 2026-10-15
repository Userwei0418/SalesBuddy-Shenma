"""Opt-in HTTPS acceptance with clearly labelled fictional records and real DB writes.

Credentials remain in memory. The evidence file contains IDs and check outcomes only.
Run against the deployed acceptance environment, never implicitly as part of pytest.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx


async def main(base_url, output):
    state = {"started_at": datetime.now(UTC).isoformat(), "checks": [], "ids": {}}
    clients, actors = {}, {}
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(state, ensure_ascii=False, indent=2))

    def check(name, passed, **detail):
        state["checks"].append(dict(name=name, passed=bool(passed), **detail))
        save()
        print(json.dumps(state["checks"][-1], ensure_ascii=False), flush=True)
        assert passed, name

    async def req(account, method, path, expected=200, **kwargs):
        response = await clients[account].request(method, path, **kwargs)
        if response.status_code != expected:
            check(
                f"HTTP {method} {path}",
                False,
                status=response.status_code,
                expected=expected,
                error=response.text[:300],
            )
        return response.json() if response.content else None

    def key():
        return {"Idempotency-Key": str(uuid4())}

    try:
        for account in ("XS001", "XS002", "XS003", "ZJ001", "ZJL001"):
            client = clients[account] = httpx.AsyncClient(base_url=base_url, timeout=90)
            session = await req(account, "POST", "/auth/session", json={"account_code": account})
            client.headers["Authorization"] = "Bearer " + session["access_token"]
            actors[account] = session["actor"]
        for account in clients:
            for path in (
                "/assistant/home",
                "/dashboard?personal=true",
                "/workbench",
                "/customer-assets/map",
                "/profile/evaluation",
                "/profile/performance?period=year",
                "/directory/colleagues",
            ):
                await req(account, "GET", path)
            check(f"{account}七个页面数据接口", True)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        suffix = uuid4().hex[:7]
        state["prefix"] = name = f"【演示验收】新版闭环·{suffix}"
        body = dict(
            name=name,
            industry="软件服务",
            customer_type="商机客户",
            level_code="Tier-1",
            source="销售线索",
            target_team=actors["XS001"]["team_names"][0],
            contact_name="林悦",
            contact_title="客服经理",
            contact_role="影响者",
        )
        idem = key()
        created = await req("XS001", "POST", "/customers", 201, json=body, headers=idem)
        cid = state["ids"]["customer"] = created["id"]
        replay = await req("XS001", "POST", "/customers", 201, json=body, headers=idem)
        check("客户创建重试只产生一条记录", replay["id"] == cid)
        await req("XS001", "POST", "/customers", 409, json={**body, "name": name + "变更"}, headers=idem)
        await req("XS002", "GET", f"/customers/{cid}", 404)
        pool = await req("XS002", "GET", "/customers", params={"scope": "department", "q": name})
        check("未认领客户在部门候选池可搜索", any(r["id"] == cid for r in pool["items"]))
        idem = key()
        await req("XS002", "POST", f"/customers/{cid}/claims", 201, json={}, headers=idem)
        await req("XS002", "POST", f"/customers/{cid}/claims", 201, json={}, headers=idem)
        for account in ("XS001", "XS002"):
            await req(account, "GET", f"/customers/{cid}")
            own_map = await req(account, "GET", "/customer-assets/map")
            check(f"{account}认领客户在作战地图可见", cid in json.dumps(own_map))
        opportunity = dict(
            name="客服知识助手一期",
            probability=30,
            status="open",
            amount=120000,
            product_line="智能客服",
            expected_close_date=(today + timedelta(days=30)).isoformat(),
            quarterly_forecasts=[
                dict(
                    year=today.year,
                    quarter=(today.month - 1) // 3 + 1,
                    recognized_amount=80000,
                    collection_amount=60000,
                )
            ],
        )
        a = await req("XS001", "POST", f"/customers/{cid}/opportunities", 201, json=opportunity, headers=key())
        state["ids"]["opportunity_a"] = a["id"]
        await req("XS002", "POST", f"/customers/{cid}/opportunities", 409, json=opportunity)
        b = await req(
            "XS002",
            "POST",
            f"/customers/{cid}/opportunities",
            201,
            json={**opportunity, "name": "客服知识助手二期", "amount": 230000},
            headers=key(),
        )
        state["ids"]["opportunity_b"] = b["id"]
        for account, own, other in (("XS001", a, b), ("XS002", b, a)):
            customer = await req(account, "GET", f"/customers/{cid}")
            visible = {o["id"] for o in customer["opportunities"]}
            check(f"{account}共享客户只显示本人商机", own["id"] in visible and other["id"] not in visible)
            await req(
                account,
                "POST",
                f"/customers/{cid}/opportunities",
                404,
                json={**opportunity, "action": "update", "opportunity_id": other["id"]},
            )
        current = await req("XS001", "GET", f"/customers/{cid}")
        version = next(o["version_no"] for o in current["opportunities"] if o["id"] == a["id"])
        await req(
            "XS001",
            "POST",
            f"/customers/{cid}/opportunities",
            201,
            json={
                **opportunity,
                "action": "update",
                "opportunity_id": a["id"],
                "probability": 50,
                "version_no": version,
            },
            headers=key(),
        )
        notifications = await req("XS001", "GET", "/notifications", params={"page_size": 100})
        changes = [n for n in notifications["items"]
                   if n["template_code"] == "business_changed" and n["object_id"] == a["id"]]
        check("商机变化进入总览所用通知接口", any(n["payload"].get("tone") == "green" for n in changes))
        for index, event in enumerate(("accept", "reject")):
            task_body = dict(
                description=f"{name}任务{index}：核对演示方案，测试后关闭。",
                assignee_account_code="XS002",
                due_at=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
                priority_code="high",
            )
            idem = key()
            task = await req("XS001", "POST", "/tasks", 201, json=task_body, headers=idem)
            tid = state["ids"][f"task_{event}"] = task["id"]
            repeat = await req("XS001", "POST", "/tasks", 201, json=task_body, headers=idem)
            check(f"{event}任务请求重试幂等", repeat["id"] == tid)
            notes = await req("XS002", "GET", "/notifications", params={"page_size": 100})
            check(
                f"{event}接收人分配卡片唯一",
                sum(n["object_id"] == tid and n["template_code"] == "task_assigned" for n in notes["items"]) == 1,
            )
            await req("XS001", "POST", f"/tasks/{tid}/events", 403, json={"event_type": "accept"})
            await req("XS002", "POST", f"/tasks/{tid}/events", 409, json={"event_type": "complete"})
            idem = key()
            action = {"event_type": event, "note": "仅为演示验收；模拟资源冲突"}
            await req("XS002", "POST", f"/tasks/{tid}/events", json=action, headers=idem)
            await req("XS002", "POST", f"/tasks/{tid}/events", json=action, headers=idem)
            if event == "accept":
                await req(
                    "XS002",
                    "POST",
                    f"/tasks/{tid}/events",
                    json={"event_type": "complete", "note": "演示核对已完成"},
                    headers=key(),
                )
            detail = await req("XS001", "GET", f"/tasks/{tid}")
            check(f"{event}流程状态正确", detail["status"] == ("completed" if event == "accept" else "cancelled"))
            notes = await req("XS001", "GET", "/notifications", params={"page_size": 100})
            needed = ("task_accepted", "task_completed") if event == "accept" else ("task_rejected",)
            check(
                f"{event}发起人反馈卡片唯一",
                all(
                    sum(n["object_id"] == tid and n["template_code"] == t for n in notes["items"]) == 1 for t in needed
                ),
            )
        target = dict(scope="self", year=today.year, kind="recognized", amount=1800000)
        # Existing real target values are preserved; only initialize an unset demo account target.
        perf = await req("XS001", "GET", "/profile/performance", params={"period": "year"})
        target["amount"] = perf["targets"].get("recognized", target["amount"])
        idem = key()
        first = await req("XS001", "POST", "/profile/sales-targets", json=target, headers=idem)
        again = await req("XS001", "POST", "/profile/sales-targets", json=target, headers=idem)
        check("年度目标真实保存及幂等", first["id"] == again["id"] and first["version_no"] == again["version_no"])
        await req(
            "XS002", "POST", "/profile/sales-targets", 403, json={**target, "scope": "person", "account_code": "XS001"}
        )
        for period in ("week", "quarter", "year"):
            perf = await req("XS001", "GET", "/profile/performance", params={"period": period})
            check(f"{period}营销表现取数据库目标", float(perf["targets"]["recognized"]) == float(target["amount"]))
        await req("ZJ001", "GET", "/profile/performance", params={"scope": "team"})
        await req("ZJL001", "GET", "/profile/performance", params={"scope": "department"})
        check("总监团队及总经理部门汇总可查询", True)
    finally:
        state["finished_at"] = datetime.now(UTC).isoformat()
        save()
        for client in clients.values():
            await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.output))
