# ruff: noqa: S608 -- Only the fixed creation_date_projection() fragment forms SQL; values are bound.
"""Current-authority Review rows. Every source intersects its own read grant."""

from datetime import datetime

from sales_backend.db import json_value
from sales_backend.domain.review_changes import (
    audited_opportunity_changes,
    history_coverage,
    merge_update_history,
    opportunity_changes,
)
from sales_backend.domain.work_report import WorkReportError
from sales_backend.repositories import review_history
from sales_backend.repositories.opportunity_business_date import business_created_on, creation_date_projection


async def assert_read_context(connection, actor):
    row = await connection.fetchrow(
        "SELECT current_setting('transaction_isolation') AS isolation,"
        "current_setting('row_security') AS rls,common.current_workspace_id()::text AS workspace_id,"
        "common.current_user_ref_id()::text AS user_id,current_setting('transaction_read_only') AS readonly"
    )
    if not row or dict(row) != dict(
        isolation="repeatable read", rls="on", workspace_id=actor.workspace_id, user_id=actor.user_id, readonly="on"
    ):
        raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "Review读取需要当前用户的只读可重复读事务", status=403)


async def opportunity_rows(connection, actor, scope, *, year, quarter):
    # Forecast LEFT JOIN and one row per opportunity are essential: missing is
    # not zero and joining task/member/risk tables must never multiply money.
    # Authorize each relevant visit once, then reuse it for direct and linked
    # opportunities. Both source tables retain RLS and both visit read grants;
    # MAX collapses duplicated links without multiplying opportunity money.
    records = await connection.fetch(
        """/* review_current_opportunities_v1 */
      WITH scoped_opportunities AS MATERIALIZED (
        SELECT o.id,o.workspace_id,o.version_no,o.name,o.customer_id,c.name AS customer_name,
          o.owner_user_ref_id,o.owner_team_id,o.status,o.stage_code,o.probability,o.amount,o.currency,o.updated_at,
          """
        + creation_date_projection()
        + """ AS creation_date
        FROM crm.opportunity o JOIN crm.customer c ON c.workspace_id=o.workspace_id AND c.id=o.customer_id
        WHERE o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL AND c.deleted_at IS NULL
          AND o.status IN ('open','won')
          AND security.authorization_opportunity('opportunity.read',o.id)
          AND security.authorization_opportunity('work_report.read',o.id)
          AND (($1='team' AND o.owner_team_id=$3::uuid)
            OR ($1 IN ('self','member') AND o.owner_user_ref_id=ANY($2::uuid[])))
      ), scoped_links AS MATERIALIZED (
        SELECT linked.opportunity_id,linked.visit_id
        FROM activity.visit_opportunity linked
        WHERE linked.workspace_id=common.current_workspace_id()
          AND linked.opportunity_id IN (SELECT id FROM scoped_opportunities)
      ), visit_ids AS MATERIALIZED (
        SELECT v.id FROM activity.visit v
        WHERE v.workspace_id=common.current_workspace_id()
          AND v.opportunity_id IN (SELECT id FROM scoped_opportunities)
          AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
        UNION
        SELECT visit_id FROM scoped_links
      ), authorized_visits AS MATERIALIZED (
        SELECT v.id,v.opportunity_id,v.interaction_at
        FROM visit_ids selected JOIN activity.visit v ON v.id=selected.id
        WHERE v.workspace_id=common.current_workspace_id()
          AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
          AND security.authorization_visit('visit.read',v.id)
          AND security.authorization_visit('work_report.read',v.id)
      ), latest_follow_up AS (
        SELECT follow_up.opportunity_id,max(follow_up.interaction_at) AS last_follow_up_at
        FROM (
          SELECT opportunity_id,interaction_at FROM authorized_visits
          UNION ALL
          SELECT linked.opportunity_id,v.interaction_at FROM scoped_links linked
          JOIN authorized_visits v ON v.id=linked.visit_id
        ) follow_up GROUP BY follow_up.opportunity_id
      )
      SELECT o.id::text,o.version_no,o.name,o.customer_id::text,o.customer_name,
        o.owner_user_ref_id::text AS owner_id,
        CASE WHEN security.authorization_subject('work_report.read','person',owner.id,NULL)
          THEN owner.display_name END AS owner_name,o.owner_team_id::text AS team_id,
        o.status,o.stage_code,o.probability::text,o.amount::text,o.currency,o.updated_at,
        o.creation_date,
        f.id::text AS forecast_id,f.updated_at AS forecast_updated_at,f.collection_amount::text,
        f.recognized_amount::text,f.collection_confidence,
        (SELECT min(e.opportunity_created_at) FROM crm.opportunity_state_event e
          WHERE e.workspace_id=o.workspace_id AND e.opportunity_id=o.id AND e.mutation_kind='insert'
            AND e.history_origin='native_creation' AND e.complete_from_origin) AS native_created_at,
        follow_up.last_follow_up_at,
        risk.risk_id,risk.risk_title,risk.risk_description,risk.risk_severity,
        (risk.risk_id IS NOT NULL) AS high_risk,
        security.authorization_visit_target('visit.create',o.customer_id,o.id,$4::uuid,$5::uuid) AS can_follow_up,
        security.authorization_opportunity('task.create_customer',o.id) AS can_create_task
      FROM scoped_opportunities o
      LEFT JOIN latest_follow_up follow_up ON follow_up.opportunity_id=o.id
      LEFT JOIN platform.user_ref owner ON owner.workspace_id=o.workspace_id AND owner.id=o.owner_user_ref_id
      LEFT JOIN crm.opportunity_forecast f ON f.workspace_id=o.workspace_id AND f.opportunity_id=o.id
        AND f.year=$6 AND f.quarter=$7
      LEFT JOIN LATERAL (
        SELECT r.id::text AS risk_id,r.title AS risk_title,left(r.description,600) AS risk_description,
          r.severity_code AS risk_severity
        FROM insight.risk r
        WHERE r.workspace_id=o.workspace_id AND r.opportunity_id=o.id
          AND r.deleted_at IS NULL AND r.status<>'resolved' AND r.severity_code IN ('critical','high')
          AND security.authorization_risk('risk.read',r.id)
          AND security.authorization_risk('work_report.read',r.id)
        ORDER BY CASE r.severity_code WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,
          r.opened_at DESC NULLS LAST,r.id DESC LIMIT 1
      ) risk ON true
      ORDER BY o.id""",
        scope.kind,
        [str(i) for i in scope.member_ids],
        str(scope.team_id) if scope.team_id else None,
        actor.user_id,
        actor.team_ids[0] if actor.team_ids else None,
        year,
        quarter,
    )
    result = []
    for record in records:
        row = dict(record)
        row["business_created_on"] = business_created_on(json_value(row.pop("creation_date")))
        result.append(row)
    return result


