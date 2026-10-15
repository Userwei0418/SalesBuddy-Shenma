"""Explicitly invoked demo task acceptance; real HTTP, no token persisted or printed."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

BASE = "https://www.ericepc.com/api/v1"
PREFIX = "【演示任务验收0910】"


def login(account):
    client = httpx.Client(base_url=BASE, timeout=30)
    response = client.post("/auth/session", json={"account_code": account})
    response.raise_for_status()
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return client


def run(args):
    target = Path(args.state)
    state = json.loads(target.read_text()) if target.exists() else {"tasks": {}, "checks": []}
    clients = {account: login(account) for account in ("XS001", "XS002", "ZJL001")}
    creator, receiver, outsider = clients["XS002"], clients["XS001"], clients["ZJL001"]

    def record(name, ok, detail):
        state["checks"].append({"name": name, "passed": bool(ok), "detail": detail})
        print(json.dumps(state["checks"][-1], ensure_ascii=False), flush=True)

    def notes(client, task_id):
        response = client.get("/notifications", params={"page_size": 100})
        response.raise_for_status()
        return [x for x in response.json()["items"] if x["object_id"] == task_id]

    def create(key, description, priority="medium"):
        if key in state["tasks"]:
            return state["tasks"][key]
        body = {
            "description": PREFIX + description,
            "assignee_account_code": "XS001",
            "due_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "priority_code": priority,
        }
        r = creator.post("/tasks", json=body)
        r.raise_for_status()
        item = r.json()
        state["tasks"][key] = {"id": item["id"], "description": body["description"], "created_body": body}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        return state["tasks"][key]

    try:
        if args.phase == "prepare":
            for key, description, priority in (
                ("accept", "A-接受并完成：请核对演示方案的3个字段，完成后反馈核对结果；本任务仅用于功能验收。", "high"),
                ("reject", "B-拒绝说明：请在演示环境准备临时方案；因模拟资源冲突，由接收人填写拒绝理由。", "normal"),
            ):
                item = create(key, description, priority)
                r = receiver.get("/tasks/" + item["id"])
                r.raise_for_status()
                n = notes(receiver, item["id"])
                record(
                    key + ":创建后接收人可见且只有一条分配通知",
                    r.json()["status"] == "pending_confirm"
                    and len([x for x in n if x["template_code"] == "task_assigned"]) == 1,
                    {"task_id": item["id"], "status": r.json()["status"], "notifications": n},
                )
            accept = state["tasks"]["accept"]["id"]
            base = state["tasks"]["accept"]["created_body"]
            for name, changes, expected in (
                ("空描述", {"description": ""}, 422),
                ("截止时间已过", {"due_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()}, 409),
                ("不存在接收人", {"assignee_account_code": "NONEXISTENT_DEMO_0910"}, 404),
                ("非法优先级", {"priority_code": "invalid"}, 422),
            ):
                r = creator.post("/tasks", json={**base, **changes})
                record(name, r.status_code == expected, {"status": r.status_code})
            for name, client, event, note, expected in (
                ("发起人不能替接收人接受", creator, "accept", None, 403),
                ("无关账号不能接受", outsider, "accept", None, 404),
                ("拒绝理由为空", receiver, "reject", "  ", 409),
                ("未接受不能直接完成", receiver, "complete", "不应成功", 409),
            ):
                r = client.post(f"/tasks/{accept}/events", json={"event_type": event, "note": note})
                record(name, r.status_code == expected, {"status": r.status_code, "response": r.json()})
        elif args.phase == "verify":
            for key in ("accept", "reject"):
                tid = state["tasks"][key]["id"]
                r = creator.get("/tasks/" + tid)
                r.raise_for_status()
                item = r.json()
                state["tasks"][key]["latest"] = item
                expected = "completed" if key == "accept" else "cancelled"
                record(
                    key + ":状态和历史回显",
                    item["status"] == expected,
                    {"task_id": tid, "status": item["status"], "events": item["events"]},
                )
                ns = notes(creator, tid)
                state["tasks"][key]["creator_notifications"] = ns
                templates = [n["template_code"] for n in ns]
                needed = ["task_accepted", "task_completed"] if key == "accept" else ["task_rejected"]
                record(key + ":发起人收到反馈通知", all(templates.count(t) == 1 for t in needed), templates)
                r = receiver.post(f"/tasks/{tid}/events", json={"event_type": key, "note": "重复操作"})
                record(key + ":重复响应被拦截", r.status_code == 409, {"status": r.status_code})
                r = outsider.get("/tasks/" + tid)
                record(key + ":无关账号不可见", r.status_code == 404, {"status": r.status_code})
            tid = state["tasks"]["accept"]["id"]
            r = receiver.post(f"/tasks/{tid}/events", json={"event_type": "complete", "note": "重复完成"})
            record(
                "重复完成幂等且通知不重复",
                r.status_code == 200
                and len([x for x in notes(creator, tid) if x["template_code"] == "task_completed"]) == 1,
                {"status": r.status_code},
            )
            tid = state["tasks"]["reject"]["id"]
            r = receiver.post(f"/tasks/{tid}/events", json={"event_type": "complete", "note": "不应完成"})
            record("已拒绝任务不能完成", r.status_code == 409, {"status": r.status_code})
        elif args.phase == "race":
            item = create("race", "C-并发响应：仅检验接受与拒绝同时请求时只生效一次。")
            tid = item["id"]
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda event: receiver.post(
                            f"/tasks/{tid}/events", json={"event_type": event, "note": "演示并发验证，非真实任务要求"}
                        ),
                        ["accept", "reject"],
                    )
                )
            codes = sorted(r.status_code for r in results)
            record("并发接受拒绝只生效一次", codes == [200, 409], codes)
            detail = receiver.get("/tasks/" + tid).json()
            if detail["status"] == "pending_execution":
                receiver.post(
                    f"/tasks/{tid}/events", json={"event_type": "complete", "note": "演示并发验收已结束"}
                ).raise_for_status()
            events = receiver.get("/tasks/" + tid).json()["events"]
            record(
                "并发响应只留一次接受或拒绝事件",
                len([e for e in events if e["event_type"] in {"accept", "reject"}]) == 1,
                [e["event_type"] for e in events],
            )
    finally:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        for client in clients.values():
            client.close()
    if any(not check["passed"] for check in state["checks"]):
        raise SystemExit("Task acceptance has failed checks; inspect the state file.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--phase", choices=["prepare", "verify", "race"], required=True)
    run(parser.parse_args())
