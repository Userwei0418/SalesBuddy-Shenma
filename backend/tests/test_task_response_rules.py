from sales_backend.domain.tasks import TASK_RESPONSE_RULES


def test_only_accept_and_reject_are_responses() -> None:
    assert set(TASK_RESPONSE_RULES) == {"accept", "reject"}


def test_accepting_moves_the_task_into_execution_and_stamps_the_assignee() -> None:
    rules = TASK_RESPONSE_RULES["accept"]

    assert rules.to_status == "pending_execution"
    assert rules.stamps_assignee is True
    assert rules.requires_comment is False
    assert rules.notification_template == "task_accepted"
    assert rules.notification_title == "任务已被接受"
    assert rules.event_note == "已接受任务"


def test_rejecting_cancels_the_task_and_demands_a_reason() -> None:
    rules = TASK_RESPONSE_RULES["reject"]

    assert rules.to_status == "cancelled"
    assert rules.requires_comment is True
    assert rules.stamps_assignee is False, "拒绝不应写 accepted_at"
    assert rules.notification_template == "task_rejected"
    assert rules.notification_title == "任务已被拒绝"
    assert rules.event_note == "已拒绝任务"


def test_the_two_responses_never_share_a_status_or_template() -> None:
    accept = TASK_RESPONSE_RULES["accept"]
    reject = TASK_RESPONSE_RULES["reject"]

    assert accept.to_status != reject.to_status
    assert accept.notification_template != reject.notification_template
    assert accept.notification_title != reject.notification_title