async def targets(connection, scope, *, start):
    typ = "team" if scope.kind == "team" else "person"
    person = str(scope.member_ids[0]) if typ == "person" else None
    team = str(scope.team_id) if typ == "team" else None
    allowed = await connection.fetchval(
        "SELECT security.authorization_subject('target.read',$1,$2::uuid,$3::uuid)"
        " AND security.target_scope_read($1,$2::uuid,$3::uuid,'sales')",
        typ,
        person,
        team,
    )
    if not allowed:
        return {}, False
    records = await connection.fetch(
        """SELECT id::text,kind,
        CASE WHEN (COALESCE(source,'') LIKE '%Q4目标表 revision54 导入%'
                   OR COALESCE(change_reason,'') LIKE '%Q4目标表 revision54 导入%')
             THEN (amount*10000)::text ELSE amount::text END AS value,
        version_no, change_reason AS note
      FROM crm.sales_target WHERE workspace_id=common.current_workspace_id() AND period_type='quarter'
        AND period_start=$1 AND scope_type=$2 AND user_ref_id IS NOT DISTINCT FROM $3::uuid
        AND team_id IS NOT DISTINCT FROM $4::uuid AND department_code='sales'
        AND kind IN ('collection','collection_ppl','forecast_recognized')""",
        start,
        typ,
        person,
        team,
    )
    return {row["kind"]: dict(row) for row in records}, True


