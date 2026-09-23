"""Acceptance orchestration tests: synthetic transports, never model evidence."""

import importlib.util
import json
from collections import Counter
from pathlib import Path
from uuid import UUID

import httpx
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/acceptance/business_ai_matrix.py"
spec = importlib.util.spec_from_file_location("business_ai_matrix", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
RUN, CONVERSATION, OP, ACTOR, ADVICE = [str(UUID(int=i)) for i in range(1, 6)]


def case(**changes):
    value = {
        "id": "risk-1",
        "scenario": "personal_risks",
        "kind": "conversation",
        "capability": "personal_risks",
        "account": "XS001",
        "text": "查看已有风险",
    }
    value.update(changes)
    return value


def plan(value=None):
    return {
        "schema_version": 1,
        "base_url": "https://demo.invalid/api/v1",
        "workspace": "demo-sales-workspace",
        "cases": [value or case()],
    }


def session(request):
    if request.url.path.endswith("/auth/password/login"):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "access_token": "test-secret-token",
                "refresh_token": "test-refresh-token",
                "must_change_password": False,
                "actor": {"account_code": body["account_code"], "role": body["role"], "user_id": ACTOR},
            },
        )
    if request.url.path.endswith("/auth/logout"):
        return httpx.Response(204)


def runner(tmp_path, handler, value=None):
    return module.Runner(
        plan(value),
        tmp_path / "state.json",
        "test-password",
        transport=httpx.MockTransport(lambda r: session(r) or handler(r)),
        wait_seconds=0,
    )


def operation(provider="agent_platform", **changes):
    value = {
        "id": OP,
        "run_id": RUN,
        "actor_id": ACTOR,
        "capability": "personal_risks",
        "inference_status": "accepted",
        "business_status": "succeeded",
        "started_at": "2026-09-13T10:00:00+08:00",
        "events": [{"phase": "contract_accepted", "provider": provider}],
        "trace": {"provider": provider},
        "attempts": [{"id": OP, "provider": provider, "http_status": 200}],
    }
    value.update(changes)
    return value


def test_offline_plan_covers_eleven_scenarios_three_contexts_and_no_chatbi():
    p = module.make_plan({"base_url": "https://demo.invalid/api/v1", "workspace": "demo-sales-workspace"})
    counts = Counter(c["scenario"] for c in p["cases"])
    assert len(p["cases"]) == 33 and set(counts.values()) == {3}
    assert "chatbi" not in counts
    assert all(c["blocked_reason"] for c in p["cases"] if c["kind"] in {"advice", "observe"})
    assert all(c["allow_task_materialization"] is False for c in p["cases"] if c["capability"] == "today_tasks")


@pytest.mark.parametrize(
    "url",
    [
        "http://demo.invalid/api/v1",
        "https://demo.invalid/api/v2",
        "https://secret@demo.invalid/api/v1",
        "https://demo.invalid/api/v1?key=secret",
    ],
)
def test_validate_rejects_bad_or_credentialed_target(url):
    p = plan()
    p["base_url"] = url
    with pytest.raises(module.Failure, match="https_api_base_required"):
        module.validate(p)


def test_disabled_chatbi_cannot_be_added_to_manifest():
    with pytest.raises(module.Failure, match="unsupported_actor_or_capability"):
        module.validate(plan(case(capability="chatbi")))


def test_no_confirmation_or_task_materialization_without_review(tmp_path):
    value = case(capability="today_tasks")
    r = runner(tmp_path, lambda _: pytest.fail("must reject before network"), value)
    with pytest.raises(module.Failure, match="task_materialization_not_reviewed"):
        r.execute(value)
    assert r.clients == {}


def test_conversation_real_adapter_paths_persist_receipts_and_audit(tmp_path):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/conversations"):
            state = json.loads((tmp_path / "state.json").read_text())
            assert state["cases"]["risk-1"]["stage"] == "dispatched"
            assert request.headers["Idempotency-Key"]
            return httpx.Response(201, json={"id": CONVERSATION})
        if request.url.path.endswith("/messages"):
            assert json.loads(request.content)["client_message_id"]
            return httpx.Response(202, json={"run_id": RUN})
        if request.url.path.endswith("/agent/runs/" + RUN):
            return httpx.Response(200, json={"id": RUN, "status": "succeeded", "result": {"private": "customer-body"}})
        if request.url.path.endswith("/console/ai/runs"):
            return httpx.Response(
                200,
                json={
                    "items": [{"operations": [operation(), operation(id=str(UUID(int=20)), run_id=str(UUID(int=21)))]}],
                    "total": 1,
                },
            )
        pytest.fail(str(request.url))

    r = runner(tmp_path, handle)
    r.execute(case())
    r.close()
    state = json.loads((tmp_path / "state.json").read_text())
    rec = state["cases"]["risk-1"]
    assert rec["run_id"] == RUN and len(rec["operations"]) == 1
    assert module.classify(rec)["first_operation_platform_contract_pass"] is True
    serialized = json.dumps(state)
    for forbidden in ("test-password", "test-secret-token", "test-refresh-token", "customer-body"):
        assert forbidden not in serialized
    assert not any(p.endswith("/visits") or p.endswith("/confirm") or p.endswith("/decisions") for p in paths)
    assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600


