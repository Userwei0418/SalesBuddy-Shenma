# ruff: noqa: S608 -- Only the fixed lock suffix is composed into SQL.
"""Normal owner cancellation, serialized with every claimant by the job row lock.

No job_effect SELECT is needed or granted: legitimate report effects require a
running lease, are committed with a ready/failed projection, and no normal path
resets a report job to queued/attempt0. The locked never-started tuple below is
the invariant; it is not a claim that effect rows were queried.
"""

from datetime import timedelta

from pydantic_core import to_jsonable_python

from sales_backend.contracts.work_report import ReportError
from sales_backend.contracts.work_report_cancellation import SnapshotCancellationState
from sales_backend.db import json_value
from sales_backend.domain.work_report import WorkReportError, canonical_hash
from sales_backend.repositories.work_report import WorkReportRepository, decoded, snapshot_view

ERROR_CODE = "WORK_REPORT_SNAPSHOT_CANCELLED"
ERROR_DETAIL = "材料准备未启动且已超过等待期限，已结束本次请求，请重新准备材料"
WAIT_LIMIT = timedelta(minutes=30)
UNSTARTED = ("started_at", "completed_at", "lease_token", "locked_by", "locked_until",
             "last_error_code", "last_error_detail")
repository = WorkReportRepository()


async def _job(connection, actor, snapshot, *, lock=False):
    if snapshot.get("job_id") is None:
        return None
    row = await connection.fetchrow("""SELECT id,workspace_id,job_type,aggregate_type,aggregate_id,payload,
      status,attempts,max_attempts,created_at,updated_at,available_at,started_at,completed_at,
      lease_token,locked_by,locked_until,last_error_code,last_error_detail FROM ops.job
      WHERE id=$1::uuid AND workspace_id=$2::uuid""" + (" FOR UPDATE" if lock else ""),
      str(snapshot["job_id"]), actor.workspace_id)
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json_value(result["payload"])
    return result


def _matches(actor, snapshot, job):
    payload = job.get("payload")
    return (str(snapshot["workspace_id"]) == actor.workspace_id
        and str(snapshot["owner_user_ref_id"]) == actor.user_id
        and str(job["workspace_id"]) == actor.workspace_id
        and str(snapshot["job_id"]) == str(job["id"])
        and str(job["aggregate_id"]) == str(snapshot["id"])
        and job["aggregate_type"] == "work_report" and job["job_type"] == "work_report.snapshot"
        and isinstance(payload, dict) and payload.get("workspace_id") == actor.workspace_id
        and payload.get("user_id") == actor.user_id)


def _unstarted(job):
    return (job["status"] == "queued" and job["attempts"] == 0 and job["max_attempts"] > 0
            and all(job.get(name) is None for name in UNSTARTED))


def _revision(actor, snapshot, job):
    projection = {key: snapshot.get(key) for key in ("id", "workspace_id", "owner_user_ref_id", "job_id",
        "created_at", "selection", "period", "scope", "status", "snapshot_hash", "payload", "error", "data_as_of")}
    return canonical_hash(to_jsonable_python({"kind": "work_report.snapshot_cancellation.v1",
        "actor": actor.model_dump(mode="json"), "snapshot": projection, "job": job}))


def _state(actor, snapshot, job, instant):
    eligible_at = max(snapshot["created_at"], job["created_at"]) + WAIT_LIMIT if job is not None else None
    if snapshot["status"] != "preparing":
        reason = "snapshot_terminal"
    elif job is None:
        reason = "job_unavailable"
    elif not _matches(actor, snapshot, job):
        reason = "job_reference_mismatch"
    elif not _unstarted(job) or any(snapshot.get(key) is not None
                                   for key in ("payload", "snapshot_hash", "data_as_of", "error")):
        reason = "already_started_or_finished"
    elif instant <= eligible_at:
        reason = "not_expired"
    else:
        reason = "expired_unstarted"
    return SnapshotCancellationState(id=snapshot["id"], status=snapshot["status"],
        revision=_revision(actor, snapshot, job), cancellable=reason == "expired_unstarted",
        eligible_at=eligible_at, reason=reason)


async def cancellation_state(connection, actor, snapshot_id):
    await repository._enter(connection, actor, "work_report.write")
    snapshot = await repository._snapshot(connection, actor, snapshot_id)
    job = await _job(connection, actor, snapshot)
    return _state(actor, snapshot, job, await connection.fetchval("SELECT clock_timestamp()"))


async def cancel_snapshot(connection, actor, snapshot_id, body):
    await repository._enter(connection, actor, "work_report.write")
    initial = await repository._snapshot(connection, actor, snapshot_id)
    job = await _job(connection, actor, initial, lock=True)
    # Claim and cancellation serialize on this row. Reject running/retried jobs
    # before taking a projection lock: a real worker may hold that projection.
    if (initial["status"] != "preparing" or job is None
            or not _matches(actor, initial, job) or not _unstarted(job)):
        raise WorkReportError("WORK_REPORT_SNAPSHOT_NOT_CANCELLABLE", "材料任务已开始、已结束或引用不匹配，请刷新状态")
    snapshot = await repository._snapshot(connection, actor, snapshot_id, lock=True)
    state = _state(actor, snapshot, job, await connection.fetchval("SELECT clock_timestamp()"))
    if state.revision != body.expected_revision:
        raise WorkReportError("WORK_REPORT_VERSION_CONFLICT", "材料状态已更新，请刷新后重试")
    if not state.cancellable:
        raise WorkReportError("WORK_REPORT_SNAPSHOT_NOT_CANCELLABLE", "仅可结束超过等待期限且尚未启动的材料准备")
    ended = await connection.fetchval("""UPDATE ops.job SET status='dead_letter',
      completed_at=clock_timestamp(),updated_at=clock_timestamp(),
      last_error_code=$4,last_error_detail=$5
      WHERE id=$1::uuid AND workspace_id=$2::uuid AND aggregate_id=$3::uuid
        AND aggregate_type='work_report' AND job_type='work_report.snapshot' AND status='queued' AND attempts=0
        AND started_at IS NULL AND completed_at IS NULL AND lease_token IS NULL
        AND locked_by IS NULL AND locked_until IS NULL AND last_error_code IS NULL AND last_error_detail IS NULL
      RETURNING id""", str(job["id"]), actor.workspace_id, str(snapshot_id), ERROR_CODE, ERROR_DETAIL)
    if ended is None:
        raise WorkReportError("WORK_REPORT_VERSION_CONFLICT", "材料任务状态已更新，请刷新后重试")
    error = ReportError(code=ERROR_CODE, detail=ERROR_DETAIL, retryable=False)
    saved = decoded(await connection.fetchrow("""UPDATE agent.work_report_snapshot SET status='failed',error=$4::jsonb
      WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid
        AND job_id=$5::uuid AND status='preparing' AND payload IS NULL AND error IS NULL
        AND snapshot_hash IS NULL AND data_as_of IS NULL RETURNING *""",
      str(snapshot_id), actor.workspace_id, actor.user_id, error.model_dump(mode="json"), str(job["id"])))
    await repository._event(connection, actor, "snapshot", snapshot_id, "cancelled", status="failed")
    return snapshot_view(saved)
