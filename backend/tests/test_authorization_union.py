import pytest

from sales_backend.domain.authorization import Authorization, Grant, ObjectScope
from sales_backend.domain.permission_catalog import CATALOG


def test_sales_and_fde_grants_are_combined_without_losing_either_role():
    permissions = Authorization.combine("company", "person", [
        Grant("opportunity.create", "self", source="sales"),
        Grant("profile.fde_read", "assigned", source="fde"),
    ])
    assert permissions.allows("opportunity.create")
    assert permissions.allows("profile.fde_read")
    assert not permissions.allows("account.reset_password")


def test_each_action_retains_its_own_scope_when_roles_are_combined():
    permissions = Authorization.combine("company", "person", [
        Grant("opportunity.update", "self", source="east-sales"),
        Grant("opportunity.update", "teams", frozenset({"north"}), "north-supervisor"),
        Grant("customer.reference", "workspace", source="company-directory"),
    ])
    assert permissions.allows("opportunity.update", ObjectScope("company", "person", "east"))
    assert permissions.allows("opportunity.update", ObjectScope("company", "other", "north"))
    assert not permissions.allows("opportunity.update", ObjectScope("company", "other", "east"))
    assert not permissions.allows("opportunity.update", ObjectScope("company", "other", "west"))
    assert permissions.allows("customer.reference", ObjectScope("company", "other", "west"))


def test_extra_permission_can_be_granted_without_a_sales_appointment():
    permissions = Authorization.combine("company", "fde-person", [
        Grant("profile.fde_read", "self", source="fde"),
    ], [Grant("opportunity.create", "teams", frozenset({"product"}), "account-override")])
    assert permissions.allows("opportunity.create", ObjectScope("company", "fde-person", "product"))
    assert not permissions.allows("opportunity.create", ObjectScope("company", "fde-person", "other"))
    assert permissions.for_permission("opportunity.create")[0].source == "account-override"


def test_personal_deny_beats_all_role_and_personal_grants():
    permissions = Authorization.combine("company", "person", [
        Grant("opportunity.create", "workspace", source="manager"),
        Grant("opportunity.update", "workspace", source="manager"),
    ], [Grant("opportunity.create", "self", source="override")], ["opportunity.create"])
    assert not permissions.allows("opportunity.create")
    assert permissions.allows("opportunity.update")


def test_unchecked_permission_in_one_role_does_not_cancel_another_role_grant():
    permissions = Authorization.combine("company", "person", [Grant("opportunity.create", "self")])
    assert permissions.allows("opportunity.create")


@pytest.mark.parametrize("scope,teams", [("workspace", frozenset()), ("teams", frozenset({"team"})),
                                        ("self", frozenset()), ("assigned", frozenset())])
def test_no_scope_can_cross_company(scope, teams):
    permissions = Authorization.combine("company", "person", [Grant("opportunity.read", scope, teams)])
    assert not permissions.allows("opportunity.read", ObjectScope("other-company", "person", "team", frozenset({"person"})))


def test_fde_assignment_does_not_implicitly_grant_team_access():
    permissions = Authorization.combine("company", "fde", [Grant("opportunity.read", "assigned")])
    assert permissions.allows("opportunity.read", ObjectScope("company", "sales", "team", frozenset({"fde"})))
    assert not permissions.allows("opportunity.read", ObjectScope("company", "sales", "team"))


@pytest.mark.parametrize("permission,scope,teams", [
    ("unknown.operation", "workspace", frozenset()),
    ("opportunity.create", "all_companies", frozenset()),
    ("opportunity.create", "teams", frozenset()),
    ("opportunity.create", "self", frozenset({"team"})),
    ("authorization.roles_manage", "self", frozenset()),
    ("notification.read", "workspace", frozenset()),
])
def test_unsupported_or_ambiguous_grant_fails_closed(permission, scope, teams):
    with pytest.raises(ValueError):
        Grant(permission, scope, teams)


def test_unknown_permission_is_not_allowed_and_configuration_is_rejected():
    permissions = Authorization.combine("company", "person", [])
    assert not permissions.allows("unknown")
    with pytest.raises(ValueError):
        Authorization.combine("company", "person", [], account_denies=["unknown"])
    with pytest.raises(PermissionError):
        permissions.require("opportunity.create")


def test_catalog_separates_create_edit_export_and_administrative_authorization():
    expected = {"opportunity.create", "opportunity.update", "opportunity.export", "authorization.roles_manage",
                "visit.download_original", "customer.reference", "customer.read", "task.create_daily", "task.create_customer"}
    assert expected <= CATALOG.keys()
    assert len(CATALOG) == len(set(CATALOG))