def test_transport_uncertainty_is_not_replaced_with_another_paid_call(tmp_path):
    writes = []

    def handle(request):
        writes.append(request.url.path)
        raise httpx.ReadTimeout("response containing secrets must not be printed", request=request)

    r = runner(tmp_path, handle)
    with pytest.raises(module.Failure, match="transport_failed_receipt_unknown"):
        r.execute(case())
    with pytest.raises(module.Failure, match="prior_dispatch_uncertain"):
        r.execute(case())
    assert len(writes) == 1
    r.close()


def test_http_error_does_not_store_response_body(tmp_path):
    r = runner(tmp_path, lambda _: httpx.Response(500, text="private-payload-secret"))
    with pytest.raises(module.Failure) as error:
        r.execute(case())
    assert error.value.status == 500 and "private" not in str(error.value)
    r.close()


def test_http_200_and_fallback_do_not_count_as_platform_contract_success():
    op = operation(
        provider="senseaudio",
        events=[
            {"phase": "route_failed", "provider": "agent_platform", "contract_code": "invalid"},
            {"phase": "fallback_requested", "provider": "agent_platform"},
            {"phase": "contract_accepted", "provider": "senseaudio"},
        ],
        attempts=[
            {"id": "platform", "provider": "agent_platform", "http_status": 200},
            {"id": "direct", "provider": "senseaudio", "http_status": 200},
        ],
    )
    result = module.classify({"operations": [module.safe_operation(op)], "business_status": "succeeded"})
    assert result["platform_http_200_attempts"] == 1
    assert result["first_operation_platform_contract_pass"] is False
    assert result["fallback_contract_pass"] is True and result["analysis_complete"] is True


def test_first_failure_survives_later_worker_success_and_injected_attempt_not_network():
    failed = operation(
        id="first",
        inference_status="failed",
        events=[],
        attempts=[{"id": "injected", "provider": "agent_platform", "network_dispatch_suppressed": True}],
    )
    success = operation(id="second", started_at="2026-09-13T10:01:00+08:00")
    result = module.classify({"operations": [module.safe_operation(success), module.safe_operation(failed)]})
    assert result["first_operation_platform_contract_pass"] is False
    assert result["latest_operation_platform_contract_pass"] is True
    assert result["additional_worker_operations"] == 1 and result["platform_network_attempts"] == 1


def test_cache_reuse_and_unique_operation_counts_are_separate_from_fresh_success():
    rec = {
        "scenario": "personal_risks",
        "account": "XS001",
        "operations": [module.safe_operation(operation())],
        "request_origin": "cache_reused",
    }
    result = module.report({"plan_sha256": "fixture", "cases": {"a": rec, "b": rec}})
    assert result["totals"]["cases"] == 2
    assert result["totals"]["unique_operations"] == 1
    assert result["totals"]["cache_reused"] == 2
    assert result["totals"].get("fresh_first_round_platform_contract_pass", 0) == 0


def test_advice_existing_result_is_marked_cache_and_exact_id_correlated(tmp_path):
    value = case(
        kind="advice",
        capability="customer_advice",
        scenario="customer_advice",
        subject_kind="customer",
        subject_id=ACTOR,
    )

    def handle(request):
        if request.url.path.endswith("/advice"):
            assert json.loads(request.content)["retry"] is False
            return httpx.Response(200, json={"id": ADVICE, "created_at": "2020-01-01T00:00:00Z"})
        if request.url.path.endswith("/advice/" + ADVICE):
            return httpx.Response(200, json={"id": ADVICE, "status": "succeeded", "summary": "private-summary"})
        return httpx.Response(
            200,
            json={"items": [{"operations": [operation(advice_id=ADVICE, capability="customer_advice")]}], "total": 1},
        )

    r = runner(tmp_path, handle, value)
    r.execute(value)
    rec = r.record(value)
    assert rec["request_origin"] == "cache_reused" and rec["operations"][0]["advice_id"] == ADVICE
    assert "private-summary" not in json.dumps(r.state)
    r.close()


def test_failed_competency_without_exact_receipt_is_unknown_not_time_matched(tmp_path):
    value = case(kind="competency", capability="competency_review", scenario="competency_review")
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(202, json={"review_id": ADVICE, "status": "queued"})
        return httpx.Response(200, json={"today_status": "failed", "history": [], "latest": None})

    r = runner(tmp_path, handle, value)
    r.execute(value)
    rec = r.record(value)
    assert rec["business_status"] == "unknown" and not rec.get("operations")
    assert rec["audit_gap"] == "exact_operation_receipt_not_exposed_for_failed_competency_review"
    assert not any("/console/ai/runs" in p for p in paths)
    r.close()


