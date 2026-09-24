from __future__ import annotations

from typing import Any

import asyncpg

from sales_backend.domain.agent import ActorContext
from sales_backend.domain.concurrency import require_version
from sales_backend.repositories.attribute_overlay import overlay_risk_attributes


class RiskNotFound(LookupError):
    pass


class RiskForbidden(PermissionError):
    pass


class RiskRepository:
    _SELECT = """
        SELECT r.id::text, r.risk_type_code, r.title, r.description,
               r.customer_id::text, c.name AS customer_name,
               r.opportunity_id::text, o.name AS opportunity_name,
               r.source_visit_id::text, r.severity_code, r.status,
               r.owner_user_ref_id::text, owner.display_name AS owner_name,
               r.owner_team_id::text, team.name AS team_name,
               r.input_snapshot, r.evidence, r.opened_at, r.due_at,
               r.resolved_at, r.resolution_note, r.attributes, r.import_meta,
               r.source_code, r.suggested_action, r.agent_key, r.source_run_id,
               r.version_no, r.created_at, r.updated_at
          FROM insight.risk r
          LEFT JOIN crm.customer c ON c.id = r.customer_id
          LEFT JOIN crm.opportunity o ON o.id = r.opportunity_id
          LEFT JOIN platform.user_ref owner ON owner.id = r.owner_user_ref_id
          LEFT JOIN platform.team team ON team.id = r.owner_team_id
    """

    async def list(
        self,
        connection: asyncpg.Connection,
        *,
        status: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = await connection.fetch(
            self._SELECT
            + """
             WHERE r.deleted_at IS NULL
               AND (
                 $1::text IS NULL
                 OR ($1 = 'open' AND r.status IN ('new', 'pending', 'in_progress', 'escalated'))
                 OR ($1 = 'resolved' AND r.status IN ('resolved', 'accepted'))
               )
             ORDER BY (r.status IN ('resolved', 'accepted')),
                      CASE r.severity_code
                        WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4
                      END,
                      r.opened_at DESC
             LIMIT $2
            """,
            status,
            limit,
        )
        return [overlay_risk_attributes(dict(row)) for row in rows]

    async def detail(self, connection: asyncpg.Connection, *, risk_id: str) -> dict[str, Any] | None:
        row = await connection.fetchrow(
            self._SELECT + " WHERE r.id = $1::uuid AND r.deleted_at IS NULL",
            risk_id,
        )
        if not row:
            return None
        events = await connection.fetch(
            """
            SELECT e.id::text, e.event_type, e.from_status, e.to_status,
                   actor.display_name AS actor_name, e.payload, e.occurred_at
              FROM insight.risk_event e
              LEFT JOIN platform.user_ref actor ON actor.id = e.actor_user_ref_id
             WHERE e.risk_id = $1::uuid
             ORDER BY e.occurred_at
            """,
            risk_id,
        )
        result = overlay_risk_attributes(dict(row))
        result["events"] = [dict(item) for item in events]
        resolved_event = next((item for item in reversed(events) if item["event_type"] == "resolved"), None)
        if resolved_event:
            result["resolved_by_name"] = resolved_event["actor_name"]
        snapshot = result.get("input_snapshot") or {}
        result["next_action"] = snapshot.get("next_action")
        return result

    async def resolve(
        self,
        connection: asyncpg.Connection,
        *,
        actor: ActorContext,
        risk_id: str,
        resolution_note: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        risk = await connection.fetchrow(
            """
            SELECT id::text, title, status, owner_user_ref_id::text, version_no
              FROM insight.risk
             WHERE id = $1::uuid AND deleted_at IS NULL
             FOR UPDATE
            """,
            risk_id,
        )
        if not risk:
            raise RiskNotFound("RISK_NOT_FOUND")
        require_version(risk["version_no"], expected_version)
        if not await connection.fetchval("SELECT security.authorization_risk('risk.resolve',$1::uuid)", risk_id):
            raise RiskForbidden("当前风险不在处置授权范围")
        if risk["status"] in {"resolved", "accepted"}:
            existing = await self.detail(connection, risk_id=risk_id)
            if existing is None:
                raise RiskNotFound("RISK_NOT_FOUND")
            return existing

        previous_status = risk["status"]
        await connection.execute(
            """
            UPDATE insight.risk
               SET status = 'resolved', resolved_at = clock_timestamp(), resolution_note = $2,
                   version_no = version_no + 1
             WHERE id = $1::uuid
            """,
            risk_id,
            resolution_note,
        )
        await connection.execute(
            """
            INSERT INTO insight.risk_event (
              workspace_id, risk_id, event_type, from_status, to_status, actor_user_ref_id, payload
            ) VALUES ($1::uuid, $2::uuid, 'resolved', $3, 'resolved', $4::uuid, $5::jsonb)
            """,
            actor.workspace_id,
            risk_id,
            previous_status,
            actor.user_id,
            {"resolution_note": resolution_note},
        )
        owner_id = risk["owner_user_ref_id"]
        if owner_id and owner_id != actor.user_id:
            await connection.execute(
                """
                INSERT INTO workflow.notification (
                  workspace_id, recipient_user_ref_id, template_code, title, body,
                  object_type, object_id, status, dedupe_key, payload
                ) VALUES ($1::uuid, $2::uuid, 'risk_resolved', '客户风险已解除', $3,
                          'risk', $4::uuid, 'pending', $5, $6::jsonb)
                ON CONFLICT (workspace_id, recipient_user_ref_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
                """,
                actor.workspace_id,
                owner_id,
                risk["title"],
                risk_id,
                f"risk-resolved:{risk_id}:{owner_id}",
                {"risk_id": risk_id, "resolved_by_user_ref_id": actor.user_id, "resolution_note": resolution_note},
            )
        resolved = await self.detail(connection, risk_id=risk_id)
        if resolved is None:
            raise RiskNotFound("RISK_NOT_FOUND")
        return resolved
