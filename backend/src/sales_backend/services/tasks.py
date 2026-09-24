from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg

from sales_backend.domain.agent import ActorContext
from sales_backend.domain.concurrency import require_version
from sales_backend.domain.tasks import (
    NOTIFICATION_BODY_FALLBACK,
    TASK_RESPONSE_RULES,
    TaskConflict,
    TaskForbidden,
    TaskNotFound,
    task_association_kind,
)
from sales_backend.repositories.task_links import TaskLinkRepository
from sales_backend.repositories.task_mutations import TaskMutationRepository
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.authorization import require_permission
from sales_backend.domain.route_permissions import TASK_EVENTS
from sales_backend.domain.authorization import ObjectScope
from sales_backend.repositories.authorization import AuthorizationRepository


async def validate_task_links(connection, actor, customer_id=None, opportunity_id=None, association_kind=None):
    customer_id = str(customer_id).strip() if customer_id else None
    opportunity_id = str(opportunity_id).strip() if opportunity_id else None
    kind = task_association_kind(customer_id, opportunity_id, association_kind)
    if customer_id and not await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer_id):
        raise TaskNotFound("CUSTOMER_NOT_FOUND")
    if opportunity_id:
        opportunity = await connection.fetchrow(
            "SELECT id::text,customer_id::text FROM crm.opportunity WHERE id=$1::uuid AND deleted_at IS NULL",
            opportunity_id,
        )
        if not opportunity or opportunity["customer_id"] != customer_id:
            raise TaskConflict("商机不存在、无权关联或不属于当前客户")
        allowed = await TaskLinkRepository().allowed(connection, actor, opportunity_id)
        if not allowed:
            raise TaskForbidden("无权关联此商机，请选择本人或授权团队的商机")
    return kind


