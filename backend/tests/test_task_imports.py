from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from sales_backend.contracts.task_imports import TaskImportConfirm, TaskImportDraft, TaskImportPreview
from sales_backend.domain.task_imports import (
    ImportLimitError,
    candidate_lines,
    confirmation_hash,
    materialize_items,
    similar,
    suggested_due,
)
from sales_backend.services.task_imports import TaskImportService, counters, editable, editable_presentation

PEOPLE = [dict(id="a", name="张三", account_code="ZHANGSAN"), dict(id="b", name="李四", account_code="LISI")]


def parse(text, people=PEOPLE):
    lines = candidate_lines(text, people)
    return materialize_items(lines, lines, people, date(2026, 9, 28))


def test_heading_owns_task_not_person_mentioned_in_description():
    items = parse("@张三：\n1. 联系李四核对方案(9.30)\n@李四：\n2. 请张三提供完整报价")
    assert [row["assignee_account_code"] for row in items] == ["ZHANGSAN", "LISI"]
    assert items[0]["due_at"] == "2026-09-30T23:59:00+08:00"
    assert items[1]["due_at"] is None
    assert items[0]["source_line"] == 2


def test_unknown_inline_heading_never_inherits_previous_person():
    items = parse("张三：\n完成测试方案\n王五：联系李四核对预算")
    assert items[1]["assignee_hint"] == "王五"
    assert items[1]["assignee_account_code"] is None


def test_duplicate_names_are_unresolved_and_no_customer_association_is_inferred():
    people = [*PEOPLE, dict(id="c", name="张三", account_code="ZHANGSAN2")]
    item = parse("张三：联系李四跟进商汤客户商机", people)[0]
    assert item["assignee_account_code"] is None
    assert item["association_kind"] == "daily" and item["customer_id"] is None and item["opportunity_id"] is None


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("今天提交", 28),
        ("明天提交", 29),
        ("周五提交", 2),
        ("本周五提交", 2),
        ("下周一提交", 5),
        ("下周天提交", 11),
        ("(9.28)提交", 28),
        ("9月30日提交", 30),
    ],
)
def test_dates_are_anchored_to_meeting_day(text, day):
    value, _ = suggested_due(text, date(2026, 9, 28))
    assert datetime.fromisoformat(value).day == day
    assert value.endswith("+08:00")


@pytest.mark.parametrize(
    "text",
    ["安排明日和韩总面试（9.28）", "明天提交（2026-09-28）", "下周一（9.28）完成核对", "9月28日（9.28）提交"],
)
def test_same_resolved_date_is_not_a_conflict(text):
    value, warning = suggested_due(text, date(2026, 9, 27))
    assert value == "2026-09-28T23:59:00+08:00"
    assert "23:59" in warning


@pytest.mark.parametrize(("text", "day"), [("明晚日会（9.28）", 28), ("明早提交方案", 28), ("今晚提交方案", 27)])
def test_daypart_has_a_date_but_does_not_invent_a_time(text, day):
    value, warning = suggested_due(text, date(2026, 9, 27))
    assert value == f"2026-09-{day}T23:59:00+08:00"
    assert "早晚时段" in warning and "未给具体时刻" in warning and "23:59" in warning


def test_matching_explicit_clock_is_preserved_without_a_default_time_warning():
    value, warning = suggested_due("明晚20:00日会（9.28 20:00）", date(2026, 9, 27))
    assert value == "2026-09-28T20:00:00+08:00" and warning is None


@pytest.mark.parametrize("text", ["明晚8:00提交方案", "明晚12:00提交方案", "明早18:00提交方案", "今晚00:30提交方案"])
def test_clock_cannot_contradict_daypart_or_silently_guess_a_12_hour_conversion(text):
    value, warning = suggested_due(text, date(2026, 9, 27))
    assert value is None and "早晚时段与具体时刻" in warning and "人工确认" in warning


def test_unambiguous_morning_clock_is_preserved():
    value, warning = suggested_due("明早08:00提交方案", date(2026, 9, 27))
    assert value == "2026-09-28T08:00:00+08:00" and warning is None


@pytest.mark.parametrize(
    "text", ["明日提交（9.29）", "明晚日会（9.29）", "今晚核对，明早提交", "明早核对，明晚提交"]
)
def test_different_resolved_dates_or_dayparts_still_require_review(text):
    value, warning = suggested_due(text, date(2026, 9, 27))
    assert value is None and "多个日期或时点" in warning


@pytest.mark.parametrize("text", ["目标Q3回款", "明确每个人1030、1130分别需要达成什么", "1030前提交", "1130提交"])
def test_quarters_and_compact_numbers_remain_manual_dates(text):
    value, warning = suggested_due(text, date(2026, 9, 27))
    assert value is None and "未给出明确截止日期" in warning


