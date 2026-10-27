"""Boundary tests for new intents and trusted-fact rules; not DB/RLS evidence."""

from itertools import product
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.contracts.opportunity_access import OpportunityCollaboratorsUpdate, OpportunityTransfer
from sales_backend.domain.authorization import Authorization, Grant, ObjectScope
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.opportunity_access import (
    OpportunityAccessError,
    OwnerTarget,
    plan_collaborators,
    plan_transfer,
    transfer_block_reason,
)
from sales_backend.domain.permission_defaults import ROLE_LABELS, default_permissions

SOURCE = ObjectScope("company", "owner", "east")
TARGET = OwnerTarget("company", "next", "north", True, True)


def auth(*grants, user="owner", denied=()):
    return Authorization.combine("company", user, grants, account_denies=denied)


def transfer(authorization, target=TARGET, **overrides):
    facts = dict(status="open", version_no=7, expected_version=7,
                 collaborator_ids=frozenset({"helper", "next"}), historical_attribution_safe=True)
    return plan_transfer(authorization, SOURCE, target, **(facts | overrides))


def test_edit_and_read_permissions_never_imply_transfer():
    with pytest.raises(OpportunityAccessError, match="转交授权"):
        transfer(auth(Grant("opportunity.update", "workspace"), Grant("opportunity.read", "workspace")))


@pytest.mark.parametrize("source_allowed,target_allowed", list(product((False, True), repeat=2)))
def test_team_grants_must_cover_both_sides(source_allowed, target_allowed):
    teams = {"unrelated"} | ({"east"} if source_allowed else set()) | ({"north"} if target_allowed else set())
    authorization = auth(Grant("opportunity.transfer", "teams", frozenset(teams)))
    if source_allowed and target_allowed:
        assert transfer(authorization).changed
    else:
        with pytest.raises(OpportunityAccessError) as error:
            transfer(authorization)
        assert error.value.code == "OPPORTUNITY_ACCESS_DENIED"


def test_explicit_deny_wins_over_workspace_and_team_grants():
    authorization = auth(Grant("opportunity.transfer", "workspace"),
                         Grant("opportunity.transfer", "teams", frozenset({"east", "north"})),
                         denied=("opportunity.transfer",))
    with pytest.raises(OpportunityAccessError):
        transfer(authorization)


@pytest.mark.parametrize("target", [
    OwnerTarget("foreign", "next", "north", True, True),
    OwnerTarget("company", "next", "north", False, True),
    OwnerTarget("company", "next", "north", True, False),
])
def test_workspace_grant_does_not_admit_foreign_or_inactive_destination(target):
    with pytest.raises(OpportunityAccessError):
        transfer(auth(Grant("opportunity.transfer", "workspace")), target)


@pytest.mark.parametrize("state", ["won", "lost", "cancelled", "archived"])
def test_first_slice_rejects_non_open_even_for_workspace_grant(state):
    with pytest.raises(OpportunityAccessError) as error:
        transfer(auth(Grant("opportunity.transfer", "workspace")), status=state)
    assert error.value.code == "TRANSFER_STATE_NOT_ALLOWED"


def test_missing_history_proof_blocks_real_change_but_not_identical_owner_team():
    authorization = auth(Grant("opportunity.transfer", "workspace"))
    with pytest.raises(OpportunityAccessError) as error:
        transfer(authorization, historical_attribution_safe=False)
    assert error.value.code == "TRANSFER_ATTRIBUTION_UNRESOLVED"
    unchanged = transfer(authorization, OwnerTarget("company", "owner", "east", True, True),
                         historical_attribution_safe=False)
    assert not unchanged.changed
    assert unchanged.collaborator_ids == {"helper", "next"}
    assert transfer(authorization, OwnerTarget("company", "owner", "north", True, True)).changed


def test_transfer_preserves_explicit_collaborators_without_adding_old_owner():
    plan = transfer(auth(Grant("opportunity.transfer", "workspace")))
    assert plan.collaborator_ids == {"helper"}
    assert plan.owner_user_id == "next" and plan.owner_team_id == "north"


@pytest.mark.parametrize("source", [ObjectScope("company", None, "east"), ObjectScope("company", "owner", None)])
def test_unresolved_source_attribution_is_not_repaired_by_transfer(source):
    authorization = auth(Grant("opportunity.transfer", "workspace"))
    assert transfer_block_reason(
        authorization, source, status="open", historical_attribution_safe=True
    ) == "TRANSFER_ATTRIBUTION_UNRESOLVED"
    with pytest.raises(OpportunityAccessError) as error:
        plan_transfer(authorization, source, TARGET, status="open", version_no=7, expected_version=7,
                      collaborator_ids=frozenset(), historical_attribution_safe=True)
    assert error.value.code == "TRANSFER_ATTRIBUTION_UNRESOLVED"


@pytest.mark.parametrize("proof", [False, None, 1, "true"])
def test_unproven_history_disables_read_capability_and_write(proof):
    authorization = auth(Grant("opportunity.transfer", "workspace"))
    assert transfer_block_reason(
        authorization, SOURCE, status="open", historical_attribution_safe=proof
    ) == "TRANSFER_ATTRIBUTION_UNRESOLVED"
    with pytest.raises(OpportunityAccessError):
        transfer(authorization, historical_attribution_safe=proof)


