# ruff: noqa: S608 -- SQL structure is fixed; the only suffix is a server-owned lock clause.
"""Meeting draft storage; confirmed summaries expose only current task.read scope."""

from sales_backend.repositories.task_outcomes import NORMAL_COMPLETED_SQL
from sales_backend.db import json_value
from sales_backend.repositories.tasks import TaskRepository


class TaskImportRepository:
    async def replace_parse(self, connection, batch_id, version_no, method, warnings, items, editable):
        from sales_backend.domain.tasks import TaskConflict

        if method != "ai":
            raise TaskConflict("重新整理须成功取得AI结果，原稿保持不变")
        changed = await connection.fetchval(
            "SELECT security.reparse_task_import_batch($1::uuid,$2::integer,$3::jsonb,$4::jsonb,$5::jsonb)",
            batch_id, version_no, items, warnings, editable,
        )
        if not changed:
            raise TaskConflict("草稿已变化，请刷新后重试")

    async def own(self, connection, batch_id, *, lock=False):
        row = await connection.fetchrow(
            "SELECT * FROM workflow.task_import_batch WHERE id=$1::uuid "
            "AND creator_user_ref_id=common.current_user_ref_id()" + (" FOR UPDATE" if lock else ""),
            batch_id,
        )
        return self.decode(row) if row else None

    async def matching(self, connection, meeting_date, fingerprint):
        row = await connection.fetchrow(
            "SELECT * FROM workflow.task_import_batch WHERE workspace_id=common.current_workspace_id() "
            "AND creator_user_ref_id=common.current_user_ref_id() AND meeting_date=$1 AND source_hash=$2",
            meeting_date,
            fingerprint,
        )
        return self.decode(row) if row else None

    async def summaries(self, connection, batch_id=None):
        return (
            json_value(await connection.fetchval("SELECT security.task_import_batch_summary($1::uuid)", batch_id)) or []
        )

    async def page(self, connection, *, status, limit, offset):
        return (
            json_value(
                await connection.fetchval(
                    "SELECT security.task_import_batch_page($1::text,$2::integer,$3::integer)",
                    status,
                    limit,
                    offset,
                )
            )
            or []
        )

    async def task_rows(self, connection, ids):
        if not ids:
            return []
        rows = await connection.fetch(
            TaskRepository._SELECT
            + """
            WHERE t.id=ANY($1::uuid[]) AND t.deleted_at IS NULL
              AND (security.authorization_task('task.read',t.id) OR
                security.authorization_allows('task.read',t.workspace_id,t.creator_user_ref_id,ARRAY[t.creator_team_id],false))
            GROUP BY t.id,c.name,creator.display_name,creator_team.name
            ORDER BY t.created_at,t.id""",
            ids,
        )
        return [TaskRepository._normalize(dict(row)) for row in rows]

    async def task_statistics(self, connection, batch_ids):
        # The source is the safe summary function, not the creator-only raw
        # import table. One aggregate query covers the entire requested page.
        return (
            [
                dict(row)
                for row in await connection.fetch(
                    """
            WITH permitted AS (
              SELECT b->>'id' AS batch_id, jsonb_array_elements_text(b->'task_ids')::uuid AS task_id
              FROM unnest($1::uuid[]) requested(batch_id)
              CROSS JOIN LATERAL jsonb_array_elements(security.task_import_batch_summary(requested.batch_id)) b
            ) SELECT p.batch_id,count(*)::int AS visible_task_count,
              count(*) FILTER(WHERE """ + NORMAL_COMPLETED_SQL + """)::int AS completed,
              count(*) FILTER(WHERE t.status='cancelled')::int AS cancelled,
              count(*) FILTER(WHERE t.status='pending_review' OR (t.status='needs_adjustment' AND EXISTS(SELECT 1 FROM workflow.task_execution x WHERE x.task_id=t.id AND x.workspace_id=t.workspace_id AND x.state->>'processing_reason'='awaiting_review')))::int AS pending_review,
              count(*) FILTER(WHERE t.status NOT IN ('completed','cancelled'))::int AS active,
              count(*) FILTER(WHERE t.status NOT IN ('completed','cancelled')
                AND t.due_at<clock_timestamp())::int AS overdue
            FROM permitted p JOIN workflow.task t ON t.id=p.task_id
            WHERE t.deleted_at IS NULL AND (security.authorization_task('task.read',t.id) OR
              security.authorization_allows('task.read',t.workspace_id,t.creator_user_ref_id,ARRAY[t.creator_team_id],false))
            GROUP BY p.batch_id
            """,
                    batch_ids,
                )
            ]
            if batch_ids
            else []
        )

    async def insert(self, connection, actor, request, fingerprint, method, warnings, items, editable):
        await connection.execute(
            "INSERT INTO workflow.task_import_batch(workspace_id,creator_user_ref_id,batch_name,meeting_date,"
            "source_text,source_hash,parse_method,parse_warnings,items,editable_items) "
            "VALUES($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb) "
            "ON CONFLICT(workspace_id,creator_user_ref_id,meeting_date,source_hash) DO NOTHING",
            actor.workspace_id,
            actor.user_id,
            request.batch_name.strip(),
            request.meeting_date,
            request.source_text,
            fingerprint,
            method,
            warnings,
            items,
            editable,
        )
        return await self.matching(connection, request.meeting_date, fingerprint)

    @staticmethod
    def decode(row):
        value = dict(row)
        for field in ("items", "editable_items", "parse_warnings"):
            value[field] = json_value(value.get(field)) or []
        value["id"] = str(value["id"])
        value["creator_user_ref_id"] = str(value["creator_user_ref_id"])
        return value

    async def existing_tasks(self, connection):
        # RLS remains authoritative. A duplicate warning never reveals a task
        # that the importing actor cannot read.
        return [
            dict(row)
            for row in await connection.fetch(
                "SELECT t.id::text,t.description,a.assignee_user_ref_id::text AS owner_id,"
                "u.account_code,u.display_name "
                "FROM workflow.task t JOIN workflow.task_assignee a ON a.task_id=t.id AND a.responsibility='owner' "
                "JOIN platform.user_ref u ON u.id=a.assignee_user_ref_id AND u.workspace_id=t.workspace_id "
                "WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL "
                "AND t.status NOT IN ('completed','cancelled') AND (security.authorization_task('task.read',t.id) OR "
                "security.authorization_allows('task.read',t.workspace_id,t.creator_user_ref_id,"
                "ARRAY[t.creator_team_id],false)) "
                "ORDER BY t.created_at DESC,t.id LIMIT 500"
            )
        ]
