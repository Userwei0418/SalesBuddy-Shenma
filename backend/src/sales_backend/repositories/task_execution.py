"""Atomic, explicitly authorized PostgreSQL task execution commands."""

from sales_backend.db import json_value


class TaskExecutionRepository:
    async def summary(self, connection, task_id):
        return json_value(await connection.fetchval("SELECT workflow.task_execution_summary($1::uuid)", task_id))

    async def read(self, connection, task_id):
        adoption = json_value(await connection.fetchval("""SELECT jsonb_build_object(
            'task_id',t.id,'execution_mode','legacy','protocol_version',NULL,
            'supported_protocol_version',2,'status',t.status,'version_no',t.version_no,
            'requirements_version',1,'responsibility_version',1,'checklist_revision',1,
            'creator_user_ref_id',t.creator_user_ref_id,'current_reviewer',t.creator_user_ref_id,
            'executor_user_id',o.user_id,'self_managed',COALESCE(o.user_id=t.creator_user_ref_id,false),
            'review_required',o.user_id IS DISTINCT FROM t.creator_user_ref_id,
            'requirements',jsonb_build_object('title',t.title,'description',t.description,
              'due_at',t.due_at,'priority_code',t.priority_code,'deliverable','','acceptance_criteria',''),
            'checklist','[]'::jsonb,'events','[]'::jsonb,'submissions','[]'::jsonb,
            'requirements_incomplete',true,'requires_single_owner',o.owner_count>1,
            'initialize_resume_options',CASE WHEN t.status='deferred'
              AND security.authorization_task('task.coordinate',t.id) THEN
                CASE WHEN t.accepted_at IS NOT NULL AND o.owner_accepted
                  THEN '["pending_execution","in_progress"]'::jsonb ELSE '["pending_confirm"]'::jsonb END
              ELSE '[]'::jsonb END,
            'capabilities',jsonb_build_object('initialize_execution',
              t.status NOT IN ('completed','cancelled') AND o.owner_count<=1
              AND security.workspace_module_enabled(t.workspace_id,'task')
              AND (t.status<>'deferred' OR security.authorization_task('task.coordinate',t.id))
              AND to_regprocedure('workflow.task_execution_initialize_v2(uuid,jsonb,boolean)') IS NOT NULL
              AND EXISTS(SELECT 1 FROM platform.user_ref u
                JOIN platform.role_binding r ON r.user_ref_id=u.id AND r.workspace_id=u.workspace_id
                WHERE u.id=common.current_user_ref_id() AND u.workspace_id=t.workspace_id
                  AND u.status='active' AND u.deleted_at IS NULL
                  AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
                  AND r.role_code IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead')
                  AND (r.role_code NOT IN ('fde','fde_lead')
                    OR security.fde_user_is_active(u.id,r.role_code,r.team_id)))
              AND ((o.user_id=common.current_user_ref_id() AND security.authorization_task('task.complete',t.id))
                OR security.authorization_task('task.coordinate',t.id))))
          FROM workflow.task t LEFT JOIN LATERAL (
            SELECT count(*) owner_count,bool_and(a.accepted_at IS NOT NULL) owner_accepted,
              CASE WHEN count(*)=1 THEN min(a.assignee_user_ref_id::text)::uuid ELSE NULL END user_id
            FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.responsibility='owner') o ON true
          WHERE t.id=$1::uuid AND t.workspace_id=common.current_workspace_id()
            AND t.deleted_at IS NULL AND t.execution_mode='legacy'
            AND security.authorization_task('task.read',t.id)""", task_id))
        if adoption is not None:
            if await connection.fetchval("SELECT to_regprocedure('workflow.task_execution_adoption_v3(uuid)') IS NOT NULL") is True:
                light = json_value(await connection.fetchval(
                    "SELECT workflow.task_execution_adoption_v3($1::uuid)", task_id))
                if isinstance(light, dict):
                    adoption["supported_protocol_version"] = 3
                    adoption["capabilities"]["adopt_v3"] = light.get("available") is True
                    adoption["adopt_versions"] = {key: light[key] for key in
                        ("version_no", "requirements_version", "responsibility_version") if key in light}
                    adoption["adopt_unavailable_reason"] = light.get("reason")
            return adoption
        return json_value(await connection.fetchval("SELECT workflow.task_execution_summary($1::uuid)", task_id))

    async def visible(self, connection, task_id):
        return await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM workflow.task
          WHERE id=$1::uuid AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL
          AND security.authorization_task('task.read',id))""", task_id)

    async def history(self, connection, task_id, kind, before_at, before_id, limit):
        # Both sources retain RLS and current object authorization; names are fixed here.
        if kind == "events":
            sql = """SELECT id::text,actor_user_ref_id::text,kind,body,body AS payload,occurred_at FROM (
              SELECT id,actor_user_ref_id,kind,body,occurred_at
              FROM workflow.task_execution_event WHERE task_id=$1::uuid
                AND workspace_id=common.current_workspace_id()
                AND security.authorization_task('task.read',task_id)
              UNION ALL
              SELECT e.id,e.actor_user_ref_id,e.event_type,
                jsonb_build_object('legacy',true,'command',jsonb_build_object('note',e.note),
                  'payload',e.payload,'actor_name',e.payload->>'actor_name',
                  'from_status',e.from_status,'to_status',e.to_status),e.occurred_at
              FROM workflow.task_event e WHERE e.task_id=$1::uuid
                AND e.workspace_id=common.current_workspace_id()
                AND security.authorization_task('task.read',e.task_id)
                AND NOT EXISTS(SELECT 1 FROM workflow.task_execution_event x WHERE x.id=e.id)
              ) history
              WHERE ($2::timestamptz IS NULL OR (occurred_at,id)<($2::timestamptz,$3::uuid))
              ORDER BY occurred_at DESC,id DESC LIMIT $4"""
        else:
            sql = """SELECT id::text,actor_user_ref_id::text,snapshot,created_at
              FROM workflow.task_execution_submission WHERE task_id=$1::uuid
                AND workspace_id=common.current_workspace_id()
                AND security.authorization_task('task.read',task_id)
                AND ($2::timestamptz IS NULL OR (created_at,id)<($2::timestamptz,$3::uuid))
              ORDER BY created_at DESC,id DESC LIMIT $4"""
        return [dict(row) for row in await connection.fetch(sql, task_id, before_at, before_id, limit)]

    async def command(self, connection, task_id, action, body, key):
        return json_value(
            await connection.fetchval(
                "SELECT workflow.task_execution_command($1::uuid,$2::text,$3::jsonb,$4::uuid)",
                task_id,
                action,
                body,
                key,
            )
        )

    async def initialize(self, connection, task_id, checklist, *, protocol_version=1, confirmed_self=False):
        if protocol_version == 3:
            return json_value(await connection.fetchval(
                "SELECT workflow.task_execution_initialize_v3($1::uuid,$2::jsonb)", task_id, {}))
        if protocol_version == 2:
            return json_value(await connection.fetchval(
                "SELECT workflow.task_execution_initialize_v2($1::uuid,$2::jsonb,$3::boolean)",
                task_id, checklist, confirmed_self))
        return json_value(
            await connection.fetchval(
                "SELECT workflow.task_execution_initialize($1::uuid,$2::jsonb)", task_id, checklist
            )
        )

    async def candidates(self, connection, task_id, purpose, query, limit, offset):
        return json_value(
            await connection.fetchval(
                "SELECT workflow.task_execution_candidates($1::uuid,$2::text,$3::text,$4::integer,$5::integer)",
                task_id,
                purpose,
                query,
                limit,
                offset,
            )
        )

    async def command_v3(self, connection, task_id, action, body, key):
        if action == "adopt":
            return json_value(await connection.fetchval(
                "SELECT workflow.task_execution_adopt_v3($1::uuid,$2::jsonb,$3::uuid)", task_id, body, key))
        return json_value(await connection.fetchval(
            "SELECT workflow.task_execution_command_v3($1::uuid,$2::text,$3::jsonb,$4::uuid)",
            task_id, action, body, key))
