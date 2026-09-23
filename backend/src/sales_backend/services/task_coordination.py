"""Explicit task handover preserves the task, past assignees and human decision history."""

from sales_backend.domain.concurrency import require_version
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, TaskNotFound
from sales_backend.repositories.task_mutations import TaskMutationRepository
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.tasks import TaskRepository


async def coordinate_task(connection, *, actor, task_id, event_type, note, expected_version, account=None):

    if not (note or "").strip() or expected_version is None:
        raise TaskConflict("任务转交或取消需要原因及当前版本")
    task = await connection.fetchrow(
        "SELECT id::text,title,status,version_no,creator_user_ref_id::text,customer_id::text,opportunity_id::text "
        "FROM workflow.task WHERE id=$1::uuid AND deleted_at IS NULL FOR UPDATE",
        task_id,
    )
    if not task:
        raise TaskNotFound("TASK_NOT_FOUND")
    if not await connection.fetchval("SELECT security.fde_can_coordinate_task($1::uuid)", task_id):
        raise TaskForbidden("只有原发起人或有权负责人可以转交、取消此任务")
    require_version(task["version_no"], expected_version)
    if task["status"] in {"completed", "cancelled"}:
        raise TaskConflict("已结束任务不能转交或取消")
    if task["status"] == "pending_review":
        raise TaskConflict("请先验收或驳回本次完成申请，再转交或取消任务")
    old = await connection.fetch(
        "SELECT assignee_user_ref_id::text AS user_id,assignee_role AS role,assignee_team_id::text AS team_id "
        "FROM workflow.task_assignee WHERE task_id=$1::uuid AND responsibility='owner'",
        task_id,
    )
    target = None
    if event_type == "reassign":
        targets = await TaskTargetRepository().resolve(
            connection, actor, account=account, opportunity_id=task["opportunity_id"], customer_id=task["customer_id"]
        )
        if not targets:
            raise TaskForbidden("新接收人没有该项目资格")
        target = targets[0]
        if any(row["user_id"] == target["user_id"] for row in old):
            raise TaskConflict("请选择不同的有效接收人")
    status = "pending_confirm" if target else "cancelled"
    # Replace within the same transaction. Inserting first keeps the department's
    # coordination authorization valid until the old owner is removed.
    if target:
        await TaskMutationRepository.assign_owner(connection, actor.workspace_id, task_id, target)
        await connection.execute(
            "DELETE FROM workflow.task_assignee WHERE task_id=$1::uuid AND responsibility='owner' "
            "AND assignee_user_ref_id<>$2::uuid",
            task_id,
            target["user_id"],
        )
    # Cancellation retains its historical owner; cancelled tasks have no action recipient.
    await connection.execute(
        "UPDATE workflow.task_candidate SET decision='declined',decided_at=clock_timestamp(),decision_note=$2 "
        "WHERE task_id=$1::uuid AND decision='pending'",
        task_id,
        note.strip(),
    )
    await connection.execute(
        "UPDATE workflow.task SET status=$2,target_position=NULL,accepted_at=NULL,rejection_comment=$3,"
        "version_no=version_no+1 WHERE id=$1::uuid",
        task_id,
        status,
        note.strip() if not target else None,
    )
    await TaskMutationRepository.event(
        connection,
        actor,
        task_id,
        event_type,
        task["status"],
        status,
        note.strip(),
        {"previous_owners": [dict(r) for r in old], "new_owner": target},
    )
    recipients = {r["user_id"] for r in old} | {task["creator_user_ref_id"]}
    if target:
        recipients.add(target["user_id"])
    for recipient in recipients - {actor.user_id}:
        await TaskMutationRepository.notify(
            connection,
            actor,
            task_id,
            recipient,
            "task_reassigned" if target else "task_cancelled",
            "任务已转交" if target else "任务已取消",
            task["title"],
            {
                "status": status,
                "reason": note.strip(),
                "actor_user_ref_id": actor.user_id,
                "new_owner": target,
                "event_version": expected_version + 1,
            },
        )
    return await TaskRepository().detail(connection, task_id=task_id)