class TaskService:
    """Resolve recipients, authorize transitions and persist the task and reminders atomically."""

    async def create(
        self,
        connection,
        *,
        actor,
        description,
        due_at,
        priority_code,
        assignee_account_code=None,
        target_position=None,
        customer_id=None,
        opportunity_id=None,
        source_suggestion_id=None,
        association_kind=None,
    ):
        permission = "task.create_" + task_association_kind(customer_id, opportunity_id, association_kind)
        effective = await AuthorizationRepository().effective(connection)
        own_scope = ObjectScope(actor.workspace_id, actor.user_id,
            actor.team_ids[0] if actor.team_ids else None, frozenset({actor.user_id}))
        effective.require(permission, own_scope)
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=UTC)
        if due_at <= datetime.now(UTC):
            raise TaskConflict("TASK_DUE_AT_MUST_BE_FUTURE")
        customer_id = str(customer_id).strip() if customer_id else None
        opportunity_id = str(opportunity_id).strip() if opportunity_id else None
        association_kind = await validate_task_links(connection, actor, customer_id, opportunity_id, association_kind)
        targets = await TaskTargetRepository().resolve(
            connection, actor, account=assignee_account_code, position=target_position,
            opportunity_id=opportunity_id, customer_id=customer_id,
        )
        if any(target["user_id"] != actor.user_id for target in targets):
            effective.require("task.assign", own_scope)
        creator_name = await connection.fetchval(
            "SELECT display_name FROM platform.user_ref WHERE id=$1::uuid", actor.user_id
        )
        compact = " ".join(description.split())
        title = compact if len(compact) <= 42 else compact[:42] + "…"
        task_id = await connection.fetchval(
            """INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,creator_team_id,
              customer_id,priority_code,status,due_at,source_code,opportunity_id,target_position,source_suggestion_id,association_kind)
            VALUES($1::uuid,$2,$3,$4::uuid,$5::uuid,$6::uuid,$7,'pending_confirm',$8,
              CASE WHEN $11::uuid IS NULL THEN 'mini_program' ELSE 'business_advice' END,$9::uuid,$10,$11::uuid,$12)
            RETURNING id::text""",
            actor.workspace_id,
            title,
            description,
            actor.user_id,
            actor.team_ids[0] if actor.team_ids else None,
            customer_id,
            priority_code,
            due_at,
            opportunity_id,
            target_position,
            source_suggestion_id,
            association_kind,
        )
        for target in targets:
            if target_position:
                await connection.execute(
                    """INSERT INTO workflow.task_candidate(workspace_id,task_id,user_ref_id,team_id,role_code)
                    VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5)""",
                    actor.workspace_id,
                    task_id,
                    target["user_id"],
                    target["team_id"],
                    target["role_code"],
                )
            else:
                await TaskMutationRepository.assign_owner(connection, actor.workspace_id, task_id, target)
            await TaskMutationRepository.notify(
                connection,
                actor,
                task_id,
                target["user_id"],
                "task_assigned",
                f"{creator_name or '任务发起人'}向你下发新任务",
                title,
                {
                    "creator_user_ref_id": actor.user_id,
                    "creator_name": creator_name,
                    "target_position": target_position,
                    "assignee_name": target["display_name"],
                    "due_at": due_at.isoformat(),
                    "priority_code": priority_code,
                },
            )
        await TaskMutationRepository.event(
            connection,
            actor,
            task_id,
            "created",
            None,
            "pending_confirm",
            "岗位待办已发出，等待一人领取" if target_position else f"任务下发给{targets[0]['display_name']}",
            {"target_position": target_position, "recipient_ids": [t["user_id"] for t in targets]},
        )
        return await TaskRepository().detail(connection, task_id=task_id)

    async def _respond_position(self, connection, actor, task, event_type, note):
        task_id = task["id"]
        candidate = await TaskTargetRepository().current_candidate(connection, actor, task_id)
        if not candidate or candidate["decision"] != "pending":
            raise TaskForbidden("你不是当前岗位的有效待领取人")
        if task["status"] != "pending_confirm":
            raise TaskConflict("任务已被领取或结束，请刷新查看")
        comment = (note or "").strip()
        if event_type == "reject" and not comment:
            raise TaskConflict("REJECTION_COMMENT_REQUIRED")
        if event_type == "accept":
            next_status = "pending_execution"
        else:
            remaining = await connection.fetchval(
                """SELECT count(*) FROM workflow.v_task_action_recipient a WHERE a.task_id=$1::uuid
                  AND a.assignee_user_ref_id<>$2::uuid AND a.responsibility='candidate'""",
                task_id,
                actor.user_id,
            )
            next_status = "pending_confirm" if remaining else "cancelled"
        # Update while the caller is still a pending candidate; task row is already locked.
        await connection.execute(
            """UPDATE workflow.task SET status=$2,version_no=version_no+1,
              accepted_at=CASE WHEN $2='pending_execution' THEN clock_timestamp() ELSE accepted_at END,
              rejection_comment=CASE WHEN $2='cancelled' THEN $3 ELSE rejection_comment END WHERE id=$1::uuid""",
            task_id,
            next_status,
            comment,
        )
        await connection.execute(
            "UPDATE workflow.task_candidate SET decision=$3,decided_at=clock_timestamp(),decision_note=$4 "
            "WHERE task_id=$1::uuid AND user_ref_id=$2::uuid",
            task_id,
            actor.user_id,
            "claimed" if event_type == "accept" else "declined",
            comment,
        )
        if event_type == "accept":
            await TaskMutationRepository.assign_owner(
                connection,
                actor.workspace_id,
                task_id,
                {"user_id": actor.user_id, "team_id": candidate["team_id"], "role_code": candidate["role_code"]},
            )
            await connection.execute(
                "UPDATE workflow.task_assignee SET accepted_at=clock_timestamp() "
                "WHERE task_id=$1::uuid AND responsibility='owner'",
                task_id,
            )
        await TaskMutationRepository.event(
            connection,
            actor,
            task_id,
            event_type,
            "pending_confirm",
            next_status,
            comment or "已领取岗位任务",
            {"target_position": task["target_position"], "candidate_decision": event_type},
        )
        name = await connection.fetchval("SELECT display_name FROM platform.user_ref WHERE id=$1::uuid", actor.user_id)
        recipients = {task["creator_user_ref_id"]}
        if event_type == "accept":
            recipients.update(
                str(r["user_ref_id"])
                for r in await connection.fetch(
                    "SELECT user_ref_id FROM workflow.task_candidate WHERE task_id=$1::uuid", task_id
                )
            )
        for recipient in (recipients | {actor.user_id} if event_type == "accept" else recipients - {actor.user_id}):
            await TaskMutationRepository.notify(
                connection,
                actor,
                task_id,
                recipient,
                "task_claimed" if event_type == "accept" else "task_candidate_declined",
                "岗位任务已领取" if event_type == "accept" else "岗位任务接收反馈",
                f"{name}已领取：{task['title']}" if event_type == "accept" else f"{name}不领取：{comment}",
                {
                    "claimed_by_user_ref_id": actor.user_id if event_type == "accept" else None,
                    "status": next_status,
                    "target_position": task["target_position"],
                },
            )
        return await TaskRepository().detail(connection, task_id=task_id)

    async def apply_event(
        self,
        connection: asyncpg.Connection,
        *,
        actor: ActorContext,
        task_id: str,
        event_type: str,
        note: str | None,
        expected_version: int | None = None,
        assignee_account_code: str | None = None,
    ) -> dict[str, Any]:
        await require_permission(connection, TASK_EVENTS.get(event_type, ""), task_id=task_id)
        if event_type in {"cancel", "reassign"}:
            from sales_backend.services.task_coordination import coordinate_task
            return await coordinate_task(connection, actor=actor, task_id=task_id, event_type=event_type,
                                         note=note, expected_version=expected_version, account=assignee_account_code)
        if event_type in {"approve_completion", "reject_completion"}:
            from sales_backend.services.task_review import review_completion
            return await review_completion(connection, actor=actor, task_id=task_id,
                event_type=event_type, note=note, expected_version=expected_version)
        if event_type == "complete":
            return await self.complete(
                connection, actor=actor, task_id=task_id, note=note, expected_version=expected_version
            )
        rules = TASK_RESPONSE_RULES.get(event_type)
        if rules is None:
            raise TaskConflict("UNSUPPORTED_TASK_EVENT")
        task = await connection.fetchrow(
            """SELECT id::text, title, status, creator_user_ref_id::text, version_no, target_position
                 FROM workflow.task WHERE id = $1::uuid AND deleted_at IS NULL FOR UPDATE""",
            task_id,
        )
        if not task:
            raise TaskNotFound("TASK_NOT_FOUND")
        require_version(task["version_no"], expected_version)
        if task["target_position"]:
            return await self._respond_position(connection, actor, task, event_type, note)
        is_owner = await connection.fetchval(
            """SELECT EXISTS (SELECT 1 FROM workflow.task_assignee
                 WHERE task_id = $1::uuid AND assignee_user_ref_id = $2::uuid
                   AND responsibility = 'owner')""",
            task_id,
            actor.user_id,
        )
        if not is_owner:
            raise TaskForbidden("ONLY_TASK_OWNER_CAN_RESPOND")
        await self._require_current_owner_eligibility(connection, actor, task_id)
        if task["status"] != "pending_confirm":
            raise TaskConflict("TASK_ALREADY_RESPONDED")
        comment = (note or "").strip()
        if rules.requires_comment and not comment:
            raise TaskConflict("REJECTION_COMMENT_REQUIRED")
        to_status = rules.to_status
        await connection.execute(
            """UPDATE workflow.task
                  SET status = $2,
                      accepted_at = CASE WHEN $2 = 'pending_execution' THEN clock_timestamp() ELSE accepted_at END,
                      rejection_comment = CASE WHEN $2 = 'cancelled' THEN $3 ELSE rejection_comment END,
                      version_no = version_no + 1
                WHERE id = $1::uuid""",
            task_id,
            to_status,
            comment,
        )
        if rules.stamps_assignee:
            await connection.execute(
                """UPDATE workflow.task_assignee SET accepted_at = clock_timestamp()
                     WHERE task_id = $1::uuid AND assignee_user_ref_id = $2::uuid
                       AND responsibility = 'owner'""",
                task_id,
                actor.user_id,
            )
        await connection.execute(
            """INSERT INTO workflow.task_event (
                 workspace_id, task_id, event_type, from_status, to_status,
                 actor_user_ref_id, note, payload
               ) VALUES ($1::uuid, $2::uuid, $3, 'pending_confirm', $4,
                 $5::uuid, $6, '{}'::jsonb)""",
            actor.workspace_id,
            task_id,
            event_type,
            to_status,
            actor.user_id,
            comment or rules.event_note,
        )
        template = rules.notification_template
        body = f"{task['title']} · {comment or NOTIFICATION_BODY_FALLBACK}"
        await TaskMutationRepository.notify(
            connection,
            actor,
            task_id,
            task["creator_user_ref_id"],
            template,
            rules.notification_title,
            body,
            {"responded_by_user_ref_id": actor.user_id, "comment": comment,
             "event_version": task["version_no"] + 1},
        )
        if event_type == "accept" and actor.user_id != task["creator_user_ref_id"]:
            await TaskMutationRepository.notify(connection, actor, task_id, actor.user_id,
                template, "你已接受任务", body, {"event_version": task["version_no"] + 1})
        updated = await TaskRepository().detail(connection, task_id=task_id)
        if updated is None:
            raise TaskNotFound("TASK_NOT_FOUND")
        return updated

    async def complete(self, connection, *, actor, task_id, note, expected_version=None):
        from sales_backend.services.task_review import submit_completion
        return await submit_completion(connection, actor=actor, task_id=task_id,
                                       note=note, expected_version=expected_version)

    @staticmethod
    async def _require_current_owner_eligibility(connection, actor, task_id):
        if not await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=$1::uuid "
            "AND a.assignee_user_ref_id=$2::uuid AND a.responsibility='owner' "
            "AND security.task_recipient_eligible("
            "a.task_id,a.assignee_user_ref_id,a.assignee_role,a.assignee_team_id))",
            task_id, actor.user_id,
        ):
            raise TaskForbidden("当前账号或岗位资格已变化，任务待交接，请联系发起人或负责人")
