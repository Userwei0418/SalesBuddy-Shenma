from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sales_backend.db import Database, json_value
from sales_backend.domain.agent import RoleCode
from sales_backend.domain.business_time import BUSINESS_TIMEZONE, business_datetime, localize_business_times
from sales_backend.domain.follow_up_schedule import deadline_context
from sales_backend.repositories.attribute_overlay import overlay_risk_attributes
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.services.agent_run.models import RunInput


class AgentFactsLoader:
    """按运行模式读取该次运行需要的业务事实，只读事务，受 RLS 约束。"""

    def __init__(self, database: Database):
        self.database = database

    database: Database

    async def load(self, run: RunInput, *, today_tasks_scope=None) -> dict[str, Any]:
        # Every Agent mode sees the same business calendar. Keep the actual
        # instants unchanged, including operating reports and risk analyses.
        facts = await self._load(run, today_tasks_scope=today_tasks_scope)
        if run.mode == "visit_entry":
            # Human-reviewed strings are immutable. In particular interaction_at
            # is a date input, not a timestamp to expand inside the quality payload.
            facts["data_as_of"] = business_datetime(facts["data_as_of"]).isoformat()
            return facts
        return localize_business_times(facts)

    async def _load(self, run: RunInput, *, today_tasks_scope=None) -> dict[str, Any]:
        async with self.database.transaction(run.actor, readonly=True) as connection:
            from sales_backend.services.agent_access import require_agent_access
            from sales_backend.domain.route_permissions import AGENT_PERMISSIONS

            await require_agent_access(connection,run.actor,run.mode,run.customer_id,
                opportunity_id=run.opportunity_id,permission_version=run.permission_version)
            await connection.execute("SELECT set_config('app.authorized_feature',$1,true)",
                'profile.fde_read' if run.surface=='fde_profile' else AGENT_PERMISSIONS[run.mode])
            if run.surface == "fde_profile":
                from sales_backend.services.fde_profile import current_run_facts

                return await current_run_facts(connection, run)
            if run.mode == "visit_entry":
                if not run.customer_id:
                    raise ValueError("录入拜访必须先选择客户")
                # Only bound customer reference fields and this actor's default
                # recording values supplement the original input. No customer
                # contacts, previous visit content or aggregates enter extraction.
                request = run.visit_request or {"stage": "structure", "is_first_visit": False}
                stage = request["stage"]
                server = await self._load_visit_server_fields(connection, run)
                server["customer_type"] = "客户"
                facts = {
                    "data_as_of": datetime.now(UTC).isoformat(),
                    "visit_stage": stage,
                    "server_fields": server,
                    "is_first_visit": request.get("is_first_visit", False),
                }
                if stage == "quality":
                    from sales_backend.services.visit_flow import (
                        relative_time_context, structure_date_anchor, structure_source,
                    )

                    anchor = request.get("date_anchor")
                    if not anchor:
                        # Compatibility for quality jobs queued before this
                        # release. Re-read their owned, durable source run.
                        source = await structure_source(connection, run.actor, request["source_run_id"])
                        if not source or source["business_context"].get("customer_id") != run.customer_id:
                            raise ValueError("结构化原始记录不存在，请重新整理")
                        anchor = structure_date_anchor(source)
                    server["created_date"] = anchor
                    facts.update(
                        relative_time_context=relative_time_context(anchor, facts["data_as_of"]),
                        fields=request["fields"],
                        summary=request["summary"],
                        company_policy=await CompanyRulesRepository().active(connection, "visit_admission"),
                    )
                return facts
            if run.mode == "today_tasks":
                facts = await self._load_today_task_facts(connection, run, scope=today_tasks_scope)
                facts["company_policy"] = await CompanyRulesRepository().active(connection, "task_schedule")
                facts["business_timezone"] = BUSINESS_TIMEZONE
                facts["data_as_of"] = business_datetime(facts["data_as_of"]).isoformat()
                facts["company_policy"]["date_instruction"] = (
                    "业务日期按Asia/Shanghai。候选deadline是后端从原文确认的当前录入人期限："
                    "explicit时due_at原样复制；missing时due_at留空，后端才使用默认排期；"
                    "needs_confirmation表示日期或责任人有歧义，不得猜测。已存在任务不得改期，"
                    "原文过去日期不得改成未来，过去的计划不等于已违约。"
                )
                for candidate in facts["follow_up_candidates"]:
                    candidate["deadline"] = deadline_context(candidate, facts["company_policy"])
                return localize_business_times(facts)
            if run.mode == "personal_risks":
                return await self._load_personal_risk_facts(connection, run)
            if run.actor.role in {RoleCode.FDE, RoleCode.FDE_LEAD} and run.mode in {"chatbi", "operating_report"}:
                from sales_backend.repositories.fde_analysis import fde_analysis_facts

                return await fde_analysis_facts(connection, run.actor, personal="范围仅本人" in run.text,
                                                permission=AGENT_PERMISSIONS[run.mode])
            if run.mode == "operating_report":
                return await self._load_operating_report_facts(connection, run)
            if run.mode == "opportunity_draft":
                return await self._load_opportunity_draft_facts(connection, run)
            if run.mode == "customer_chatbi" and run.customer_id:
                return await self._load_customer_chatbi_facts(connection, run)
            summary = await connection.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM crm.customer WHERE deleted_at IS NULL) AS customers,
                  (SELECT count(*) FROM activity.visit WHERE deleted_at IS NULL) AS visits,
                  (SELECT count(*) FROM activity.visit
                    WHERE deleted_at IS NULL
                      AND interaction_at >= date_trunc('day', clock_timestamp())) AS today_visits,
                  (SELECT count(*) FROM crm.opportunity
                    WHERE deleted_at IS NULL AND status = 'open') AS open_opportunities,
                  (SELECT COALESCE(sum(amount), 0) FROM crm.opportunity
                    WHERE deleted_at IS NULL AND status = 'open') AS open_pipeline_amount_cny,
                  (SELECT count(*) FROM insight.risk
                    WHERE deleted_at IS NULL
                      AND status IN ('new','pending','in_progress','escalated')) AS open_risks,
                  (SELECT count(*) FROM workflow.task
                    WHERE deleted_at IS NULL
                      AND status NOT IN ('completed', 'cancelled')) AS open_tasks,
                  (SELECT count(*) FROM workflow.task
                    WHERE deleted_at IS NULL AND status = 'completed') AS completed_tasks
                """
            )
            members = await connection.fetch(
                """
                SELECT u.display_name,
                       count(DISTINCT v.id) FILTER (
                         WHERE v.interaction_at >= clock_timestamp() - interval '7 days'
                       ) AS visits_7d,
                       count(DISTINCT o.id) FILTER (WHERE o.status = 'open') AS open_opportunities,
                       (SELECT COALESCE(sum(scope_o.amount), 0)
                          FROM crm.opportunity scope_o
                         WHERE scope_o.owner_user_ref_id = u.id
                           AND scope_o.deleted_at IS NULL
                           AND scope_o.status = 'open') AS open_pipeline_amount_cny,
                       count(DISTINCT r.id) FILTER (
                         WHERE r.status IN ('new','pending','in_progress','escalated')
                       ) AS open_risks
                FROM platform.user_ref u
                LEFT JOIN activity.visit v ON v.recorder_user_ref_id = u.id AND v.deleted_at IS NULL
                LEFT JOIN crm.opportunity o ON o.owner_user_ref_id = u.id AND o.deleted_at IS NULL
                LEFT JOIN insight.risk r ON r.owner_user_ref_id = u.id AND r.deleted_at IS NULL
                WHERE u.workspace_id = $1::uuid AND u.deleted_at IS NULL
                  AND security.authorization_subject($2,'person',u.id,NULL)
                GROUP BY u.id, u.display_name
                ORDER BY u.display_name
                """,
                run.actor.workspace_id,
                AGENT_PERMISSIONS[run.mode],
            )
        return {
            "scope": {
                "type": run.actor.data_scope.value,
                "team_ids": list(run.actor.team_ids),
                "user_id": run.actor.user_id,
            },
            "summary": dict(summary),
            "members": [dict(row) for row in members],
            "data_as_of": datetime.now(UTC).isoformat(),
        }

    async def _load_visit_server_fields(self, connection: Any, run: RunInput) -> dict[str, str]:
        identity = await connection.fetchrow(
            """SELECT display_name,
                (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date::text AS created_date
            FROM platform.user_ref WHERE id=$1::uuid AND workspace_id=$2::uuid
              AND status='active' AND deleted_at IS NULL""",
            run.actor.user_id,
            run.actor.workspace_id,
        )
        if not identity:
            raise PermissionError("拜访记录人不存在或已停用")
        fields = {
            "created_date": identity["created_date"],
            "recorder_user_id": identity["display_name"],
            "customer_type": "",
        }
        if run.customer_id:
            customer = json_value(
                await connection.fetchval(
                    "SELECT security.customer_reference($1::uuid)",
                    run.customer_id,
                )
            )
            if not customer:
                raise LookupError("客户不存在或不在当前权限范围内")
            fields.update(customer_name=customer["name"], customer_type=customer["customer_type_code"] or "")
        return fields

    async def _load_opportunity_draft_facts(self, connection: Any, run: RunInput) -> dict[str, Any]:
        if not run.customer_id:
            raise ValueError("商机草案缺少关联客户")
        customer = await connection.fetchrow(
            """
            SELECT c.id::text, c.name, c.owner_user_ref_id::text,
                   owner.display_name AS owner_name
              FROM crm.customer c
              LEFT JOIN platform.user_ref owner ON owner.id = c.owner_user_ref_id
             WHERE c.id = $1::uuid AND c.deleted_at IS NULL
            """,
            run.customer_id,
        )
        if not customer:
            raise LookupError("客户不存在或不在当前权限范围内")
        # Customer membership and personal opportunity scope are enforced by the shared RLS contract.
        opportunities = await connection.fetch(
            """
            SELECT id::text, name, stage_code, amount, currency, probability,
                   expected_close_date, status, updated_at
              FROM crm.opportunity
             WHERE customer_id = $1::uuid
               AND deleted_at IS NULL AND status = 'open'
             ORDER BY updated_at DESC
             LIMIT 20
            """,
            run.customer_id,
        )
        return {
            "scope": {"role": run.actor.role.value, "user_id": run.actor.user_id},
            "customer": dict(customer),
            "current_opportunities": [dict(row) for row in opportunities],
            "data_as_of": datetime.now(UTC).isoformat(),
        }

    async def _load_customer_chatbi_facts(self, connection: Any, run: RunInput) -> dict[str, Any]:
        customer = await connection.fetchrow(
            """
            SELECT c.id::text, c.name, c.level_code, c.industry_code,
                   owner.display_name AS owner_name, team.name AS team_name
              FROM crm.customer c
              LEFT JOIN platform.user_ref owner ON owner.id = c.owner_user_ref_id
              LEFT JOIN platform.team team ON team.id = c.owner_team_id
             WHERE c.id = $1::uuid AND c.deleted_at IS NULL
            """,
            run.customer_id,
        )
        if not customer:
            raise LookupError("客户不存在或不在当前权限范围内")
        opportunities = await connection.fetch(
            """
            SELECT id::text, name, status, amount, probability, expected_close_date, stage_code
              FROM crm.opportunity
             WHERE customer_id = $1::uuid AND deleted_at IS NULL
             ORDER BY (status = 'open') DESC, updated_at DESC
             LIMIT 20
            """,
            run.customer_id,
        )
        visits = await connection.fetch(
            """
            SELECT id::text, interaction_at, left(follow_up_record, 400) AS follow_up_record,
                   left(next_action, 200) AS next_action, expectation_code
              FROM activity.visit
             WHERE customer_id = $1::uuid AND deleted_at IS NULL
               AND status IN ('confirmed','archived')
             ORDER BY interaction_at DESC
             LIMIT 8
            """,
            run.customer_id,
        )
        tasks = await connection.fetch(
            """
            SELECT id::text, title, status, due_at
              FROM workflow.task
             WHERE customer_id = $1::uuid AND deleted_at IS NULL
             ORDER BY (status IN ('completed','cancelled')), due_at
             LIMIT 20
            """,
            run.customer_id,
        )
        risks = await connection.fetch(
            """
            SELECT id::text, title, status, severity_code
              FROM insight.risk
             WHERE customer_id = $1::uuid AND deleted_at IS NULL
             ORDER BY (status IN ('resolved','accepted')), updated_at DESC
             LIMIT 12
            """,
            run.customer_id,
        )
        # Detail pages are bounded model context, never the source for totals.
        # Aggregate in the same actor-scoped read-only transaction so both AI
        # providers receive the complete visible metrics under the existing RLS.
        totals = await connection.fetchrow(
            """
            WITH opportunity_totals AS (
              SELECT count(*) AS total,
                     count(*) FILTER (WHERE status = 'open') AS open_count,
                     COALESCE(sum(amount) FILTER (WHERE status = 'open'), 0) AS pipeline_amount
                FROM crm.opportunity WHERE customer_id = $1::uuid AND deleted_at IS NULL
            ), task_totals AS (
              SELECT count(*) AS total,
                     count(*) FILTER (WHERE status NOT IN ('completed','cancelled')) AS open_count,
                     count(*) FILTER (WHERE status = 'completed') AS completed_count
                FROM workflow.task WHERE customer_id = $1::uuid AND deleted_at IS NULL
            ), risk_totals AS (
              SELECT count(*) AS total,
                     count(*) FILTER (WHERE status IN ('new','pending','in_progress','escalated')) AS open_count
                FROM insight.risk WHERE customer_id = $1::uuid AND deleted_at IS NULL
            ), visit_totals AS (
              SELECT count(*) AS total FROM activity.visit
               WHERE customer_id = $1::uuid AND deleted_at IS NULL AND status IN ('confirmed','archived')
            )
            SELECT o.total AS opportunities, o.open_count AS open_opportunities,
                   o.pipeline_amount AS open_pipeline_amount_cny,
                   t.total AS tasks, t.open_count AS open_tasks, t.completed_count AS completed_tasks,
                   r.total AS risks, r.open_count AS open_risks, v.total AS visits
              FROM opportunity_totals o CROSS JOIN task_totals t CROSS JOIN risk_totals r CROSS JOIN visit_totals v
            """,
            run.customer_id,
        )
        details = {
            "opportunities": [dict(row) for row in opportunities],
            "visits": [dict(row) for row in visits],
            "tasks": [dict(row) for row in tasks],
            "risks": [dict(row) for row in risks],
        }
        return {
            "scope": {"type": "customer", "customer_id": run.customer_id},
            "customer": dict(customer),
            **details,
            "detail_coverage": {
                key: {"total": totals[key], "returned": len(rows), "truncated": len(rows) < totals[key]}
                for key, rows in details.items()
            },
            "summary": {
                "customers": 1,
                **{
                    key: totals[key]
                    for key in (
                        "open_opportunities",
                        "open_pipeline_amount_cny",
                        "open_tasks",
                        "completed_tasks",
                        "open_risks",
                        "visits",
                    )
                },
                "recent_visits": len(visits),
            },
            "data_as_of": datetime.now(UTC).isoformat(),
        }

    async def _load_operating_report_facts(self, connection: Any, run: RunInput) -> dict[str, Any]:
        from sales_backend.repositories.authorization import AuthorizationRepository

        grants = (await AuthorizationRepository().effective(connection)).for_permission("agent.operating_report")
        scopes = {grant.scope for grant in grants}
        personal_scope = scopes <= {"self"} or "范围仅本人" in run.text
        visits = await connection.fetch(
            """
            SELECT v.id::text, v.interaction_at, v.expectation_code,
                   left(v.follow_up_record, 600) AS follow_up_record,
                   left(v.next_action, 300) AS next_action, v.duration_minutes,
                   v.customer_id::text AS customer_id,
                   COALESCE(c.name,security.customer_reference(v.customer_id)->>'name') AS customer_name,
                   o.id::text AS opportunity_id, o.name AS opportunity_name,
                   o.amount AS opportunity_amount,
                   v.recorder_user_ref_id::text, recorder.display_name AS recorder_name,
                   team.name AS team_name
              FROM activity.visit v
              LEFT JOIN crm.customer c ON c.id = v.customer_id
              LEFT JOIN crm.opportunity o ON o.id = v.opportunity_id
              JOIN platform.user_ref recorder ON recorder.id = v.recorder_user_ref_id
              LEFT JOIN platform.team team ON team.id = v.recorder_team_id
             WHERE v.deleted_at IS NULL
               AND v.status IN ('confirmed', 'archived')
               AND v.interaction_at >= clock_timestamp() - interval '90 days'
               AND (NOT $1::boolean OR v.recorder_user_ref_id = $2::uuid)
             ORDER BY v.interaction_at DESC
             LIMIT 300
            """,
            personal_scope,
            run.actor.user_id,
        )
        tasks = await connection.fetch(
            """
            SELECT t.id::text, left(t.title, 160) AS title,
                   left(t.description, 400) AS description, t.status,
                   t.priority_code, t.due_at, t.completed_at,
                   assignee.id::text AS assignee_user_ref_id, assignee.display_name AS assignee_name,
                   team.name AS team_name, c.id::text AS customer_id, c.name AS customer_name
              FROM workflow.task t
              JOIN workflow.task_assignee ta
                ON ta.task_id = t.id AND ta.responsibility = 'owner'
              JOIN platform.user_ref assignee ON assignee.id = ta.assignee_user_ref_id
              LEFT JOIN platform.team team ON team.id = ta.assignee_team_id
              LEFT JOIN crm.customer c ON c.id = t.customer_id
             WHERE t.deleted_at IS NULL
               AND (t.created_at >= clock_timestamp() - interval '7 days'
                    OR t.updated_at >= clock_timestamp() - interval '7 days'
                    OR t.due_at >= clock_timestamp() - interval '7 days')
               AND (NOT $1::boolean OR assignee.id = $2::uuid)
             ORDER BY t.due_at, t.created_at DESC
             LIMIT 300
            """,
            personal_scope,
            run.actor.user_id,
        )
        risks = await connection.fetch(
            """
            SELECT r.id::text, left(r.title, 160) AS title,
                   left(r.description, 400) AS description, r.severity_code,
                   r.status, r.opened_at, r.due_at, r.resolved_at,
                   r.customer_id::text AS customer_id,
                   COALESCE(c.name,security.customer_reference(r.customer_id)->>'name') AS customer_name,
                   r.owner_user_ref_id::text AS owner_user_ref_id,
                   owner.display_name AS owner_name, team.name AS team_name
              FROM insight.risk r
              LEFT JOIN crm.customer c ON c.id = r.customer_id
              LEFT JOIN platform.user_ref owner ON owner.id = r.owner_user_ref_id
              LEFT JOIN platform.team team ON team.id = r.owner_team_id
             WHERE r.deleted_at IS NULL
               AND (r.opened_at >= clock_timestamp() - interval '7 days'
                    OR r.updated_at >= clock_timestamp() - interval '7 days'
                    OR r.status IN ('new','pending','in_progress','escalated'))
               AND (NOT $1::boolean OR owner.id = $2::uuid)
             ORDER BY CASE r.severity_code
               WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
               r.opened_at DESC
             LIMIT 300
            """,
            personal_scope,
            run.actor.user_id,
        )
        opportunities = await connection.fetch(
            """
            SELECT o.id::text, o.name, o.amount, o.status, o.stage_code,
                   o.probability, o.expected_close_date, o.updated_at,
                   o.customer_id::text AS customer_id,
                   COALESCE(c.name,security.customer_reference(o.customer_id)->>'name') AS customer_name,
                   o.owner_user_ref_id::text AS owner_user_ref_id,
                   owner.display_name AS owner_name, team.name AS team_name
              FROM crm.opportunity o
              LEFT JOIN crm.customer c ON c.id = o.customer_id
              LEFT JOIN platform.user_ref owner ON owner.id = o.owner_user_ref_id
              LEFT JOIN platform.team team ON team.id = o.owner_team_id
             WHERE o.deleted_at IS NULL
               AND (o.status = 'open' OR o.updated_at >= clock_timestamp() - interval '7 days')
               AND (NOT $1::boolean OR owner.id = $2::uuid)
             ORDER BY o.amount DESC, o.updated_at DESC
             LIMIT 300
            """,
            personal_scope,
            run.actor.user_id,
        )
        scope_label = ("个人" if personal_scope else "全公司授权范围" if "workspace" in scopes
                       else "指定团队" if "teams" in scopes else "参与项目")
        return {
            "scope": {
                "role": run.actor.role.value,
                "scope_type": run.actor.data_scope.value,
                "scope_label": scope_label,
                "team_ids": list(run.actor.team_ids),
                "user_id": run.actor.user_id,
            },
            "windows": {
                "daily_hours": 24,
                "weekly_days": 7,
                "timezone": "Asia/Shanghai",
            },
            "visits": [dict(row) for row in visits],
            "tasks": [dict(row) for row in tasks],
            "risks": [dict(row) for row in risks],
            "opportunities": [dict(row) for row in opportunities],
            "data_as_of": datetime.now(UTC).isoformat(),
        }

    async def _load_today_task_facts(self, connection: Any, run: RunInput, *, scope=None) -> dict[str, Any]:
        active_tasks = await connection.fetch(
            """
            SELECT CASE WHEN t.task_type = 'visit_follow_up'
                        THEN t.source_visit_id::text ELSE t.id::text END AS source_id,
                   CASE WHEN t.task_type = 'visit_follow_up'
                        THEN 'visit_follow_up' ELSE 'management_task' END AS source_type,
                   t.title, t.description, t.due_at, t.priority_code,
                   c.name AS customer_name, creator.display_name AS creator_name,
                   t.created_at
              FROM workflow.task t
              JOIN workflow.task_assignee ta
                ON ta.task_id = t.id
               AND ta.assignee_user_ref_id = $1::uuid
               AND ta.responsibility = 'owner'
              LEFT JOIN crm.customer c ON c.id = t.customer_id
              LEFT JOIN platform.user_ref creator ON creator.id = t.creator_user_ref_id
             WHERE t.deleted_at IS NULL
               AND t.status IN ('pending_confirm', 'pending_execution', 'in_progress', 'deferred')
             ORDER BY t.due_at, t.created_at DESC
            """,
            run.actor.user_id,
        )
        follow_up_candidates = await connection.fetch(
            """
            SELECT latest.id::text AS source_id, 'visit_follow_up' AS source_type,
                   latest.customer_id::text, latest.opportunity_id::text,
                   latest.customer_name, latest.follow_up_record,
                   latest.next_action, latest.interaction_at, latest.recorder_name
              FROM (
                SELECT DISTINCT ON (v.customer_id)
                       v.id, v.customer_id, v.opportunity_id, c.name AS customer_name,
                       v.follow_up_record, v.next_action, v.interaction_at,
                       recorder.display_name AS recorder_name
                  FROM activity.visit v
                  LEFT JOIN crm.customer c ON c.id = v.customer_id
                  LEFT JOIN platform.user_ref recorder ON recorder.id = v.recorder_user_ref_id
                 WHERE v.deleted_at IS NULL
                   AND v.recorder_user_ref_id = $1::uuid
                   AND v.follow_up_task_mode='legacy'
                   AND v.status IN ('confirmed', 'archived')
                   AND NULLIF(btrim(v.next_action), '') IS NOT NULL
                   AND v.interaction_at >= clock_timestamp() - interval '90 days'
                   AND ($2::uuid[] IS NULL OR v.id = ANY($2::uuid[]))
                   AND NOT EXISTS (
                     SELECT 1 FROM workflow.task existing
                      WHERE existing.source_visit_id = v.id
                        AND existing.deleted_at IS NULL
                   )
                 ORDER BY v.customer_id, v.interaction_at DESC, v.created_at DESC
              ) latest
             ORDER BY latest.interaction_at DESC
             LIMIT 30
            """,
            run.actor.user_id,
            list(scope.source_ids) if scope is not None else None,
        )
        if scope is not None:
            scope.check_candidates([dict(row) for row in follow_up_candidates])
        return {
            "scope": {
                "type": run.actor.data_scope.value,
                "user_id": run.actor.user_id,
            },
            "active_tasks": [dict(row) for row in active_tasks],
            "follow_up_candidates": [
                {**dict(row), "association_complete": bool(row["customer_id"] and row["opportunity_id"])}
                for row in follow_up_candidates
            ],
            "data_as_of": datetime.now(UTC).isoformat(),
        }

    async def _load_personal_risk_facts(self, connection: Any, run: RunInput) -> dict[str, Any]:
        visits = await connection.fetch(
            """
            SELECT v.id::text AS source_visit_id, v.customer_id::text,
                   v.opportunity_id::text, c.name AS customer_name,
                   o.name AS opportunity_name, o.status AS opportunity_status,
                   o.amount AS opportunity_amount,
                   v.interaction_at, v.interaction_mode_code,
                   v.visit_location, v.duration_minutes, v.expectation_code,
                   v.follow_up_record, v.next_action, c.level_code
              FROM activity.visit v
              LEFT JOIN crm.customer c ON c.id = v.customer_id
              LEFT JOIN crm.opportunity o ON o.id = v.opportunity_id
             WHERE v.deleted_at IS NULL
               AND v.recorder_user_ref_id = $1::uuid
               AND v.status IN ('confirmed', 'archived')
               AND v.interaction_at >= clock_timestamp() - interval '180 days'
             ORDER BY v.interaction_at DESC, v.created_at DESC
             LIMIT 12
            """,
            run.actor.user_id,
        )
        open_risks = await connection.fetch(
            """
            SELECT r.id::text AS risk_id, r.source_visit_id::text,
                   r.risk_type_code, r.title, r.description,
                   r.severity_code, r.status, c.name AS customer_name,
                   r.due_at, r.opened_at, r.attributes, r.import_meta,
                   r.source_code, r.suggested_action, r.agent_key, r.source_run_id
              FROM insight.risk r
              LEFT JOIN crm.customer c ON c.id = r.customer_id
             WHERE r.deleted_at IS NULL
               AND r.owner_user_ref_id = $1::uuid
               AND r.status IN ('new', 'pending', 'in_progress', 'escalated')
             ORDER BY CASE r.severity_code
                        WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3 ELSE 4 END,
                      r.opened_at DESC
            """,
            run.actor.user_id,
        )
        return {
            "scope": {
                "type": run.actor.data_scope.value,
                "user_id": run.actor.user_id,
            },
            "visits": [dict(row) for row in visits],
            "existing_open_risks": [overlay_risk_attributes(dict(row)) for row in open_risks],
            "data_as_of": datetime.now(UTC).isoformat(),
        }
