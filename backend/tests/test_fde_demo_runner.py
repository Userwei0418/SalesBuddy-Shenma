"""Exercise the opt-in demo runner without network or database credentials."""
import argparse
import importlib.util
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/demo/fde_demo.py"
module_spec = importlib.util.spec_from_file_location("fde_demo_runner", SCRIPT)
demo_module = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(demo_module)


def args(tmp_path, **changes):
    values = dict(spec=SCRIPT.with_name("fde_scenarios.json"), state=tmp_path / "state.json",
                  base_url="https://demo.invalid/api/v1", workspace="demo-sales-workspace",
                  case=None, record=None, phase="prepare", model_wait_seconds=10,
                  conversation_idempotency_verified=False)
    values.update(changes)
    return argparse.Namespace(**values)


def session_handler(request):
    if request.url.path.endswith("/auth/password/login"):
        body = json.loads(request.content)
        return httpx.Response(200, json={"access_token": "fixture-access-token-" + body["account_code"],
            "refresh_token": "fixture-refresh-token", "must_change_password": False,
            "actor": {"account_code": body["account_code"], "role": body["role"],
                      "user_id": str(UUID(int=int.from_bytes(body["account_code"].encode(), "big"))),
                      "display_name": "测试参与人", "team_names": ["测试部门"]}})
    if request.url.path.endswith("/auth/logout"):
        return httpx.Response(204)
    return None


def runner(tmp_path, handler, **changes):
    return demo_module.Demo(args(tmp_path, **changes), "fixture-hidden-password",
                           httpx.MockTransport(lambda r: session_handler(r) or handler(r)))


def test_spec_covers_current_accounts_ownership_and_visit_cases(tmp_path):
    demo = runner(tmp_path, lambda r: pytest.fail("offline test must not call API"))
    spec = demo.spec
    assert len(spec["cases"]) == 8
    assert len(spec["customers"]) == 4
    assert len(spec["opportunities"]) == 6
    assert len([v for v in spec["visits"] if v["expected"] == "archive" and v["account"].startswith("FDE")]) == 13
    assert len([v for v in spec["visits"] if v["account"].startswith("XS")]) == 3
    assert {v["account"] for v in spec["visits"] if v["account"].startswith("FDE")} == {"FDE001", "FDE002", "FDE003", "FDEL001"}
    for visit in spec["visits"]:
        opportunity = demo.opportunities[visit["opportunity"]]
        if visit["account"].startswith("FDE"):
            assert visit["account"] in opportunity["members"]
            assert visit["participants"] == []
        else:
            assert visit["account"] == opportunity["owner"]
    demo.state["actors"]["FDE001"] = {"display_name": "周玮"}
    text = demo.material(spec["visits"][0])
    for term in ("客户主营业务", "预算初步估计", "影响者", "下一步", "12:00前", "10条脱敏样本"):
        assert term in text


def test_mutation_persists_key_before_write_and_replays_identical_request(tmp_path):
    requests = []
    fail_once = True

    def handler(request):
        nonlocal fail_once
        saved = json.loads((tmp_path / "state.json").read_text())
        assert saved["operations"]["write"]["request_id"] == request.headers["Idempotency-Key"]
        requests.append((request.headers["Idempotency-Key"], json.loads(request.content)))
        if fail_once:
            fail_once = False
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(201, json={"id": "created-record"})

    first = runner(tmp_path, handler)
    with pytest.raises(demo_module.DemoFailure, match="transport_failed"):
        first.mutation("write", "XS001", "POST", "/visits", {"value": "original"}, (201,))
    second = runner(tmp_path, handler)
    result = second.mutation("write", "XS001", "POST", "/visits", {"value": "changed retry must not replace frozen body"}, (201,))
    assert result["id"] == "created-record"
    assert requests[0] == requests[1]
    second.mutation("write", "XS001", "POST", "/visits", {}, (201,))
    assert len(requests) == 2
    state = (tmp_path / "state.json").read_text()
    for value in ("fixture-hidden-password", "fixture-access-token", "fixture-refresh-token"):
        assert value not in state


