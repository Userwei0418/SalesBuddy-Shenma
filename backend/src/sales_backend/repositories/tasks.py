# ruff: noqa: S608 -- Filter columns/casts are fixed internally and values stay bound.
from __future__ import annotations

from typing import Any

import asyncpg

from sales_backend.repositories.attribute_overlay import overlay_task_attributes

TASK_HANDOVER_REQUIRED_SQL = """(t.status IN ('pending_confirm','pending_execution','in_progress','deferred','pending_review') AND (
                 EXISTS(SELECT 1 FROM workflow.task_assignee ha WHERE ha.task_id=t.id AND ha.responsibility='owner'
                   AND NOT security.task_recipient_eligible(
                     t.id,ha.assignee_user_ref_id,ha.assignee_role,ha.assignee_team_id))
                 OR (t.target_position IS NOT NULL AND t.status='pending_confirm'
                   AND NOT EXISTS(SELECT 1 FROM workflow.v_task_action_recipient hc WHERE hc.task_id=t.id
                     AND hc.responsibility='candidate'))
               ))"""


class TaskRepository:
    _SELECT = """
        SELECT t.id::text, t.task_type, t.association_kind, t.title, t.description,
               t.customer_id::text,
               COALESCE(c.name,security.customer_reference(t.customer_id)->>'name',
                 security.task_link_context(t.id)->>'customer_name') AS customer_name,
               t.opportunity_id::text,
               COALESCE((SELECT o.name FROM crm.opportunity o WHERE o.id=t.opportunity_id),
                 security.task_link_context(t.id)->>'opportunity_name') AS opportunity_name,
               security.has_customer_access(t.customer_id) AS can_open_customer,
               security.has_opportunity_read_access(t.opportunity_id) AS can_open_opportunity,
               t.target_position,t.source_suggestion_id::text,
               security.fde_can_coordinate_task(t.id) AS can_coordinate,
               """ + TASK_HANDOVER_REQUIRED_SQL + """ AS handover_required,
               COALESCE((SELECT jsonb_agg(jsonb_build_object(
                 'user_id',tc.user_ref_id::text,'name',cu.display_name,'decision',tc.decision,
                 'role',tc.role_code,'note',tc.decision_note))
                 FROM workflow.task_candidate tc JOIN platform.user_ref cu ON cu.id=tc.user_ref_id
                 WHERE tc.task_id=t.id),'[]'::jsonb) AS candidates,
               CASE WHEN t.status='pending_review' THEN t.creator_user_ref_id=common.current_user_ref_id()
                 ELSE EXISTS(SELECT 1 FROM workflow.v_task_action_recipient ar
                 WHERE ar.task_id=t.id AND ar.assignee_user_ref_id=common.current_user_ref_id()
                   AND ar.assignee_role=common.current_role_code()) END AS requires_action,
               (SELECT e.event_type FROM workflow.task_event e WHERE e.task_id=t.id
                 ORDER BY e.occurred_at DESC,e.id DESC LIMIT 1) AS last_event_type,
               (SELECT e.note FROM workflow.task_event e WHERE e.task_id=t.id
                 ORDER BY e.occurred_at DESC,e.id DESC LIMIT 1) AS last_event_note,
               t.creator_user_ref_id::text, creator.display_name AS creator_name,
               t.creator_team_id::text, creator_team.name AS creator_team_name,
               t.priority_code, t.status, t.due_at, t.accepted_at,
               t.completed_at, t.completion_note, t.completion_submitted_at, t.completion_review_note, t.attributes, t.import_meta,
               t.source_code, t.agent_reason, t.source_follow_up_record,
               t.source_interaction_at, t.rejection_comment, t.version_no,
               t.created_at, t.updated_at,
               COALESCE(
                 jsonb_agg(DISTINCT jsonb_build_object(
                   'user_id', assignee.id::text,
                   'name', assignee.display_name,
                   'account_code', assignee.account_code,
                   'team_id', ta.assignee_team_id::text,
                   'team_name', assignee_team.name,
                   'role', ta.assignee_role,
                   'responsibility', ta.responsibility
                 )) FILTER (WHERE assignee.id IS NOT NULL),
                 '[]'::jsonb
               ) AS assignees
          FROM workflow.task t
          LEFT JOIN crm.customer c ON c.id = t.customer_id
          JOIN platform.user_ref creator ON creator.id = t.creator_user_ref_id
          LEFT JOIN platform.team creator_team ON creator_team.id = t.creator_team_id
          LEFT JOIN workflow.task_assignee ta ON ta.task_id = t.id
          LEFT JOIN platform.user_ref assignee ON assignee.id = ta.assignee_user_ref_id
          LEFT JOIN platform.team assignee_team ON assignee_team.id = ta.assignee_team_id
    """

    @staticmethod
    def _filters(*, status=None, customer_id=None, opportunity_id=None, fde_view=None, inbox=False):
        args: list[Any] = []
        predicates = ["t.deleted_at IS NULL"]
        for column, value, cast in (("status", status, "text"), ("customer_id", customer_id, "uuid"),
                                    ("opportunity_id", opportunity_id, "uuid")):
            if value is not None:
                args.append(value)
                predicates.append(f"t.{column}=${len(args)}::{cast}")
        if fde_view == "self":
            predicates.append("""(t.creator_user_ref_id=common.current_user_ref_id()
                OR EXISTS(SELECT 1 FROM workflow.task_assignee s WHERE s.task_id=t.id
                  AND s.assignee_user_ref_id=common.current_user_ref_id())
                OR EXISTS(SELECT 1 FROM workflow.task_candidate s WHERE s.task_id=t.id
                  AND s.user_ref_id=common.current_user_ref_id()))""")
        elif fde_view == "team":
            predicates.append("security.fde_can_coordinate_task(t.id)")
        elif fde_view is not None:
            raise ValueError("Unknown task view")
        if inbox:
            predicates.append("""((t.status='pending_review' AND t.creator_user_ref_id=common.current_user_ref_id()) OR EXISTS(SELECT 1 FROM workflow.task_assignee a
                WHERE a.task_id=t.id AND a.assignee_user_ref_id=common.current_user_ref_id())
                OR EXISTS(SELECT 1 FROM workflow.task_candidate tc
                  WHERE tc.task_id=t.id AND tc.user_ref_id=common.current_user_ref_id()))""")
        return predicates, args

    async def list(
        self,
        connection: asyncpg.Connection,
        *,
        status: str | None,
        customer_id: str | None,
        limit: int | None,
        offset: int = 0,
        inbox: bool = False,
        opportunity_id: str | None = None,
        fde_view: str | None = None,
    ) -> list[dict[str, Any]]:
        # Bind only filters that are present. Nullable OR filters produced an
        # expensive generic plan after prepared statements had been reused.
        # Select the page before building recipients/events and display fields.
        predicates, args = self._filters(status=status, customer_id=customer_id,
            opportunity_id=opportunity_id, fde_view=fde_view, inbox=inbox)
        args.extend((limit, offset))
        page = (
            "WITH task_page AS MATERIALIZED (SELECT t.id FROM workflow.task t WHERE "
            + " AND ".join(predicates)
            + " ORDER BY (t.status='completed'),t.due_at,t.created_at DESC,t.id"
            + f" LIMIT ${len(args)-1} OFFSET ${len(args)}) "
        )
        selected = self._SELECT.replace(
            "FROM workflow.task t", "FROM task_page page JOIN workflow.task t ON t.id=page.id")
        rows = await connection.fetch(
            page + selected + """
             GROUP BY t.id, c.name, creator.display_name, creator_team.name
             ORDER BY (t.status = 'completed'), t.due_at, t.created_at DESC,t.id
            """,
            *args,
        )
        return [self._normalize(dict(row)) for row in rows]

    async def overview(self, connection, *, task_ids=(), fde_view=None):
        predicates, args = self._filters(fde_view=fde_view)
        assert not args
        where = " AND ".join(predicates)
        metrics = await connection.fetchrow("""
            SELECT count(*) FILTER(WHERE t.status='completed' AND
                     timezone('Asia/Shanghai',t.completed_at)::date=timezone('Asia/Shanghai',clock_timestamp())::date)::int AS today_completed,
              count(*) FILTER(WHERE t.status NOT IN ('completed','cancelled') AND
                     timezone('Asia/Shanghai',t.due_at)::date=timezone('Asia/Shanghai',clock_timestamp())::date)::int AS today_pending,
              count(*) FILTER(WHERE t.status NOT IN ('completed','cancelled'))::int AS all_pending
            FROM workflow.task t WHERE """ + where)
        rows = []
        if task_ids:
            rows = await connection.fetch("""
                SELECT t.id::text,t.status,t.completion_note,
                  jsonb_build_object('rejection_comment',COALESCE(NULLIF(t.rejection_comment,''),
                    t.import_meta->>'rejection_comment',t.attributes->>'rejection_comment')) AS attributes,
                  """ + TASK_HANDOVER_REQUIRED_SQL + """ AS handover_required,
                  e.event_type AS last_event_type,e.note AS last_event_note
                FROM workflow.task t LEFT JOIN LATERAL (
                  SELECT event_type,note FROM workflow.task_event WHERE task_id=t.id
                  ORDER BY occurred_at DESC,id DESC LIMIT 1
                ) e ON true WHERE """ + where + " AND t.id=ANY($1::uuid[]) ORDER BY t.id", list(task_ids))
        return {"metrics": dict(metrics), "items": [dict(row) for row in rows]}

    async def detail(self, connection: asyncpg.Connection, *, task_id: str) -> dict[str, Any] | None:
        row = await connection.fetchrow(
            self._SELECT
            + """
             WHERE t.id = $1::uuid AND t.deleted_at IS NULL
             GROUP BY t.id, c.name, creator.display_name, creator_team.name
            """,
            task_id,
        )
        if not row:
            return None
        events = await connection.fetch(
            """
            SELECT e.id::text, e.event_type, e.from_status, e.to_status,
                   actor.display_name AS actor_name, e.note, e.payload, e.occurred_at
              FROM workflow.task_event e
              LEFT JOIN platform.user_ref actor ON actor.id = e.actor_user_ref_id
             WHERE e.task_id = $1::uuid
             ORDER BY e.occurred_at
            """,
            task_id,
        )
        result = self._normalize(dict(row))
        result["events"] = [dict(item) for item in events]
        completed_event = next((item for item in reversed(events) if item["event_type"] in {"complete", "approve_completion"} and item["to_status"] == "completed"), None)
        if completed_event:
            result["completed_by_name"] = completed_event["actor_name"]
        return result

    @staticmethod
    def _normalize(item: dict[str, Any]) -> dict[str, Any]:
        assignees = item.get("assignees") or []
        owner = next((person for person in assignees if person.get("responsibility") == "owner"), None)
        item["owner_name"] = owner.get("name") if owner else None
        item["team_name"] = owner.get("team_name") if owner else None
        return overlay_task_attributes(item)
