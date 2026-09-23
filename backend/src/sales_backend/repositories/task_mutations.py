"""Shared task assignment, event and notification persistence; caller owns the transaction."""


class TaskMutationRepository:
    @staticmethod
    async def assign_owner(connection, workspace, task_id, person):
        await connection.execute(
            """INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,
              assignee_role,responsibility) VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,'owner')""",
            task_id,
            workspace,
            person["user_id"],
            person["team_id"],
            person["role_code"],
        )

    @staticmethod
    async def event(connection, actor, task_id, event, before, after, note, payload):
        await connection.execute(
            """INSERT INTO workflow.task_event(workspace_id,task_id,event_type,from_status,to_status,
              actor_user_ref_id,note,payload) VALUES($1::uuid,$2::uuid,$3,$4,$5,$6::uuid,$7,$8::jsonb)""",
            actor.workspace_id,
            task_id,
            event,
            before,
            after,
            actor.user_id,
            note,
            payload,
        )

    @staticmethod
    async def notify(connection, actor, task_id, recipient, template, title, body, payload):
        await connection.fetchval(
            "SELECT workflow.enqueue_task_notification($1::uuid,$2::uuid,$3,$4,$5,$6::uuid,$7,$8::jsonb)",
            actor.workspace_id,
            recipient,
            template,
            title,
            body,
            task_id,
            f"{template}:{task_id}:{recipient}"
            + (f":{actor.user_id}" if template == "task_candidate_declined" else "")
            + (f":{payload['event_version']}" if payload.get("event_version") else ""),
            {"task_id": task_id, **payload},
        )
