"""Customer-host synthetic Agent contracts; no database access or CRM writes."""

import argparse
import asyncio
import json
import os
import pwd
import socket
from pathlib import Path
from time import monotonic

from sales_backend.domain.agent import ActorContext
from sales_backend.integrations.supreme_fde import FdeClient, FdeConfig
from sales_backend.services.agent_platform.result_contracts import validate_run_result
from sales_backend.services.agent_run.models import RunInput

ACTOR = ActorContext(workspace_id="f98b0486-0f83-4930-8219-b86c73c764ef",
                     user_id="b792b9b5-f71e-49f0-9421-f9dda981c2b2",
                     role="administrator", data_scope="workspace")
SERVER_FIELDS = {"customer_name": "合成验收客户", "customer_type": "客户",
                 "created_date": "2026-09-24"}
CASES = [
    {"name": "opportunity_create", "capability": "opportunity_draft",
     "text": "为合成验收客户新增独立项目：传感器试点，金额80000元，概率50%，预计2026年12月20日成交。只生成待确认草稿。",
     "facts": {"customer": {"id": "77777777-7777-4777-8777-777777777777", "name": "合成验收客户"},
               "current_opportunities": [], "data_as_of": "2026-09-24T08:00:00+08:00"}},
    {"name": "opportunity_none", "capability": "opportunity_draft",
     "text": "本次仅维护日常客户关系，没有新项目，也不更新任何商机。",
     "facts": {"customer": {"id": "77777777-7777-4777-8777-777777777777", "name": "合成验收客户"},
               "current_opportunities": [], "data_as_of": "2026-09-24T08:00:00+08:00"}},
    {"name": "visit_repeat", "capability": "visit_entry",
     "text": "再次拜访，2026年9月24日，与王经理沟通。客户测试5条问题，4条准确，1条缺少最新退款规则；同意补齐资料后小范围试点。下一步：2026年9月27日前销售提供试点方案，王经理提供20条脱敏样本。",
     "facts": {"data_as_of": "2026-09-24T08:00:00+08:00", "server_fields": SERVER_FIELDS,
               "visit_stage": "structure", "is_first_visit": False}},
]


def coaching_cases():
    from sales_backend.domain.advice import messages
    from sales_backend.services.competency_reviews import CompetencyReviewHandler

    visit = {"id": "22222222-2222-4222-8222-222222222222",
             "follow_up_record": "客户同意安排试点，但试点范围、验收标准和负责人尚未确认。",
             "next_action": "9月27日前与客户确认试点范围、验收标准和负责人。"}
    cases = []
    for kind, empty in [("opportunity", False), ("visit", False), ("visit", True)]:
        subject = ({"id": "33333333-3333-4333-8333-333333333333", "name": "合成试点商机",
                    "status": "open", "amount": 80000} if kind == "opportunity" else
                   {**visit, **({"follow_up_record": "", "next_action": ""} if empty else {})})
        facts = {"subject": subject, "records": {"visits": [visit]} if kind == "opportunity" else {},
                 "actor_context": {"role": "sales"}, "data_as_of": "2026-09-24T08:00:00+08:00"}
        cases.append({"name": kind + ("_empty" if empty else "_advice"), "capability": kind + "_advice",
                      "text": "仅根据所给资料给出建议。", "facts": facts, "empty": empty,
                      "backend_prompt": messages(kind, "overview", facts)[0].content})
    framework = {"dimensions": [{"code": f"dimension_{i}", "name": name, "weight": 1}
                                for i, name in enumerate(["需求洞察", "决策链经营", "方案沟通", "商机推进",
                                                          "客户关系", "跟进执行"])]}
    for empty in [False, True]:
        facts = {"framework": framework, "visits": [] if empty else [
            {"visit_id": visit["id"], "follow_up_record": visit["follow_up_record"],
             "next_action": visit["next_action"]}], "visit_count": 0 if empty else 1,
                 "review_date": "2026-09-24", "window_days": 30,
                 "data_as_of": "2026-09-24T08:00:00+08:00"}
        cases.append({"name": "competency_empty" if empty else "competency_review",
                      "capability": "competency_review", "text": "按给定六维框架进行证据化复盘。",
                      "facts": facts, "empty": empty,
                      "backend_prompt": CompetencyReviewHandler._messages(framework, facts)[0].content})
    return cases


