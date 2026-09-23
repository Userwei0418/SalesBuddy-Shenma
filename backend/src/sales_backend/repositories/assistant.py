from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from sales_backend.db import json_value
from sales_backend.domain.agent import ActorContext, AgentMode
from sales_backend.domain.visit_contract import VISIT_FIELDS, quality_grade


class MessageReplayConflict(Exception):
    pass


class AssistantRepository:
    async def archived_visits(self, connection: asyncpg.Connection, actor: ActorContext) -> list[dict]:
        """Recent receipts are projections of committed visits, scoped to their recorder.

        A stable visit ID makes reloads/retries idempotent without a second notification
        write. Older history remains available in the customer archive.
        """
        rows = await connection.fetch(
            """SELECT v.id::text,v.customer_id::text,
            COALESCE(v.archived_fields->>'customer_name',
                     security.customer_reference(v.customer_id)->>'name') AS customer_name,
            v.customer_type_code_snapshot AS customer_type,o.name AS opportunity_name,
            v.interaction_at,v.interaction_mode_code AS interaction_mode,v.archived_at,
            v.visit_goal,v.follow_up_record,v.next_action,v.visit_location,
            v.contact_name_snapshot AS contact_name,v.contact_title_snapshot AS contact_title,
            v.partner_name_snapshot AS partner_name,v.follow_up_score AS score,v.quality_review,v.archived_fields,
            ARRAY(SELECT fd.field_key FROM config.form_version_field vf
              JOIN config.field_definition fd ON fd.id=vf.field_definition_id
              WHERE vf.form_version_id=v.form_version_id) AS field_keys
            FROM activity.visit v
            LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.deleted_at IS NULL
            WHERE v.workspace_id=$1::uuid AND v.recorder_user_ref_id=$2::uuid
              AND v.status='archived' AND v.deleted_at IS NULL AND v.archived_at IS NOT NULL
              AND ($3::text NOT IN ('fde','fde_lead') OR
                   (v.created_by_user_ref_id=$2::uuid AND v.confirmed_by_user_ref_id=$2::uuid
                    AND v.recording_role_code_snapshot IN ('fde','fde_lead')))
            ORDER BY v.archived_at DESC,v.id DESC LIMIT 20""",
            actor.workspace_id,
            actor.user_id,
            actor.role.value,
        )
        result = []
        for row in rows:
            item = dict(row)
            score = item["score"]
            item["grade"] = (
                quality_grade(score, ((item.get("quality_review") or {}).get("company_policy") or {}).get("definition"))
                if score is not None
                else "已归档"
            )
            archived = json_value(item.get("archived_fields")) or {}
            item["completed_count"] = (
                sum(bool(archived.get(k)) for k in item["field_keys"])
                if archived
                else sum(bool(item.get(key)) for key in VISIT_FIELDS)
            )
            item["total_count"] = len(item["field_keys"]) or len(VISIT_FIELDS)
            result.append(
                {
                    key: item[key]
                    for key in (
                        "id",
                        "customer_id",
                        "customer_name",
                        "opportunity_name",
                        "interaction_at",
                        "interaction_mode",
                        "archived_at",
                        "completed_count",
                        "total_count",
                        "score",
                        "grade",
                        "next_action",
                    )
                }
            )
        return result

    async def home(self, connection: asyncpg.Connection, actor: ActorContext) -> dict[str, Any]:
        if actor.role.value in {"fde", "fde_lead"}:
            from sales_backend.repositories.fde_home import fde_home

            return await fde_home(connection, actor)
        metrics = await connection.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM activity.visit
                WHERE deleted_at IS NULL
                  AND interaction_at >= date_trunc('day', clock_timestamp())) AS today_visits,
              (SELECT count(*) FROM crm.opportunity
                WHERE deleted_at IS NULL AND status = 'open') AS open_opportunities,
              (SELECT count(*) FROM insight.risk
                WHERE deleted_at IS NULL
                  AND status IN ('new', 'pending', 'in_progress', 'escalated')) AS open_risks,
              (SELECT count(*) FROM workflow.task t
                WHERE t.deleted_at IS NULL
                  AND t.status IN ('pending_confirm', 'pending_execution', 'in_progress', 'deferred', 'pending_review')) AS my_tasks
              ,(SELECT count(*) FROM workflow.task t
                WHERE t.deleted_at IS NULL
                  AND t.status = 'completed') AS completed_tasks
            """
        )
        priority_items = await connection.fetch(
            """
            SELECT * FROM (
              SELECT 'task' AS source_type, t.id::text AS source_id,
                     t.title,
                     COALESCE(NULLIF(btrim(t.description), ''), '请按任务要求及时完成并反馈。') AS detail,
                     '截止 ' || to_char(t.due_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') AS meta,
                     '12小时内' AS tag, 'warning' AS tone,
                     t.due_at AS sort_time, 1 AS sort_group,
                     CASE WHEN ta.assignee_user_ref_id = $1::uuid THEN 'personal' ELSE 'team' END AS scope_group
                FROM workflow.task t
                JOIN workflow.v_task_action_recipient ta
                  ON ta.task_id = t.id

                LEFT JOIN platform.team_membership assignee_tm
                  ON assignee_tm.user_ref_id = ta.assignee_user_ref_id
                 AND assignee_tm.is_primary
                 AND clock_timestamp() >= assignee_tm.valid_from
                 AND clock_timestamp() < assignee_tm.valid_to
               WHERE t.deleted_at IS NULL
                 AND t.status IN ('pending_confirm', 'pending_execution', 'in_progress', 'deferred', 'pending_review')
                 AND (
                   ta.assignee_user_ref_id = $1::uuid
                   OR ($2 = 'supervisor' AND ta.assignee_role='sales' AND ta.assignee_user_ref_id <> $1::uuid
                     AND assignee_tm.team_id = ANY($3::uuid[]))
                 )
                 AND t.due_at > clock_timestamp()
                 AND t.due_at <= clock_timestamp() + interval '12 hours'
              UNION ALL
              SELECT 'risk' AS source_type, r.id::text AS source_id,
                     r.title,
                     COALESCE(NULLIF(btrim(r.description), ''), '进入风险详情查看判断依据和建议。') AS detail,
                     COALESCE(c.name, '客户风险') AS meta,
                     CASE r.severity_code
                       WHEN 'critical' THEN '严重'
                       WHEN 'high' THEN '高风险'
                       ELSE '中风险'
                     END AS tag,
                     r.severity_code AS tone,
                     r.opened_at AS sort_time,
                     CASE r.severity_code WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END AS sort_group,
                     CASE WHEN r.owner_user_ref_id = $1::uuid THEN 'personal' ELSE 'team' END AS scope_group
                FROM insight.risk r
                LEFT JOIN crm.customer c ON c.id = r.customer_id
               WHERE r.deleted_at IS NULL
                 AND r.status IN ('new', 'pending', 'in_progress', 'escalated')
                 AND r.severity_code IN ('medium', 'high', 'critical')
            ) alerts
            ORDER BY sort_group, sort_time
            LIMIT 8
            """,
            actor.user_id,
            actor.role.value,
            list(actor.team_ids),
        )
        task_items = await connection.fetch(
            """
            SELECT 'task' AS source_type, t.id::text AS source_id, t.title,
                   COALESCE(NULLIF(btrim(t.description), ''), '进入任务详情查看执行要求。') AS detail,
                   '截止 ' || to_char(t.due_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') AS meta,
                   CASE WHEN t.status = 'pending_confirm' AND t.target_position IS NOT NULL THEN '待领取'
                        WHEN t.status = 'pending_confirm' THEN '待接受'
                        WHEN t.status = 'pending_review' THEN '待发起人确认'
                        WHEN t.due_at < clock_timestamp() THEN '已逾期'
                        ELSE '待完成' END AS tag,
                   CASE WHEN t.due_at < clock_timestamp() THEN 'warning' ELSE 'normal' END AS tone,
                   'personal' AS scope_group
              FROM workflow.task t
              WHERE t.deleted_at IS NULL AND (
               (t.status='pending_review' AND t.creator_user_ref_id=$1::uuid) OR EXISTS(
                 SELECT 1 FROM workflow.v_task_action_recipient ta WHERE ta.task_id=t.id
                 AND ta.assignee_user_ref_id=$1::uuid))
               AND t.status IN ('pending_confirm','pending_execution','in_progress','deferred','pending_review')
             ORDER BY (t.due_at < clock_timestamp()) DESC, t.due_at, t.created_at DESC
             LIMIT 8
            """,
            actor.user_id,
        )
        return {
            "today_visits": metrics["today_visits"],
            "open_opportunities": metrics["open_opportunities"],
            "open_risks": metrics["open_risks"],
            "my_tasks": metrics["my_tasks"],
            "completed_tasks": metrics["completed_tasks"],
            "priority_items": [dict(row) for row in priority_items],
            "task_items": [dict(row) for row in task_items],
        }

    async def create_conversation(
        self,
        connection: asyncpg.Connection,
        actor: ActorContext,
        *,
        mode: AgentMode,
        customer_id: str | None,
        opportunity_id: str | None = None,
    ) -> asyncpg.Record:
        context = {"mode": mode.value, "customer_id": customer_id, "opportunity_id": opportunity_id}
        return await connection.fetchrow(
            """
            INSERT INTO agent.conversation (
              workspace_id, user_ref_id, role_code, data_scope_snapshot, context
            ) VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::jsonb)
            RETURNING id::text, status, context, created_at
            """,
            actor.workspace_id,
            actor.user_id,
            actor.role.value,
            {
                "scope": actor.data_scope.value,
                "team_ids": list(actor.team_ids),
            },
            context,
        )

    async def conversation_context(self, connection, conversation_id):
        row = await connection.fetchrow(
            "SELECT context FROM agent.conversation WHERE id=$1::uuid AND status='active'", conversation_id
        )
        if not row:
            raise LookupError("conversation not found")
        return row["context"] or {}

    async def enqueue_message(
        self,
        connection: asyncpg.Connection,
        actor: ActorContext,
        *,
        conversation_id: str,
        text: str,
        client_message_id: str | None,
        input_source: str,
        identity_snapshot: dict | None = None,
        business_context: dict | None = None,
    ) -> str:
        conversation = await connection.fetchrow(
            """
            SELECT id, context FROM agent.conversation
            WHERE id = $1::uuid AND status = 'active'
            FOR UPDATE
            """,
            conversation_id,
        )
        if not conversation:
            raise LookupError("conversation not found")
        if client_message_id is not None:
            # The conversation lock serializes retries before any message/job is
            # created. Return the original run even if it is already complete.
            existing = await connection.fetchrow(
                """SELECT r.id::text AS run_id, m.text_content, m.structured_content
                     FROM agent.message m JOIN agent.run r ON r.trigger_message_id = m.id
                    WHERE m.workspace_id = $1::uuid AND m.conversation_id = $2::uuid
                      AND m.client_message_id = $3""",
                actor.workspace_id,
                conversation_id,
                client_message_id,
            )
            if existing:
                original_source = (existing["structured_content"] or {}).get("input_source", "text")
                if existing["text_content"] != text or original_source != input_source:
                    raise MessageReplayConflict("该消息标识已用于不同内容，请重新提交")
                return existing["run_id"]
        if identity_snapshot is None:
            from sales_backend.repositories.capabilities import CapabilityRepository

            identity_snapshot = await CapabilityRepository().analysis_identity(connection, actor)
        message_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        context = conversation["context"] or {}
        mode = str(context.get("mode") or AgentMode.CHATBI.value)
        await connection.execute(
            """
            INSERT INTO agent.message (
              id, workspace_id, conversation_id, sender_type, content_type,
              text_content, structured_content, client_message_id
            ) VALUES ($1::uuid, $2::uuid, $3::uuid, 'user', 'text', $4, $5::jsonb, $6)
            """,
            message_id,
            actor.workspace_id,
            conversation_id,
            text,
            {"input_source": input_source},
            client_message_id,
        )
        await connection.execute(
            """
            INSERT INTO agent.run (
              id, workspace_id, conversation_id, trigger_message_id, intent_code,
              status, identity_context, business_context, model_ref
            ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 'queued',
                      $6::jsonb, $7::jsonb, 'senseaudio-s2-lite')
            """,
            run_id,
            actor.workspace_id,
            conversation_id,
            message_id,
            (
                "bi.query"
                if mode in {"chatbi", "customer_chatbi"}
                else "todo.plan"
                if mode == "today_tasks"
                else "risk.analyze"
                if mode == "personal_risks"
                else "report.generate"
                if mode == "operating_report"
                else mode
            ),
            identity_snapshot,
            {
                "mode": mode,
                "customer_id": context.get("customer_id"),
                "opportunity_id": context.get("opportunity_id"),
                **(business_context or {}),
            },
        )
        await connection.execute(
            """
            INSERT INTO ops.job (
              workspace_id, job_type, aggregate_type, aggregate_id, payload,
              priority, correlation_id
            ) VALUES ($1::uuid, 'agent.run', 'agent_run', $2::uuid, $3::jsonb, 60, $2::uuid)
            """,
            actor.workspace_id,
            run_id,
            {
                "workspace_id": actor.workspace_id,
                "run_id": run_id,
                "user_id": actor.user_id,
                "role": actor.role.value,
                "data_scope": actor.data_scope.value,
                "team_ids": list(actor.team_ids),
            },
        )
        return run_id

    async def list_messages(
        self, connection: asyncpg.Connection, *, conversation_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await connection.fetch(
            """
            SELECT id::text, sender_type, content_type, text_content,
                   structured_content, created_at
            FROM agent.message
            WHERE conversation_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT $2
            """,
            conversation_id,
            limit,
        )
        return [dict(row) for row in reversed(rows)]

    async def get_run(self, connection: asyncpg.Connection, *, run_id: str) -> dict[str, Any] | None:
        row = await connection.fetchrow(
            """
            SELECT r.id::text, r.status, r.intent_code, r.error_code, r.error_detail,
                   r.created_at, r.completed_at,
                   m.structured_content AS result
            FROM agent.run r
            LEFT JOIN LATERAL (
              SELECT structured_content
              FROM agent.message
              WHERE conversation_id = r.conversation_id
                AND sender_type = 'assistant'
                AND source_run_id = r.id
              ORDER BY created_at DESC LIMIT 1
            ) m ON true
            WHERE r.id = $1::uuid

            """,
            run_id,
        )
        return dict(row) if row else None
