"""Resumable, explicitly invoked FDE demonstrations through normal HTTPS APIs.

No business writes happen on import. Passwords/tokens remain in memory. The
caller reviews each real AI candidate before using the separate archive phase.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import getpass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("Asia/Shanghai")
REPO = Path(__file__).resolve().parents[3]
# The CLI shares the exact backend/frontend review semantics without installing the app.
sys.path.insert(0, str(REPO / "backend/src"))

REVISION_FIELDS = frozenset({
    "follow_up_record", "next_action", "visit_goal", "partner_name", "contact_name", "contact_title",
    "interaction_mode", "visit_location", "customer_main_business", "customer_needs", "customer_budget", "contact_role",
})


class DemoFailure(Exception):
    def __init__(self, code, status=None):
        self.code, self.status = code, status


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def expect(condition, code):
    if not condition:
        raise DemoFailure(code)


def atomic_save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Demo:
    def __init__(self, args, password, transport=None):
        self.args, self.password, self.transport = args, password, transport
        self.clients, self.refresh_tokens = {}, {}
        self.spec = json.loads(args.spec.read_text(encoding="utf-8"))
        self.customers = {v["key"]: v for v in self.spec["customers"]}
        self.opportunities = {v["key"]: v for v in self.spec["opportunities"]}
        self.cases = {v["key"]: v for v in self.spec["cases"]}
        expect(all(v["name"].startswith("【演示】") for v in [*self.customers.values(), *self.opportunities.values()]), "demo_names_required")
        expect(not args.case or args.case in self.cases, "unknown_case")
        expect(not args.record or any(v["key"] == args.record for v in self.spec["visits"]), "unknown_record")
        state_path = args.state.resolve()
        expect(state_path != REPO and REPO not in state_path.parents and not args.state.is_symlink(), "state_must_be_outside_repository")
        self.state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {
            "schema_version": 1, "batch": self.spec["batch"], "spec_hash": fingerprint(self.spec),
            "base_url": args.base_url.rstrip("/"), "workspace": args.workspace,
            "base_date": datetime.now(TZ).date().isoformat(), "operations": {}, "actors": {},
            "customers": {}, "opportunities": {}, "visits": {}, "checks": {}, "tasks": {},
        }
        expect(self.state["spec_hash"] == fingerprint(self.spec), "spec_changed_start_separate_batch")
        expect(self.state["base_url"] == args.base_url.rstrip("/") and self.state["workspace"] == args.workspace, "state_target_mismatch")
        self.today = date.fromisoformat(self.state["base_date"])

    def save(self):
        atomic_save(self.args.state, self.state)

    def emit(self, event, **safe):
        print(json.dumps({"event": event, **safe}, ensure_ascii=False), flush=True)

    def check(self, key, passed, **facts):
        self.state["checks"][key] = {"passed": bool(passed), **facts}
        self.save()
        expect(passed, key)

    def request_raw(self, client, method, path, expected=(200,), **kwargs):
        try:
            response = client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise DemoFailure("transport_failed_resume_same_state") from None
        if response.status_code not in expected:
            raise DemoFailure("unexpected_http_status", response.status_code)
        try:
            return response.json() if response.content else None
        except ValueError:
            raise DemoFailure("invalid_json_response") from None

    def client(self, account):
        if account not in self.clients:
            role = self.spec["accounts"][account]
            client = httpx.Client(base_url=self.args.base_url.rstrip("/"), timeout=45, transport=self.transport)
            try:
                auth = self.request_raw(client, "POST", "/auth/password/login", json={
                    "workspace": self.args.workspace, "account_code": account, "role": role, "password": self.password,
                })
                client.headers["Authorization"] = "Bearer " + auth["access_token"]
                self.clients[account] = client
                self.refresh_tokens[account] = auth["refresh_token"]
                expect(auth.get("must_change_password") is False, "existing_account_requires_password_change")
                actor = auth["actor"]
                expect(actor["account_code"] == account and actor["role"] == role, "login_identity_mismatch")
                previous = self.state["actors"].get(account)
                expect(not previous or previous["user_id"] == actor["user_id"], "existing_account_id_changed")
                self.state["actors"][account] = {k: actor[k] for k in ("user_id", "account_code", "display_name", "role", "team_names")}
                self.save()
            except Exception:
                if account not in self.clients:
                    client.close()
                raise
        return self.clients[account]

    def req(self, account, method, path, expected=(200,), **kwargs):
        client = self.client(account)
        try:
            return self.request_raw(client, method, path, expected, **kwargs)
        except DemoFailure as failure:
            if failure.status != 401:
                raise
        auth = self.request_raw(client, "POST", "/auth/refresh", json={"refresh_token": self.refresh_tokens[account]})
        client.headers["Authorization"] = "Bearer " + auth["access_token"]
        self.refresh_tokens[account] = auth["refresh_token"]
        return self.request_raw(client, method, path, expected, **kwargs)

    def mutation(self, key, account, method, path, body, expected=(200,)):
        op = self.state["operations"].get(key)
        if op is None:
            op = {"account": account, "method": method, "path": path,
                  "body": body, "request_id": str(uuid4()), "status": "planned"}
            self.state["operations"][key] = op
            self.save()  # The idempotency key and exact body exist before any network write.
        expect((op["account"], op["method"], op["path"]) == (account, method, path), "operation_identity_changed")
        if op["status"] == "succeeded":
            return op["response"]
        if path == "/conversations" and op.get("attempted") and not self.args.conversation_idempotency_verified:
            raise DemoFailure("conversation_result_uncertain_check_server_idempotency_before_replay")
        op["attempted"] = True
        self.save()
        response = self.req(account, method, path, expected, json=op["body"], headers={"Idempotency-Key": op["request_id"]})
        op.update(status="succeeded", response=response)
        self.save()
        self.emit("mutation_saved", operation=key)
        return response

    def selected_cases(self):
        if self.args.record:
            record = next(v for v in self.spec["visits"] if v["key"] == self.args.record)
            expect(not self.args.case or record["case"] == self.args.case, "record_case_mismatch")
            return [self.cases[record["case"]]]
        return [self.cases[self.args.case]] if self.args.case else list(self.cases.values())

    def selected_visits(self):
        cases = {c["key"] for c in self.selected_cases()}
        return [v for v in self.spec["visits"] if v["case"] in cases and (not self.args.record or v["key"] == self.args.record)]

    def actor_id(self, account):
        self.client(account)
        return self.state["actors"][account]["user_id"]

    def ready_customer(self, key):
        if key in self.state["customers"]:
            return self.state["customers"][key]
        spec = self.customers[key]
        self.client(spec["owner"])
        operation_key = "customer:" + key
        if operation_key not in self.state["operations"]:
            rows = self.req("OPSADMIN", "GET", "/console/customers", params={"q": spec["name"], "limit": 100})["items"]
            expect(not any(v["name"] == spec["name"] for v in rows), "same_name_customer_without_batch_receipt")
        team_names = self.state["actors"][spec["owner"]]["team_names"]
        expect(len(team_names) == 1, "sales_team_requires_explicit_resolution")
        body = {k: spec[k] for k in ("name", "industry", "customer_type", "contact_name", "contact_title")}
        body.update(company_reference=f"DEMO-{self.spec['batch']}-{key}", level_code="Tier-2",
                    source="其他", target_team=team_names[0], contact_role="影响者")
        customer = self.mutation(operation_key, "OPSADMIN", "POST", "/customers", body, (201,))
        claim = self.mutation("claim:" + key, spec["owner"], "POST", f"/customers/{customer['id']}/claims", {}, (201,))
        expect(claim.get("status") == "pending" and bool(claim.get("request_id")), "claim_not_pending")
        approved = self.mutation("approve:" + key, "OPSADMIN", "POST", f"/console/claims/{claim['request_id']}/decision",
                                 {"decision": "approved", "reason": f"本批演示客户公司建档编号DEMO-{self.spec['batch']}-{key}，仅演示认领流程"})
        expect(approved.get("status") == "approved", "claim_not_approved")
        self.state["customers"][key] = {"id": customer["id"], "name": spec["name"], "claim_id": claim["request_id"], "owner": spec["owner"]}
        self.save()
        return self.state["customers"][key]

    def ready_opportunity(self, key):
        if key in self.state["opportunities"]:
            return self.state["opportunities"][key]
        spec = self.opportunities[key]
        customer = self.ready_customer(spec["customer"])
        operation_key = "opportunity:" + key
        if operation_key not in self.state["operations"]:
            rows = self.req(spec["owner"], "GET", "/opportunities", params={"customer_id": customer["id"], "page_size": 300, "include_closed": True})["items"]
            expect(not any(v["name"] == spec["name"] for v in rows), "same_name_opportunity_without_batch_receipt")
        close_date = self.today + timedelta(days=spec["close_days"])
        body = {k: spec[k] for k in ("name", "amount", "probability", "product_line")}
        body.update(action="create", status="open", sales_channel="direct", expected_close_date=close_date.isoformat(),
                    follow_up_plan="仅演示：完成样本核对和试点范围确认，客户尚未承诺采购。",
                    fde_member_ids=[self.actor_id(a) for a in spec["members"]],
                    quarterly_forecasts=[{"year": close_date.year, "quarter": (close_date.month - 1)//3+1,
                                          "recognized_amount": spec["recognized_amount"], "collection_amount": spec["collection_amount"]}])
        row = self.mutation(operation_key, spec["owner"], "POST", f"/customers/{customer['id']}/opportunities", body, (201,))
        self.state["opportunities"][key] = {"id": row["id"], "customer_id": customer["id"], "name": spec["name"], "owner": spec["owner"]}
        self.save()
        return self.state["opportunities"][key]

    def prepare(self):
        for case in self.selected_cases():
            for key in case["opportunities"]:
                self.ready_opportunity(key)
            if case.get("workflow") == "handover":
                self.handover()
            self.emit("case_prepared", case=case["key"])

    def material(self, spec):
        opportunity = self.opportunities[spec["opportunity"]]
        customer = self.customers[opportunity["customer"]]
        actor = self.state["actors"][spec["account"]]["display_name"]
        visit_date = (self.today + timedelta(days=spec["day_offset"])).isoformat()
        prefix = f"{self.spec['marker']}\n演示编号：{self.spec['batch']}/{spec['key']}。\n{visit_date}，{actor}通过视频会议拜访{customer['name']}的{customer['contact_title']}{customer['contact_name']}，关联既有商机“{opportunity['name']}”。"
        if spec["expected"] == "insufficient":
            return prefix + spec["facts"] + "下一步：" + spec["next_task"] + "。"
        deadline = (self.today + timedelta(days=3)).isoformat()
        meeting = (self.today + timedelta(days=4)).isoformat()
        first_profile = (f"客户主营业务为工业零部件制造；本项目需求为质量异常知识检索；预算初步估计为{opportunity['amount']/10000:g}万元，尚未经采购批准；联系人{customer['contact_name']}为影响者。" if spec["first_visit"] else "")
        return (prefix + ("这是本人的首次拜访。" + first_profile if spec["first_visit"] else "这是持续跟进，不新建商机。")
                + "\n沟通内容：" + spec["facts"]
                + f"\n下一步：{actor}于{deadline}12:00前{spec['next_task']}；{customer['contact_name']}于{deadline}15:00前提供10条脱敏样本及对应有效文档；双方于{meeting}10:00视频复核10条样本，逐条确认来源和待补字段。"
                + "本次记录由本人填写，未确认新的合同、收入或回款，AI不得推断未提供的商业事实。")

    def eligible(self, account, opportunity, expected=True):
        value = self.req(account, "GET", "/fde/visit-opportunities", params={
            "customer_id": opportunity["customer_id"], "opportunity_id": opportunity["id"], "limit": 1})
        found = any(row["id"] == opportunity["id"] for row in value["items"])
        expect(found is expected, "fde_recording_eligibility_mismatch")
        return value

    def review(self):
        for spec in self.selected_visits():
            key = spec["key"]
            expect(spec["opportunity"] in self.state["opportunities"], "run_prepare_first")
            record = self.state["visits"].setdefault(key, {})
            if record.get("candidate"):
                self.emit("candidate_already_saved", record=key)
                continue
            account = spec["account"]
            self.client(account)
            opportunity = self.state["opportunities"][spec["opportunity"]]
            if account.startswith("FDE"):
                self.eligible(account, opportunity)
            record.setdefault("original_text", self.material(spec))
            record.update(account=account, customer_id=opportunity["customer_id"], opportunity_id=opportunity["id"])
            self.save()
            conv = self.mutation("conversation:" + key, account, "POST", "/conversations", {
                "mode": "visit_entry", "customer_id": opportunity["customer_id"], "opportunity_id": opportunity["id"]}, (201,))
            expect(conv.get("opportunity_id") == opportunity["id"], "conversation_binding_mismatch")
            accepted = self.mutation("message:" + key, account, "POST", f"/conversations/{conv['id']}/messages", {
                "text": record["original_text"], "client_message_id": f"{self.spec['batch']}:{key}", "input_source": "text"}, (202,))
            record["run_id"] = accepted["run_id"]
            self.save()
            end = time.monotonic() + self.args.model_wait_seconds
            while True:
                run = self.req(account, "GET", f"/agent/runs/{record['run_id']}")
                if run["status"] in {"waiting_human", "succeeded"} and run.get("result"):
                    break
                if run["status"] in {"failed", "cancelled", "superseded"}:
                    record["run_status"] = run["status"]
                    self.save()
                    raise DemoFailure("real_ai_terminal_failure_no_automatic_retry")
                if time.monotonic() >= end:
                    raise DemoFailure("real_ai_still_running_resume_same_record")
                time.sleep(2)
            candidate = run["result"]
            quality = candidate.get("quality_review", {})
            score = quality.get("follow_up_score")
            expect(type(score) is int and 0 <= score <= 100, "invalid_real_quality_score")
            record.update(candidate=candidate, candidate_hash=fingerprint(candidate), run_status=run["status"],
                          archive_allowed=score > 60 and quality.get("next_action", {}).get("passed") is True,
                          checked=None, ai_completed_at=run.get("completed_at"))
            self.save()
            if spec["expected"] == "insufficient":
                self.check(key + ":low_quality_observed", not record["archive_allowed"], score=score)
            self.emit("review_candidate_ready", record=key, score=score, archive_allowed=record["archive_allowed"])

    def revise(self):
        """Keep reviewer edits; reuse a review only when its canonical text is unchanged."""
        from sales_backend.domain.visit_review import review_text

        path = getattr(self.args, "revision_file", None)
        expect(path is not None and self.args.record, "revision_file_and_record_required")
        path = Path(path)
        expect(REPO not in path.resolve().parents and path.resolve() != REPO, "revision_file_must_be_outside_repository")
        plan = json.loads(path.read_text(encoding="utf-8"))
        expect(plan.get("schema_version") == 1 and isinstance(plan.get("revisions"), list), "invalid_revision_plan")
        matches = [r for r in plan["revisions"] if r.get("record") == self.args.record]
        expect(len(matches) == 1, "revision_record_missing_or_duplicate")
        patch = matches[0]
        expect(set(patch) == {"record", "source_candidate_hash", "fields", "reason"}, "invalid_revision_keys")
        expect(isinstance(patch["reason"], str) and bool(patch["reason"].strip()), "revision_reason_required")
        edits = patch["fields"]
        expect(isinstance(edits, dict) and bool(edits) and set(edits) <= REVISION_FIELDS
               and all(isinstance(value, str) for value in edits.values()), "invalid_revision_fields")
        spec = self.selected_visits()[0]
        expect(spec["expected"] == "archive", "low_quality_scenario_cannot_be_revised")
        key, account = spec["key"], spec["account"]
        record = self.state["visits"].get(key, {})
        expect(not record.get("archived_id") and not record.get("verified_archive"), "already_archived_cannot_revise")
        expect("archive:" + key not in self.state["operations"], "archive_dispatch_exists_resolve_before_revision")
        revision_id = fingerprint(patch)
        entries = record.setdefault("revisions", [])
        entry = next((item for item in entries if item["id"] == revision_id), None)
        if entry and entry["status"] == "applied":
            expect(record.get("candidate_hash") == entry["result_candidate_hash"], "revision_is_not_current_candidate")
            self.emit("revision_already_applied", record=key, revision_id=revision_id)
            return
        if entry is None:
            pending = next((item for item in entries if item["id"] == record.get("pending_revision_id")), None)
            expect(not record.get("pending_revision_id") or (pending and pending["status"] == "failed"),
                   "another_revision_is_pending")
            candidate = record.get("candidate")
            expect(bool(candidate) and fingerprint(candidate) == record.get("candidate_hash") == patch["source_candidate_hash"],
                   "revision_source_candidate_changed")
            candidate = json.loads(json.dumps(candidate))
            fields = {**candidate["fields"], **edits}
            expect(fields != candidate["fields"], "revision_has_no_change")
            entry = {"id": revision_id, "patch": patch, "status": "planned", "created_at": datetime.now(TZ).isoformat(),
                     "source_candidate": candidate, "source_candidate_hash": record["candidate_hash"],
                     "source_run_id": record["run_id"], "source_run_status": record.get("run_status"),
                     "source_checked": record.get("checked"), "source_ai_completed_at": record.get("ai_completed_at"),
                     "submitted_fields": fields, "review_text": review_text(fields),
                     "requires_ai_review": review_text(fields) != review_text(candidate["fields"]),
                     "retry_of_revision_id": pending["id"] if pending else None}
            entries.append(entry)
            record["pending_revision_id"] = revision_id
            record["checked"] = None
            self.save()  # Original output, edits and revision identity exist before any request.
        else:
            expect(record.get("pending_revision_id") == revision_id
                   and record.get("candidate_hash") == entry["source_candidate_hash"], "revision_state_changed")
        if entry.get("status") == "failed":
            raise DemoFailure("revision_failed_create_explicit_new_revision_after_review")
        if entry["requires_ai_review"]:
            opportunity = self.state["opportunities"][spec["opportunity"]]
            if account.startswith("FDE"):
                self.eligible(account, opportunity)
            operation_key = "revise:" + key + ":" + revision_id
            conv = self.mutation(operation_key + ":conversation", account, "POST", "/conversations", {
                "mode": "visit_entry", "customer_id": opportunity["customer_id"], "opportunity_id": opportunity["id"]}, (201,))
            expect(conv.get("opportunity_id") == opportunity["id"], "conversation_binding_mismatch")
            accepted = self.mutation(operation_key + ":message", account, "POST", f"/conversations/{conv['id']}/messages", {
                "text": entry["review_text"], "client_message_id": "revision:" + revision_id, "input_source": "text"}, (202,))
            entry.update(run_id=accepted["run_id"], status="running")
            self.save()
            end = time.monotonic() + self.args.model_wait_seconds
            while True:
                run = self.req(account, "GET", f"/agent/runs/{entry['run_id']}")
                expect(run.get("id", entry["run_id"]) == entry["run_id"], "revision_run_receipt_mismatch")
                if run["status"] in {"waiting_human", "succeeded"} and run.get("result"):
                    break
                if run["status"] in {"failed", "cancelled", "superseded"}:
                    entry.update(status="failed", run_status=run["status"], error_code=run.get("error_code"))
                    self.save()
                    raise DemoFailure("revision_ai_terminal_failure_no_automatic_retry")
                if time.monotonic() >= end:
                    raise DemoFailure("revision_ai_still_running_resume_same_record")
                time.sleep(2)
            raw_candidate = run["result"]
            entry.update(review_candidate=raw_candidate, review_candidate_hash=fingerprint(raw_candidate),
                         run_status=run["status"], ai_completed_at=run.get("completed_at"))
            self.save()
            if review_text(raw_candidate.get("fields") or {}) != entry["review_text"]:
                entry.update(status="failed", error_code="revision_model_changed_reviewed_text")
                self.save()
                raise DemoFailure("revision_model_changed_reviewed_text")
            # Auxiliary extraction from the model cannot overwrite the reviewer's chosen values.
            revised = {**raw_candidate, "fields": dict(entry["submitted_fields"])}
            run_id, run_status, completed = entry["run_id"], run["status"], run.get("completed_at")
        else:
            revised = {**entry["source_candidate"], "fields": dict(entry["submitted_fields"])}
            run_id, run_status, completed = entry["source_run_id"], entry["source_run_status"], entry["source_ai_completed_at"]
        quality = revised.get("quality_review") or {}
        score = quality.get("follow_up_score")
        expect(type(score) is int and 0 <= score <= 100, "invalid_real_quality_score")
        record.update(candidate=revised, candidate_hash=fingerprint(revised), run_id=run_id, run_status=run_status,
                      archive_allowed=score > 60 and quality.get("next_action", {}).get("passed") is True,
                      checked=None, ai_completed_at=completed)
        entry.update(status="applied", result_candidate_hash=record["candidate_hash"], result_run_id=run_id,
                     applied_at=datetime.now(TZ).isoformat(), reused_review=not entry["requires_ai_review"])
        record.pop("pending_revision_id", None)
        self.save()
        self.emit("revision_applied_needs_content_check", record=key, revision_id=revision_id,
                  reused_review=entry["reused_review"], archive_allowed=record["archive_allowed"])

    def board_count(self, account):
        value = self.req(account, "GET", "/fde/dashboard", params={"scope": "self", "year": self.today.year})
        return value["summary"]["period_visits"]

    def notifications(self, account, object_id):
        rows = self.req(account, "GET", "/notifications", params={"page_size": 100})["items"]
        return [row for row in rows if row.get("object_id") == object_id
                or object_id in {row.get("payload", {}).get("visit_id"), row.get("payload", {}).get("task_id"), row.get("payload", {}).get("opportunity_id")}]

    def archive_feedback(self, spec, record, detail):
        """Check the recorder's real home projection and each FDE participant's receipt.

        A sales recorder is not a recipient of the FDE collaboration notification.
        The home card is rebuilt from committed visits, including after re-login.
        """
        key, account, visit_id = spec["key"], spec["account"], detail["id"]
        home = self.req(account, "GET", "/assistant/home")
        receipts = [r for r in home.get("archived_visits", []) if r.get("id") == visit_id]
        self.check(key + ":own_overview_receipt", len(receipts) == 1
                   and receipts[0].get("customer_id") == detail["customer_id"]
                   and receipts[0].get("score") == detail["follow_up_score"]
                   and receipts[0].get("next_action") == detail["next_action"],
                   visit_id=visit_id, receipt_count=len(receipts), source="/assistant/home.archived_visits")
        participants = sorted(set(spec["participants"]) | (
            {account} if self.spec["accounts"][account] in {"fde", "fde_lead"} else set()))
        self.check(key + ":archived_fde_participants",
                   set(detail.get("fde_participant_ids", [])) == {self.actor_id(a) for a in participants})
        notes_by_account = {}
        for participant in participants:
            notes = [n for n in self.notifications(participant, visit_id)
                     if n.get("template_code") == "visit_archived"]
            self.check(key + ":collaboration_notification:" + participant, len(notes) == 1,
                       recipient=participant, visit_id=visit_id, notification_count=len(notes))
            notes_by_account[participant] = notes
        # Replace the former recorder-only check on resume with verified evidence
        # from its two actual delivery channels; do not retain a stale false result.
        self.check(key + ":archive_notification", True,
                   semantics="recorder_overview_and_fde_participant_notifications",
                   collaboration_recipients=participants)
        record.update(archive_overview_receipt=receipts[0],
                      archive_notifications=notes_by_account.get(account, []),
                      archive_notifications_by_account=notes_by_account)

    def archive(self):
        for spec in self.selected_visits():
            if spec["expected"] != "archive":
                continue
            key, account = spec["key"], spec["account"]
            record = self.state["visits"].get(key, {})
            if record.get("verified_archive"):
                self.emit("archive_already_verified", record=key)
                continue
            expect(not record.get("pending_revision_id"), "revision_pending_cannot_archive")
            candidate = record.get("candidate")
            expect(bool(candidate), "run_review_first")
            checked = record.get("checked") or {}
            expect(checked.get("candidate_hash") == fingerprint(candidate) == record["candidate_hash"]
                   and checked.get("run_id") == record["run_id"]
                   and bool(checked.get("reviewer")) and bool(checked.get("reviewed_at")), "explicit_candidate_review_required")
            quality = candidate["quality_review"]
            expect(quality["follow_up_score"] > 60 and quality.get("next_action", {}).get("passed") is True, "quality_gate_not_passed")
            fields = dict(candidate["fields"])
            expect(all(str(fields.get(k) or "").strip() for k in ("follow_up_record", "next_action")), "candidate_body_missing")
            opportunity = self.state["opportunities"][spec["opportunity"]]
            if account.startswith("FDE"):
                self.eligible(account, opportunity)
            watched = [account] if account.startswith("FDE") else spec["participants"]
            if "counts_before" not in record:
                record["counts_before"] = {a: self.board_count(a) for a in watched}
                self.save()
            fields.update(interaction_at=(self.today + timedelta(days=spec["day_offset"])).isoformat(),
                          created_date=datetime.now(TZ).date().isoformat(), is_first_visit=spec["first_visit"],
                          opportunity_id=opportunity["id"], _quality_review_run_id=record["run_id"])
            payload = {"customer_id": opportunity["customer_id"], "fields": fields,
                       "fde_participant_ids": [self.actor_id(a) for a in spec["participants"]]}
            archived = self.mutation("archive:" + key, account, "POST", "/visits", payload, (201,))
            record["archived_id"] = archived["id"]
            self.save()
            operation = self.state["operations"]["archive:" + key]
            replay = self.req(account, "POST", "/visits", (201,), json=operation["body"], headers={"Idempotency-Key": operation["request_id"]})
            self.check(key + ":archive_replay", replay.get("id") == archived["id"])
            detail = self.req(account, "GET", f"/visits/{archived['id']}")
            actor_id = self.actor_id(account)
            self.check(key + ":creator_recorder", detail.get("recorder_id") == actor_id and detail.get("creator_id") == actor_id)
            self.check(key + ":bound_opportunity", detail.get("customer_id") == opportunity["customer_id"] and detail.get("opportunity_id") == opportunity["id"])
            self.check(key + ":real_score", detail.get("follow_up_score") == quality["follow_up_score"])
            for field in ("follow_up_record", "next_action"):
                self.check(key + ":" + field, detail.get(field) == fields[field])
            counts_after = {a: self.board_count(a) for a in watched}
            self.check(key + ":own_count_only", all(counts_after[a] == record["counts_before"][a] + (1 if a == account else 0) for a in watched), before=record["counts_before"], after=counts_after)
            self.archive_feedback(spec, record, detail)
            record.update(readback=detail, verified_archive=True)
            self.save()
            self.emit("archive_verified", record=key, visit_id=archived["id"])

    def opportunity_detail(self, account, opportunity):
        detail = self.req(account, "GET", f"/opportunities/{opportunity['id']}/detail")
        row = next((r for r in detail["opportunities"] if r["id"] == opportunity["id"]), None)
        expect(row is not None, "opportunity_detail_missing")
        return row

    def set_members(self, key, opportunity, accounts):
        owner = opportunity["owner"]
        row = self.opportunity_detail(owner, opportunity)
        return self.mutation(key, owner, "PUT", f"/opportunities/{opportunity['id']}/fde-members", {
            "member_ids": [self.actor_id(a) for a in accounts], "version_no": row["version_no"]})

    def task_event(self, key, actor, task, event, note, assignee=None):
        # Replay uses the exact saved version/body, including after an uncertain response.
        previous = self.state["operations"].get(key)
        if previous and previous["status"] == "succeeded":
            return previous["response"]
        detail = self.req(actor, "GET", f"/tasks/{task['id']}")
        body = {"event_type": event, "note": note, "version_no": detail["version_no"]}
        if assignee:
            body["assignee_account_code"] = assignee
        return self.mutation(key, actor, "POST", f"/tasks/{task['id']}/events", body)

    def handover(self):
        if self.state.get("handover_verified"):
            return
        opportunity = self.state["opportunities"]["dispatch"]
        original = self.opportunities["dispatch"]["members"]
        added = self.set_members("handover:add_member", opportunity, original + ["FDE003"])
        # Completed removal must not be undone when resuming later steps.
        if "handover:remove_member" not in self.state["operations"]:
            self.eligible("FDE003", opportunity)
            self.state["handover_added_notifications"] = self.notifications("FDE003", opportunity["id"])
            self.check("handover:added_notification", any(n.get("template_code") == "fde_joined" for n in self.state["handover_added_notifications"]))
        due = datetime.combine(self.today + timedelta(days=7), datetime.min.time(), TZ).replace(hour=17).isoformat()
        task = self.mutation("handover:task", "XS002", "POST", "/tasks", {
            "description": "【演示】运输调度：核对6个异常场景字段；本任务演示协助名单变化后的正式交接。",
            "assignee_account_code": "FDE003", "due_at": due, "priority_code": "high",
            "customer_id": opportunity["customer_id"], "opportunity_id": opportunity["id"]}, (201,))
        self.task_event("handover:first_accept", "FDE003", task, "accept", "已接收演示场景字段核对")
        removed = self.set_members("handover:remove_member", opportunity, original)
        self.eligible("FDE003", opportunity, expected=False)
        self.state["handover_removed_notifications"] = self.notifications("FDE003", opportunity["id"])
        self.check("handover:removed_notification", any(n.get("template_code") == "fde_removed" for n in self.state["handover_removed_notifications"]))
        if "handover:reassign" not in self.state["operations"]:
            detail = self.req("XS002", "GET", f"/tasks/{task['id']}")
            self.check("handover:pending_handover", detail.get("handover_required") is True)
        self.task_event("handover:reassign", "XS002", task, "reassign", "演示：原FDE移出商机，由仍参与的周玮承接", "FDE001")
        self.task_event("handover:second_accept", "FDE001", task, "accept", "已确认接收交接资料")
        self.task_event("handover:complete", "FDE001", task, "complete", "【演示】6个异常场景已逐项核对，交接验收完成")
        self.state["tasks"]["handover"] = self.req("XS002", "GET", f"/tasks/{task['id']}")
        self.check("handover:completed", self.state["tasks"]["handover"]["status"] == "completed")
        self.state["handover_task_notifications"] = {a: self.notifications(a, task["id"]) for a in ("XS002", "FDE001", "FDE003")}
        self.check("handover:task_notifications", any(n.get("template_code") == "task_reassigned" for n in self.state["handover_task_notifications"]["FDE001"])
                   and any(n.get("template_code") == "task_completed" for n in self.state["handover_task_notifications"]["XS002"]))
        position = self.mutation("position:task", "XS002", "POST", "/tasks", {
            "description": "【演示】运输调度：由一位协助FDE领取并整理试点资料目录，避免多人重复完成。",
            "target_position": "fde", "due_at": due, "priority_code": "normal",
            "customer_id": opportunity["customer_id"], "opportunity_id": opportunity["id"]}, (201,))
        self.task_event("position:accept", "FDE001", position, "accept", "由本人领取此岗位演示任务")
        self.task_event("position:complete", "FDE001", position, "complete", "【演示】目录已核对，岗位任务由一人完成")
        self.state["tasks"]["position"] = self.req("XS002", "GET", f"/tasks/{position['id']}")
        self.check("position:single_owner", len([a for a in self.state["tasks"]["position"]["assignees"] if a["responsibility"] == "owner"]) == 1)
        rejected = self.mutation("rejected:task", "XS002", "POST", "/tasks", {
            "description": "【演示】临时追加调度报告，由接收人说明资源冲突并拒绝，保留完整拒绝记录。",
            "assignee_account_code": "FDE001", "due_at": due, "priority_code": "normal",
            "customer_id": opportunity["customer_id"], "opportunity_id": opportunity["id"]}, (201,))
        self.task_event("rejected:reject", "FDE001", rejected, "reject", "【演示】当前需优先完成已约定的试点验证，临时报告资源冲突，请另行安排")
        self.state["tasks"]["rejected"] = self.req("XS002", "GET", f"/tasks/{rejected['id']}")
        self.check("rejected:cancelled", self.state["tasks"]["rejected"]["status"] == "cancelled")
        pending = self.mutation("pending:task", "XS002", "POST", "/tasks", {
            "description": "【演示】请FDE负责人复核运输调度试点的责任边界，保留待接收状态方便现场演示。",
            "assignee_account_code": "FDEL001", "due_at": due, "priority_code": "medium",
            "customer_id": opportunity["customer_id"], "opportunity_id": opportunity["id"]}, (201,))
        self.state["tasks"]["pending"] = pending
        self.state["handover_verified"] = True
        self.state["handover_versions"] = {"added": added["version_no"], "removed": removed["version_no"]}
        self.save()

    def verify(self):
        if any(c.get("workflow") == "handover" for c in self.selected_cases()):
            expect(self.state.get("handover_verified"), "handover_not_finished")
        for spec in self.selected_visits():
            record = self.state["visits"].get(spec["key"], {})
            if spec["expected"] == "insufficient":
                self.check(spec["key"] + ":not_archived", not record.get("archived_id"))
                continue
            expect(record.get("verified_archive"), "selected_record_not_archived")
            readback = self.req(spec["account"], "GET", f"/visits/{record['archived_id']}")
            self.check(spec["key"] + ":final_readback", readback.get("id") == record["archived_id"] and readback.get("recorder_id") == self.actor_id(spec["account"]))
            self.archive_feedback(spec, record, readback)
        accounts = {v["account"] for v in self.selected_visits() if v["account"].startswith("FDE")}
        for account in accounts | {"FDEL001"}:
            self.state.setdefault("final_dashboards", {})[account] = self.req(account, "GET", "/fde/dashboard", params={"scope": "team" if account == "FDEL001" else "self", "year": self.today.year})
            if account not in accounts:
                continue
            expected_ids = {v["archived_id"] for v in self.state["visits"].values() if v.get("account") == account and v.get("verified_archive")}
            offset, found = 0, {}
            for _ in range(100):
                page = self.req(account, "GET", "/fde/activity", params={"scope": "self", "period": "all", "offset": offset, "limit": 100})
                found.update({row["id"]: row for row in page["items"] if row["id"] in expected_ids})
                if not page.get("has_more"):
                    break
                next_offset = page.get("next_offset")
                expect(type(next_offset) is int and next_offset > offset, "activity_pagination_contract")
                offset = next_offset
            self.check(account + ":personal_history", set(found) == expected_ids and all(row["recorder_user_id"] == self.actor_id(account) for row in found.values()), batch_record_count=len(found))
            self.state.setdefault("final_personal_history", {})[account] = list(found.values())
        self.save()
        self.emit("verify_finished", selected_records=len(self.selected_visits()))

    def run(self):
        self.save()
        try:
            getattr(self, self.args.phase)()
            self.state.pop("last_failure", None)
        except Exception as error:
            self.state["last_failure"] = {"phase": self.args.phase, "case": self.args.case, "record": self.args.record,
                                          "code": error.code if isinstance(error, DemoFailure) else "unexpected_error_redacted",
                                          "http_status": error.status if isinstance(error, DemoFailure) else None}
            self.emit("stopped", **self.state["last_failure"])
            raise
        finally:
            self.password = ""
            logout_failures = []
            for account, client in self.clients.items():
                try:
                    self.request_raw(client, "POST", "/auth/logout", (204,))
                except Exception:
                    logout_failures.append(account)
                client.headers.pop("Authorization", None)
                client.close()
            self.refresh_tokens.clear()
            self.state["logout_failures"] = logout_failures
            self.save()
        expect(not self.state["logout_failures"], "temporary_session_logout_failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path(__file__).with_name("fde_scenarios.json"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    parser.add_argument("--workspace", default="demo-sales-workspace")
    parser.add_argument("--phase", choices=("prepare", "review", "revise", "archive", "verify"), required=True)
    parser.add_argument("--case")
    parser.add_argument("--record")
    parser.add_argument("--revision-file", type=Path, help="仓库外人工修正清单，配合revise和record")
    parser.add_argument("--model-wait-seconds", type=int, default=300)
    parser.add_argument("--conversation-idempotency-verified", action="store_true", help="仅在服务器已部署并验证会话创建幂等后启用未知结果重放")
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.scheme != "https" or url.username or url.password or url.query or url.fragment:
        parser.error("HTTPS base URL without credentials/query/fragment required")
    if not 10 <= args.model_wait_seconds <= 900:
        parser.error("model wait must be 10–900 seconds")
    password = getpass.getpass("现有业务账号统一演示密码：") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\r\n")
    if not password:
        parser.error("existing account password required via hidden prompt or stdin")
    try:
        state_path = args.state.resolve()
        expect(REPO not in state_path.parents and state_path != REPO, "state_must_be_outside_repository")
        args.state.parent.mkdir(parents=True, exist_ok=True)
        with args.state.with_suffix(args.state.suffix + ".lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DemoFailure("state_is_in_use") from None
            Demo(args, password).run()
    except Exception as error:
        code = error.code if isinstance(error, DemoFailure) else "unexpected_error_redacted"
        parser.exit(1, "演示流程已停止：" + code + "；检查仓库外状态文件后用相同参数恢复。\n")


if __name__ == "__main__":
    main()
