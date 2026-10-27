"""Current-authority reads of immutable history; caller supplies its existing RR."""

import re
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from sales_backend.domain.opportunity_history import OpportunityHistoryChanged, first_ge30, freeze_history_guard
from sales_backend.repositories.authorization_checks import require_permission

EVENT_SQL = """SELECT id::text,workspace_id::text,opportunity_id::text,occurred_at,recorded_at,mutation_kind,
 before_version,after_version,before_probability,after_probability,before_status,after_status,before_stage,after_stage,
 source_kind,source_ref,source_version,history_origin,origin_event_id::text,trustworthy_from,complete_from_origin,
 opportunity_created_at FROM crm.opportunity_state_event
 WHERE workspace_id=$1::uuid AND opportunity_id=$2::uuid ORDER BY after_version,id"""

BATCH_EVENT_SQL = EVENT_SQL.replace(
    "opportunity_id=$2::uuid ORDER BY after_version,id",
    "opportunity_id=ANY($2::uuid[]) ORDER BY opportunity_id,after_version,id",
)


async def _read(connection, actor, opportunity_id, report_permission):
    if not connection.is_in_transaction():
        raise RuntimeError("opportunity history requires the caller transaction")
    if report_permission not in {
        "work_report.read",
        "work_report.write",
        "work_report.share",
        "report.export",
        "agent.work_report",
    }:
        raise ValueError("unsupported history permission")
    identity = await connection.fetchrow(
        "SELECT common.current_workspace_id()::text AS workspace_id, common.current_user_ref_id()::text AS user_id"
    )
    if not identity or (identity["workspace_id"], identity["user_id"]) != (actor.workspace_id, actor.user_id):
        raise PermissionError("商机历史必须使用当前真实用户上下文")
    await connection.execute("SELECT security.authorization_refresh()")
    for permission in ("opportunity.read", "work_report.read", report_permission):
        await require_permission(connection, permission, opportunity_id=opportunity_id)
    row = await connection.fetchrow(
        """SELECT id::text,workspace_id::text,version_no,probability,status,stage_code,created_at
      FROM crm.opportunity WHERE id=$1::uuid AND workspace_id=$2::uuid AND deleted_at IS NULL""",
        opportunity_id,
        actor.workspace_id,
    )
    if not row or (str(row["workspace_id"]), str(row["id"])) != (actor.workspace_id, opportunity_id):
        raise PermissionError("商机来源不存在或当前无权读取")
    events = [dict(event) for event in await connection.fetch(EVENT_SQL, actor.workspace_id, opportunity_id)]
    return dict(row), events


async def collect_first_ge30(connection, actor, opportunity_ids, *, period_start, cutoff):
    """IDs must be the caller's entire authorized scope, including closed/fallen rows.

    Proof is computed from the whole chain; dates only classify the proven first
    event. A future or past first never becomes another first in this period.
    """
    if not connection.is_in_transaction():
        raise RuntimeError("opportunity history requires the caller transaction")
    if (
        not all(isinstance(t, datetime) and t.tzinfo is not None for t in (period_start, cutoff))
        or cutoff < period_start
    ):
        raise ValueError("threshold period requires aware, ordered bounds")
    if await connection.fetchval("SHOW transaction_isolation") != "repeatable read":
        raise RuntimeError("threshold collection requires the caller readonly repeatable-read snapshot")
    if await connection.fetchval("SHOW transaction_read_only") != "on":
        raise RuntimeError("threshold collection requires the caller readonly snapshot")
    ids = list(dict.fromkeys(str(UUID(str(value))) for value in opportunity_ids))
    if not ids:
        return {}
    # One caller snapshot and current authorization context for the entire set.
    # Check every candidate before fetching any history; never drop denied rows
    # and turn a partially authorized scope into a complete cohort.
    identity = await connection.fetchrow(
        "SELECT common.current_workspace_id()::text AS workspace_id, common.current_user_ref_id()::text AS user_id"
    )
    if not identity or (identity["workspace_id"], identity["user_id"]) != (actor.workspace_id, actor.user_id):
        raise PermissionError("商机历史必须使用当前真实用户上下文")
    await connection.execute("SELECT security.authorization_refresh()")
    allowed = await connection.fetch("""SELECT candidate.id::text AS id,
      security.authorization_opportunity('opportunity.read',candidate.id)
        AND security.authorization_opportunity('work_report.read',candidate.id) AS allowed
      FROM unnest($1::uuid[]) candidate(id)""", ids)
    if len(allowed) != len(ids) or {r['id'] for r in allowed if r['allowed']} != set(ids):
        raise PermissionError("当前账号未获此操作或数据范围的授权")
    rows = await connection.fetch("""SELECT id::text,workspace_id::text,version_no,probability,status,stage_code,created_at
      FROM crm.opportunity WHERE id=ANY($1::uuid[]) AND workspace_id=$2::uuid AND deleted_at IS NULL""",
      ids, actor.workspace_id)
    by_id = {str(row['id']): dict(row) for row in rows}
    if len(rows) != len(ids) or set(by_id) != set(ids) or any(
        str(row['workspace_id']) != actor.workspace_id for row in rows
    ):
        raise PermissionError("商机来源不存在或当前无权读取")
    by_opportunity = {ident: [] for ident in ids}
    for event in await connection.fetch(BATCH_EVENT_SQL, actor.workspace_id, ids):
        ident = str(event['opportunity_id'])
        if ident not in by_opportunity or str(event['workspace_id']) != actor.workspace_id:
            raise PermissionError("商机历史必须使用当前授权集合")
        by_opportunity[ident].append(dict(event))
    result = {}
    for opportunity_id in ids:
        row, events = by_id[opportunity_id], by_opportunity[opportunity_id]
        fact = first_ge30(row, events)
        result[opportunity_id] = {
            "first_ge30": fact,
            "history_guard": freeze_history_guard(row, events, fact),
            "in_period": fact["status"] == "proven" and period_start <= fact["occurred_at"] < cutoff,
        }
    return result


