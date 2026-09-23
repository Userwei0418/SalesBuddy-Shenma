# ruff: noqa: S608 -- Shared filter SQL is constant; all request values stay bound.
from sales_backend.domain.sanitization import redact_log


class OperationsLogRepository:
    async def system_events(self, connection, *, start, end, level=None, module=None, q=None, limit=50, offset=0):
        filters = """occurred_at >= $1 AND occurred_at < $2 AND ($3::text IS NULL OR level=$3)
            AND ($4::text IS NULL OR service_module ILIKE '%'||$4||'%')
            AND ($5::text IS NULL OR detail ILIKE '%'||$5||'%' OR request_id::text=$5)"""
        args = (start, end, level, module, q)
        total = await connection.fetchval("SELECT count(*) FROM ops.system_event WHERE " + filters, *args)
        rows = await connection.fetch(
            "SELECT id::text,occurred_at,level,service_module,event_type,detail,error_stack,request_id::text "
            "FROM ops.system_event WHERE " + filters + " ORDER BY occurred_at DESC,id DESC LIMIT $6 OFFSET $7",
            *args, limit, offset,
        )
        return {"items": [dict(r) for r in rows], "total": total}

    async def audit(self, connection, *, start, end, actor=None, module=None, action=None, q=None, limit=50, offset=0, role=None):
        # Count narrow rows separately; never spool every before/after snapshot
        # through a window function just to return one page of audit receipts.
        source = " FROM ops.audit_log l LEFT JOIN platform.user_ref u ON u.id=l.actor_user_ref_id WHERE "
        filters = """l.occurred_at >= $1 AND l.occurred_at < $2 AND ($3::uuid IS NULL OR l.actor_user_ref_id=$3)
            AND ($4::text IS NULL OR l.module_code=$4 OR l.object_type=$4)
            AND ($7::text IS NULL OR l.actor_role_code=$7)
            AND ($5::text IS NULL OR l.action_code ILIKE '%'||$5||'%')
            AND ($6::text IS NULL OR l.object_label ILIKE '%'||$6||'%' OR COALESCE(l.actor_name_snapshot,u.display_name) ILIKE '%'||$6||'%'
             OR l.request_id::text=$6 OR l.object_id::text=$6)"""
        args = (start, end, actor, module, action, q, role)
        total = await connection.fetchval("SELECT count(*)" + source + filters, *args)
        rows = await connection.fetch(
            """SELECT l.id,l.occurred_at,COALESCE(l.actor_name_snapshot,u.display_name) AS actor_name,u.account_code,l.actor_role_code,
            l.module_code,l.action_code,l.object_type,l.object_id::text,l.object_label,host(l.client_ip) AS client_ip,
            l.request_id::text,l.result_code,l.changed_fields,l.before_snapshot,l.after_snapshot"""
            + source + filters + " ORDER BY l.occurred_at DESC,l.id DESC LIMIT $8 OFFSET $9",
            *args, limit, offset,
        )
        return {"items": [dict(r) for r in rows], "total": total}

    async def record_request(self, connection, actor, *, method, path, status, export_count=None, filters=None, authenticated_actor=None):
        # Row-level audit captures exact changes. This receipt covers reads,
        # exports, rejected operations and idempotent response replay as well.
        action = "export" if path.endswith("/export") else "read" if method == "GET" else "request"
        await connection.execute(
            """INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,
            object_type,object_label,request_id,client_ip,user_agent,after_snapshot,result_code)
            VALUES($1::uuid,$2::uuid,$3,$4,'http','api_request',$5,NULLIF(current_setting('app.request_id',true),'')::uuid,
            NULLIF(current_setting('app.client_ip',true),'')::inet,left(current_setting('app.user_agent',true),500),$6::jsonb,$7)""",
            actor.workspace_id,
            actor.user_id,
            actor.role.value,
            f"http.{action}",
            redact_log(f"{method} {path}", 500),
            {
                "method": method,
                "path": path[:500],
                "status": status,
                **({"authenticated_workspace_id":authenticated_actor.workspace_id,"authenticated_user_id":authenticated_actor.user_id} if authenticated_actor else {}),
                **({"export_count": export_count, "filters": filters or {}} if action == "export" else {}),
            },
            "success" if status < 400 else f"http_{status}",
        )

    async def append_system(self, connection, event):
        await connection.execute(
            "SELECT ops.record_system_event($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7,$8)",
            event["workspace_id"],
            event["actor_id"],
            event["request_id"],
            event["level"],
            event["module"],
            event["event_type"],
            event["detail"],
            event["stack"],
        )