def test_uncertain_conversation_requires_verified_server_contract(tmp_path):
    demo = runner(tmp_path, lambda r: httpx.Response(201, json={"id": "conv"}))
    demo.state["operations"]["conversation:v01"] = {"account": "FDE001", "method": "POST", "path": "/conversations",
        "status": "planned", "attempted": True, "request_id": "old-key", "body": {"mode": "visit_entry"}}
    with pytest.raises(demo_module.DemoFailure, match="conversation_result_uncertain"):
        demo.mutation("conversation:v01", "FDE001", "POST", "/conversations", {}, (201,))
    assert not demo.clients
    demo.args.conversation_idempotency_verified = True
    assert demo.mutation("conversation:v01", "FDE001", "POST", "/conversations", {}, (201,))["id"] == "conv"


def test_archive_needs_content_review_for_current_real_candidate(tmp_path):
    demo = runner(tmp_path, lambda r: pytest.fail("not reviewed: no network writes"), record="v01")
    candidate = {"fields": {"follow_up_record": "事实", "next_action": "计划"},
                 "quality_review": {"follow_up_score": 90, "next_action": {"passed": True}}}
    record = {"candidate": candidate, "candidate_hash": demo_module.fingerprint(candidate), "run_id": "real-run"}
    demo.state["visits"]["v01"] = record
    with pytest.raises(demo_module.DemoFailure, match="explicit_candidate_review_required"):
        demo.archive()
    record["checked"] = {"candidate_hash": record["candidate_hash"], "reviewer": "content reviewer",
                         "reviewed_at": "2026-09-13", "run_id": "another-run"}
    with pytest.raises(demo_module.DemoFailure, match="explicit_candidate_review_required"):
        demo.archive()
    record["checked"]["run_id"] = "real-run"
    candidate["quality_review"]["follow_up_score"] = 60
    record["candidate_hash"] = record["checked"]["candidate_hash"] = demo_module.fingerprint(candidate)
    with pytest.raises(demo_module.DemoFailure, match="quality_gate_not_passed"):
        demo.archive()
    assert not demo.clients


@pytest.mark.parametrize("record_key", ["s01", "v01", "v10"])
def test_archive_feedback_uses_own_home_and_actual_fde_recipients(tmp_path, record_key):
    calls = []
    def handler(request):
        account = request.headers["Authorization"].removeprefix("Bearer fixture-access-token-")
        calls.append((account, request.url.path))
        assert request.method == "GET"
        if request.url.path.endswith("/assistant/home"):
            assert account == spec["account"]
            return httpx.Response(200, json={"archived_visits": [receipt]})
        assert request.url.path.endswith("/notifications") and account in participants
        return httpx.Response(200, json={"items": [{"id": "notification-" + account,
            "object_id": "visit", "template_code": "visit_archived", "status": "read"}]})

    demo = runner(tmp_path, handler, record=record_key)
    spec = demo.selected_visits()[0]
    participants = spec["participants"] or [spec["account"]]
    detail = {"id": "visit", "customer_id": "customer", "follow_up_score": 90, "next_action": "真实计划",
              "fde_participant_ids": [demo.actor_id(a) for a in participants]}
    receipt = {"id": "visit", "customer_id": "customer", "score": 90, "next_action": "真实计划"}
    record = {}
    demo.state["checks"][record_key + ":archive_notification"] = {"passed": False}
    demo.archive_feedback(spec, record, detail)
    assert record["archive_overview_receipt"] == receipt
    assert set(record["archive_notifications_by_account"]) == set(participants)
    assert demo.state["checks"][record_key + ":archive_notification"]["passed"]
    if record_key == "s01":
        assert ("XS001", "/api/v1/notifications") not in calls
        assert record["archive_notifications"] == []
    else:
        assert len(record["archive_notifications"]) == 1


