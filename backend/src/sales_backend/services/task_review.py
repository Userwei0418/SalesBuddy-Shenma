"""Owner submits evidence; only the original creator can close another person's task."""
from sales_backend.domain.concurrency import require_version
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, TaskNotFound
from sales_backend.repositories.task_mutations import TaskMutationRepository as Mutations
from sales_backend.repositories.tasks import TaskRepository


async def _locked(connection, task_id, expected_version):
    task = await connection.fetchrow(
        "SELECT id::text,title,status,creator_user_ref_id::text,version_no,completion_note "
        "FROM workflow.task WHERE id=$1::uuid AND deleted_at IS NULL FOR UPDATE", task_id)
    if not task:
        raise TaskNotFound("TASK_NOT_FOUND")
    require_version(task["version_no"], expected_version)
    return task


async def _notify(connection, actor, task, template, title, note, *, creator_only=False, skip_actor=False):
    recipients = {task["creator_user_ref_id"]}
    if not creator_only:
        rows = await connection.fetch(
            "SELECT assignee_user_ref_id::text AS user_id FROM workflow.task_assignee WHERE task_id=$1::uuid "
            "UNION SELECT user_ref_id::text FROM workflow.task_candidate WHERE task_id=$1::uuid", task["id"])
        recipients.update(row["user_id"] for row in rows)
    for recipient in recipients - ({actor.user_id} if skip_actor else set()):
        await Mutations.notify(connection, actor, task["id"], recipient, template, title,
            f"{task['title']} · {note}", {"event_version": task["version_no"] + 1, "note": note})


async def submit_completion(connection, *, actor, task_id, note, expected_version=None):
    from sales_backend.services.tasks import TaskService
    task = await _locked(connection, task_id, expected_version)
    owner = await connection.fetchval(
        "SELECT EXISTS(SELECT 1 FROM workflow.task_assignee WHERE task_id=$1::uuid "
        "AND assignee_user_ref_id=$2::uuid AND responsibility='owner')", task_id, actor.user_id)
    if not owner:
        raise TaskForbidden("ONLY_TASK_OWNER_CAN_COMPLETE")
    await TaskService._require_current_owner_eligibility(connection, actor, task_id)
    if task["status"] not in {"pending_execution", "in_progress"}:
        raise TaskConflict("任务当前不能提交完成，请刷新查看")
    comment = (note or "").strip()
    if not comment:
        raise TaskConflict("请填写完成说明")
    self_task = task["creator_user_ref_id"] == actor.user_id
    status = "completed" if self_task else "pending_review"
    await connection.execute(
        "UPDATE workflow.task SET status=$2,completion_note=$3,completion_submitted_at=clock_timestamp(),"
        "completion_review_note=NULL,verification_status=$4,"
        "completed_at=CASE WHEN $2='completed' THEN clock_timestamp() ELSE NULL END,"
        "version_no=version_no+1 WHERE id=$1::uuid",
        task_id, status, comment, "not_required" if self_task else "pending")
    await Mutations.event(connection, actor, task_id, "complete" if self_task else "submit_completion",
        task["status"], status, comment, {"self_assigned": self_task})
    await _notify(connection, actor, task,
        "task_completed" if self_task else "task_completion_submitted",
        "任务已完成" if self_task else "任务待你确认完成", comment, creator_only=not self_task, skip_actor=self_task)
    return await TaskRepository().detail(connection, task_id=task_id)


async def review_completion(connection, *, actor, task_id, event_type, note, expected_version):
    if expected_version is None:
        raise TaskConflict("验收须提供当前版本，请刷新任务")
    task = await _locked(connection, task_id, expected_version)
    if task["creator_user_ref_id"] != actor.user_id:
        raise TaskForbidden("只有任务发起人可以验收")
    if task["status"] != "pending_review":
        raise TaskConflict("本次完成申请已处理或任务不在待确认状态")
    approved = event_type == "approve_completion"
    comment = (note or "").strip()
    if not approved and not comment:
        raise TaskConflict("请填写驳回原因")
    status = "completed" if approved else "in_progress"
    await connection.execute(
        "UPDATE workflow.task SET status=$2,completion_review_note=$3,verification_status=$4,"
        "completed_at=CASE WHEN $2='completed' THEN clock_timestamp() ELSE NULL END,"
        "version_no=version_no+1 WHERE id=$1::uuid",
        task_id, status, comment, "passed" if approved else "rejected")
    await Mutations.event(connection, actor, task_id, event_type, "pending_review", status,
        comment or "发起人确认完成", {"completion_note": task["completion_note"]})
    await _notify(connection, actor, task,
        "task_completed" if approved else "task_completion_rejected",
        "发起人已确认任务完成" if approved else "完成申请被驳回，请继续处理",
        comment or "发起人确认完成")
    return await TaskRepository().detail(connection, task_id=task_id)