def test_map_requires_exact_receipt_and_never_invents_recompute_api(tmp_path):
    value = case(kind="observe", capability="battle_map_review", scenario="battle_map_review")
    r = runner(tmp_path, lambda _: pytest.fail("no receipt means no API"), value)
    with pytest.raises(module.Failure, match="exact_receipt_required"):
        r.collect(value)


def test_safe_audit_export_whitelists_metadata_and_keeps_sse_milestones():
    op = operation(
        configuration={"api_key": "secret", "agent_id": "agent-id"},
        attempts=[
            {
                "provider": "agent_platform",
                "body": "private-body",
                "transport": {"first_text_ms": 700, "secret": "secret"},
            }
        ],
    )
    safe = module.safe_operation(op)
    assert safe["attempts"][0]["transport"]["first_text_ms"] == 700
    assert "secret" not in json.dumps(safe) and "private-body" not in json.dumps(safe)


def test_fde_profile_reuses_exact_run_and_does_not_generate_new_persona_scores(tmp_path):
    value = case(kind="fde_profile", capability="operating_report", scenario="fde_coaching", account="FDE001", days=30)
    writes = []

    def handle(request):
        if request.url.path.endswith("/fde/profile"):
            return httpx.Response(200, json={"review_run_id": RUN, "review_status": "succeeded"})
        if request.url.path.endswith("/fde/profile/review"):
            writes.append(request.url.path)
            return httpx.Response(202, json={"review_run_id": RUN, "review_status": "succeeded"})
        if request.url.path.endswith("/agent/runs/" + RUN):
            return httpx.Response(200, json={"id": RUN, "status": "succeeded", "result": {"action_plan": []}})
        return httpx.Response(
            200, json={"items": [{"operations": [operation(capability="operating_report")]}], "total": 1}
        )

    r = runner(tmp_path, handle, value)
    r.execute(value)
    assert r.record(value)["request_origin"] == "cache_reused"
    assert len(writes) == 1
    assert module.classify(r.record(value))["human_confirmation_submitted"] is False
    r.close()


def test_competency_success_matches_review_id_and_operation_not_other_user_run(tmp_path):
    value = case(kind="competency", capability="competency_review", scenario="competency_review")
    reads = 0

    def handle(request):
        nonlocal reads
        if request.url.path.endswith("/profile/sales-growth/review"):
            return httpx.Response(202, json={"review_id": ADVICE, "status": "queued"})
        if request.url.path.endswith("/profile/sales-growth"):
            reads += 1
            return httpx.Response(
                200,
                json={
                    "today_status": "succeeded" if reads > 1 else "missing",
                    "history": []
                    if reads == 1
                    else [
                        {
                            "id": ADVICE,
                            "status": "succeeded",
                            "input_snapshot": {"inference_route": {"operation_id": OP}},
                            "dimension_scores": {"a": 65},
                        }
                    ],
                },
            )
        return httpx.Response(
            200, json={"items": [{"operations": [operation(run_id=None, capability="competency_review")]}], "total": 1}
        )

    r = runner(tmp_path, handle, value)
    r.execute(value)
    rec = r.record(value)
    assert rec["operation_id"] == OP and rec["business_status"] == "succeeded"
    assert rec["operations"][0]["id"] == OP
    r.close()


def test_map_completion_requires_effect_receipt_and_score_write(tmp_path):
    value = case(kind="observe", capability="battle_map_review", scenario="battle_map_review", operation_id=OP)

    def handle(_):
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "operations": [
                            operation(
                                run_id=None,
                                capability="battle_map_review",
                                business_status=None,
                                job_status="succeeded",
                                job_effect_recorded=True,
                                business_effects={"scores": [{"id": "score"}]},
                            )
                        ]
                    }
                ],
                "total": 1,
            },
        )

    r = runner(tmp_path, handle, value)
    r.collect(value)
    assert r.record(value)["business_status"] == "succeeded"
    assert r.record(value)["request_origin"] == "historical_observation"
    r.close()


def test_runtime_snapshot_evidence_is_nested_and_never_inferred_from_expected_configuration():
    safe = module.safe_operation(
        operation(
            trace={
                "provider": "agent_platform",
                "platform_run": {
                    "expected_snapshot_id": OP,
                    "actual_snapshot_id": None,
                    "runtime_snapshot_verified": False,
                    "ids": {"task_id": RUN, "secret": "sensitive"},
                },
            }
        )
    )
    assert safe["platform_run"]["runtime_snapshot_verified"] is False
    assert safe["platform_run"]["actual_snapshot_id"] is None
    assert safe["platform_run"]["ids"]["task_id"] == RUN
    assert "sensitive" not in json.dumps(safe)
