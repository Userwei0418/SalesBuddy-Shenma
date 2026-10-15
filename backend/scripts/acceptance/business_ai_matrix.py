"""Real API AI acceptance. No network on import; no confirmations or account creation.

`plan` / `report` are offline. `run --execute --case ...` makes billable calls.
`collect` only reads receipts. Passwords and tokens never enter the state/report.
The manifest and state must live outside the repository (they may contain demo facts).
"""

from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("Asia/Shanghai")
REPO = Path(__file__).resolve().parents[3]
MODES = {"opportunity_draft", "personal_risks", "visit_entry", "today_tasks", "operating_report"}
CAPABILITIES = MODES | {
    "battle_map_review",
    "customer_advice",
    "opportunity_advice",
    "visit_advice",
    "competency_review",
}
ACCOUNTS = {
    "OPSADMIN": "administrator",
    **dict.fromkeys(("XS001", "XS002", "XS003"), "sales"),
    **dict.fromkeys(("FDE001", "FDE002", "FDE003"), "fde"),
    "FDEL001": "fde_lead",
}
TERMINAL = {"succeeded", "waiting_human", "failed", "cancelled", "superseded", "empty"}


class Failure(Exception):
    def __init__(self, code, status=None):
        self.code, self.status = code, status
        super().__init__(code)


