"""Authorized demo acceptance through HTTP and real SenseAudio; no embedded credentials.

Creates clearly named demo records. Pass --base-url for an isolated server first.
Results contain record IDs and assertions only; authentication stays in memory.
"""

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx


async def check(base_url):
    async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:

        async def req(method, path, expected=200, **kwargs):
            response = await client.request(method, path, **kwargs)
            assert response.status_code == expected, (path, response.status_code, response.text[:400])
            return response.json()

        auth = await req("POST", "/auth/session", json={"account_code": "XS001"})
        client.headers["Authorization"] = "Bearer " + auth["access_token"]
        actor = auth["actor"]
        today = datetime.now(timezone(timedelta(hours=8))).date()
        name = f"【演示】字段验收-{uuid4().hex[:6]}"
        customer = await req(
            "POST",
            "/customers",
            expected=201,
            json={
                "name": name,
                "industry": "",
                "customer_type": "潜在客户",
                "source": "销售线索",
                "target_team": actor["team_names"][0],
                "partner_name": "",
                "contact_name": "林悦",
                "contact_title": "客服经理",
                "contact_role": "影响者",
            },
        )
        cid = customer["id"]
        next_date = (today + timedelta(days=3)).isoformat()
        text = (
            f"{today.isoformat()}，刘志德和同事王新源通过视频拜访{name}客服经理林悦，"
            "本次目标是确认知识检索试点范围及验收方式。林悦介绍目前12名客服每天约需处理80条咨询，"
            "产品资料分散在三个知识库，尤其退款流程的版本不统一。双方演示了5个真实问题，"
            "其中4个找到正确说明，1个缺少最新退款规则；林悦同意先做小范围验证，尚未批准采购或报价。"
            f"下一步刘志德于{next_date}17:00前提交试点计划及验收清单，"
            f"王新源于{next_date}12:00前完成退款资料版本核对，"
            f"林悦于{next_date}15:00前提供20条脱敏咨询样本。该拜访暂不关联商机。"
        )
        conv = await req("POST", "/conversations", expected=201, json={"mode": "visit_entry"})
        accepted = await req(
            "POST",
            f"/conversations/{conv['id']}/messages",
            expected=202,
            json={"text": text, "client_message_id": str(uuid4())},
        )
        for _ in range(180):
            run = await req("GET", f"/agent/runs/{accepted['run_id']}")
            if run["status"] in {"waiting_human", "succeeded"}:
                break
            assert run["status"] not in {"failed", "cancelled"}, run.get("error_detail")
            await asyncio.sleep(1)
        else:
            raise AssertionError("Real model timeout")
        result = run["result"]
        score = result["quality_review"]["follow_up_score"]
        assert score >= 70 and result["quality_review"]["next_action"]["passed"], result["quality_review"]
        cols = (await req("GET", "/directory/colleagues"))["items"]
        colleague = next(x["id"] for x in cols if x["id"] != actor["user_id"])
        visit = await req(
            "POST",
            "/visits",
            expected=201,
            json={
                "customer_id": cid,
                "fields": {
                    **result["fields"],
                    "interaction_at": today.isoformat(),
                    "opportunity_id": None,
                    "collaborator_ids": [colleague],
                    "customer_type": "伪造类型",
                    "created_by_user_ref_id": colleague,
                    "_quality_review_run_id": run["id"],
                    "_follow_up_quality_score": 100,
                },
            },
        )
        vid = visit["id"]
        original = await req("GET", f"/visits/{vid}")
        assert original["customer_type"] == "潜在客户"
        assert original["creator_id"] == original["recorder_id"] == actor["user_id"]
        assert original["collaborator_ids"] == [colleague]
        assert original["within_seven_days"] is True and original["visit_date"] == today.isoformat()
        assert original["quality_review"]["follow_up_score"] == score
        assert original["opportunity_id"] is None
        detail = await req("GET", f"/customers/{cid}")
        assert not detail["opportunities"]
        await req("PATCH", f"/customers/{cid}", json={"customer_type": "商机客户", "version_no": detail["version_no"]})
        updated = await req(
            "PATCH",
            f"/visits/{vid}",
            json={
                "interaction_at": (today - timedelta(days=7)).isoformat(),
                "version_no": original["version_no"],
            },
        )
        assert updated["within_seven_days"] is False
        assert updated["created_at"] == original["created_at"] and updated["customer_type"] == "潜在客户"
        updated = await req(
            "PATCH",
            f"/visits/{vid}",
            json={
                "interaction_at": today.isoformat(),
                "version_no": updated["version_no"],
            },
        )
        home = await req("GET", "/assistant/home")
        assert vid in json.dumps(home), "Archive receipt missing"
        data = {"name": "客服 AI 助手", "amount": 50000, "probability": 30, "expected_close_date": next_date}
        opportunity = await req("POST", f"/customers/{cid}/opportunities", expected=201, json=data)
        availability = await req("GET", f"/customers/{cid}/opportunities/check-name", params={"name": " 客服ai助手 "})
        assert availability["available"] is False
        await req("POST", f"/customers/{cid}/opportunities", expected=409, json={**data, "name": "客服ai助手"})
        second = await req("POST", f"/customers/{cid}/opportunities", expected=201, json={**data, "name": "财务助手"})
        detail = await req("GET", f"/customers/{cid}")
        version = next(x["version_no"] for x in detail["opportunities"] if x["id"] == second["id"])
        update = {**data, "action": "update", "opportunity_id": second["id"], "version_no": version}
        await req("POST", f"/customers/{cid}/opportunities", expected=409, json=update)
        await req("POST", f"/customers/{cid}/opportunities", expected=201, json={**update, "name": "财务助手"})
        responses = await asyncio.gather(
            *[client.post(f"/customers/{cid}/opportunities", json={**data, "name": "并发验收助手"}) for _ in range(2)]
        )
        assert sorted(r.status_code for r in responses) == [201, 409]
        detail = await req("GET", f"/customers/{cid}")
        assert len([x for x in detail["opportunities"] if x["name"] == "并发验收助手"]) == 1
        print(
            json.dumps(
                {
                    "customer_id": cid,
                    "customer_name": name,
                    "visit_id": vid,
                    "run_id": run["id"],
                    "score": score,
                    "prompt_version": result["prompt_version"],
                    "opportunity_id": opportunity["id"],
                    "checks": [
                        "real-ai",
                        "archive-no-opportunity",
                        "trusted-identities-and-score",
                        "snapshot-after-customer-change",
                        "immutable-created-time",
                        "seven-days",
                        "receipt",
                        "duplicate-create",
                        "duplicate-rename",
                        "same-id-update",
                        "concurrent-409",
                    ],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    asyncio.run(check(parser.parse_args().base_url))