async def revalidate_first_ge30(connection, actor, guard, *, report_permission="work_report.read"):
    required = {
        "opportunity_id",
        "current_version",
        "event_count",
        "head_event_id",
        "history_sha256",
        "first_event_id",
        "first_event_version",
    }
    if not isinstance(guard, dict) or set(guard) != required:
        raise OpportunityHistoryChanged("商机历史引用不完整，请重新取材")
    if (
        type(guard["current_version"]) is not int
        or guard["current_version"] < 1
        or type(guard["event_count"]) is not int
        or guard["event_count"] < 0
        or not isinstance(guard["history_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", guard["history_sha256"]) is None
        or (guard["first_event_id"] is None) != (guard["first_event_version"] is None)
        or (
            guard["first_event_version"] is not None
            and (type(guard["first_event_version"]) is not int or guard["first_event_version"] < 1)
        )
    ):
        raise OpportunityHistoryChanged("商机历史引用不完整，请重新取材")
    try:
        opportunity_id = str(UUID(guard["opportunity_id"]))
    except (TypeError, ValueError, AttributeError) as error:
        raise OpportunityHistoryChanged("商机历史引用不完整，请重新取材") from error
    row, events = await _read(connection, actor, opportunity_id, report_permission)
    current = freeze_history_guard(row, events, first_ge30(row, events))
    if current != guard:
        raise OpportunityHistoryChanged("商机或冻结历史已变化，请重新取材")


@asynccontextmanager
async def native_creation(connection, opportunity_id):
    """Internal creation path only; never expose the marker in HTTP contracts.

    The DB trigger consumes this exact UUID once and rejects import/backdated
    origin claims. The caller still owns rollback if INSERT or cleanup fails.
    """
    if not connection.is_in_transaction():
        raise RuntimeError("native opportunity creation requires the caller transaction")
    opportunity_id = str(UUID(str(opportunity_id)))
    previous = await connection.fetchval("SELECT current_setting('app.native_opportunity_create',true)")
    await connection.execute("SELECT set_config('app.native_opportunity_create',$1,true)", opportunity_id)
    try:
        yield
    except BaseException as primary:
        try:
            await connection.execute("SELECT set_config('app.native_opportunity_create',$1,true)", previous or "")
        except BaseException:
            # An aborted SQL transaction cannot restore a GUC until rollback.
            # Keep the actual insert error; never turn a failed write into success.
            primary.add_note("Native creation marker restoration failed; caller transaction must roll back.")
        raise
    else:
        await connection.execute("SELECT set_config('app.native_opportunity_create',$1,true)", previous or "")
