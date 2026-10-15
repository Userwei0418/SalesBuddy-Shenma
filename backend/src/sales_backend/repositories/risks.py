from __future__ import annotations

from typing import Any

import asyncpg

from sales_backend.contracts.risk_browse import RiskBrowseQuery
from sales_backend.db import json_value
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.concurrency import VersionConflict, require_version
from sales_backend.repositories.attribute_overlay import overlay_risk_attributes


class RiskNotFound(LookupError):
    pass


class RiskForbidden(PermissionError):
    pass


class RiskRepository:
    @staticmethod
    async def _require_action_access(connection, *, risk_id: str, permission: str) -> None:
        # FOR UPDATE applies both SELECT and UPDATE RLS. Check ordinary read
        # visibility first so a visible read-only risk produces 403, not 404.
        if not await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM insight.risk WHERE id=$1::uuid AND deleted_at IS NULL)", risk_id
        ):
            raise RiskNotFound("RISK_NOT_FOUND")
        if not await connection.fetchval("SELECT security.authorization_risk($1,$2::uuid)", permission, risk_id):
            raise RiskForbidden("当前风险不在操作授权范围")

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

    async def page(
        self, connection: asyncpg.Connection, *, actor: ActorContext,
        filters: RiskBrowseQuery, status: str | None, limit: int, offset: int,
    ) -> dict[str, Any]:
        # Both rows and counts use one database snapshot and the same RLS scan.
        owner_id = actor.user_id if filters.scope == "self" else filters.member_id
        order = """(r.status = 'resolved'),
            CASE r.severity_code WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                WHEN 'medium' THEN 3 ELSE 4 END,r.opened_at DESC,r.id"""
        row = await connection.fetchrow(
            """WITH scoped AS MATERIALIZED (  -- All selectors use bound parameters.
              SELECT r.* FROM insight.risk r
              WHERE r.deleted_at IS NULL AND r.workspace_id=$1::uuid
                AND ($2::uuid IS NULL OR r.owner_user_ref_id=$2::uuid)
                AND ($3::uuid IS NULL OR r.owner_team_id=$3::uuid)
                AND (NOT $7::boolean OR r.opportunity_id IS NOT NULL)
            ), filtered AS MATERIALIZED (
              SELECT r.* FROM scoped r WHERE ($4::text IS NULL
                OR ($4='open' AND r.status <> 'resolved')
                OR ($4='resolved' AND r.status = 'resolved'))
            ), page AS MATERIALIZED (
              SELECT r.* FROM filtered r ORDER BY """ + order + """
              LIMIT $5 OFFSET $6
            ), items AS (""" + self._SELECT.replace("FROM insight.risk r", "FROM page r") + """),
            counts AS (
              SELECT count(*)::int AS total,
                count(*) FILTER(WHERE status <> 'resolved')::int AS open_count,
                count(*) FILTER(WHERE status = 'resolved')::int AS resolved_count,
                count(*) FILTER(WHERE status <> 'resolved'
                  AND due_at<statement_timestamp())::int AS overdue_count FROM scoped
            )
            SELECT (SELECT to_jsonb(counts) FROM counts) AS summary,
              (SELECT count(*)::int FROM filtered) AS total,
              COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY """ + order + """) FROM items r),'[]'::jsonb) AS items
            """,  # noqa: S608 -- Only static SELECT/order fragments are composed above.
            actor.workspace_id, owner_id, filters.team_id, status, limit, offset, filters.opportunity_only,
        )
        summary = json_value(row["summary"])
        items = [overlay_risk_attributes(item) for item in json_value(row["items"])]
        more = offset + len(items) < row["total"]
        return {"items": items, "next_cursor": None, "total": row["total"], "summary": summary,
                "offset": offset, "page_size": limit, "has_more": more,
                "next_offset": offset + limit if more else None}

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
                 OR ($1 = 'open' AND r.status <> 'resolved')
                 OR ($1 = 'resolved' AND r.status = 'resolved')
               )
             ORDER BY (r.status = 'resolved'),
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
                   actor.display_name AS actor_name, e.actor_user_ref_id::text, e.payload, e.occurred_at
              FROM insight.risk_event e
              LEFT JOIN platform.user_ref actor ON actor.id = e.actor_user_ref_id
             WHERE e.risk_id = $1::uuid
             ORDER BY e.occurred_at,e.id
            """,
            risk_id,
        )
        result = overlay_risk_attributes(dict(row))
        result["events"] = [dict(item) for item in events]
        result["can_resolve"] = result["status"] != "resolved" and bool(await connection.fetchval(
            "SELECT security.authorization_risk('risk.resolve',$1::uuid)", risk_id))
        resolved_event = next((item for item in reversed(events) if item["event_type"] == "resolved"), None)
        if resolved_event:
            result["resolved_by_name"] = resolved_event["actor_name"]
        # The promoted current column is authoritative, including an explicit clear.
        result["next_action"] = result.get("suggested_action")
        result["can_decide"] = result["status"] != "resolved" and bool((result["next_action"] or "").strip()) and bool(
            await connection.fetchval("SELECT security.authorization_risk('risk.decide',$1::uuid)", risk_id))
        latest = next((e for e in reversed(result["events"]) if e["event_type"] == "suggestion_decided"), None)
        result["current_decision"] = None
        if latest:
            payload = json_value(latest["payload"])
            result["current_decision"] = {
                "event_id": latest["id"], "decision": payload["decision"], "note": payload["note"],
                "actor_name": latest["actor_name"], "occurred_at": latest["occurred_at"],
                "risk_version": payload["risk_version_after"],
                "is_current": payload["risk_version_after"] == result["version_no"],
            }
        return result

    async def decide(self, connection, *, actor: ActorContext, risk_id: str, body) -> dict[str, Any]:
        await self._require_action_access(connection, risk_id=risk_id, permission="risk.decide")
        risk = await connection.fetchrow(
            "SELECT id::text,status,version_no,suggested_action,evidence FROM insight.risk "
            "WHERE id=$1::uuid AND deleted_at IS NULL FOR UPDATE", risk_id)
        if not risk:
            await self._require_action_access(connection, risk_id=risk_id, permission="risk.decide")
            raise RiskNotFound("RISK_NOT_FOUND")
        if not await connection.fetchval("SELECT security.authorization_risk('risk.decide',$1::uuid)", risk_id):
            raise RiskForbidden("当前风险不在建议确认授权范围")
        previous = await connection.fetchrow(
            "SELECT id::text,payload FROM insight.risk_event WHERE workspace_id=$1::uuid "
            "AND risk_id=$2::uuid AND actor_user_ref_id=$3::uuid AND event_type='suggestion_decided' "
            "AND payload->>'request_id'=$4", actor.workspace_id, risk_id, actor.user_id, body.request_id)
        request = body.model_dump()
        if previous:
            payload = json_value(previous["payload"])
            if payload["request"] != request:
                raise VersionConflict("请求标识已用于另一条建议处理记录，请刷新")
            result = await self.detail(connection, risk_id=risk_id)
            if result is None:
                raise RiskNotFound("RISK_NOT_FOUND")
            return {"risk": result, "decision_event_id": previous["id"], "idempotent_replay": True}
        require_version(risk["version_no"], body.version_no)
        if risk["status"] == "resolved":
            raise VersionConflict("风险已解除，请刷新后查看")
        if not (risk["suggested_action"] or "").strip():
            raise VersionConflict("当前没有可确认的建议动作，请刷新")
        payload = {"request_id": body.request_id, "request": request, "decision": body.decision,
                   "note": body.note, "risk_version_before": risk["version_no"],
                   "risk_version_after": risk["version_no"] + 1,
                   "suggested_action": risk["suggested_action"], "evidence": json_value(risk["evidence"])}
        await connection.execute("UPDATE insight.risk SET version_no=version_no+1 WHERE id=$1::uuid", risk_id)
        event_id = await connection.fetchval(
            "INSERT INTO insight.risk_event(workspace_id,risk_id,event_type,from_status,to_status,"
            "actor_user_ref_id,payload) VALUES($1::uuid,$2::uuid,'suggestion_decided',$3,$3,$4::uuid,$5::jsonb) "
            "RETURNING id::text", actor.workspace_id, risk_id, risk["status"], actor.user_id, payload)
        result = await self.detail(connection, risk_id=risk_id)
        if result is None:
            raise RiskNotFound("RISK_NOT_FOUND")
        return {"risk": result, "decision_event_id": event_id, "idempotent_replay": False}

    async def resolve(
        self,
        connection: asyncpg.Connection,
        *,
        actor: ActorContext,
        risk_id: str,
        resolution_note: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        await self._require_action_access(connection, risk_id=risk_id, permission="risk.resolve")
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
            await self._require_action_access(connection, risk_id=risk_id, permission="risk.resolve")
            raise RiskNotFound("RISK_NOT_FOUND")
        if not await connection.fetchval("SELECT security.authorization_risk('risk.resolve',$1::uuid)", risk_id):
            raise RiskForbidden("当前风险不在处置授权范围")
        # Explicit versions keep their original strict conflict behavior, even
        # for already resolved risks. Legacy requests have no observed version;
        # they must never silently adopt an evolved open record's current one.
        if expected_version is not None:
            require_version(risk["version_no"], expected_version)
        if risk["status"] == "resolved":
            existing = await self.detail(connection, risk_id=risk_id)
            if existing is None:
                raise RiskNotFound("RISK_NOT_FOUND")
            return existing
        if expected_version is None and risk["version_no"] != 1:
            raise VersionConflict("风险已更新，当前客户端不支持版本校验；请升级小程序后重新打开风险再试")

        previous_status = risk["status"]
        updated = await connection.execute(
            """
            UPDATE insight.risk
               SET status = 'resolved', resolved_at = clock_timestamp(), resolution_note = $2,
                   version_no = version_no + 1
             WHERE id = $1::uuid AND version_no = $3 AND status <> 'resolved'
            """,
            risk_id,
            resolution_note,
            risk["version_no"],
        )
        if updated != "UPDATE 1":
            raise VersionConflict("记录已被他人更新，请刷新后再试")
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
            {"resolution_note": resolution_note,
             "version_check": "legacy_initial_version" if expected_version is None else "client_version",
             "risk_version_before": risk["version_no"], "risk_version_after": risk["version_no"] + 1},
        )
        # V230 derives the owner's notification from this exact resolved event
        # in the same transaction, without granting access to another inbox.
        resolved = await self.detail(connection, risk_id=risk_id)
        if resolved is None:
            raise RiskNotFound("RISK_NOT_FOUND")
        return resolved
