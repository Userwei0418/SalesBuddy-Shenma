"""Current demo DB: real file parsing, model review, FDE attendance and access revocation.

Requires fde_identity_live.py evidence; password is read from stdin, never persisted.
No model mocking, client score injection or direct business-table writes.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx


async def main(args):
    source = json.loads(args.business.read_text())
    password = sys.stdin.readline().rstrip("\r\n")
    cid, oid = source["ids"]["customer"], source["ids"]["opportunity"]
    people = [source["actors"][c]["id"] for c in ("FDE001", "FDE002")]
    state = (
        json.loads(args.output.read_text()) if args.output.exists()
        else {"data_source": "current_database_via_https", "checks": [], "ids": {}}
    )
    clients = {}
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    future = (today + timedelta(days=3)).isoformat()

    def check(name, passed, **facts):
        state["checks"].append({"name": name, "passed": bool(passed), **facts})
        args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        print(json.dumps(state["checks"][-1], ensure_ascii=False), flush=True)
        assert passed, name

    async def req(code, method, path, expected=200, **kwargs):
        response = await clients[code].request(method, path, **kwargs)
        if response.status_code != expected:
            raise RuntimeError(f"{code} {method} {path}: expected {expected}, got {response.status_code}")
        return response.json() if response.content else None

    async def settled(code, path, complete):
        for _ in range(120):
            value = await req(code, "GET", path)
            if value.get("status") in complete:
                return value
            if value.get("status") in {"failed", "cancelled", "superseded"}:
                raise RuntimeError(f"{path} ended {value['status']} code={value.get('error_code')}")
            await asyncio.sleep(2)
        raise RuntimeError(f"{path} timed out")

    def checkpoint():
        args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2))

    async def run(text, name):
        if state["ids"].get(name):
            return await settled("XS001", f"/agent/runs/{state['ids'][name]}", {"waiting_human", "succeeded"})
        conv = await req("XS001", "POST", "/conversations", 201, json={"mode": "visit_entry", "customer_id": cid})
        accepted = await req(
            "XS001",
            "POST",
            f"/conversations/{conv['id']}/messages",
            202,
            json={"text": text, "client_message_id": str(uuid4())},
        )
        state["ids"][name] = accepted["run_id"]
        checkpoint()
        return await settled("XS001", f"/agent/runs/{accepted['run_id']}", {"waiting_human", "succeeded"})

    async def set_members(selected):
        detail = await req("XS001", "GET", f"/opportunities/{oid}/detail")
        opportunity = next(o for o in detail["opportunities"] if o["id"] == oid)
        return await req(
            "XS001", "PUT", f"/opportunities/{oid}/fde-members",
            json={"member_ids": selected, "version_no": opportunity["version_no"]},
            headers={"Idempotency-Key": str(uuid4())},
        )

    async def archive(run_result, attendees, opportunity_id, source_import=None, expected=201):
        fields = {
            **run_result["result"]["fields"],
            "interaction_at": today.isoformat(),
            "created_date": today.isoformat(),
            "contact_name": "林悦",
            "opportunity_id": opportunity_id,
            "_quality_review_run_id": run_result["id"],
            "source_import_id": source_import,
        }
        payload = {"customer_id": cid, "fields": fields, "fde_participant_ids": attendees}
        headers = {"Idempotency-Key": str(uuid4())}
        result = await req("XS001", "POST", "/visits", expected, json=payload, headers=headers)
        if expected == 201:
            again = await req("XS001", "POST", "/visits", 201, json=payload, headers=headers)
            assert result["id"] == again["id"]
        return result

    try:
        for code in ("XS001", "FDE001", "FDE002", "FDE003", "FDEL001"):
            clients[code] = httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=150)
            auth = await req(
                code,
                "POST",
                "/auth/password/login",
                json={"account_code": code, "password": password, "workspace": "demo-sales-workspace"},
            )
            clients[code].headers["Authorization"] = "Bearer " + auth["access_token"]
        password = ""
        if "baseline" not in state:
            state["baseline"] = {
                "team": (await req("FDEL001", "GET", "/fde/dashboard?scope=team"))["summary"],
                "unlinked_member": (await req("FDE003", "GET", "/fde/dashboard"))["summary"],
            }
            checkpoint()
        customer = await req("XS001", "GET", f"/customers/{cid}")
        text = (
            f"{today}，刘志德与FDE周玮、张家涛一同视频拜访{customer['name']}客服经理林悦。"
            "本次不是首次拜访。12名客服每日处理80条咨询，退款规则散落在3个知识库。"
            "双方用5个真实问题演示，4个找到准确依据，1个缺最新退款版本。"
            "客户认可知识检索方向，要求补齐资料版本及业务生效时间，尚未接受报价或决定采购。"
            f"下一步刘志德于{future}前提交两周试点计划与5条验收标准；周玮整理技术限制清单，"
            f"林悦于{future}前提供20条脱敏咨询样本，双方当天15点复核试点范围。"
        )
        if not state["ids"].get("import"):
            imported = await req(
                "XS001", "POST", "/visit-imports", 202,
                files={"file": (source["prefix"] + "联合拜访.md", text.encode())},
            )
            state["ids"]["import"] = imported["id"]
            checkpoint()
        imported = {"id": state["ids"]["import"]}
        document = await settled("XS001", f"/visit-imports/{imported['id']}", {"succeeded"})
        check("真实MD上传与Worker解析", document["extracted_text"].strip() == text)
        result = await run(document["extracted_text"], "review_run")
        score = result["result"]["quality_review"]["follow_up_score"]
        state["ids"]["review_run"] = result["id"]
        check("真实模型结构化与评分", score > 60, score=score, run_id=result["id"])
        visit = await archive(result, people, oid, imported["id"])
        state["ids"]["visit"] = visit["id"]
        detail = await req("FDE001", "GET", f"/visits/{visit['id']}")
        check("销售归档实际参与FDE及幂等", set(detail["fde_participant_ids"]) == set(people))
        await req("FDE001", "GET", f"/visit-imports/{imported['id']}")
        team = await req("FDEL001", "GET", "/fde/dashboard?scope=team")
        baseline = state["baseline"]["team"]
        extra = int(bool(state["ids"].get("unlinked_visit")))
        check(
            "一场拜访两名FDE计一场两人次",
            team["summary"]["period_visits"] == baseline["period_visits"] + 1 + extra
            and team["summary"]["period_visit_people"] == baseline["period_visit_people"] + 2 + extra,
        )
        notifications = await req("FDE001", "GET", "/notifications")
        check(
            "FDE收到协同归档卡片",
            any(
                n["template_code"] == "visit_archived" and n.get("payload", {}).get("visit_id") == visit["id"]
                for n in notifications["items"]
            ),
        )
        advice = await req("FDE001", "POST", "/advice", json={"subject_kind": "opportunity", "subject_id": oid})
        state["ids"]["advice"] = advice["id"]
        checkpoint()
        advice = await settled("FDE001", f"/advice/{advice['id']}", {"succeeded"})
        state["ids"]["advice"] = advice["id"]
        check("FDE真实业务建议模型链", advice["status"] == "succeeded", analysis_id=advice["id"])
        await set_members(people[1:])
        state["restore_members"] = True
        checkpoint()
        await req("FDE001", "GET", f"/visits/{visit['id']}", 404)
        await req("FDE001", "GET", f"/visit-imports/{imported['id']}", 404)
        await req("FDE001", "GET", f"/advice/{advice['id']}", 404)
        history = await req("FDE001", "GET", "/fde/activity")
        item = next(r for r in history["items"] if r["id"] == visit["id"])
        check("移出收回正文文件AI但保留本人历史摘要", not item["can_read_detail"] and "follow_up_record" not in item)
        await set_members(people)
        state["restore_members"] = False
        checkpoint()
        low = await run("拜访审核 v2\n拜访结果：今天聊了一下，感觉还可以。\n下一步行动计划：有空再联系。", "low_review_run")
        low_score = low["result"]["quality_review"]["follow_up_score"]
        check("低质量输入取得真实低分", low_score <= 60, score=low_score, run_id=low["id"])
        rejected = await archive(low, people, oid, expected=422)
        check("低分禁止归档", str(rejected.get("detail", "")).startswith("AI 质量审核"))
        no_opp = await run(text + " 本次作为不关联商机的沟通记录，由叶源协助。", "unlinked_review_run")
        unlinked = await archive(no_opp, [source["actors"]["FDE003"]["id"]], None)
        state["ids"]["unlinked_visit"] = unlinked["id"]
        await req("FDE003", "GET", f"/customers/{cid}", 404)
        own = await req("FDE003", "GET", "/fde/dashboard")
        check(
            "无商机拜访可记参与但不授予客户范围",
            own["summary"]["period_visits"] == state["baseline"]["unlinked_member"]["period_visits"] + 1
            and own["summary"]["opportunities"] == state["baseline"]["unlinked_member"]["opportunities"],
        )
    finally:
        if state.get("restore_members") and clients.get("XS001"):
            await set_members(people)
            state["restore_members"] = False
        checkpoint()
        for client in clients.values():
            if "Authorization" in client.headers:
                try:
                    await client.post("/auth/logout")
                except httpx.HTTPError:
                    pass
            await client.aclose()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--business", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    asyncio.run(main(p.parse_args()))
