from uuid import uuid4
import pytest
from pydantic import ValidationError
from sales_backend.contracts.authorization import DelegationSave


def policy(**changes):
    return dict(
        version_no=0,
        reason="配置委派",
        recipient_ids=[str(uuid4())],
        permissions=[dict(permission="customer.read", scope="self")],
        **changes,
    )


def test_delegation_has_explicit_scope_and_unique_targets():
    good = DelegationSave(**policy())
    assert good.permissions[0].scope == "self"
    data = policy()
    data["recipient_ids"] *= 2
    with pytest.raises(ValidationError):
        DelegationSave(**data)
    data = policy()
    data["permissions"][0]["scope"] = "inherit"
    with pytest.raises(ValidationError):
        DelegationSave(**data)


@pytest.mark.parametrize(
    "code",
    [
        "authorization.roles_manage",
        "authorization.accounts_manage",
        "organization.manage",
        "account.create",
        "account.password_policy",
    ],
)
def test_company_control_cannot_be_redelegated(code):
    data = policy()
    data["permissions"] = [dict(permission=code, scope="workspace")]
    with pytest.raises(ValidationError):
        DelegationSave(**data)


def test_delegation_rejects_unknown_fields_timezone_less_expiry_and_blank_reason():
    for patch in [{"other_tenant": str(uuid4())}, {"expires_at": "2026-10-01T12:00:00"}, {"reason": " "}]:
        with pytest.raises(ValidationError):
            DelegationSave(**(policy() | patch))