@pytest.mark.parametrize("missing", ["home", "participant", "duplicate"])
def test_archive_feedback_fails_for_missing_home_or_wrong_collaboration_delivery(tmp_path, missing):
    def handler(request):
        if request.url.path.endswith("/assistant/home"):
            return httpx.Response(200, json={"archived_visits": [] if missing == "home" else [receipt]})
        return httpx.Response(200, json={"items": [] if missing == "participant" else [note, {**note, "id": "duplicate"}]})
    demo = runner(tmp_path, handler, record="s01")
    spec = demo.selected_visits()[0]
    detail = {"id": "visit", "customer_id": "customer", "follow_up_score": 90, "next_action": "真实计划",
              "fde_participant_ids": [demo.actor_id(a) for a in spec["participants"]]}
    receipt = {"id": "visit", "customer_id": "customer", "score": 90, "next_action": "真实计划"}
    note = {"id": "note", "object_id": "visit", "template_code": "visit_archived"}
    with pytest.raises(demo_module.DemoFailure, match="own_overview_receipt" if missing == "home" else "collaboration_notification"):
        demo.archive_feedback(spec, {}, detail)
    assert not demo.state["checks"].get("s01:archive_notification", {}).get("passed")


def test_prepare_rejects_same_name_without_this_batch_receipt(tmp_path):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/console/customers")
        return httpx.Response(200, json={"items": [{"id": "foreign", "name": "【演示】澄星制造"}]})
    demo = runner(tmp_path, handler, case="01_first_visit")
    with pytest.raises(demo_module.DemoFailure, match="same_name_customer_without_batch_receipt"):
        demo.prepare()
    assert demo.state["operations"] == {}