async def updated_opportunities(connection, scope, *, period):
    captured = await review_history.update_activity(connection, scope, period=period)
    items = audited_opportunity_changes(captured["items"])
    coverage = captured["history_coverage"]
    started = datetime.fromisoformat(coverage["complete_from"]) if coverage.get("complete_from") else None
    if started is not None and period.start >= started:
        if items:
            coverage = {**coverage, "count_status": "complete",
                        "count_message": "按已记录的确定性商机变动去重计数；字段和操作人详情按可见证据展示"}
        return items, coverage
    legacy_period = period.model_copy(update={"cutoff": min(period.cutoff, started)}) if started else period
    legacy, _ = await _legacy_updated_opportunities(connection, scope, period=legacy_period)
    items = merge_update_history(legacy, items)
    coverage = {**coverage, "status": "partial" if items else "unavailable"}
    # A retained business/state/forecast event is a deterministic committed
    # action even when older rows cannot prove every field or actor.  Expose a
    # separate count basis so the Review card can count these de-duplicated
    # actions without pretending the historical detail projection is complete.
    deterministic = {
        "business_change", "feishu_notification", "opportunity_state", "opportunity_forecast", "opportunity_audit"
    }
    if items and all(
        item.get("events") and all(event.get("kind") in deterministic for event in item["events"])
        for item in items
    ):
        coverage["count_status"] = "complete"
        coverage["count_message"] = "按已记录的确定性商机变动去重计数；字段和操作人详情按可见证据展示"
    return items, coverage


async def _legacy_updated_opportunities(connection, scope, *, period):
    records = await connection.fetch(
        """/* review_updated_opportunities_v1 */
      WITH updates AS (
        SELECT b.opportunity_id,'business_change:'||b.id::text AS event_id,b.created_at AS occurred_at,
          'business_change'::text AS source,b.actor_user_ref_id::text AS actor_id,
          b.changes AS changes,b.version_no AS version_no
        FROM crm.business_change b WHERE b.workspace_id=common.current_workspace_id()
          AND b.opportunity_id IS NOT NULL AND b.version_no>1 AND jsonb_array_length(b.changes)>0
          AND b.created_at>=$4 AND b.created_at<$5
          AND NOT EXISTS(SELECT 1 FROM crm.opportunity_state_event origin
            WHERE origin.workspace_id=b.workspace_id AND origin.opportunity_id=b.opportunity_id
              AND origin.mutation_kind='insert' AND origin.after_version=b.version_no)
        UNION ALL
        SELECT e.opportunity_id,'opportunity_state:'||e.id::text,e.occurred_at,'opportunity_state',NULL,
          jsonb_build_object('before',jsonb_build_object('state',e.meeting_before,
            'probability',e.before_probability,'stage',e.before_stage,'status',e.before_status),
            'after',jsonb_build_object('state',e.meeting_after,'probability',e.after_probability,'stage',e.after_stage,'status',e.after_status)),
          e.after_version
        FROM crm.opportunity_state_event e WHERE e.workspace_id=common.current_workspace_id()
          AND e.mutation_kind='update' AND e.source_kind<>'historical_import'
          AND e.occurred_at>=$4 AND e.occurred_at<$5
          AND (e.before_probability,e.before_status,e.before_stage,e.meeting_before)
            IS DISTINCT FROM (e.after_probability,e.after_status,e.after_stage,e.meeting_after)
        UNION ALL
        SELECT e.opportunity_id,'opportunity_forecast:'||e.event_no::text,e.recorded_at,'opportunity_forecast',NULL,
          jsonb_build_object('before',e.before_state,'after',e.after_state,'year',e.year,'quarter',e.quarter),NULL
        FROM crm.opportunity_forecast_event e WHERE e.workspace_id=common.current_workspace_id()
          AND e.recorded_at>=$4 AND e.recorded_at<$5
          AND e.before_state IS DISTINCT FROM e.after_state
          AND (e.mutation_kind<>'INSERT' OR EXISTS(SELECT 1 FROM crm.opportunity_state_event origin
            WHERE origin.workspace_id=e.workspace_id AND origin.opportunity_id=e.opportunity_id
              AND origin.mutation_kind='update' AND origin.occurred_at<=e.recorded_at))
      ) SELECT o.id::text,o.id::text AS opportunity_id,o.name,o.customer_id::text,c.name AS customer_name,
        o.owner_user_ref_id::text AS owner_id,o.version_no,max(e.occurred_at) AS occurred_at,count(*) AS event_count,
        jsonb_agg(jsonb_build_object('kind',e.source,'id',e.event_id,'occurred_at',e.occurred_at,
          'actor_id',changing_actor.id,'actor_display_name',changing_actor.display_name,
          'actor_status',CASE WHEN e.actor_id IS NULL THEN 'not_recorded'
            WHEN changing_actor.id IS NULL THEN 'restricted' ELSE 'available' END,
          'actor_name_basis',CASE WHEN changing_actor.id IS NOT NULL THEN 'current_authorized_directory' END,
          'changes',e.changes,'version_no',e.version_no)
          ORDER BY e.occurred_at,e.event_id) AS events
      FROM updates e JOIN crm.opportunity o ON o.id=e.opportunity_id AND o.workspace_id=common.current_workspace_id()
      JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
      LEFT JOIN platform.user_ref changing_actor ON changing_actor.id=e.actor_id::uuid
        AND changing_actor.workspace_id=o.workspace_id AND changing_actor.deleted_at IS NULL
        AND security.authorization_subject('work_report.read','person',changing_actor.id,NULL)
      WHERE o.deleted_at IS NULL AND c.deleted_at IS NULL
        AND security.authorization_opportunity('opportunity.read',o.id)
        AND security.authorization_opportunity('work_report.read',o.id)
        AND (($1='team' AND o.owner_team_id=$3::uuid)
          OR ($1 IN ('self','member') AND o.owner_user_ref_id=ANY($2::uuid[])))
      GROUP BY o.id,c.id ORDER BY max(e.occurred_at) DESC,o.id""",
        scope.kind,
        [str(i) for i in scope.member_ids],
        str(scope.team_id) if scope.team_id else None,
        period.start,
        period.cutoff,
    )
    items = opportunity_changes([{**dict(row), "events": json_value(row["events"])} for row in records])
    return items, history_coverage(has_records=bool(items))