def test_invalid_missing_dates_and_long_text_are_not_invented_or_truncated():
    assert suggested_due("尽快处理", date(2026, 9, 28))[0] is None
    assert suggested_due("2026-02-30提交", date(2026, 9, 28))[0] is None
    text = "完整事实" * 150
    item = parse("张三：\n" + text)[0]
    assert item["description"] == text and len(text) > 500
    assert any("5–500" in warning for warning in item["warnings"])


@pytest.mark.parametrize("text", ["9月30日完成方案，10月8日发送报价", "今天核对明天提交", "9月30日09:00开会17:00提交"])
def test_ambiguous_source_dates_are_not_copied_to_each_ai_split(text):
    lines = candidate_lines("张三：\n" + text, PEOPLE)
    extracted = [{"source_line": 2, "description": action} for action in ("完成方案核对", "发送最终报价")]
    items = materialize_items(extracted, lines, PEOPLE, date(2026, 9, 28))
    assert all(item["due_at"] is None for item in items)
    assert all(any("多个日期" in warning for warning in item["warnings"]) for item in items)


@pytest.mark.parametrize("text", ["预算1.23万元", "版本1.2发布", "报价 9.30 万元"])
def test_decimal_or_version_is_not_a_date(text):
    assert suggested_due(text, date(2026, 9, 28))[0] is None


def test_batch_name_is_trimmed_and_whitespace_only_is_rejected_without_changing_source():
    source = " 张三：\n核对测试任务 "
    body = TaskImportPreview(source_text=source, meeting_date="2026-09-28", batch_name=" 周会 ")
    assert body.batch_name == "周会" and body.source_text == source
    with pytest.raises(ValidationError):
        TaskImportPreview(source_text=source, meeting_date="2026-09-28", batch_name="   ")


def test_more_than_100_candidates_is_rejected_not_truncated():
    with pytest.raises(ImportLimitError):
        candidate_lines("\n".join(f"{i}. 完整原文行动" for i in range(101)), PEOPLE)


def test_model_cannot_attach_a_task_to_an_invented_source_line():
    lines = candidate_lines("张三：\n跟进测试方案", PEOPLE)
    with pytest.raises(ValueError, match="原文"):
        materialize_items([{"source_line": 99, "description": "伪造行动"}], lines, PEOPLE, date(2026, 9, 28))


def test_confirm_allows_different_tasks_for_same_person_but_not_duplicate_item_id():
    base = dict(description="逐项人工核对内容", assignee_account_code="ZHANGSAN", due_at="2026-10-05T23:59:00+08:00")
    a, b = dict(base, item_id=uuid4()), dict(base, item_id=uuid4(), description="完成另一条不同事项")
    body = TaskImportConfirm(version_no=1, items=[a, b])
    assert len(body.items) == 2
    assert confirmation_hash(body.items) == confirmation_hash(list(reversed(body.items)))
    with pytest.raises(ValidationError):
        TaskImportConfirm(version_no=1, items=[a, a])
    with pytest.raises(ValidationError):
        TaskImportConfirm(version_no=1, items=[dict(a, due_at="2026-10-05T23:59:00")])


def test_draft_can_keep_unresolved_fields_but_confirmation_cannot():
    item = {"item_id": uuid4(), "description": "整理方案"}
    assert TaskImportDraft(version_no=1, items=[item]).items[0].due_at is None
    with pytest.raises(ValidationError):
        TaskImportConfirm(version_no=1, items=[item])


def test_counters_do_not_count_pending_review_as_complete_or_cancellation_in_denominator():
    past = datetime.now(UTC) - timedelta(days=1)
    tasks = [
        {"status": status, "due_at": past, "updated_at": datetime.now(UTC)}
        for status in ("completed", "cancelled", "pending_review", "pending_confirm")
    ]
    result = counters(tasks)
    assert result["completed"] == 1 and result["pending_review"] == 1
    assert result["completion_rate"] == 0.3333 and result["overdue"] == 2
    assert counters([tasks[1]])["completion_rate"] is None
    assert similar("跟进客户，提交方案。", "跟进客户提交方案")


@pytest.mark.asyncio
@pytest.mark.parametrize("as_datetime", [False, True])
async def test_draft_date_evidence_survives_editable_projection_and_timezone_conversion(monkeypatch, as_datetime):
    originals = parse("张三：\n9月30日提交完整方案")
    items = editable(originals)
    current = datetime.fromisoformat(items[0]["due_at"]).astimezone(UTC)
    items[0]["due_at"] = current if as_datetime else current.isoformat().replace("+00:00", "Z")
    monkeypatch.setattr("sales_backend.services.task_imports.annotate_duplicates", AsyncMock(return_value=items))
    result = await editable_presentation(None, items, source_items=originals, meeting_date="2026-09-28")
    assert result[0]["warnings"] == []
    assert result[0]["source_warnings"] == ["原文仅给日期，暂按北京时间23:59，请确认"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["尽快提交完整方案", "9月30日核对10月8日提交方案", "明早提交完整方案"])