def test_review_records_real_model_output_without_archiving(tmp_path):
    requests = []
    candidate = {"fields": {"follow_up_record": "模型返回的原始沟通内容", "next_action": "模型返回的原始计划"},
                 "quality_review": {"follow_up_score": 65, "next_action": {"passed": True}}}

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/fde/visit-opportunities"):
            return httpx.Response(200, json={"items": [{"id": "opp"}]})
        if request.url.path.endswith("/conversations"):
            assert json.loads(request.content)["opportunity_id"] == "opp"
            return httpx.Response(201, json={"id": "conv", "opportunity_id": "opp"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(202, json={"run_id": "real-run"})
        if request.url.path.endswith("/agent/runs/real-run"):
            return httpx.Response(200, json={"status": "waiting_human", "result": candidate, "completed_at": "2026-09-13"})
        pytest.fail("unexpected endpoint")

    demo = runner(tmp_path, handler, record="v01", phase="review")
    demo.state["opportunities"]["quality"] = {"id": "opp", "customer_id": "customer"}
    demo.run()
    record = demo.state["visits"]["v01"]
    assert record["candidate"] == candidate
    assert record["archive_allowed"] is True
    assert record["checked"] is None
    assert not any(request.url.path.endswith("/visits") for request in requests)
    before = len(requests)
    demo.review()
    assert len(requests) == before


def test_handover_resume_does_not_requery_old_owners_revoked_task(tmp_path):
    demo = runner(tmp_path, lambda r: pytest.fail("completed event must not requery revoked task"))
    demo.state["operations"]["first_accept"] = {"status": "succeeded", "response": {"id": "task"}}
    assert demo.task_event("first_accept", "FDE003", {"id": "task"}, "accept", "note") == {"id": "task"}


def test_spec_or_target_changes_are_not_silently_applied_to_existing_state(tmp_path):
    demo = runner(tmp_path, lambda r: pytest.fail("offline"))
    demo.save()
    with pytest.raises(demo_module.DemoFailure, match="state_target_mismatch"):
        runner(tmp_path, lambda r: None, base_url="https://different.invalid/api/v1")
    demo.state["spec_hash"] = "other-spec"
    demo.save()
    with pytest.raises(demo_module.DemoFailure, match="spec_changed"):
        runner(tmp_path, lambda r: None)


def revision_fixture(tmp_path, handler, key="v05", edits=None, first=False, **kwargs):
    patch_path = tmp_path / "revision.json"
    demo = runner(tmp_path, handler, record=key, phase="revise", revision_file=patch_path, **kwargs)
    fields = {"follow_up_record": "销售和FDE共同核对8份材料，2份缺编号。", "next_action": "2026-09-16前刘志德提供2份编号。",
              "is_first_visit": first, "customer_main_business": "不应推断的主营业务", "customer_needs": "核对材料",
              "customer_budget": "暂未接受报价", "contact_role": "影响者", "contact_name": "沈宁"}
    candidate = {"mode": "visit_entry", "fields": fields, "run_id": "original-run", "prompt_version": "visit-v3-20260910.2",
                 "quality_review": {"follow_up_score": 88, "next_action": {"passed": True}, "suggestions": ["原始建议"]}}
    record = {"original_text": "不可改写的原始演示原文", "candidate": candidate, "candidate_hash": demo_module.fingerprint(candidate),
              "run_id": "original-run", "run_status": "waiting_human", "checked": {"reviewer": "original reviewer"},
              "ai_completed_at": "2026-09-13"}
    demo.state["visits"][key] = record
    demo.state["opportunities"][next(v["opportunity"] for v in demo.spec["visits"] if v["key"] == key)] = {
        "id": "opp", "customer_id": "customer"}
    patch = {"record": key, "source_candidate_hash": record["candidate_hash"],
             "fields": edits if edits is not None else {"customer_main_business": "", "customer_budget": "", "contact_role": ""},
             "reason": "基于原文移除无依据提取，不改变实际记录"}
    patch_path.write_text(json.dumps({"schema_version": 1, "revisions": [patch]}, ensure_ascii=False))
    return demo, record, patch


def test_revision_auxiliary_only_preserves_original_review_and_evidence_without_network(tmp_path):
    demo, record, _ = revision_fixture(tmp_path, lambda _: pytest.fail("unchanged review text must not call model"))
    original = json.loads(json.dumps(record["candidate"]))
    demo.revise()
    assert record["run_id"] == "original-run" and record["candidate"]["quality_review"] == original["quality_review"]
    assert record["candidate"]["fields"]["customer_budget"] == ""
    assert record["candidate"]["fields"]["contact_role"] == ""
    assert record["checked"] is None and record["original_text"] == "不可改写的原始演示原文"
    revision = record["revisions"][0]
    assert revision["source_candidate"] == original and revision["source_run_id"] == "original-run"
    assert revision["reused_review"] is True and revision["requires_ai_review"] is False
    assert record["candidate_hash"] == demo_module.fingerprint(record["candidate"]) != revision["source_candidate_hash"]
    assert not demo.clients and not demo.state["operations"]
    demo.revise()
    assert len(record["revisions"]) == 1


def test_body_revision_uses_v2_native_review_and_retains_manual_fields(tmp_path):
    from sales_backend.domain.visit_review import review_text
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/conversations"):
            return httpx.Response(201, json={"id": "review-conversation", "opportunity_id": "opp"})
        if request.url.path.endswith("/messages"):
            body = json.loads(request.content)
            assert body["text"] == review_text({**record["revisions"][0]["submitted_fields"]})
            assert body["text"].startswith("拜访审核 v2\n") and body["client_message_id"].startswith("revision:")
            return httpx.Response(202, json={"run_id": "revised-run"})
        reviewed_fields = {**record["revisions"][0]["submitted_fields"], "contact_name": "模型不应覆盖人工字段"}
        return httpx.Response(200, json={"id": "revised-run", "status": "waiting_human", "completed_at": "2026-09-13",
            "result": {"fields": reviewed_fields, "quality_review": {"follow_up_score": 92, "next_action": {"passed": True}}, "run_id": "revised-run"}})
    demo, record, _ = revision_fixture(tmp_path, handler, key="s02", edits={"follow_up_record": "刘志德与叶源共同核对8份材料，2份缺编号。"})
    demo.revise()
    assert len(calls) == 3 and record["run_id"] == "revised-run"
    assert record["candidate"]["quality_review"]["follow_up_score"] == 92
    assert record["candidate"]["fields"]["contact_name"] == "沈宁"
    entry = record["revisions"][0]
    assert entry["source_candidate"]["quality_review"]["follow_up_score"] == 88
    assert entry["review_candidate"]["fields"]["contact_name"] == "模型不应覆盖人工字段"
    assert entry["review_candidate_hash"] == demo_module.fingerprint(entry["review_candidate"])
    assert entry["requires_ai_review"] is True and record["checked"] is None
    assert not any(r.url.path.endswith("/visits") for r in calls)


def test_first_visit_profile_change_requires_new_quality_review(tmp_path):
    def handler(request):
        if request.url.path.endswith("/fde/visit-opportunities"):
            return httpx.Response(200, json={"items": [{"id": "opp"}]})
        if request.url.path.endswith("/conversations"):
            return httpx.Response(201, json={"id": "conv", "opportunity_id": "opp"})
        if request.url.path.endswith("/messages"):
            assert "拜访类型：首次拜访" in json.loads(request.content)["text"]
            return httpx.Response(202, json={"run_id": "new-run"})
        return httpx.Response(200, json={"status": "waiting_human", "result": {
            "fields": record["revisions"][0]["submitted_fields"],
            "quality_review": {"follow_up_score": 59, "next_action": {"passed": True}}}})
    demo, record, _ = revision_fixture(tmp_path, handler, key="v01", first=True, edits={"customer_budget": "预算尚未确认"})
    demo.revise()
    assert record["run_id"] == "new-run" and record["archive_allowed"] is False
    assert record["candidate"]["quality_review"]["follow_up_score"] == 59


def test_revision_rejects_stale_candidate_and_quality_field_edits_before_network(tmp_path):
    demo, record, patch = revision_fixture(tmp_path, lambda _: pytest.fail("invalid patch must not call API"))
    record["candidate_hash"] = "tampered"
    with pytest.raises(demo_module.DemoFailure, match="revision_source_candidate_changed"):
        demo.revise()
    patch["fields"] = {"quality_review": "fake-score"}
    demo.args.revision_file.write_text(json.dumps({"schema_version": 1, "revisions": [patch]}))
    with pytest.raises(demo_module.DemoFailure, match="invalid_revision_fields"):
        demo.revise()
    assert not demo.state["operations"]


def test_low_quality_demonstration_cannot_be_revised_into_passing_sample(tmp_path):
    demo, _, _ = revision_fixture(tmp_path, lambda _: pytest.fail("low-quality evidence must stay intact"), key="v12_low")
    with pytest.raises(demo_module.DemoFailure, match="low_quality_scenario_cannot_be_revised"):
        demo.revise()


def test_revision_poll_resume_uses_original_conversation_and_message_receipts(tmp_path):
    posts, polls = [], []
    def handler(request):
        if request.method == "POST":
            posts.append(request.url.path)
        if request.url.path.endswith("/conversations"):
            return httpx.Response(201, json={"id": "conv", "opportunity_id": "opp"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(202, json={"run_id": "run"})
        polls.append(request.url.path)
        if len(polls) == 1:
            return httpx.Response(200, json={"status": "running"})
        return httpx.Response(200, json={"status": "waiting_human", "result": {"fields": record["revisions"][0]["submitted_fields"],
            "quality_review": {"follow_up_score": 89, "next_action": {"passed": True}}}})
    demo, record, _ = revision_fixture(tmp_path, handler, key="s02", edits={"follow_up_record": "已恢复刘志德与叶源共同跟进。"}, model_wait_seconds=0)
    with pytest.raises(demo_module.DemoFailure, match="revision_ai_still_running"):
        demo.revise()
    assert record["run_id"] == "original-run" and record["checked"] is None
    with pytest.raises(demo_module.DemoFailure, match="revision_pending_cannot_archive"):
        demo.archive()
    demo.revise()
    assert len(posts) == 2 and len(polls) == 2
    assert record["run_id"] == "run" and not record.get("pending_revision_id")


def test_failed_rereview_never_falls_back_to_old_quality_or_auto_retries(tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/conversations"):
            return httpx.Response(201, json={"id": "conv", "opportunity_id": "opp"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(202, json={"run_id": "failed-run"})
        return httpx.Response(200, json={"status": "failed", "error_code": "model_unavailable"})
    demo, record, _ = revision_fixture(tmp_path, handler, key="s02", edits={"follow_up_record": "恢复协作者叶源。"})
    with pytest.raises(demo_module.DemoFailure, match="revision_ai_terminal_failure"):
        demo.revise()
    assert record["run_id"] == "original-run" and record["candidate"]["quality_review"]["follow_up_score"] == 88
    assert record["pending_revision_id"] and record["checked"] is None
    with pytest.raises(demo_module.DemoFailure, match="revision_failed"):
        demo.revise()
    assert len(calls) == 3


def test_revision_rejects_model_rewriting_confirmed_body(tmp_path):
    def handler(request):
        if request.url.path.endswith("/conversations"):
            return httpx.Response(201, json={"id": "conv", "opportunity_id": "opp"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(202, json={"run_id": "new-run"})
        return httpx.Response(200, json={"status": "waiting_human", "result": {"fields": {"follow_up_record": "模型擅自改写正文", "next_action": "也改了计划"},
            "quality_review": {"follow_up_score": 100, "next_action": {"passed": True}}}})
    demo, record, _ = revision_fixture(tmp_path, handler, key="s02", edits={"follow_up_record": "人工确认刘志德与叶源共同跟进。"})
    with pytest.raises(demo_module.DemoFailure, match="revision_model_changed_reviewed_text"):
        demo.revise()
    assert record["candidate"]["quality_review"]["follow_up_score"] == 88
    assert record["revisions"][0]["status"] == "failed"
    assert record["revisions"][0]["review_candidate"]["quality_review"]["follow_up_score"] == 100
    assert record["checked"] is None and record["pending_revision_id"]


def test_explicit_retry_preserves_prior_failed_revision(tmp_path):
    failed = True
    def handler(request):
        if request.url.path.endswith("/conversations"):
            return httpx.Response(201, json={"id": "conv", "opportunity_id": "opp"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(202, json={"run_id": "failed-run" if failed else "retry-run"})
        if failed:
            return httpx.Response(200, json={"status": "failed"})
        return httpx.Response(200, json={"status": "waiting_human", "result": {"fields": record["revisions"][-1]["submitted_fields"],
            "quality_review": {"follow_up_score": 90, "next_action": {"passed": True}}}})
    demo, record, patch = revision_fixture(tmp_path, handler, key="s02", edits={"follow_up_record": "刘志德与叶源共同跟进。"})
    with pytest.raises(demo_module.DemoFailure, match="revision_ai_terminal_failure"):
        demo.revise()
    original_failed_id = record["revisions"][0]["id"]
    failed = False
    patch["reason"] += "；已核对原调用失败，明确进行一次新的重审。"
    demo.args.revision_file.write_text(json.dumps({"schema_version": 1, "revisions": [patch]}))
    demo.revise()
    assert len(record["revisions"]) == 2
    assert record["revisions"][0]["status"] == "failed"
    assert record["revisions"][1]["retry_of_revision_id"] == original_failed_id
    assert record["run_id"] == "retry-run" and record["checked"] is None