def check(case, answer):
    if case["capability"] in {"opportunity_advice", "visit_advice"}:
        from sales_backend.domain.advice import validate_advice
        clean = validate_advice(answer, case["facts"])
        assert bool(clean["suggestions"]) is not case["empty"]
        return clean
    if case["capability"] == "competency_review":
        from sales_backend.domain.competency_review import validate_competency_result
        clean = validate_competency_result(answer, case["facts"]["framework"], case["facts"])
        if case["empty"]:
            assert all(not dimension["evidence"] for dimension in clean["dimensions"])
        else:
            assert any(dimension["evidence"] for dimension in clean["dimensions"])
        return clean
    run = RunInput("synthetic-contract-run", "synthetic-contract-conversation", case["text"],
                   "visit_entry" if case["capability"] == "visit_quality" else case["capability"],
                   None, ACTOR)
    clean = validate_run_result(run, answer, case["facts"])
    if case["name"] == "opportunity_create":
        assert clean["action"] == "create"
        assert clean["amount"] == 80000 and clean["probability"] == 50
        assert clean["expected_close_date"] == "2026-12-20"
        assert not clean["opportunity_id"]
    elif case["name"] == "opportunity_none":
        assert clean["action"] == "none"
    elif case["name"] == "visit_quality":
        assert clean["fields"] == case["facts"]["fields"]
        assert clean["summary"] == case["facts"]["summary"]
        action = clean["quality_review"]["next_action"]
        assert action["passed"] and action["time_found"] and action["goal_or_plan_found"]
    else:
        fields = clean["fields"]
        assert {key: fields[key] for key in SERVER_FIELDS} == SERVER_FIELDS
        assert fields["is_first_visit"] is False and fields["contact_name"] == "王经理"
        assert "退款" in fields["follow_up_record"] and "20" in fields["next_action"]
    return clean


async def run(bindings, output, suite, case_names=None):
    results = []
    cases = list(CASES) if suite == "core" else coaching_cases()
    if case_names:
        if set(case_names) - {case["name"] for case in cases}:
            raise ValueError("Unknown case for selected suite")
        cases = [case for case in cases if case["name"] in case_names]
    for case in cases:
        binding = bindings[case["capability"]]
        config = FdeConfig("https://ops-salesbuddy.shenzhoukuntai.com:18899/v1", binding["api_key"],
                           "agent_final", timeout_seconds=30, ca_bundle_path="/etc/shenma-sales/agent-ca.pem")
        query = {"mode": case["capability"], "role": "sales", "user_text": case["text"],
                 "current_time": case["facts"]["data_as_of"], "facts": case["facts"]}
        if "backend_prompt" in case:
            query["backend_prompt"] = case["backend_prompt"]
        result = {"case": case["name"], "capability": case["capability"], "passed": False}
        started = monotonic()
        try:
            async with FdeClient(config) as client:
                response = await client.chat(query=json.dumps(query, ensure_ascii=False),
                                             user="shenma-synthetic-contract", inputs={})
            answer = json.loads(response.answer)
            # Keep synthetic output for manual semantic review, including a rejected result.
            result["answer"] = answer
            result["validated"] = check(case, answer)
            result["passed"] = True
            if case["name"] == "visit_repeat":
                clean = result["validated"]
                cases.append({"name": "visit_quality", "capability": "visit_quality", "text": case["text"],
                              "facts": {**case["facts"], "visit_stage": "quality", "fields": clean["fields"],
                                        "summary": clean["summary"],
                                        "company_policy": {"definition": {}}}})
        except (AssertionError, ValueError, TypeError, KeyError, RuntimeError, OSError) as error:
            # Never include upstream response bodies or credential-bearing exception strings.
            result["error_type"] = type(error).__name__
        result["elapsed_ms"] = round((monotonic() - started) * 1000)
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k not in {"answer", "validated"}},
                         ensure_ascii=False), flush=True)
    json.dump({"service_uid": os.getuid(), "synthetic_only": True, "business_writes": False,
               "validation_scope": "domain contracts and explicit case assertions; semantic review is separate",
               "results": results}, output, ensure_ascii=False, indent=2)
    return all(item["passed"] for item in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--suite", choices=("core", "coaching"), default="core")
    parser.add_argument("--case", action="append", dest="case_names")
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != "salesbuddy":
        raise SystemExit("Run as root on customer salesbuddy only")
    bindings = json.loads(Path("/var/lib/shenma-provision/agent-runtime-bindings.json").read_text())
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    service = pwd.getpwnam("shenma-sales")
    os.initgroups(service.pw_name, service.pw_gid)
    os.setgid(service.pw_gid)
    os.setuid(service.pw_uid)
    with os.fdopen(fd, "w") as output:
        passed = asyncio.run(run(bindings, output, args.suite, args.case_names))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
