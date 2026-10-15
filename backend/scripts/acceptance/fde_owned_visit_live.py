"""One opt-in, real FDE001 visit against the current demo database via HTTPS.

Reads one password from stdin; never loads secrets or prints response bodies.
Creates at most one explicit visit_entry run. A passing model result is archived
through the normal API, including its normal downstream jobs and notifications.
The script does not provision accounts, change memberships/policies, modify model
bindings, or suppress downstream work. A fresh output file is mandatory so a
second invocation cannot silently replace the evidence of a previous run.

Prepare/check this file without business writes using --help or Python AST parse.
Actual invocation: python fde_owned_visit_live.py --output /approved/evidence.json
Supply the existing FDE001 password on stdin, not as an argument or environment.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx


CUSTOMER_ID = "a2c95d6f-b92e-478a-8e9b-7c6338ad12e3"
OPPORTUNITY_ID = "62726002-b75f-467b-81c8-21a0a740eb80"
OTHER_OPPORTUNITY_ID = "d3d25844-3a52-42a3-ac64-7e1290b5f9ae"
ACTOR_ID = "94c6e56f-47a9-470e-ad70-6c580a987ea4"
TZ = ZoneInfo("Asia/Shanghai")


class AcceptanceFailure(Exception):
    """Only explicitly authored, non-secret failure codes reach evidence."""

    def __init__(self, code, status=None):
        self.code = code
        self.status = status


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def opportunity_snapshot(row):
    return {
        key: row.get(key)
        for key in (
            "id", "customer_id", "name", "amount", "probability", "status",
            "stage_code", "expected_close_date", "owner_id", "product_line",
        )
    }


async def run(args, password):
    started = datetime.now(TZ)
    today = started.date()
    followup_date = (today + timedelta(days=3)).isoformat()
    case_id = "FDE本人持续跟进演示-" + started.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    evidence = {
        "case_id": case_id,
        "data_source": "current_database_via_real_https",
        "account": "FDE001",
        "started_at": started.isoformat(),
        "explicit_ai_runs_requested": 0,
        "membership_policy_model_config_writes": 0,
        "downstream_jobs": "normal_archive_workflow_not_suppressed",
        "ids": {"customer": CUSTOMER_ID, "opportunity": OPPORTUNITY_ID},
        "checks": [],
        "passed": False,
    }
    logged_in = False
    stage = "initialization"
    completed = False

    def save():
        temporary = args.output.with_name(args.output.name + ".partial")
        temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(args.output)

    def check(code, passed, **facts):
        item = {"check": code, "passed": bool(passed), **facts}
        evidence["checks"].append(item)
        save()
        print(json.dumps(item, ensure_ascii=False), flush=True)
        if not passed:
            raise AcceptanceFailure(code)

    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=45) as client:
        async def request(method, path, expected=200, **kwargs):
            response = await client.request(method, path, **kwargs)
            if response.status_code != expected:
                raise AcceptanceFailure("unexpected_http_status", response.status_code)
            return response.json() if response.content else None

        async def opportunity_detail(opportunity_id):
            result = await request("GET", f"/opportunities/{opportunity_id}/detail")
            row = next((item for item in result.get("opportunities", []) if item.get("id") == opportunity_id), None)
            if not row or result.get("id") != CUSTOMER_ID:
                raise AcceptanceFailure("opportunity_customer_reference_mismatch")
            return row

        async def all_activity():
            items, offset, seen = [], 0, set()
            for _ in range(100):
                result = await request("GET", "/fde/activity", params={
                    "scope": "self", "period": "all", "opportunity_id": OPPORTUNITY_ID,
                    "limit": 100, "offset": offset,
                })
                rows = result.get("items")
                if not isinstance(rows, list) or not isinstance(result.get("total"), int):
                    raise AcceptanceFailure("activity_response_contract")
                items.extend(rows)
                if not result.get("has_more"):
                    return items
                next_offset = result.get("next_offset")
                if not isinstance(next_offset, int) or next_offset <= offset or next_offset in seen:
                    raise AcceptanceFailure("activity_pagination_contract")
                seen.add(offset)
                offset = next_offset
            raise AcceptanceFailure("activity_pagination_limit")

        try:
            stage = "login"
            auth = await request("POST", "/auth/password/login", json={
                "workspace": args.workspace, "account_code": "FDE001", "role": "fde", "password": password,
            })
            password = ""
            actor = auth.get("actor") or {}
            token = auth.get("access_token")
            if not isinstance(token, str) or not token:
                raise AcceptanceFailure("login_missing_token")
            client.headers["Authorization"] = "Bearer " + token
            logged_in = True
            must_change_password = auth.get("must_change_password")
            del token, auth
            check("existing_FDE001_identity", actor.get("user_id") == ACTOR_ID and actor.get("role") == "fde")
            check("existing_password_ready", must_change_password is False)
            stage = "capabilities"
            me = await request("GET", "/auth/me")
            check("self_visit_enabled", me.get("capabilities", {}).get("visit.create") is True)
            check("commercial_write_not_granted", me.get("capabilities", {}).get("opportunity.edit") is False)
            evidence["permission_version_before"] = me.get("permission_version")

            stage = "own_opportunity_selection"
            eligible = await request("GET", "/fde/visit-opportunities", params={
                "customer_id": CUSTOMER_ID, "opportunity_id": OPPORTUNITY_ID, "limit": 1, "offset": 0,
            })
            choices = eligible.get("items", [])
            selected = next((row for row in choices if row.get("id") == OPPORTUNITY_ID), None)
            check("own_opportunity_is_selectable", selected is not None and selected.get("customer_id") == CUSTOMER_ID,
                  total=eligible.get("total"))
            denied = await request("GET", "/fde/visit-opportunities", params={
                "customer_id": CUSTOMER_ID, "opportunity_id": OTHER_OPPORTUNITY_ID, "limit": 1, "offset": 0,
            })
            check("other_project_under_same_customer_not_selectable", denied.get("items") == [] and denied.get("total") == 0)
            other = await opportunity_detail(OTHER_OPPORTUNITY_ID)
            check("customer_panorama_remains_readable", other.get("id") == OTHER_OPPORTUNITY_ID)
            primary = await opportunity_detail(OPPORTUNITY_ID)
            before_commercial = opportunity_snapshot(primary)
            before_members = sorted(item["id"] for item in primary.get("fde_members", []))
            evidence["opportunity_version_before"] = primary.get("version_no")
            evidence["commercial_snapshot_before"] = fingerprint(before_commercial)
            before_board = await request("GET", "/fde/dashboard", params={"scope": "self", "year": today.year})
            before_records = await all_activity()
            check("baseline_personally_recorded_history", all(
                row.get("recorder_user_id") == ACTOR_ID and row.get("opportunity_id") == OPPORTUNITY_ID
                for row in before_records
            ), existing_records=len(before_records))
            evidence["baseline"] = {
                "period_visits": before_board.get("summary", {}).get("period_visits"),
                "active_recorders": before_board.get("summary", {}).get("active_recorders"),
                "opportunity_own_records": len(before_records),
            }

            stage = "create_contextual_conversation"
            conversation_key = str(uuid4())
            message_key = str(uuid4())
            evidence["request_keys"] = {"conversation": conversation_key, "message": message_key, "archive": str(uuid4())}
            save()
            conversation = await request("POST", "/conversations", 201, json={
                "mode": "visit_entry", "customer_id": CUSTOMER_ID, "opportunity_id": OPPORTUNITY_ID,
            }, headers={"Idempotency-Key": conversation_key})
            evidence["ids"]["conversation"] = str(UUID(conversation["id"]))
            check("conversation_binds_selected_opportunity", conversation.get("customer_id") == CUSTOMER_ID
                  and conversation.get("opportunity_id") == OPPORTUNITY_ID)
            material = (
                f"【演示材料，情节为编撰；验收编号：{case_id}】\n"
                f"{today.isoformat()}，FDE周玮以视频会议方式，与{selected['customer_name']}的客服经理林悦持续跟进"
                f"既有商机“{selected['name']}”。本次不是首次拜访，也没有新建商机。\n"
                "本次目标是复核知识检索试点中退款政策版本识别的问题，并确认下一轮验证资料。"
                "客户的12名客服目前每天处理约80条咨询，资料分散在3个知识库。"
                "本次使用客户已脱敏的10条退款咨询逐条核对，8条能返回正确政策依据，"
                "2条同时检索到旧版与新版说明，缺少政策生效日期，无法直接用于答复。"
                "林悦认可检索方向，但要求先补齐版本和生效时间，再决定是否扩大试点；"
                "客户尚未接受报价、签署合同或承诺采购，本次没有发生金额和商机阶段变更。\n"
                f"下一步：周玮于{followup_date}12:00前整理两条版本冲突样本及修正建议；"
                f"林悦于{followup_date}14:00前提供带生效日期的最新退款政策和10条脱敏历史咨询；"
                f"双方于{followup_date}15:00视频复核，逐条确认10条咨询能引用正确版本与生效日期。"
                "本次跟进记录由周玮本人填写，核对后由本人确认归档。"
            )
            evidence["demo_material_sha256"] = hashlib.sha256(material.encode()).hexdigest()
            evidence["explicit_ai_runs_requested"] = 1
            save()
            accepted = await request("POST", f"/conversations/{conversation['id']}/messages", 202, json={
                "text": material, "client_message_id": message_key, "input_source": "text",
            }, headers={"Idempotency-Key": message_key})
            run_id = str(UUID(accepted["run_id"]))
            evidence["ids"]["review_run"] = run_id
            save()
            stage = "wait_real_model"
            deadline = time.monotonic() + args.model_wait_seconds
            while True:
                model_run = await request("GET", f"/agent/runs/{run_id}")
                status = model_run.get("status")
                if status in {"waiting_human", "succeeded"}:
                    break
                if status in {"failed", "cancelled", "superseded"}:
                    raise AcceptanceFailure("model_run_terminal_failure")
                if time.monotonic() >= deadline:
                    raise AcceptanceFailure("model_run_observation_timeout_no_retry")
                await asyncio.sleep(2)
            result = model_run.get("result") or {}
            quality = result.get("quality_review") or {}
            fields = result.get("fields") or {}
            score = quality.get("follow_up_score")
            check("real_run_returns_review", model_run.get("id") == run_id and isinstance(fields, dict)
                  and type(score) is int and 0 <= score <= 100, run_id=run_id, score=score)
            check("quality_above_60", score > 60, score=score)
            check("next_action_review_passes", quality.get("next_action", {}).get("passed") is True)
            check("model_body_is_present", all(str(fields.get(key) or "").strip() for key in ("follow_up_record", "next_action")))
            evidence["model_review"] = {"score": score, "status": status, "completed_at": model_run.get("completed_at")}

            stage = "human_authorized_archive"
            payload = {"customer_id": CUSTOMER_ID, "fields": {
                **fields, "interaction_at": today.isoformat(), "created_date": today.isoformat(),
                "contact_name": fields.get("contact_name") or "林悦", "opportunity_id": OPPORTUNITY_ID,
                "_quality_review_run_id": run_id,
            }}
            # Model fields/quality are used unaltered; only explicit source facts and
            # selected relationship metadata are supplied. No delegated FDE names.
            archive_headers = {"Idempotency-Key": evidence["request_keys"]["archive"]}
            archived = await request("POST", "/visits", 201, json=payload, headers=archive_headers)
            visit_id = str(UUID(archived["id"]))
            evidence["ids"]["visit"] = visit_id
            save()
            repeated = await request("POST", "/visits", 201, json=payload, headers=archive_headers)
            check("archive_request_replays_same_visit", repeated.get("id") == visit_id)
            check("archive_receipt_links_selected_project", archived.get("status") == "archived"
                  and archived.get("customer_id") == CUSTOMER_ID and archived.get("opportunity_id") == OPPORTUNITY_ID)
            stage = "read_back_record_and_statistics"
            detail = await request("GET", f"/visits/{visit_id}")
            check("recorder_and_creator_are_FDE001", detail.get("recorder_id") == ACTOR_ID and detail.get("creator_id") == ACTOR_ID)
            check("archived_score_is_real_model_score", detail.get("follow_up_score") == score
                  and detail.get("quality_review", {}).get("follow_up_score") == score, score=score)
            check("archived_version_and_form_snapshot", type(detail.get("version_no")) is int
                  and detail["version_no"] >= 1 and bool(detail.get("form_version_id")),
                  version_no=detail.get("version_no"), form_version_id=detail.get("form_version_id"))
            check("no_other_FDE_is_recorded_for_this_archive", set(detail.get("fde_participant_ids", [])) == {ACTOR_ID})
            evidence["visit_version"] = detail["version_no"]
            after_records = await all_activity()
            own_rows = [row for row in after_records if row.get("id") == visit_id]
            check("own_full_history_contains_one_archived_record", len(own_rows) == 1
                  and own_rows[0].get("recorder_user_id") == ACTOR_ID and own_rows[0].get("can_read_detail") is True)
            check("own_project_history_only_contains_own_records", all(
                row.get("recorder_user_id") == ACTOR_ID and row.get("opportunity_id") == OPPORTUNITY_ID
                for row in after_records
            ))
            after_board = await request("GET", "/fde/dashboard", params={"scope": "self", "year": today.year})
            before_count = evidence["baseline"]["period_visits"]
            after_count = after_board.get("summary", {}).get("period_visits")
            check("personal_period_count_increases_once", type(before_count) is int and after_count == before_count + 1,
                  before=before_count, after=after_count)
            check("personal_active_recorder_is_one", after_board.get("summary", {}).get("active_recorders") == 1)
            notifications = await request("GET", "/notifications", params={"page_size": 100})
            receipts = [row for row in notifications.get("items", []) if row.get("template_code") == "visit_archived"
                        and (row.get("object_id") == visit_id or row.get("payload", {}).get("visit_id") == visit_id)]
            check("archive_notification_is_persisted_once", len(receipts) == 1,
                  notification_ids=[row["id"] for row in receipts])
            home = await request("GET", "/assistant/home")
            check("overview_has_own_archive_receipt", any(row.get("id") == visit_id for row in home.get("archived_visits", [])))
            after_primary = await opportunity_detail(OPPORTUNITY_ID)
            check("commercial_fields_unchanged", opportunity_snapshot(after_primary) == before_commercial)
            check("existing_collaboration_list_unchanged", sorted(row["id"] for row in after_primary.get("fde_members", [])) == before_members)
            evidence["opportunity_version_after"] = after_primary.get("version_no")
            final_me = await request("GET", "/auth/me")
            check("normal_entry_remains_enabled", final_me.get("capabilities", {}).get("visit.create") is True)
            evidence["permission_version_after"] = final_me.get("permission_version")
            completed = True
        except Exception as exc:
            evidence["failure"] = {"stage": stage, "type": type(exc).__name__,
                                   "code": exc.code if isinstance(exc, AcceptanceFailure) else "unexpected_failure_details_redacted"}
            if isinstance(exc, AcceptanceFailure) and exc.status is not None:
                evidence["failure"]["http_status"] = exc.status
            print(json.dumps({"failure": evidence["failure"]}, ensure_ascii=False), flush=True)
        finally:
            password = ""
            if logged_in:
                try:
                    response = await client.post("/auth/logout")
                    check("logout", response.status_code == 204, http_status=response.status_code)
                except Exception as exc:
                    completed = False
                    evidence["logout_error_type"] = type(exc).__name__
                client.headers.pop("Authorization", None)
            evidence["completed_at"] = datetime.now(TZ).isoformat()
            evidence["passed"] = completed and all(row["passed"] for row in evidence["checks"])
            save()
            print(json.dumps({"passed": evidence["passed"], "checks": len(evidence["checks"]), "ids": evidence["ids"]}, ensure_ascii=False), flush=True)
    return 0 if evidence["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    parser.add_argument("--workspace", default="demo-sales-workspace")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-wait-seconds", type=int, default=300)
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.scheme != "https" or url.username or url.password or url.query or url.fragment:
        parser.error("base URL must be HTTPS without credentials, query or fragment")
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("use a fresh output file in an existing evidence directory")
    if not 10 <= args.model_wait_seconds <= 900:
        parser.error("model wait must be between 10 and 900 seconds")
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        parser.error("an existing FDE001 password is required on stdin")
    return asyncio.run(run(args, password))


if __name__ == "__main__":
    sys.exit(main())