async def test_manual_deadline_resolves_original_date_warnings(monkeypatch, text):
    originals = parse("王五：\n" + text)
    items = editable(originals)
    assert originals[0]["warnings"]
    items[0].update(assignee_account_code="ZHANGSAN", due_at="2026-10-02T17:00:00+08:00")
    monkeypatch.setattr("sales_backend.services.task_imports.annotate_duplicates", AsyncMock(return_value=items))
    result = await editable_presentation(None, items, source_items=originals, meeting_date=date(2026, 9, 28))
    assert result[0]["warnings"] == [] and result[0]["source_warnings"] == []


@pytest.mark.asyncio
async def test_unresolved_date_keeps_original_reason_and_current_missing_field_warning(monkeypatch):
    originals = parse("张三：\n9月30日核对10月8日提交方案")
    items = editable(originals)
    monkeypatch.setattr("sales_backend.services.task_imports.annotate_duplicates", AsyncMock(return_value=items))
    result = await editable_presentation(None, items, source_items=originals, meeting_date=date(2026, 9, 28))
    assert result[0]["warnings"] == ["请补充截止日期与时间"]
    assert result[0]["source_warnings"] == ["原文有多个日期或时点，请逐项确认截止时间"]


@pytest.mark.asyncio
async def test_old_blank_draft_gets_new_date_explanation_without_changing_its_saved_deadline(monkeypatch):
    originals = parse("张三：\n安排明日和韩总面试（9.28）")
    items = editable(originals)
    items[0]["due_at"] = None
    monkeypatch.setattr("sales_backend.services.task_imports.annotate_duplicates", AsyncMock(return_value=items))
    result = await editable_presentation(None, items, source_items=originals, meeting_date=date(2026, 9, 27))
    assert result[0]["due_at"] is None
    assert result[0]["warnings"] == ["请补充截止日期与时间"]
    assert result[0]["source_warnings"] == ["原文日期可识别为北京时间2026-09-28，请核对并补充截止时间"]


@pytest.mark.asyncio
async def test_detail_read_rejoins_source_evidence_and_clears_it_after_manual_draft_correction(monkeypatch):
    originals = parse("张三：\n明晚提交完整方案")
    batch = {
        "id": "batch",
        "status": "draft",
        "meeting_date": date(2026, 9, 28),
        "creator_user_ref_id": "creator",
        "items": originals,
        "editable_items": editable(originals),
    }
    assert "warnings" not in batch["editable_items"][0]
    service = TaskImportService(None, None)
    service.repo.own = AsyncMock(return_value=batch)
    service.repo.summaries = AsyncMock(return_value=[])
    service.repo.task_rows = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "sales_backend.services.task_imports.TaskImportRepository.existing_tasks", AsyncMock(return_value=[])
    )
    connection = SimpleNamespace(fetchval=AsyncMock(return_value="创建人"))
    result = await service.detail(connection, SimpleNamespace(), "batch")
    assert "早晚时段" in result["editable_items"][0]["source_warnings"][0]
    assert "source_warnings" not in batch["editable_items"][0]
    batch["editable_items"][0]["due_at"] = "2026-09-29T20:00:00+08:00"
    reread = await service.detail(connection, SimpleNamespace(), "batch")
    assert reread["editable_items"][0]["source_warnings"] == []
    assert reread["editable_items"][0]["warnings"] == []
    assert originals[0]["warnings"]


@pytest.mark.asyncio
async def test_sql_failure_preserves_original_exception_instead_of_cleanup_error(monkeypatch):
    identifier = uuid4()
    body = TaskImportConfirm(
        version_no=1,
        items=[
            dict(
                item_id=identifier,
                description="核对失败事务原始原因",
                assignee_account_code="ZHANGSAN",
                due_at="2032-01-02T09:00:00+08:00",
            )
        ],
    )
    service = TaskImportService(None, None)
    service.repo.own = AsyncMock(
        return_value={"status": "draft", "version_no": 1, "items": [{"item_id": str(identifier), "source_line": 1}]}
    )
    monkeypatch.setattr("sales_backend.services.task_imports.require_capability", AsyncMock())
    monkeypatch.setattr("sales_backend.services.task_imports.annotate_duplicates", AsyncMock(return_value=[]))
    broken = False

    async def create(*args, **kwargs):
        nonlocal broken
        broken = True
        raise asyncpg.InsufficientPrivilegeError("synthetic original permission failure")

    async def execute(*args):
        if broken:
            raise asyncpg.InFailedSQLTransactionError("cleanup must not hide original error")

    monkeypatch.setattr("sales_backend.services.task_imports.TaskService.create", create)
    connection = SimpleNamespace(fetchval=AsyncMock(return_value="task.create_daily"), execute=execute)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="original permission"):
        await service.confirm(connection, SimpleNamespace(), str(uuid4()), body)
