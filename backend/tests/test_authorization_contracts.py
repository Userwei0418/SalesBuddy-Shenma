from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.contracts.authorization import AccountAuthorizationSave, RoleSave, ScopedPermission


def test_role_template_and_personal_exception_are_independent_from_job_roles():
    role = RoleSave(name="产品售前协作", reason="需要看自己参与的商机", permissions=[
        ScopedPermission(permission="opportunity.read", scope="assigned"),
    ])
    assert role.permissions[0].scope == "assigned"
    account = AccountAuthorizationSave(roles=[], overrides=[
        dict(permission="opportunity.create", effect="allow", scope="teams", team_ids=[uuid4()]),
        dict(permission="opportunity.export", effect="deny"),
    ], version_no=0, reason="允许创建指定团队商机，禁止导出")
    assert len(account.overrides) == 2
    assert "business_role" not in account.model_dump()


@pytest.mark.parametrize("permission", [
    dict(permission="made.up", scope="workspace"),
    dict(permission="opportunity.create", scope="teams", team_ids=[]),
    dict(permission="opportunity.create", scope="self", team_ids=[str(uuid4())]),
    dict(permission="authorization.roles_manage", scope="self"),
    dict(permission="opportunity.create", scope="workspace", workspace_id=str(uuid4())),
])
def test_invalid_template_grants_are_rejected(permission):
    with pytest.raises(ValidationError):
        RoleSave(name="测试模板", reason="测试", permissions=[permission])


@pytest.mark.parametrize("overrides", [
    [dict(permission="opportunity.create", effect="allow")],
    [dict(permission="opportunity.create", effect="deny", scope="workspace")],
    [dict(permission="opportunity.create", effect="allow", scope="self"),
     dict(permission="opportunity.create", effect="deny")],
])
def test_ambiguous_personal_exceptions_are_rejected(overrides):
    with pytest.raises(ValidationError):
        AccountAuthorizationSave(roles=[], overrides=overrides, version_no=1, reason="测试")


def test_duplicate_role_and_team_bindings_are_rejected():
    role_id, team_id = uuid4(), uuid4()
    for roles in [
        [dict(role_id=role_id, scope="teams", team_ids=[team_id, team_id])],
        [dict(role_id=role_id, scope="self"), dict(role_id=role_id, scope="workspace")],
    ]:
        with pytest.raises(ValidationError):
            AccountAuthorizationSave(roles=roles, overrides=[], version_no=0, reason="测试")


def test_empty_labels_and_reasons_are_rejected():
    with pytest.raises(ValidationError):
        RoleSave(name="  ", reason="测试")
    with pytest.raises(ValidationError):
        AccountAuthorizationSave(version_no=0, reason=" ")


def test_role_save_canonicalizes_only_fixed_scope_inheritance_without_mutating_input():
    original = ScopedPermission(permission="access.console")
    role = RoleSave(name="后台入口", reason="确认固定范围", permissions=[
        original, dict(permission="visit.upload"), dict(permission="customer.read"),
    ])
    assert [p.scope for p in role.permissions] == ["workspace", "self", "inherit"]
    assert original.scope == "inherit"
    override = AccountAuthorizationSave(version_no=0, reason="明确禁用", overrides=[
        dict(permission="access.console", effect="deny"),
        dict(permission="visit.upload", effect="deny"),
    ])
    assert [p.scope for p in override.overrides] == ["inherit", "inherit"]


@pytest.mark.parametrize("permission,scope", [("access.console", "self"), ("visit.upload", "workspace")])
def test_fixed_role_scope_does_not_promote_explicit_invalid_input(permission, scope):
    with pytest.raises(ValidationError, match="不支持"):
        RoleSave(name="错误范围", reason="不能静默提升", permissions=[dict(permission=permission, scope=scope)])


def test_delegation_still_requires_explicit_scope_for_fixed_functions():
    from sales_backend.contracts.authorization import DelegationPermission
    with pytest.raises(ValidationError, match="明确数据范围"):
        DelegationPermission(permission="access.console")
