from uuid import uuid4

from sales_backend.db import json_value
from sales_backend.domain.concurrency import require_version

REPORT = "security.ai_invocation_report($1,$2,$3,$4)"


class OperationsAIRepository:
    async def report(self, connection, *, start, end, role=None, user=None):
        result = await connection.fetchval(
            """WITH calls AS MATERIALIZED (SELECT * FROM """
            + REPORT
            + """), current_calls AS (
            SELECT * FROM calls WHERE record_kind='provider_attempt'
              AND COALESCE(request_summary->>'network_dispatch_suppressed','false')<>'true')
            SELECT jsonb_build_object(
            'summary',(SELECT jsonb_build_object('calls',count(*),'succeeded',count(*) FILTER(WHERE status='succeeded'),
              'failed',count(*) FILTER(WHERE status='failed'),'running',count(*) FILTER(WHERE status='running'),
              'cancelled',count(*) FILTER(WHERE status='cancelled'),'business_operations',count(DISTINCT operation_id),
              'input_tokens',sum(input_tokens),'output_tokens',sum(output_tokens),
              'unknown_token_calls',count(*) FILTER(WHERE endpoint_code LIKE '%chat%' AND (input_tokens IS NULL OR output_tokens IS NULL)),
              'audio_seconds',sum(audio_seconds),'average_latency_ms',round(avg(latency_ms))) FROM current_calls),
            'legacy_logical_calls',(SELECT count(*) FROM calls WHERE record_kind='legacy_logical'),
            'suppressed_attempts',(SELECT count(*) FROM calls
              WHERE record_kind='provider_attempt' AND request_summary->>'network_dispatch_suppressed'='true'),
            'trend',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.day) FROM (
              SELECT timezone('Asia/Shanghai',started_at)::date AS day,count(*) AS calls,
              count(*) FILTER(WHERE status='succeeded') AS succeeded,sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens
              FROM current_calls GROUP BY day) t),'[]'::jsonb),
            'roles',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.calls DESC) FROM (
              SELECT COALESCE(actor_role_code,'unknown') AS role,count(*) AS calls,count(DISTINCT actor_user_ref_id) AS users,
              sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens,
              count(*) FILTER(WHERE endpoint_code LIKE '%chat%' AND (input_tokens IS NULL OR output_tokens IS NULL)) AS unknown_token_calls
              FROM current_calls GROUP BY actor_role_code) t),'[]'::jsonb))""",
            start,
            end,
            role,
            user,
        )
        return json_value(result)

    async def calls(self, connection, *, start, end, role=None, user=None, status=None, limit=50, offset=0):
        rows = await connection.fetch(
            "SELECT *,count(*) OVER()::integer AS total_count FROM "
            + REPORT
            + """
            WHERE ($5::text IS NULL OR status=$5) ORDER BY started_at DESC,id DESC LIMIT $6 OFFSET $7""",
            start,
            end,
            role,
            user,
            status,
            limit,
            offset,
        )
        return {"items": [dict(r) for r in rows], "total": rows[0]["total_count"] if rows else 0}

    async def rules(self, connection):
        return [
            dict(r)
            for r in await connection.fetch(
                "SELECT id::text,name,role_code,period,calls_limit,tokens_limit,enabled,version_no,created_at FROM ops.ai_usage_rule ORDER BY created_at DESC"
            )
        ]

    async def save_rule(self, connection, actor, rule_id, data):
        if rule_id:
            current = await connection.fetchrow(
                "SELECT version_no FROM ops.ai_usage_rule WHERE id=$1::uuid FOR UPDATE", rule_id
            )
            if not current:
                raise LookupError("规则不存在")
            if data["version_no"] is None:
                raise ValueError("请刷新后修改规则")
            require_version(current["version_no"], data["version_no"])
            await connection.execute(
                "UPDATE ops.ai_usage_rule SET name=$2,role_code=$3,period=$4,calls_limit=$5,tokens_limit=$6,enabled=$7,version_no=version_no+1,updated_at=clock_timestamp() WHERE id=$1::uuid",
                rule_id,
                data["name"],
                data["role_code"],
                data["period"],
                data["calls_limit"],
                data["tokens_limit"],
                data["enabled"],
            )
        else:
            rule_id = str(uuid4())
            await connection.execute(
                "INSERT INTO ops.ai_usage_rule(id,workspace_id,name,role_code,period,calls_limit,tokens_limit,enabled,created_by_user_ref_id) VALUES($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,$9::uuid)",
                rule_id,
                actor.workspace_id,
                data["name"],
                data["role_code"],
                data["period"],
                data["calls_limit"],
                data["tokens_limit"],
                data["enabled"],
                actor.user_id,
            )
        return {"id": str(rule_id)}

    async def alerts(self, connection):
        return [
            dict(r)
            for r in await connection.fetch("""SELECT a.id::text,r.name,a.rule_version,a.period_start,a.calls_count,a.known_tokens,a.unknown_usage_calls,a.created_at
        FROM ops.ai_usage_alert a JOIN ops.ai_usage_rule r ON r.id=a.rule_id ORDER BY a.created_at DESC LIMIT 100""")
        ]
