"""Append-only connectivity receipts in the existing tenant audit log."""


class ConnectivityRepository:
    async def lock(self, conn, actor):
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "connectivity:" + actor.workspace_id)

    async def get(self, conn, actor, request_id):
        row = await conn.fetchrow(
            """SELECT actor_user_ref_id::text,after_snapshot,
              occurred_at<clock_timestamp()-interval '90 seconds' AS stale
            FROM ops.audit_log WHERE workspace_id=$1::uuid AND object_type='ai_connectivity'
              AND object_id=$2::uuid ORDER BY occurred_at DESC,id DESC LIMIT 1""",
            actor.workspace_id,
            str(request_id),
        )
        return dict(row) if row else None

    async def count_recent(self, conn, actor):
        return await conn.fetchval(
            """SELECT count(*) FROM ops.audit_log WHERE workspace_id=$1::uuid
              AND object_type='ai_connectivity' AND action_code='连通性测试发起'
              AND occurred_at>clock_timestamp()-interval '1 minute'""",
            actor.workspace_id,
        )

    async def enqueue(self, conn, actor, request_id):
        await conn.execute(
            """INSERT INTO ops.job(workspace_id,job_type,aggregate_type,aggregate_id,payload,
              priority,max_attempts,correlation_id)
            VALUES($1::uuid,'ai.connectivity','ai_connectivity',$2::uuid,$3::jsonb,40,1,$2::uuid)""",
            actor.workspace_id, str(request_id), actor.model_dump(mode="json"),
        )

    async def append(self, conn, actor, value, *, finished=False, executing=False):
        await conn.execute(
            """INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,
              action_code,module_code,object_type,object_id,object_label,request_id,after_snapshot,result_code)
            VALUES($1::uuid,$2::uuid,$3,$4,'AI连通性','ai_connectivity',$5::uuid,$6,$5::uuid,$7::jsonb,$8)""",
            actor.workspace_id,
            actor.user_id,
            actor.role.value,
            "连通性测试完成" if finished else "连通性测试执行" if executing else "连通性测试发起",
            value["id"],
            value["label"],
            value,
            {"running": "测试中", "passed": "测试通过", "failed": "测试失败", "unavailable": "未执行"}[value["status"]],
        )
