"""Reject unsupported/internal-only view features before persisting settings."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.opportunity_views import (
    DEFAULT_COLUMNS,
    OpportunityViewConfig,
    OpportunityViewCreate,
    OpportunityViewUpdate,
)


@pytest.mark.parametrize(
    "payload",
    [
        {"config": {"columns": ["internal_secret"]}},
        {"config": {"columns": []}},
        {"config": {"sql": "SELECT * FROM crm.customer"}},
        {"config": {"filters": {"activity_window": "week"}}},
        {"config": {"filters": {"owner_id": "not-a-uuid"}}},
        {"config": {"filters": {"quarters": [4]}}},
        {"config": {"filters": {"year": 2026, "quarters": [4], "close_period": "month"}}},
        {"config": {"schema_version": 2}},
        {"config": {"order": "amount DESC; DROP TABLE crm.customer"}},
        {"config": {"group_by": "arbitrary_field"}},
        {"visibility": "team", "team_id": str(uuid4())},
        {"workspace_id": str(uuid4())},
        {"owner_user_ref_id": str(uuid4())},
        {"name": " "},
    ],
)
def test_reject_unsupported_or_identity_overriding_settings(payload):
    with pytest.raises(ValidationError):
        OpportunityViewCreate.model_validate({"name": "我的视图", **payload})


def test_safe_default_and_stable_values():
    config = OpportunityViewConfig()
    assert config.columns == DEFAULT_COLUMNS
    assert config.filters.scope is None and config.filters.owner_id is None
    assert config.filters.quarters == [] and config.order == "quarter_stage" and config.group_by == "quarter"
    owner = uuid4()
    saved = OpportunityViewCreate(
        name=" 优先项目 ",
        config={
            "columns": ["owner", "owner", "stage"],
            "filters": {"owner_id": str(owner), "q": " 重点 ", "year": 2026, "quarters": [4, 2, 4]},
            "order": "close_date",
            "group_by": "none",
        },
    )
    assert saved.name == "优先项目" and saved.config.columns == ["name", "owner", "stage"]
    assert saved.config.filters.owner_id == owner and saved.config.filters.quarters == [2, 4]
    assert saved.config.filters.q == "重点"


def test_rename_omits_config_and_rejects_null_or_missing_version():
    assert OpportunityViewUpdate(name="新名称", version_no=1).config is None
    with pytest.raises(ValidationError):
        OpportunityViewUpdate(name="新名称", version_no=1, config=None)
    with pytest.raises(ValidationError):
        OpportunityViewUpdate(name="新名称")
