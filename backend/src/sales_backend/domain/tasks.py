from dataclasses import dataclass


class TaskNotFound(LookupError):
    pass


class TaskForbidden(PermissionError):
    pass


class TaskConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TaskResponseRules:
    """接收方回应待确认任务时，两种回应之间的全部差异。"""

    to_status: str
    event_note: str
    notification_template: str
    notification_title: str
    requires_comment: bool
    stamps_assignee: bool


TASK_RESPONSE_RULES = {
    "accept": TaskResponseRules(
        to_status="pending_execution",
        event_note="已接受任务",
        notification_template="task_accepted",
        notification_title="任务已被接受",
        requires_comment=False,
        stamps_assignee=True,
    ),
    "reject": TaskResponseRules(
        to_status="cancelled",
        event_note="已拒绝任务",
        notification_template="task_rejected",
        notification_title="任务已被拒绝",
        requires_comment=True,
        stamps_assignee=False,
    ),
}
NOTIFICATION_BODY_FALLBACK = "接收方已确认接受"


def task_association_kind(customer_id=None, opportunity_id=None, association_kind=None):
    """Only new tasks use this rule; historical records are not reclassified on events."""
    inferred = "customer" if customer_id or opportunity_id else "daily"
    if association_kind is not None and association_kind != inferred:
        raise TaskConflict("日常任务不能关联客户或商机；客户任务必须关联客户及商机")
    if inferred == "customer" and not (customer_id and opportunity_id):
        raise TaskConflict("客户任务必须同时选择客户和该客户下的商机")
    return inferred