def require(value, code):
    if not value:
        raise Failure(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def now():
    return datetime.now(TZ).isoformat()


def external(path):
    path = Path(path)
    resolved = path.resolve()
    require(not path.is_symlink() and resolved != REPO and REPO not in resolved.parents, "external_path_required")
    return resolved


def save(path, value):
    path = external(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(plan):
    url = urlsplit(plan["base_url"])
    require(
        url.scheme == "https"
        and url.hostname
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment
        and url.path.rstrip("/") == "/api/v1",
        "https_api_base_required",
    )
    require(plan.get("schema_version") == 1 and plan.get("workspace"), "invalid_plan")
    ids = set()
    for case in plan["cases"]:
        require(case["id"] not in ids, "duplicate_case_id")
        ids.add(case["id"])
        require(case["account"] in ACCOUNTS and case["capability"] in CAPABILITIES, "unsupported_actor_or_capability")
        kind = case["kind"]
        require(
            kind in {"conversation", "advice", "fde_profile", "competency", "observe_run", "observe"},
            "unsupported_case_kind",
        )
        if kind == "conversation":
            require(case["capability"] in MODES and case.get("text"), "invalid_conversation")
        elif kind == "advice":
            require(
                case.get("subject_kind") in {"customer", "opportunity", "visit"}
                and case["capability"] == case["subject_kind"] + "_advice",
                "invalid_advice",
            )
        elif kind == "fde_profile":
            require(
                case["capability"] == "operating_report" and case["account"].startswith("FDE"), "invalid_fde_profile"
            )
        elif kind == "competency":
            require(
                case["capability"] == "competency_review" and case["account"].startswith("XS"), "invalid_competency"
            )
        for key in ("customer_id", "opportunity_id", "subject_id", "run_id", "job_id", "operation_id"):
            if case.get(key):
                try:
                    require(str(UUID(case[key])) == case[key], "canonical_uuid_required")
                except (ValueError, TypeError, AttributeError):
                    raise Failure("canonical_uuid_required") from None
        if case.get("retry_of"):
            require(case["retry_of"] != case["id"], "invalid_retry_reference")
    for case in plan["cases"]:
        require(not case.get("retry_of") or case["retry_of"] in ids, "retry_original_missing")
    return plan


def make_plan(demo):
    """Three contexts per scenario; IDs only from the normal-API demo receipts."""
    cases = []
    prompts = {
        "opportunity_draft": (
            "根据该客户和商机的已有真实记录，整理当前机会候选、未确认信息和下一步；不要虚构金额、日期或已达成的承诺。"
        ),
        "personal_risks": "查看我当前的风险，说明事实依据和优先处理的事项。",
        "operating_report": "汇总我当前经营情况，区分已确认事实、风险和下一步建议。",
        "today_tasks": "根据已有跟进记录生成今日待办，明确依据和责任人。",
    }
    specs = read(REPO / "backend/scripts/demo/fde_scenarios.json")
    for index, account in enumerate(("XS001", "XS002", "XS003"), 1):
        spec = next(v for v in specs["opportunities"] if v["owner"] == account)
        opp = demo.get("opportunities", {}).get(spec["key"], {})
        customer_id, opportunity_id = opp.get("customer_id"), opp.get("id")
        visits = [v for v in demo.get("visits", {}).values() if v.get("account") == account and v.get("original_text")]
        visit = next((v for v in visits if v.get("opportunity_id") == opportunity_id), visits[0] if visits else {})
        for mode, text in prompts.items():
            case = {
                "id": f"{mode}-{index}",
                "scenario": mode,
                "kind": "conversation",
                "capability": mode,
                "account": account,
                "text": text,
            }
            if mode == "opportunity_draft":
                case.update(customer_id=customer_id, opportunity_id=opportunity_id)
                if not customer_id or not opportunity_id:
                    case["blocked_reason"] = "demo_customer_or_opportunity_missing"
            if mode == "today_tasks":
                case.update(allow_task_materialization=False, blocked_reason="review_task_materialization_before_run")
            cases.append(case)
        cases.append(
            {
                "id": f"visit_entry-{index}",
                "scenario": "visit_entry",
                "kind": "conversation",
                "capability": "visit_entry",
                "account": account,
                "customer_id": visit.get("customer_id"),
                "opportunity_id": visit.get("opportunity_id"),
                "text": visit.get("original_text", "等待真实演示拜访材料"),
                **({} if visit else {"blocked_reason": "demo_visit_source_missing"}),
            }
        )
        for subject_kind, subject_id in (
            ("customer", customer_id),
            ("opportunity", opportunity_id),
            ("visit", visit.get("archived_id")),
        ):
            cases.append(
                {
                    "id": f"{subject_kind}_advice-{index}",
                    "scenario": subject_kind + "_advice",
                    "kind": "advice",
                    "capability": subject_kind + "_advice",
                    "account": account,
                    "subject_kind": subject_kind,
                    "subject_id": subject_id,
                    "section": "overview",
                    **({} if subject_id else {"blocked_reason": "demo_subject_missing"}),
                }
            )
        cases.append(
            {
                "id": f"competency_review-{index}",
                "scenario": "competency_review",
                "kind": "competency",
                "capability": "competency_review",
                "account": account,
            }
        )
        cases.append(
            {
                "id": f"fde_coaching-{index}",
                "scenario": "fde_coaching",
                "kind": "fde_profile",
                "capability": "operating_report",
                "account": f"FDE00{index}",
                "days": 30,
            }
        )
        cases.append(
            {
                "id": f"battle_map_review-{index}",
                "scenario": "battle_map_review",
                "kind": "observe",
                "capability": "battle_map_review",
                "account": account,
                "source_visit_id": visit.get("archived_id"),
                "blocked_reason": "supply_exact_operation_id_or_job_id_from_archived_visit_effect",
            }
        )
    return validate(
        {
            "schema_version": 1,
            "created_at": now(),
            "base_url": demo["base_url"],
            "workspace": demo["workspace"],
            "demo_batch": demo.get("batch"),
            "demo_state_sha256": digest(demo),
            "cases": cases,
        }
    )


OP_FIELDS = (
    "id",
    "case_id",
    "run_id",
    "job_id",
    "advice_id",
    "actor_id",
    "capability",
    "started_at",
    "completed_at",
    "inference_status",
    "business_status",
    "business_completed_at",
    "job_status",
    "job_effect_recorded",
    "error_code",
    "assistant_results",
)
ATTEMPT_FIELDS = (
    "id",
    "provider",
    "model",
    "status",
    "attempt_no",
    "http_status",
    "error_code",
    "latency_ms",
    "started_at",
    "completed_at",
    "input_tokens",
    "output_tokens",
    "request_id",
    "upstream_trace_id",
    "network_dispatch_suppressed",
    "test_fault_injected",
)


def safe_operation(operation):
    result = {k: operation.get(k) for k in OP_FIELDS}
    trace = operation.get("trace") or {}
    result["trace"] = {
        k: trace.get(k)
        for k in (
            "provider",
            "fallback_reason",
            "runtime_snapshot_verified",
            "snapshot_id",
            "expected_snapshot_id",
            "execution_mode",
        )
    }
    platform_run = trace.get("platform_run") or {}
    result["platform_run"] = {
        k: platform_run.get(k)
        for k in (
            "execution_mode",
            "expected_snapshot_id",
            "actual_snapshot_id",
            "runtime_snapshot_verified",
            "tool_authority_issued",
            "error_code",
            "stop_state",
        )
    }
    result["platform_run"]["ids"] = {
        k: (platform_run.get("ids") or {}).get(k) for k in ("task_id", "message_id", "conversation_id")
    }
    result["events"] = [
        {k: event.get(k) for k in ("phase", "provider", "elapsed_ms", "budget_ms", "error_code", "contract_code")}
        for event in operation.get("events", [])
    ]
    result["attempts"] = [{k: attempt.get(k) for k in ATTEMPT_FIELDS} for attempt in operation.get("attempts", [])]
    configuration = operation.get("configuration") or {}
    result["configuration"] = {
        k: configuration.get(k)
        for k in (
            "agent_id",
            "snapshot_id",
            "expected_snapshot_id",
            "execution_mode",
            "platform_enabled",
            "adapter_configured",
            "platform_seconds",
            "total_seconds",
        )
    }
    for target, attempt in zip(result["attempts"], operation.get("attempts", []), strict=True):
        target["transport"] = {
            k: (attempt.get("transport") or {}).get(k)
            for k in (
                "http_status",
                "request_started_ms",
                "headers_received_ms",
                "first_body_byte_ms",
                "first_data_event_ms",
                "first_text_ms",
                "stream_completed_ms",
            )
        }
    result["effect_counts"] = {
        k: len(v) for k, v in (operation.get("business_effects") or {}).items() if isinstance(v, list)
    }
    result["input_summary"] = {
        k: (operation.get("input_summary") or {}).get(k)
        for k in ("facts_sha256", "facts_bytes", "data_as_of", "record_counts")
    }
    return result


def classify(record):
    operations = sorted(record.get("operations", []), key=lambda x: (x.get("started_at") or "", x.get("id") or ""))
    first = operations[0] if operations else {}
    latest = operations[-1] if operations else {}

    def accepted(op, provider):
        return op.get("inference_status") == "accepted" and any(
            e.get("phase") == "contract_accepted" and e.get("provider") == provider for e in op.get("events", [])
        )

    attempts = [a for op in operations for a in op.get("attempts", [])]
    platform = [
        a
        for a in attempts
        if a.get("provider") == "agent_platform" and a.get("network_dispatch_suppressed") is not True
    ]
    fallback = any(e.get("phase") == "fallback_requested" for op in operations for e in op.get("events", []))
    status = record.get("business_status", latest.get("business_status"))
    return {
        "business_status": status or "unknown",
        "analysis_complete": status in {"succeeded", "waiting_human"},
        "human_confirmation_submitted": False,
        "first_operation_platform_contract_pass": accepted(first, "agent_platform"),
        "latest_operation_platform_contract_pass": accepted(latest, "agent_platform"),
        "platform_network_attempts": len(platform),
        "platform_http_200_attempts": sum(a.get("http_status") == 200 for a in platform),
        "fallback_requested": fallback,
        "fallback_contract_pass": fallback and accepted(latest, "senseaudio"),
        "direct_only_contract_pass": not fallback and accepted(latest, "senseaudio"),
        "operation_count": len(operations),
        "additional_worker_operations": max(0, len(operations) - 1),
        "audit_status": "present" if operations else "unknown",
        "request_origin": record.get("request_origin", "unknown"),
        "explicit_retry": bool(record.get("retry_of")),
        "retry_existing": record.get("request_origin") == "retry_existing",
        "human_quality": record.get("human_quality", {"status": "pending_review"}),
    }


def report(state):
    rows = [
        {
            "id": key,
            "scenario": value["scenario"],
            "account": value["account"],
            **classify(value),
            "error": value.get("error"),
            "operations": value.get("operations", []),
        }
        for key, value in state.get("cases", {}).items()
    ]
    totals = Counter()
    for row in rows:
        totals["cases"] += 1
        if row["request_origin"] == "fresh_request" and not row["explicit_retry"]:
            totals["fresh_first_round_cases"] += 1
            totals["fresh_first_round_platform_contract_pass"] += int(row["first_operation_platform_contract_pass"])
        for field in (
            "analysis_complete",
            "first_operation_platform_contract_pass",
            "latest_operation_platform_contract_pass",
            "fallback_contract_pass",
            "direct_only_contract_pass",
            "explicit_retry",
        ):
            totals[field] += int(row[field])
        totals["audit_unknown"] += row["audit_status"] == "unknown"
        totals["cache_reused"] += row["request_origin"] == "cache_reused"
        totals["historical_observation"] += row["request_origin"] == "historical_observation"
        totals["platform_network_attempts"] += row["platform_network_attempts"]
        totals["additional_worker_operations"] += row["additional_worker_operations"]
    unique_operations = {op["id"]: op for row in rows for op in row["operations"]}
    totals["unique_operations"] = len(unique_operations)
    totals["unique_platform_network_attempts"] = len(
        {
            a["id"]
            for op in unique_operations.values()
            for a in op["attempts"]
            if a["provider"] == "agent_platform" and a["network_dispatch_suppressed"] is not True
        }
    )
    return {
        "schema_version": 1,
        "generated_at": now(),
        "plan_sha256": state["plan_sha256"],
        "totals": dict(totals),
        "cases": rows,
        "notes": [
            "case-level counts may reference the same cached operation; unique operation/attempt counts are separate",
            "HTTP 200, contract acceptance, analysis completion and human quality are separate evidence",
            "missing audit is unknown, not success; this runner never submits human confirmations",
            "first-operation metrics exclude no failures; explicit retries and worker retries remain visible",
            "runtime published configuration does not attest to the snapshot actually executed",
        ],
    }


class Runner:
    def __init__(self, plan, path, password, *, transport=None, wait_seconds=180):
        self.plan, self.path, self.password = validate(plan), external(path), password
        self.transport, self.wait_seconds = transport, wait_seconds
        self.clients, self.refresh = {}, {}
        self.state = (
            read(path)
            if self.path.exists()
            else {"schema_version": 1, "plan_sha256": digest(plan), "actors": {}, "cases": {}}
        )
        require(self.state["plan_sha256"] == digest(plan), "plan_changed_use_new_state")

    def flush(self):
        save(self.path, self.state)

    def raw(self, client, method, path, *, statuses=(200,), **kwargs):
        try:
            response = client.request(method, self.plan["base_url"].rstrip("/") + path, **kwargs)
        except httpx.HTTPError:
            raise Failure("transport_failed_receipt_unknown") from None
        if response.status_code not in statuses:
            raise Failure("unexpected_http_status", response.status_code)
        try:
            return response.json() if response.content else {}
        except ValueError:
            raise Failure("invalid_json_response") from None

    def client(self, account):
        if account not in self.clients:
            client = httpx.Client(timeout=45, transport=self.transport)
            try:
                auth = self.raw(
                    client,
                    "POST",
                    "/auth/password/login",
                    json={
                        "workspace": self.plan["workspace"],
                        "account_code": account,
                        "role": ACCOUNTS[account],
                        "password": self.password,
                    },
                )
                actor = auth["actor"]
                require(
                    actor["account_code"] == account and actor["role"] == ACCOUNTS[account], "login_identity_mismatch"
                )
                self.clients[account] = client
                self.refresh[account] = auth["refresh_token"]
                client.headers["Authorization"] = "Bearer " + auth["access_token"]
                require(auth.get("must_change_password") is False, "password_change_required")
                self.state["actors"][account] = {k: actor[k] for k in ("user_id", "account_code", "role")}
                self.flush()
            except Exception:
                if account not in self.clients:
                    client.close()
                raise
        return self.clients[account]

    def req(self, account, method, path, **kwargs):
        client = self.client(account)
        try:
            return self.raw(client, method, path, **kwargs)
        except Failure as exc:
            if exc.status != 401:
                raise
        auth = self.raw(client, "POST", "/auth/refresh", json={"refresh_token": self.refresh[account]})
        client.headers["Authorization"] = "Bearer " + auth["access_token"]
        self.refresh[account] = auth["refresh_token"]
        return self.raw(client, method, path, **kwargs)

    def close(self):
        failed = []
        for account, client in self.clients.items():
            try:
                self.raw(client, "POST", "/auth/logout", statuses=(204,))
            except Failure:
                failed.append(account)
            finally:
                client.close()
        self.password = None
        self.refresh.clear()
        self.clients.clear()
        self.state["session_logout_failed_accounts"] = failed
        self.flush()

    def record(self, case):
        return self.state["cases"].setdefault(
            case["id"],
            {
                "scenario": case["scenario"],
                "capability": case["capability"],
                "account": case["account"],
                "created_at": now(),
                "retry_of": case.get("retry_of"),
                "stage": "prepared",
            },
        )

    def execute(self, case):
        rec = self.record(case)
        if case.get("blocked_reason"):
            raise Failure(case["blocked_reason"])
        require(case["kind"] not in {"observe", "observe_run"}, "observation_only_use_collect")
        if case["capability"] == "today_tasks":
            require(case.get("allow_task_materialization") is True, "task_materialization_not_reviewed")
        if rec["stage"] == "dispatched":
            raise Failure("prior_dispatch_uncertain_collect_receipts_before_retry")
        if rec["stage"] not in {"prepared", "blocked"}:
            return self.collect(case)
        account, kind = case["account"], case["kind"]
        self.client(account)
        if kind == "fde_profile":
            previous = self.req(account, "GET", "/fde/profile", params={"days": case.get("days", 30)})
            rec["previous_receipt"] = previous.get("review_run_id")
            rec["previous_status"] = previous.get("review_status")
        elif kind == "competency":
            previous = self.req(account, "GET", "/profile/sales-growth")
            rec["previous_receipt"] = (previous.get("latest") or {}).get("id")
            rec["previous_status"] = previous.get("today_status")
        rec.update(
            stage="dispatched", dispatch_started_at=now(), request_origin="fresh_request", mutation_key=str(uuid4())
        )
        self.flush()
        if kind == "conversation":
            conv = self.req(
                account,
                "POST",
                "/conversations",
                statuses=(201,),
                headers={"Idempotency-Key": rec["mutation_key"]},
                json={
                    "mode": case["capability"],
                    "customer_id": case.get("customer_id"),
                    "opportunity_id": case.get("opportunity_id"),
                },
            )
            rec["conversation_id"] = conv["id"]
            rec["client_message_id"] = str(uuid4())
            self.flush()
            accepted = self.req(
                account,
                "POST",
                f"/conversations/{conv['id']}/messages",
                statuses=(202,),
                json={"text": case["text"], "client_message_id": rec["client_message_id"], "input_source": "text"},
            )
            rec["run_id"] = accepted["run_id"]
        elif kind == "advice":
            accepted = self.req(
                account,
                "POST",
                "/advice",
                json={
                    "subject_kind": case["subject_kind"],
                    "subject_id": case["subject_id"],
                    "section": case.get("section", "overview"),
                    "retry": bool(case.get("retry_of")),
                },
            )
            rec["advice_id"] = accepted["id"]
            created = accepted.get("created_at")
            if created and datetime.fromisoformat(created.replace("Z", "+00:00")) < datetime.fromisoformat(
                rec["dispatch_started_at"]
            ):
                rec["request_origin"] = "retry_existing" if case.get("retry_of") else "cache_reused"
        elif kind == "fde_profile":
            accepted = self.req(
                account, "POST", "/fde/profile/review", statuses=(202,), params={"days": case.get("days", 30)}
            )
            rec["run_id"] = accepted.get("review_run_id")
            if not rec["run_id"]:
                rec["business_status"] = accepted.get("review_status", "empty")
                rec["request_origin"] = "no_sample_no_inference"
            elif rec["run_id"] == rec.get("previous_receipt"):
                rec["request_origin"] = "cache_reused"
        elif kind == "competency":
            accepted = self.req(account, "POST", "/profile/sales-growth/review", statuses=(202,))
            rec["review_id"] = accepted["review_id"]
            if rec["review_id"] == rec.get("previous_receipt"):
                rec["request_origin"] = "cache_reused"
            elif rec.get("previous_status") == "failed":
                rec["request_origin"] = "retry_existing"
        rec["stage"] = "received"
        self.flush()
        self.collect(case)

    def collect(self, case):
        rec = self.record(case)
        if case["kind"] in {"observe", "observe_run"}:
            for key in ("run_id", "operation_id", "job_id"):
                if case.get(key):
                    rec[key] = str(UUID(case[key]))
            rec["request_origin"] = "historical_observation"
            rec["stage"] = "received"
        require(
            any(rec.get(k) for k in ("run_id", "advice_id", "review_id", "operation_id", "job_id"))
            or rec.get("business_status") == "empty",
            "exact_receipt_required",
        )
        deadline = time.monotonic() + self.wait_seconds
        while True:
            self.poll(case, rec)
            self.flush()
            if rec.get("business_status") in TERMINAL or case["kind"] == "observe" or time.monotonic() >= deadline:
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        self.collect_audit(case, rec)
        rec["stage"] = "collected"
        rec["collected_at"] = now()
        self.flush()

    def poll(self, case, rec):
        account = case["account"]
        if rec.get("run_id"):
            row = self.req(account, "GET", f"/agent/runs/{rec['run_id']}")
            require(row["id"] == rec["run_id"], "run_receipt_mismatch")
            rec["business_status"] = row["status"]
            rec["result_sha256"] = digest(row.get("result")) if row.get("result") is not None else None
            rec["business_error_code"] = row.get("error_code")
        elif rec.get("advice_id"):
            row = self.req(account, "GET", f"/advice/{rec['advice_id']}")
            require(row["id"] == rec["advice_id"], "advice_receipt_mismatch")
            rec["business_status"] = row["status"]
            rec["result_sha256"] = digest({k: row.get(k) for k in ("summary", "suggestions", "coverage")})
        elif rec.get("review_id"):
            row = self.req(account, "GET", "/profile/sales-growth")
            match = next((r for r in row.get("history", []) if r["id"] == rec["review_id"]), None)
            if match:
                rec["business_status"] = match["status"]
                rec["operation_id"] = ((match.get("input_snapshot") or {}).get("inference_route") or {}).get(
                    "operation_id"
                )
                rec["result_sha256"] = digest(
                    {k: match.get(k) for k in ("dimension_scores", "summary", "improvements")}
                )
            else:
                rec["business_status"] = "unknown"
                rec["profile_latest_status_unbound"] = row.get("today_status")

    def collect_audit(self, case, rec):
        identities = {k: rec[k] for k in ("run_id", "job_id", "advice_id", "operation_id") if rec.get(k)}
        if not identities:
            rec["audit_gap"] = "exact_operation_receipt_not_exposed_for_failed_competency_review"
            return
        self.client(case["account"])
        actor = self.state["actors"][case["account"]]["user_id"]
        start = case.get(
            "audit_start", (datetime.fromisoformat(rec["created_at"]).date() - timedelta(days=1)).isoformat()
        )
        end = datetime.now(TZ).date().isoformat()
        found = {}
        for offset in range(0, 10000, 100):
            response = self.req(
                "OPSADMIN",
                "GET",
                "/console/ai/runs",
                params={
                    "period": "custom",
                    "start": start,
                    "end": end,
                    "capability": case["capability"],
                    "actor": actor,
                    "limit": 100,
                    "offset": offset,
                },
            )
            for row in response["items"]:
                for op in row.get("operations") or [row]:
                    if any(
                        op.get("id" if key == "operation_id" else key) == value for key, value in identities.items()
                    ):
                        found[op["id"]] = safe_operation(op)
            if offset + 100 >= response["total"]:
                rec["audit_scan_complete"] = True
                break
        else:
            rec["audit_scan_complete"] = False
        # Keep earlier operations even if an operator shortens a later audit window.
        rec["operations"] = list({**{o["id"]: o for o in rec.get("operations", [])}, **found}.values())
        rec["audit_gap"] = None if found else "no_exact_persisted_audit_match"
        if case["kind"] == "observe" and found:
            latest = max(found.values(), key=lambda x: x.get("started_at") or "")
            rec["business_status"] = latest.get("business_status") or "unknown"
            if (
                case["capability"] == "battle_map_review"
                and latest.get("job_status") == "succeeded"
                and latest.get("job_effect_recorded") is True
                and latest.get("effect_counts", {}).get("scores", 0) > 0
            ):
                rec["business_status"] = "succeeded"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "collect", "report"))
    parser.add_argument("--demo-state", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            require(args.demo_state and args.output, "demo_state_and_output_required")
            save(args.output, make_plan(read(args.demo_state)))
            return 0
        require(args.state, "state_required")
        external(args.state)
        if args.command == "report":
            require(args.output, "output_required")
            save(args.output, report(read(args.state)))
            return 0
        require(
            args.plan and args.case and 0 <= args.wait_seconds <= 600, "plan_explicit_cases_and_bounded_wait_required"
        )
        plan = validate(read(args.plan))
        chosen = [case for case in plan["cases"] if case["id"] in args.case]
        require(len(chosen) == len(set(args.case)), "unknown_case")
        require(args.command != "run" or args.execute, "run_requires_execute_flag")
        args.state.parent.mkdir(parents=True, exist_ok=True)
        with open(str(args.state) + ".lock", "w", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            password = getpass.getpass("现有业务与运营账号的演示密码（不写入文件）：")
            runner = Runner(plan, args.state, password, wait_seconds=args.wait_seconds)
            del password
            failed = False
            try:
                for case in chosen:
                    try:
                        getattr(runner, "execute" if args.command == "run" else "collect")(case)
                    except Failure as exc:
                        rec = runner.record(case)
                        rec["error"] = {"code": exc.code, "http_status": exc.status, "at": now()}
                        runner.flush()
                        failed = True
                        # A failed/uncertain request is not silently replaced with another paid run.
                        break
            finally:
                runner.close()
            return 1 if failed else 0
    except (Failure, BlockingIOError) as exc:
        print(
            json.dumps({"error": getattr(exc, "code", "state_locked"), "http_status": getattr(exc, "status", None)}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