async def pending_tasks(connection, scope, *, now):
    records = await connection.fetch(
        """/* review_pending_tasks_v1 */
      SELECT t.id::text,t.execution_mode,t.id::text AS task_id,t.version_no,t.title AS name,t.status,t.due_at,t.created_at,
        workflow.task_execution_summary(t.id) AS execution,
        CASE WHEN security.authorization_customer('customer.read',c.id)
          AND security.authorization_customer('work_report.read',c.id) THEN c.id::text END AS customer_id,
        CASE WHEN security.authorization_customer('customer.read',c.id)
          AND security.authorization_customer('work_report.read',c.id) THEN c.name END AS customer_name,
        CASE WHEN security.authorization_opportunity('opportunity.read',o.id)
          AND security.authorization_opportunity('work_report.read',o.id) THEN o.id::text END AS opportunity_id,
        CASE WHEN security.authorization_opportunity('opportunity.read',o.id)
          AND security.authorization_opportunity('work_report.read',o.id) THEN o.name END AS opportunity_name,
        ARRAY(SELECT DISTINCT a.assignee_user_ref_id::text FROM workflow.task_assignee a
          WHERE a.workspace_id=t.workspace_id AND a.task_id=t.id AND a.responsibility='owner'
            AND security.authorization_subject('work_report.read','person',a.assignee_user_ref_id,NULL)
            AND (($1='team' AND a.assignee_team_id=$3::uuid)
              OR ($1 IN ('self','member') AND a.assignee_user_ref_id=ANY($2::uuid[])))) AS member_ids
      FROM workflow.task t LEFT JOIN crm.customer c ON c.id=t.customer_id AND c.workspace_id=t.workspace_id
        AND c.deleted_at IS NULL
      LEFT JOIN crm.opportunity o ON o.id=t.opportunity_id AND o.workspace_id=t.workspace_id AND o.deleted_at IS NULL
      WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL AND t.created_at<=$4
        AND t.status IN ('pending_confirm','pending_execution','in_progress','deferred','pending_review','needs_adjustment')
        AND security.authorization_task('task.read',t.id) AND security.authorization_task('work_report.read',t.id)
        AND EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.workspace_id=t.workspace_id AND a.task_id=t.id
          AND a.responsibility='owner'
          AND security.authorization_subject('work_report.read','person',a.assignee_user_ref_id,NULL)
          AND (($1='team' AND a.assignee_team_id=$3::uuid)
            OR ($1 IN ('self','member') AND a.assignee_user_ref_id=ANY($2::uuid[]))))
      ORDER BY t.due_at NULLS LAST,t.id""",
        scope.kind,
        [str(i) for i in scope.member_ids],
        str(scope.team_id) if scope.team_id else None,
        now,
    )
    return [dict(row) for row in records]


