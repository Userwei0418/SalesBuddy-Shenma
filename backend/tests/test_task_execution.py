from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.contracts.task_execution import TaskFeedback, TaskHandoverResponse, TaskItemProgress, TaskPause
from sales_backend.domain.task_execution import ChecklistFact, checklist_progress

VERSIONS = dict(version_no=9, requirements_version=2, responsibility_version=3)


def fact(ident, state="completed", **values):
    return ChecklistFact(ident, state, **({"completion_note": "已交付成果"} | values))


def test_missing_checklist_is_unknown_but_valid_uncompleted_list_is_zero():
    missing = checklist_progress(1, ())
    assert (missing.completed, missing.total, missing.percent, missing.basis) == (0, 0, None, "missing_checklist")
    known = checklist_progress(2, (fact("one", "not_started"),))
    assert (known.completed, known.total, known.percent, known.basis) == (0, 1, 0, "checklist")


def test_equal_weights_floor_percentage_and_no_partial_credit_for_in_progress():
    progress = checklist_progress(2, (fact("a"), fact("b", "in_progress"), fact("c", "rework")))
    assert (progress.completed, progress.total, progress.percent) == (1, 3, 33)
    complete = checklist_progress(2, (fact("a"), fact("b"), fact("c")))
    assert complete.percent == 100
    assert "status" not in complete.model_dump()  # No implicit task closure or KR update.


def test_requirement_revalidation_withholds_current_percentage_and_old_facts_stay_immutable():
    prior = fact("a")
    old_progress = checklist_progress(2, (prior, fact("b")))
    current = checklist_progress(3, (fact("a", requires_revalidation=True), fact("b"), fact("c", "not_started")))
    assert (current.completed, current.total, current.percent, current.basis) == (1, 3, None, "requires_revalidation")
    assert old_progress.percent == 100 and not prior.requires_revalidation


def test_same_text_different_items_counts_separately_but_duplicate_identity_fails():
    assert checklist_progress(1, (fact("a"), fact("b"))).completed == 2
    with pytest.raises(ValueError, match="重复"):
        checklist_progress(1, (fact("a"), fact("a")))
    with pytest.raises(ValueError, match="依据"):
        checklist_progress(1, (fact("a", completion_note=" "),))


@pytest.mark.parametrize("field", ["version_no", "requirements_version", "responsibility_version"])
@pytest.mark.parametrize("value", [None, 0, True, "9"])
def test_each_command_checks_all_three_strict_versions(field, value):
    with pytest.raises(ValidationError):
        TaskFeedback(**(VERSIONS | {field: value}), text="现场补充")


def test_progress_command_cannot_set_percentage_okr_or_change_requirements():
    valid = VERSIONS | dict(item_id=UUID(int=1), definition_version=2, state="completed", note=" 已交付 ")
    assert TaskItemProgress(**valid).note == "已交付"
    for injected in ("percent", "requirements", "owner", "kr_id", "actual_value"):
        with pytest.raises(ValidationError):
            TaskItemProgress(**valid, **{injected: 100})
    with pytest.raises(ValidationError):
        TaskItemProgress(**(valid | {"note": " \n "}))


def test_feedback_does_not_contain_progress_and_correction_names_original_event():
    feedback = TaskFeedback(**VERSIONS, text="  更正上一条说明  ", corrects_event_id=UUID(int=1))
    assert feedback.text == "更正上一条说明"
    with pytest.raises(ValidationError):
        TaskFeedback(**VERSIONS, text="说明", completed=True)


def test_pause_needs_zoned_recheck_and_cannot_change_deadline():
    valid = VERSIONS | dict(reason=" 等待外部输入 ", recheck_at="2026-10-20T10:00:00+08:00")
    assert TaskPause(**valid).reason == "等待外部输入"
    with pytest.raises(ValidationError):
        TaskPause(**(valid | {"recheck_at": "2026-10-20T10:00:00"}))
    with pytest.raises(ValidationError):
        TaskPause(**valid, due_at="2026-10-21T10:00:00+08:00")


def test_handover_response_is_bound_to_record_and_rejection_reason():
    with pytest.raises(ValidationError):
        TaskHandoverResponse(**VERSIONS, decision="accept")
    with pytest.raises(ValidationError):
        TaskHandoverResponse(**VERSIONS, handover_id=UUID(int=1), decision="reject", reason=" ")
    accepted = TaskHandoverResponse(**VERSIONS, handover_id=UUID(int=1), decision="accept")
    assert accepted.decision == "accept"
