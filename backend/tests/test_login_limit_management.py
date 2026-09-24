from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.domain.agent import RoleCode
from sales_backend.domain.business_activity import present_activity
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.services.operations_accounts import OperationsAccountService
from sales_backend.services.password_auth import LoginThrottled, PasswordAuthService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed,seconds,scope,text",
    [
        ([False, True], [120], "account", "该账号"),
        ([True, False], [43], "network", "当前网络"),
        ([False, False], [23, 51], "account_network", "该账号和当前网络"),
    ],
)
async def test_login_reports_actual_blocked_window_before_credential_lookup(monkeypatch, allowed, seconds, scope, text):
    repository = SimpleNamespace(
        login_identifier=AsyncMock(return_value={"workspace":"isolated","account_code":"ISOLATED"}),
        consume_attempt=AsyncMock(side_effect=allowed),
        limit_status=AsyncMock(side_effect=[{"retry_after_seconds": n} for n in seconds]),
        candidate=AsyncMock(),
    )
    monkeypatch.setattr("sales_backend.services.password_auth.PasswordRepository", lambda: repository)

    @asynccontextmanager
    async def connection():
        yield object()

    service = object.__new__(PasswordAuthService)
    service.settings = SimpleNamespace(demo_workspace="isolated")
    service.database = SimpleNamespace(connection=connection)
    with pytest.raises(LoginThrottled) as caught:
        await service.login(account="isolated", password="unused-in-throttled-flow")
    assert caught.value.scope == scope and caught.value.retry_after == max(seconds)
    assert text in str(caught.value)
    repository.candidate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,version,reason,error",
    [
        (RoleCode.OPERATIONS, 3, "核实用户本人申请", PermissionError),
        (RoleCode.ADMINISTRATOR, 2, "核实用户本人申请", VersionConflict),
        (RoleCode.ADMINISTRATOR, 3, "   ", ValueError),
    ],
)
async def test_unlock_validates_target_role_version_and_reason_before_mutation(role, version, reason, error):
    service = OperationsAccountService()
    service.repository = SimpleNamespace(
        lock_workspace=AsyncMock(),
        member=AsyncMock(return_value={"roles": ["administrator"], "version_no": 3}),
        unlock_login=AsyncMock(),
    )
    actor = SimpleNamespace(role=role, workspace_id="workspace", user_id="operator", team_ids=())
    from tests.authorization_fixtures import configured_database
    permissions={"account.unlock": "workspace"}
    if role==RoleCode.ADMINISTRATOR:permissions["authorization.accounts_manage"]="workspace"
    connection=configured_database(actor, permissions).connection
    with pytest.raises(error):
        await service.unlock_login(connection, actor, "target", version, reason)
    service.repository.unlock_login.assert_not_awaited()


@pytest.mark.parametrize("with_password_change", [False, True])
def test_unlock_is_a_human_readable_business_event_without_exposing_throttle_keys(with_password_change):
    entries = [
        {
            "type": "user_ref",
            "operation": "account.login_unlock",
            "fields": ["login_locked"],
            "before": {"login_locked": True},
            "after": {"login_locked": False, "reason": "核对本人申请后恢复"},
        }
    ]
    if with_password_change:
        entries.append({"type": "password_credential", "operation": "platform.password_credential.update"})
    result = present_activity(
        {
            "action_code": "account.change",
            "payload": {"entries": entries},
            "actor_id": "operator",
            "object_id": "target",
            "evidence_kind": "row_audit",
        }
    )
    assert result["action_label"] == ("重置账号密码" if with_password_change else "解除账号登录限制")
    if not with_password_change:
        assert "核对本人申请" in result["summary"]
        assert "网络" in result["details"]["note"]
    assert "key_hash" not in str(result)