async def formal_visits(connection, scope, *, period):
    """One visit per action, including explicit many-opportunity links only.

    Resolve the small explicit link set before checking each opportunity. Scanning
    every tenant opportunity for every visit repeats its RLS/grant predicates.
    UNION deduplicates a direct opportunity also present in the link table; both
    opportunity grants, tenant guards and table RLS still filter the final join.
    """
    records = await connection.fetch(
        """SELECT v.id::text,v.version_no,v.customer_id::text,c.name AS customer_name,
      '跟进记录'::text AS name,v.follow_up_record AS summary,v.next_action,v.interaction_at AS occurred_at,
      v.interaction_at,v.created_at AS recorded_at,v.confirmed_at,v.interaction_mode_code,
      CASE WHEN security.authorization_opportunity('opportunity.read',o.id)
        AND security.authorization_opportunity('work_report.read',o.id) THEN o.id::text END AS opportunity_id,
      CASE WHEN security.authorization_opportunity('opportunity.read',o.id)
        AND security.authorization_opportunity('work_report.read',o.id) THEN o.name END AS opportunity_name,
      ARRAY(SELECT DISTINCT people.id::text FROM (SELECT v.recorder_user_ref_id AS id UNION
        SELECT p.user_ref_id FROM activity.visit_participant p
          WHERE p.workspace_id=v.workspace_id AND p.visit_id=v.id) people
        WHERE security.authorization_subject('work_report.read','person',people.id,NULL)) AS member_ids,
      COALESCE((SELECT jsonb_agg(jsonb_build_object('id',linked.id,'name',linked.name) ORDER BY linked.id)
        FROM (SELECT v.opportunity_id AS id UNION
          SELECT vo.opportunity_id FROM activity.visit_opportunity vo
            WHERE vo.workspace_id=v.workspace_id AND vo.visit_id=v.id) explicit_links
        JOIN crm.opportunity linked ON linked.id=explicit_links.id AND linked.workspace_id=v.workspace_id
        WHERE linked.deleted_at IS NULL
        AND security.authorization_opportunity('opportunity.read',linked.id)
        AND security.authorization_opportunity('work_report.read',linked.id)),'[]'::jsonb) AS opportunities
      FROM activity.visit v JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
      LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id AND o.deleted_at IS NULL
      WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND c.deleted_at IS NULL
        AND v.status IN ('confirmed','archived') AND v.interaction_at>=$4 AND v.interaction_at<$5
        AND security.authorization_visit('visit.read',v.id) AND security.authorization_visit('work_report.read',v.id)
        AND security.authorization_customer('customer.read',c.id)
        AND security.authorization_customer('work_report.read',c.id)
        AND (($1='team' AND (v.recorder_team_id=$3::uuid OR EXISTS(SELECT 1 FROM activity.visit_participant p
          WHERE p.workspace_id=v.workspace_id AND p.visit_id=v.id AND p.team_id_at_event=$3::uuid)))
          OR ($1 IN ('self','member') AND (v.recorder_user_ref_id=ANY($2::uuid[])
            OR EXISTS(SELECT 1 FROM activity.visit_participant p WHERE p.workspace_id=v.workspace_id
              AND p.visit_id=v.id AND p.user_ref_id=ANY($2::uuid[])))))
      ORDER BY v.interaction_at DESC,v.id""",
        scope.kind,
        [str(i) for i in scope.member_ids],
        str(scope.team_id) if scope.team_id else None,
        period.start,
        period.cutoff,
    )
    return [
        {
            **dict(r),
            "opportunities": json_value(r["opportunities"]),
            "source_refs": [{"kind": "visit", "id": r["id"], "version": r["version_no"]}],
        }
        for r in records
    ]


