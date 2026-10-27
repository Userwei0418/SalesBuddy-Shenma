# ruff: noqa: S608 -- Only the fixed creation_date_projection() fragment forms SQL; values are bound.
"""Meeting source reads through the caller's actor/RLS repeatable-read transaction."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from sales_backend.db import json_value
from sales_backend.domain.work_report import WorkReportError
from sales_backend.repositories.opportunity_business_date import business_created_on, creation_date_projection


async def assert_dependencies(connection, dependencies, *, permission="work_report.read"):
    if permission not in {
        "work_report.read",
        "work_report.write",
        "work_report.share",
        "agent.work_report",
        "report.export",
    }:
        raise ValueError("invalid report permission")
    if not await connection.fetchval("SELECT security.work_report_source_access($1::jsonb,'[]'::jsonb)", dependencies):
        raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "会议报告部分来源当前不可访问", status=403)
    # A report can cite many state/forecast events for the same opportunity.
    # Keep the complete provenance check above, then check each action target
    # once per resource in fresh caller SQL; never cache across invocations.
    targets = {"customer": set(), "opportunity": set(), "member": set()}
    for source in dependencies:
        kind = source["source_type"]
        if kind in {"meeting_claim", "meeting_customer"}:
            targets["customer"].add(str(source["customer_id"] if kind == "meeting_claim" else source["record_id"]))
        elif kind in {"meeting_state", "meeting_forecast", "opportunity"}:
            targets["opportunity"].add(str(source["record_id"] if kind == "opportunity" else source["opportunity_id"]))
        elif kind == "meeting_member":
            targets["member"].add(str(source["record_id"]))
    predicates = {
        "customer": "security.authorization_customer($1,ident)",
        "opportunity": "security.authorization_opportunity($1,ident)",
        "member": "security.authorization_subject($1,'person',ident,NULL)",
    }
    for resource, ids in targets.items():
        if not ids:
            continue
        allowed = await connection.fetchval(
            "SELECT bool_and(COALESCE(" + predicates[resource] + ",false)) FROM unnest($2::uuid[]) AS ident",
            permission, sorted(ids),
        )
        if not allowed:
            raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "会议报告操作范围未覆盖原始来源", status=403)


def rows(values):
    result = []
    for row in values:
        value = dict(row)
        for key in ("events", "forecasts", "forecast_events", "creation_date", "member_ids"):
            if key in value:
                value[key] = json_value(value[key])
        result.append(value)
    return result


async def _meeting_schema_applied_at(connection):
    # V383 is Shenma's mapped V235. Preserve the first real feature timestamp,
    # including an existing source ledger when both representations are present.
    since = await connection.fetchval(
        "SELECT min(applied_at) FROM ops.schema_migration WHERE version IN ('V235','V383')"
    )
    if since is None:
        raise WorkReportError("WORK_REPORT_INVALID_INPUT", "会议事实数据库版本尚未就绪", status=409)
    return since


async def collect(connection, actor, *, scope, period, read_only=False, allow_partial_sources=False):
    # Only the read-only Review projection can opt into partial facts. Report
    # generation and all existing callers retain the complete-source gate.
    if allow_partial_sources and not read_only:
        raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "部分来源取材仅允许只读概览", status=403)
    check = await connection.fetchrow(
        "SELECT current_setting('transaction_isolation') AS isolation,"
        "current_setting('row_security') AS rls,common.current_workspace_id()::text AS workspace_id,"
        "common.current_user_ref_id()::text AS user_id"
    )
    if not check or dict(check) != dict(
        isolation="repeatable read", rls="on", workspace_id=actor.workspace_id, user_id=actor.user_id
    ):
        raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "会议取材需要当前用户的可重复读事务", status=403)
    if read_only and await connection.fetchval("SELECT current_setting('transaction_read_only')") != 'on':
        raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "概览取材必须为只读事务", status=403)
    for permission in (
        "work_report.read",
        *(() if read_only else ("work_report.write",)),
        *(() if allow_partial_sources else ("customer.read", "opportunity.read", "visit.read", "task.read")),
    ):
        if not await connection.fetchval("SELECT security.authorization_has($1)", permission):
            raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "当前缺少会议事实来源权限", status=403)
    since = await _meeting_schema_applied_at(connection)
    kind, uid, team = scope.kind, actor.user_id, str(scope.team_id) if scope.team_id else None
    if read_only and kind == 'self':
        uid = str(scope.member_ids[0])
    require_write = not read_only
    people = rows(
        await connection.fetch(
            """SELECT u.id::text,u.display_name AS name,
      CASE WHEN u.id=ANY($5::uuid[]) THEN 'current' ELSE 'period_only' END AS membership_state
      FROM platform.user_ref u WHERE u.workspace_id=common.current_workspace_id()
       AND security.authorization_subject('work_report.read','person',u.id,NULL)
       AND (NOT $7::boolean OR security.authorization_subject('work_report.write','person',u.id,NULL))
       AND (u.id=ANY($5::uuid[]) OR ($1='self' AND u.id=$2::uuid) OR ($1='managed_team' AND EXISTS(
         SELECT 1 FROM platform.team_membership m WHERE m.workspace_id=u.workspace_id AND m.user_ref_id=u.id
          AND m.team_id=$3::uuid AND m.valid_from<$6 AND m.valid_to>$4))) ORDER BY u.id""",
            kind,
            uid,
            team,
            period.start,
            [str(i) for i in scope.member_ids],
            period.cutoff,
            require_write,
        )
    )
    opportunities = rows(
        await connection.fetch(
            """SELECT o.id::text,o.workspace_id::text,o.version_no,o.name,
      o.customer_id::text,c.name AS customer_name,o.amount::text,o.currency,o.status,
      o.owner_user_ref_id::text AS owner_id,o.owner_team_id::text AS team_id,
      """
            + creation_date_projection()
            + """ AS creation_date,
      COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.after_version) FROM crm.opportunity_state_event e
       WHERE e.workspace_id=o.workspace_id AND e.opportunity_id=o.id),'[]'::jsonb) AS events,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('year',f.year,'quarter',f.quarter,
         'recognized_amount',f.recognized_amount::text,'collection_amount',f.collection_amount::text))
       FROM crm.opportunity_forecast f WHERE f.workspace_id=o.workspace_id
        AND f.opportunity_id=o.id),'[]'::jsonb) AS forecasts,
      COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.event_no) FROM crm.opportunity_forecast_event e
       WHERE e.workspace_id=o.workspace_id AND e.opportunity_id=o.id),'[]'::jsonb) AS forecast_events
      FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
      WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL AND c.deleted_at IS NULL
       AND security.authorization_opportunity('opportunity.read',o.id)
       AND security.authorization_opportunity('work_report.read',o.id)
       AND (NOT $3::boolean OR security.authorization_opportunity('work_report.write',o.id))
       AND (o.owner_user_ref_id=$1::uuid OR ($2::uuid IS NOT NULL AND o.owner_team_id=$2::uuid)
         OR EXISTS(SELECT 1 FROM crm.opportunity_state_event e WHERE e.workspace_id=o.workspace_id
           AND e.opportunity_id=o.id AND e.meeting_state_captured AND (
            ($2::uuid IS NULL AND (e.meeting_before->>'owner_id'=$1::text OR e.meeting_after->>'owner_id'=$1::text))
            OR ($2::uuid IS NOT NULL AND (e.meeting_before->>'team_id'=$2::text
              OR e.meeting_after->>'team_id'=$2::text)))))
      ORDER BY o.id""",
            uid,
            team,
            require_write,
        )
    )
    for opportunity in opportunities:
        business_day = business_created_on(opportunity.pop("creation_date"))
        opportunity["business_created_at"] = (
            datetime.combine(business_day, time(), ZoneInfo("Asia/Shanghai")) if business_day else None
        )
        for event in [*opportunity["events"], *opportunity["forecast_events"]]:
            for key in ("occurred_at", "recorded_at", "trustworthy_from", "opportunity_created_at"):
                if isinstance(event.get(key), str):
                    event[key] = datetime.fromisoformat(event[key])
        native = next(
            (
                e
                for e in opportunity["events"]
                if e["mutation_kind"] == "insert"
                and e["history_origin"] == "native_creation"
                and e["complete_from_origin"]
            ),
            None,
        )
        if native:
            opportunity["business_created_at"] = native["opportunity_created_at"]
    claims = rows(
        await connection.fetch(
            """SELECT e.id::text,e.customer_id::text,c.name,c.name AS customer_name,
      e.owner_user_ref_id::text AS owner_id,e.occurred_at,e.reason AS summary
      FROM crm.customer_ownership_event e JOIN crm.customer c ON c.id=e.customer_id AND c.workspace_id=e.workspace_id
      JOIN crm.customer_claim_request r ON r.id=e.claim_request_id AND r.workspace_id=e.workspace_id
      WHERE e.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL AND e.event_type='approved'
       AND r.status='approved' AND r.customer_id=e.customer_id AND r.applicant_user_ref_id=e.owner_user_ref_id
       AND e.occurred_at>=$4 AND e.occurred_at<$5
       AND security.authorization_customer('customer.read',c.id)
       AND security.authorization_customer('work_report.read',c.id)
       AND (NOT $6::boolean OR security.authorization_customer('work_report.write',c.id))
       AND (($1='self' AND e.owner_user_ref_id=$2::uuid) OR ($1='managed_team' AND EXISTS(
        SELECT 1 FROM platform.team_membership m WHERE m.workspace_id=e.workspace_id
         AND m.user_ref_id=e.owner_user_ref_id
         AND m.team_id=$3::uuid AND e.occurred_at>=m.valid_from AND e.occurred_at<m.valid_to)))
      ORDER BY e.occurred_at,e.id""",
            kind,
            uid,
            team,
            period.start,
            period.cutoff,
            require_write,
        )
    )
    visits = rows(
        await connection.fetch(
            """SELECT v.id::text,v.version_no,v.customer_id::text,c.name AS customer_name,
      '跟进记录'::text AS name,v.opportunity_id::text,o.name AS opportunity_name,
      v.interaction_at,v.follow_up_record AS summary,v.next_action,
      ARRAY(SELECT DISTINCT id::text FROM (SELECT v.recorder_user_ref_id AS id UNION
        SELECT p.user_ref_id FROM activity.visit_participant p WHERE p.workspace_id=v.workspace_id
         AND p.visit_id=v.id) people) AS member_ids
      FROM activity.visit v JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
      LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
      WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
       AND v.status IN ('confirmed','archived') AND v.interaction_at>=$4 AND v.interaction_at<$5
       AND security.authorization_visit('visit.read',v.id) AND security.authorization_visit('work_report.read',v.id)
       AND (NOT $6::boolean OR security.authorization_visit('work_report.write',v.id))
       AND (v.opportunity_id IS NULL OR (o.deleted_at IS NULL
           AND security.authorization_opportunity('opportunity.read',o.id)
           AND security.authorization_opportunity('work_report.read',o.id)))
       AND (($1='self' AND (v.recorder_user_ref_id=$2::uuid OR EXISTS(SELECT 1 FROM activity.visit_participant p
         WHERE p.workspace_id=v.workspace_id AND p.visit_id=v.id AND p.user_ref_id=$2::uuid)))
        OR ($1='managed_team' AND (v.recorder_team_id=$3::uuid OR EXISTS(SELECT 1 FROM activity.visit_participant p
         WHERE p.workspace_id=v.workspace_id AND p.visit_id=v.id AND p.team_id_at_event=$3::uuid))))
      ORDER BY v.interaction_at,v.id""",
            kind,
            uid,
            team,
            period.start,
            period.cutoff,
            require_write,
        )
    )
    tasks = rows(
        await connection.fetch(
            """SELECT t.id::text,t.version_no,t.title,t.due_at,t.opportunity_id::text,
      ARRAY(SELECT a.assignee_user_ref_id::text FROM workflow.task_assignee a
       WHERE a.workspace_id=t.workspace_id AND a.task_id=t.id AND a.responsibility='owner') AS member_ids
      FROM workflow.task t WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
       AND t.status IN ('pending_confirm','pending_execution','in_progress','deferred','pending_review','needs_adjustment')
       AND security.authorization_task('task.read',t.id) AND security.authorization_task('work_report.read',t.id)
       AND (NOT $4::boolean OR security.authorization_task('work_report.write',t.id))
       AND (t.opportunity_id IS NULL OR (security.authorization_opportunity('opportunity.read',t.opportunity_id)
         AND security.authorization_opportunity('work_report.read',t.opportunity_id)))
       AND EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.workspace_id=t.workspace_id AND a.task_id=t.id
         AND a.responsibility='owner' AND (($1='self' AND a.assignee_user_ref_id=$2::uuid)
           OR ($1='managed_team' AND a.assignee_team_id=$3::uuid))) ORDER BY t.id""",
            kind,
            uid,
            team,
            require_write,
        )
    )
    return dict(
        history_since=since, members=people, opportunities=opportunities, claims=claims, visits=visits, tasks=tasks
    )
