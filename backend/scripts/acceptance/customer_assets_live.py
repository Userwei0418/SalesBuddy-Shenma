"""Fictional customer assets through real APIs. Output contains evidence IDs, never tokens."""

import argparse
import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx


async def main(base_url, output):
    checks = []
    async with httpx.AsyncClient(base_url=base_url, timeout=40) as client:

        async def req(method, path, expected=200, **kwargs):
            response = await client.request(method, path, **kwargs)
            assert response.status_code == expected, (path, response.status_code, response.text[:280])
            return response.json()

        sessions = {}
        for account in ("XS001", "XS002", "XS003", "ZJ001", "ZJL001"):
            sessions[account] = await req("POST", "/auth/session", json={"account_code": account})

        def use(account):
            client.headers["Authorization"] = "Bearer " + sessions[account]["access_token"]

        suffix = uuid4().hex[:6]

        async def create_customer(account, name):
            use(account)
            return await req(
                "POST",
                "/customers",
                201,
                json=dict(
                    name=f"【演示】{name}·{suffix}",
                    industry="软件服务",
                    customer_type="商机客户",
                    level_code="Tier-2",
                    source="销售线索",
                    target_team=sessions[account]["actor"]["team_names"][0],
                    partner_name="",
                    contact_name="林悦",
                    contact_title="部门经理",
                    contact_role="决策者",
                ),
            )

        c = await create_customer("XS001", "海岚物流")
        zero = await create_customer("XS001", "星桥零售")
        empty = await create_customer("XS001", "辰星制造")
        foreign = await create_customer("XS003", "东区客户")
        use("XS001")
        as_of = date.fromisoformat((await req("GET", "/customer-assets"))["as_of"])
        opps = []
        for name, status, probability in [("客服助手一期", "won", 100), ("质检助手二期", "open", 30)]:
            opps.append(
                await req(
                    "POST",
                    f"/customers/{c['id']}/opportunities",
                    201,
                    json=dict(
                        name=name,
                        amount=500000,
                        probability=probability,
                        status=status,
                        closure_confirmed=status == "won",
                        expected_close_date=(as_of + timedelta(days=30)).isoformat(),
                        quarterly_forecasts=[
                            dict(year=as_of.year, quarter=4, recognized_amount=900000, collection_amount=800000)
                        ],
                    ),
                )
            )
        await req("GET", "/customer-assets", params={"customer_id": c["id"]})
        data = dict(
            customer_id=c["id"],
            opportunity_id=opps[0]["id"],
            kind="recognized",
            amount=240000,
            occurred_on=as_of.isoformat(),
            source_ref=f"演示台账-{suffix}-01",
            note="虚构业务情节，真实接口入库",
            request_id=str(uuid4()),
            confirmed=True,
        )
        await req("POST", "/customer-assets", 403, json=data)
        use("ZJL001")
        first = await req("POST", "/customer-assets", 201, json=data)
        again = await req("POST", "/customer-assets", 201, json=data)
        assert first["id"] == again["id"] and again["replayed"]
        await req("POST", "/customer-assets", 409, json={**data, "request_id": str(uuid4())})
        await req("POST", "/customer-assets", 409, json={**data, "amount": 1})
        await req("POST", "/customer-assets", 422, json={**data, "request_id": str(uuid4()), "customer_id": zero["id"]})
        await req(
            "POST", "/customer-assets", 422, json={**data, "occurred_on": (as_of + timedelta(days=1)).isoformat()}
        )
        await req("POST", "/customer-assets", 422, json={**data, "confirmed": False})
        entries = [first["id"]]

        async def actual(cid, amount, kind="recognized", when=None, oid=None):
            body = {
                **data,
                "customer_id": cid,
                "opportunity_id": oid,
                "amount": amount,
                "kind": kind,
                "occurred_on": (when or as_of).isoformat(),
                "source_ref": f"演示台账-{suffix}-{uuid4().hex[:8]}",
                "request_id": str(uuid4()),
            }
            row = await req("POST", "/customer-assets", 201, json=body)
            entries.append(row["id"])
            return row

        await actual(c["id"], 60000, oid=opps[1]["id"])
        await actual(c["id"], 120000, "collection", oid=opps[0]["id"])
        await actual(c["id"], 400000, when=date(as_of.year - 1, 12, 31), oid=opps[0]["id"])
        await actual(c["id"], 200000, "collection", date(as_of.year - 1, 12, 31), opps[0]["id"])
        await actual(zero["id"], 0)
        await actual(zero["id"], 50000, "collection")
        await actual(foreign["id"], 990000)
        invalid = await actual(c["id"], 999)
        await req("POST", f"/customer-assets/{invalid['id']}/void", json={"reason": "演示作废留痕", "confirmed": True})
        await req("POST", f"/customer-assets/{invalid['id']}/void", json={"reason": "重复请求", "confirmed": True})
        use("XS001")
        annual = await req("GET", "/customer-assets", params={"period": "year", "customer_id": c["id"], "page_size": 1})
        assert annual["summary"]["recognized_amount"] == 300000 and annual["summary"]["collection_amount"] == 120000
        assert annual["has_more"] and len(annual["items"]) == 1
        all_years = await req("GET", "/customer-assets", params={"period": "all", "customer_id": c["id"]})
        assert (
            all_years["summary"]["recognized_amount"] == 700000 and all_years["summary"]["collection_amount"] == 320000
        )
        assert len(all_years["items"]) == 5
        checks += [
            "period-and-history",
            "actual-not-forecast",
            "multiple-opportunities",
            "aggregate-before-pagination",
            "request-idempotency",
            "source-duplicate-409",
            "human-confirm-required",
            "future-date-rejected",
            "void-retained-excluded",
        ]
        z = await req("GET", "/customer-assets", params={"customer_id": zero["id"]})
        assert z["summary"]["recognized_amount"] == 0
        e = await req("GET", "/customer-assets", params={"customer_id": empty["id"]})
        assert e["summary"]["recognized_amount"] is None
        map_items = (await req("GET", "/customer-assets/map"))["items"]
        assert any(x["id"] == empty["id"] for x in map_items)
        linked = await req(
            "GET", "/customer-assets", params={"customer_id": c["id"], "opportunity_id": opps[0]["id"], "period": "all"}
        )
        assert linked["summary"]["recognized_amount"] == 640000
        use("XS002")
        await req("GET", "/customer-assets", 404, params={"customer_id": c["id"]})
        assert not any(x["customer_id"] == c["id"] for x in (await req("GET", "/customer-assets"))["items"])
        use("ZJ001")
        await req("GET", "/customer-assets", 404, params={"customer_id": foreign["id"]})
        assert c["id"] in [x["customer_id"] for x in (await req("GET", "/customer-assets"))["items"]]
        use("ZJL001")
        assert foreign["id"] in [x["customer_id"] for x in (await req("GET", "/customer-assets"))["items"]]
        checks += [
            "zero-versus-missing",
            "no-opportunity-kept-on-map",
            "customer-unlinked-actuals",
            "opportunity-drilldown",
            "sales-isolation",
            "supervisor-team-isolation",
            "manager-scope",
        ]
        use("XS001")
        task = await req(
            "POST",
            "/tasks",
            201,
            json=dict(
                description="【演示】复核海岚物流质检助手试点范围",
                assignee_account_code="XS001",
                customer_id=c["id"],
                opportunity_id=opps[1]["id"],
                priority_code="normal",
                due_at=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
            ),
        )
        await req("POST", f"/tasks/{task['id']}/events", json={"event_type": "accept", "note": "演示接受"})
        await req(
            "POST", f"/tasks/{task['id']}/events", json={"event_type": "complete", "note": "演示完成，客户档案保留历史"}
        )
        detail = await req("GET", f"/customers/{c['id']}")
        assert next(t for t in detail["tasks"] if t["id"] == task["id"])["opportunity_id"] == opps[1]["id"]
        checks.append("task-opportunity-persisted-and-completed")
        result = dict(
            base_url=base_url,
            as_of=as_of.isoformat(),
            customer=c,
            zero_customer=zero,
            empty_customer=empty,
            foreign_customer=foreign,
            opportunities=opps,
            actual_ids=entries,
            voided_id=invalid["id"],
            task_id=task["id"],
            expected=dict(year_recognized=300000, year_collection=120000, all_recognized=700000, all_collection=320000),
            checks=checks,
        )
        Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {"customer_id": c["id"], "customer_name": c["name"], "checks": checks, "output": output},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.output))
