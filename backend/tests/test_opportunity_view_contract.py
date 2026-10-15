"""Current v2 view contract rejects identities/SQL while preserving typed settings.

The old Shenma v1 persisted rows are covered by test_shenma_whole_contract;
these are the actual classes wired into the upgraded opportunity-views router.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.api.opportunity_views import OpportunityWorkspaceCreate, OpportunityWorkspaceUpdate
from sales_backend.contracts.opportunity_workspace import OpportunityWorkspaceConfig


@pytest.mark.parametrize(
    "payload",
    [
        {"config": {"columns": [{"key": "internal_secret"}]}},
        {"config": {"columns": []}},
        {"config": {"sql": "SELECT * FROM crm.customer"}},
        {"config": {"scope": {"kind": "workspace"}}},
        {"config": {"scope": {"kind": "self", "selected_owner_ids": ["not-a-uuid"]}}},
        {"config": {"schema_version": 3}},
        {"config": {"sorts": [{"field": "amount DESC; DROP TABLE crm.customer", "direction": "desc"}]}},
        {"config": {"group_by": "arbitrary_field"}},
        {"visibility": "team"},
        {"visibility": "private", "team_id": str(uuid4())},
        {"workspace_id": str(uuid4())},
        {"owner_user_ref_id": str(uuid4())},
        {"name": " "},
    ],
)
def test_reject_unsupported_or_identity_overriding_settings(payload):
    with pytest.raises(ValidationError):
        OpportunityWorkspaceCreate.model_validate({"name": "我的视图", **payload})


def test_current_api_preserves_typed_saved_config_and_default():
    default = OpportunityWorkspaceConfig()
    assert default.schema_version == 2
    assert default.columns[0].key == "name" and default.scope.kind == "authorized"
    saved = OpportunityWorkspaceCreate(
        name=" 优先项目 ",
        config={
            "columns": [{"key": "name"}, {"key": "owner"}, {"key": "stage"}],
            "q": "重点",
            "scope": {"kind": "self"},
            "sorts": [{"field": "expected_close_date", "direction": "asc"}],
            "group_by": "stage",
        },
    )
    assert saved.name == "优先项目" and saved.config.q == "重点"
    assert saved.config.scope.kind == "self" and saved.config.group_by == "stage"
    assert [column.key for column in saved.config.columns] == ["name", "owner", "stage"]
    assert saved.config.sorts[0].field == "expected_close_date"


def test_current_update_requires_explicit_version_and_preserves_config():
    with pytest.raises(ValidationError):
        OpportunityWorkspaceUpdate(name="新名称")
    with pytest.raises(ValidationError):
        OpportunityWorkspaceUpdate(name="新名称", version_no=True)
    with pytest.raises(ValidationError):
        OpportunityWorkspaceUpdate(name="新名称", version_no=1, config=None)
    current = OpportunityWorkspaceConfig()
    renamed = OpportunityWorkspaceUpdate(name="新名称", version_no=1, config=current)
    assert renamed.config == current