def test_read_capability_checks_permission_before_exposing_attribution_reason():
    assert transfer_block_reason(
        auth(), SOURCE, status="open", historical_attribution_safe=False
    ) == "OPPORTUNITY_ACCESS_DENIED"
    authorization = auth(Grant("opportunity.transfer", "workspace"))
    assert transfer_block_reason(
        authorization, SOURCE, status="won", historical_attribution_safe=True
    ) == "TRANSFER_STATE_NOT_ALLOWED"
    assert transfer_block_reason(authorization, SOURCE, status="open", historical_attribution_safe=True) is None


def test_access_projection_cannot_expand_minimal_customer_or_guess_capabilities():
    from sales_backend.contracts.opportunity_access import CustomerReference, OpportunityCapabilities, OpportunityOwner

    with pytest.raises(ValidationError):
        CustomerReference(id=UUID(int=1), name="客户", phone="private")
    with pytest.raises(ValidationError):
        OpportunityCapabilities(can_transfer=False, can_manage_collaborators=False, can_edit=False)
    with pytest.raises(ValidationError):
        OpportunityCapabilities(can_transfer="true", can_manage_collaborators=False, can_edit=False, can_follow_up=True)
    unresolved = OpportunityOwner(user_id=None, display_name=None, team_id=None, team_name=None)
    assert unresolved.model_dump()["user_id"] is None


@pytest.mark.parametrize("version", [None, True, 0, "7"])
def test_new_rules_never_skip_required_version(version):
    with pytest.raises(OpportunityAccessError) as error:
        transfer(auth(Grant("opportunity.transfer", "workspace")), expected_version=version)
    assert error.value.code == "INVALID_ACCESS_VERSION"


def test_stale_version_blocks_even_noop_transfer():
    with pytest.raises(VersionConflict):
        transfer(auth(Grant("opportunity.transfer", "workspace")),
                 OwnerTarget("company", "owner", "east", True, True), expected_version=6)


def collaborators(authorization, requested=frozenset({"helper"}), **overrides):
    facts = dict(version_no=7, expected_version=7, requested_ids=requested,
                 eligible_ids=frozenset({"helper"}), existing_ids=frozenset({"inactive"}))
    return plan_collaborators(authorization, SOURCE, **(facts | overrides))


def test_manager_or_collaborator_with_edit_grant_cannot_manage_owner_list():
    for user in ("manager", "helper"):
        with pytest.raises(OpportunityAccessError):
            collaborators(auth(Grant("opportunity.update", "workspace"), user=user))
    with pytest.raises(OpportunityAccessError):
        collaborators(auth())
    with pytest.raises(OpportunityAccessError):
        collaborators(auth(Grant("opportunity.update", "workspace"), denied=("opportunity.update",)))


def test_owner_can_remove_inactive_member_but_cannot_retain_or_add_invalid_member():
    authorization = auth(Grant("opportunity.update", "self"))
    assert collaborators(authorization) == (frozenset({"helper"}), True)
    assert collaborators(authorization, frozenset()) == (frozenset(), True)
    for invalid in ("inactive", "foreign", "owner"):
        with pytest.raises(OpportunityAccessError):
            collaborators(authorization, frozenset({invalid}))


def test_transfer_templates_inherit_appointment_without_role_name_scope_upgrade():
    for role in ROLE_LABELS:
        grants = [g for g in default_permissions(role) if g["permission"] == "opportunity.transfer"]
        if role in {"administrator", "operations", "manager", "supervisor"}:
            assert grants == [{"permission": "opportunity.transfer", "scope": "inherit", "team_ids": []}]
        else:
            assert grants == []


@pytest.mark.parametrize("scope", ["self", "assigned", "departments"])
def test_transfer_cannot_be_granted_an_unsupported_scope(scope):
    with pytest.raises(ValueError):
        Grant("opportunity.transfer", scope)


def test_request_contract_excludes_actor_and_business_field_injection():
    payload = dict(version_no=7, new_owner_user_ref_id=UUID(int=1),
                   new_owner_team_id=UUID(int=2), reason="  人员职责调整  ")
    assert OpportunityTransfer(**payload).reason == "人员职责调整"
    for field in ("workspace_id", "role", "customer_id", "amount", "owner_team_id", "historical_attribution_safe"):
        with pytest.raises(ValidationError):
            OpportunityTransfer(**payload, **{field: "injected"})
    for reason in ("", " \n ", 123):
        with pytest.raises(ValidationError):
            OpportunityTransfer(**(payload | {"reason": reason}))


def test_collaboration_set_empty_clear_order_and_duplicate_semantics():
    one, two = UUID(int=1), UUID(int=2)
    a = OpportunityCollaboratorsUpdate(version_no=7, member_ids=[two, one])
    b = OpportunityCollaboratorsUpdate(version_no=7, member_ids=[one, two])
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    assert OpportunityCollaboratorsUpdate(version_no=7, member_ids=[]).member_ids == []
    for value in (None, [one, one], [UUID(int=i) for i in range(31)]):
        with pytest.raises(ValidationError):
            OpportunityCollaboratorsUpdate(version_no=7, member_ids=value)
    for version in (None, True, 0, "7"):
        with pytest.raises(ValidationError):
            OpportunityCollaboratorsUpdate(version_no=version, member_ids=[])