async def activity_extras(connection, groups, *, period):
    customer_ids = [r["customer_id"] for r in groups["new_claimed_customers"]]
    if customer_ids:
        rows = await connection.fetch(
            """SELECT c.id::text,c.source_code FROM crm.customer c
          WHERE c.workspace_id=common.current_workspace_id() AND c.id=ANY($1::uuid[]) AND c.deleted_at IS NULL
            AND security.authorization_customer('customer.read',c.id)
            AND security.authorization_customer('work_report.read',c.id)""",
            customer_ids,
        )
        by_id = {r["id"]: r for r in rows}
        for item in groups["new_claimed_customers"]:
            item["source_code"] = by_id.get(item["customer_id"], {}).get("source_code")
            item["source_basis"] = "current_customer_source"
    ids = [r["opportunity_id"] for key in ("new_opportunities", "lost_opportunities") for r in groups[key]]
    if ids:
        rows = await connection.fetch(
            """SELECT e.opportunity_id::text,e.id::text,e.occurred_at,e.mutation_kind,
          e.before_status,e.after_status,e.after_probability::text,e.after_stage,e.meeting_after
          FROM crm.opportunity_state_event e WHERE e.workspace_id=common.current_workspace_id()
            AND e.opportunity_id=ANY($1::uuid[]) AND e.occurred_at>=$2 AND e.occurred_at<$3
            AND e.source_kind<>'historical_import' AND e.meeting_state_captured
            AND security.authorization_opportunity('opportunity.read',e.opportunity_id)
            AND security.authorization_opportunity('work_report.read',e.opportunity_id)
          ORDER BY e.occurred_at,e.id""",
            ids,
            period.start,
            period.cutoff,
        )
        for key in ("new_opportunities", "lost_opportunities"):
            for item in groups[key]:
                events = [
                    r
                    for r in rows
                    if r["opportunity_id"] == item["opportunity_id"]
                    and (
                        r["mutation_kind"] == "insert"
                        if key == "new_opportunities"
                        else r["mutation_kind"] == "update"
                        and r["before_status"] != "lost"
                        and r["after_status"] == "lost"
                    )
                ]
                item["business_events"] = [{**dict(r), "meeting_after": json_value(r["meeting_after"])} for r in events]
                image = json_value(events[-1]["meeting_after"]) if events else {}
                item.update(
                    amount=image.get("amount"),
                    currency=image.get("currency"),
                    owner_id=image.get("owner_id"),
                    stage_code=events[-1]["after_stage"] if events else None,
                    probability=events[-1]["after_probability"] if events else None,
                    field_basis="event_snapshot",
                )
                if key == "lost_opportunities":
                    item["lost_reason"] = None
                    item["lost_reason_status"] = "not_recorded"
            if key == "lost_opportunities":
                groups[key] = [r for r in groups[key] if r["business_events"]]
    task_ids = [r["task_id"] for key in ("completed_tasks", "pending_tasks") for r in groups[key]]
    if task_ids:
        rows = await connection.fetch(
            """SELECT t.id::text,t.execution_mode,t.due_at,t.completion_note,
          CASE WHEN security.authorization_subject('work_report.read','person',u.id,NULL)
            THEN u.id::text END AS creator_id,
          CASE WHEN security.authorization_subject('work_report.read','person',u.id,NULL)
            THEN u.display_name END AS creator_name
          FROM workflow.task t LEFT JOIN platform.user_ref u
            ON u.id=t.creator_user_ref_id AND u.workspace_id=t.workspace_id
          WHERE t.workspace_id=common.current_workspace_id() AND t.id=ANY($1::uuid[]) AND t.deleted_at IS NULL
            AND security.authorization_task('task.read',t.id)
            AND security.authorization_task('work_report.read',t.id)""",
            task_ids,
        )
        by_id = {r["id"]: dict(r) for r in rows}
        for key in ("completed_tasks", "pending_tasks"):
            for item in groups[key]:
                item.update(by_id.get(item["task_id"], {}), detail_basis="current_task_metadata")
