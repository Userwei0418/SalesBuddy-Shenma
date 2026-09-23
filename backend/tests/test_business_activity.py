from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from sales_backend.api.business_activity import Category
from sales_backend.domain.business_activity import CATEGORIES, present_activity


def record(code, body, **extra):
    return dict(
        action_code=code,
        payload=body,
        evidence_kind="saved_record",
        object_name="测试记录",
        execution_kind="human",
        **extra,
    )


def test_upload_is_not_archive_and_paths_never_exposed():
    item = present_activity(
        record(
            "material.upload",
            dict(
                filename="记录.md",
                file_size=20,
                processing_status="succeeded",
                file_path="/private/material",
                visits=[],
            ),
        )
    )
    assert item["result_label"] == "已提取文字"
    assert "file_path" not in str(item) and "/private" not in str(item)
    assert item["details"]["visits"] == []


def test_failed_processing_distinct_from_human_confirmation():
    item = present_activity(
        record(
            "material.process",
            {"entries": [{"after": {"status": "failed", "filename": "a.pdf", "error_message": "无可提取文字"}}]},
        )
    )
    assert item["result"] == "failed" and item["object_name"] == "a.pdf"
    assert "未代替人工确认" in item["details"]["note"]


def test_historical_import_is_labelled_without_inventing_uploader():
    item = present_activity(
        record(
            "customer.create",
            dict(data_source="excel_import", source_workbook="历史.xlsx", source_note="登记人不一定为上传人"),
        )
    )
    assert item["action_label"] == "导入客户资料" and item["execution_kind"] == "import"


def test_password_reset_shows_no_secret_or_raw_snapshot():
    item = present_activity(
        record(
            "account.change",
            {
                "entries": [
                    {
                        "type": "password_credential",
                        "operation": "platform.password_credential.update",
                        "fields": ["password_hash"],
                        "after": {"password_hash": "[REDACTED]"},
                    }
                ]
            },
            actor_id="a",
            object_id="b",
        )
    )
    assert item["action_label"] == "重置账号密码"
    assert "password_hash" not in str(item) and not item["details"]["changes"]


def test_display_equal_legacy_change_is_not_shown():
    item = present_activity(
        record(
            "opportunity.update",
            {
                "changes": [
                    {"label": "伙伴", "before": "未填写", "after": "未填写"},
                    {"label": "阶段", "before": "30%", "after": "50%"},
                ]
            },
        )
    )
    assert len(item["details"]["changes"]) == 1


def test_export_count_and_filter_evidence():
    item = present_activity(
        record(
            "data.export",
            {
                "entries": [
                    {
                        "result": "success",
                        "after": {
                            "path": "/api/v1/console/activities/export",
                            "export_count": 12,
                            "filters": {"q": "测试", "category": "material"},
                        },
                    }
                ]
            },
        )
    )
    assert item["object_name"] == "业务操作记录"
    assert "12" in item["summary"] and "录音与文件" in item["summary"]
    assert "已保存到" not in item["result_label"]


def test_all_advertised_business_categories_are_valid_request_filters():
    assert set(get_args(Category)) == set(CATEGORIES)
    adapter = TypeAdapter(Category)
    for category in CATEGORIES:
        assert adapter.validate_python(category) == category
    with pytest.raises(ValidationError):
        adapter.validate_python("unknown_business_category")


@pytest.mark.parametrize("versions, expected", [
    ({"rule_version": 2}, "2"),
    ({"version": 1}, "1"),
    ({"rule_version": 2, "version": 1}, "2"),
    ({"rule_version": None, "version": 1}, "1"),
])
def test_company_rule_version_uses_current_projection_and_preserves_historical_records(versions, expected):
    item = present_activity(record("company_rule.change", {
        "rule_name": "FDE自主拜访录入", "change_reason": "指定成员临时开启后恢复", **versions,
    }))
    assert {"label": "版本", "value": expected} in item["details"]["facts"]
    assert "版本：" + expected in item["summary"]
    assert item["category_label"] == "公司规则配置"
