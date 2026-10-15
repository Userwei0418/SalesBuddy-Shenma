import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sales_backend.domain.agent import RoleCode
from tests.test_fde_identity_capabilities import fde

spec = importlib.util.spec_from_file_location(
    "provision_fde", Path(__file__).parents[1] / "scripts/provision_fde_department.py"
)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


PARENT = {"id": str(uuid4()), "code": "ALL", "name": "全部团队", "status": "active", "parent_team_id": None}


def manifest():
    return script.Manifest.model_validate(
        {
            "department": {"code": "FDE", "name": "FDE部门"},
            "accounts": [
                {"account_code": "FDEL001", "display_name": "李鹏程", "role": "fde_lead"},
                {"account_code": "FDE001", "display_name": "周玮", "role": "fde"},
            ],
        }
    )


@pytest.mark.asyncio
async def test_plan_has_no_writes_or_password_requirement():
    repo, service, connection = AsyncMock(), AsyncMock(), AsyncMock()
    repo.organization.return_value = {"departments": [PARENT], "accounts": []}
    result = await script.provision(
        connection, fde(RoleCode.ADMINISTRATOR), manifest(), "", repository=repo, service=service
    )
    assert not result["applied"] and result["department"]["action"] == "create"
    repo.save_department.assert_not_awaited()
    service.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_provision_reuses_service_and_exact_existing_members_without_password_reset():
    repo, service, connection = AsyncMock(), AsyncMock(), AsyncMock()
    team = {"id": str(uuid4()), "code": "FDE", "name": "FDE部门", "status": "active", "parent_team_id": PARENT["id"]}
    lead = {
        "id": str(uuid4()),
        "account_code": "FDEL001",
        "display_name": "李鹏程",
        "roles": ["fde_lead"],
        "status": "active",
        "team_id": team["id"],
        "has_password": True,
    }
    repo.organization.return_value = {"departments": [PARENT, team], "accounts": [lead]}
    service.create.return_value = {"id": str(uuid4()), "must_change_password": True}
    result = await script.provision(
        connection,
        fde(RoleCode.ADMINISTRATOR),
        manifest(),
        "not-persisted",
        apply=True,
        repository=repo,
        service=service,
    )
    assert [x["action"] for x in result["accounts"]] == ["reuse", "create"]
    service.create.assert_awaited_once()
    assert service.create.call_args.args[2]["roles"] == ["fde"]
    service.reset_password.assert_not_awaited()
    assert "not-persisted" not in repr(result)


@pytest.mark.asyncio
async def test_conflicting_existing_account_aborts_before_any_mutation():
    repo, service, connection = AsyncMock(), AsyncMock(), AsyncMock()
    repo.organization.return_value = {
        "departments": [PARENT],
        "accounts": [{"account_code": "FDEL001", "display_name": "另一个人"}],
    }
    with pytest.raises(script.ProvisionConflict, match="differs"):
        await script.provision(
            connection, fde(RoleCode.ADMINISTRATOR), manifest(), "private", apply=True, repository=repo, service=service
        )
    repo.save_department.assert_not_awaited()
    service.create.assert_not_awaited()
    with pytest.raises(script.ProvisionConflict, match="administrator"):
        await script.provision(connection, fde(), manifest(), "private", apply=True, repository=repo, service=service)


@pytest.mark.asyncio
async def test_new_fde_department_is_created_under_all_team():
    repo, service, connection = AsyncMock(), AsyncMock(), AsyncMock()
    repo.organization.return_value = {"departments": [PARENT], "accounts": []}
    repo.save_department.return_value = {"id": str(uuid4())}
    service.create.return_value = {"id": str(uuid4()), "must_change_password": True}
    result = await script.provision(connection, fde(RoleCode.ADMINISTRATOR), manifest(), "not-logged",
                                    apply=True, repository=repo, service=service)
    assert str(repo.save_department.call_args.args[3]["parent_team_id"]) == PARENT["id"]
    assert result["department"]["parent_team_id"] == PARENT["id"]
    service.reset_password.assert_not_awaited()


def test_missing_or_conflicting_parent_is_not_silently_provisioned_as_root():
    with pytest.raises(script.ProvisionConflict, match="parent department"):
        script.preflight(manifest(), {"departments": [], "accounts": []})
    bad = manifest().model_copy(update={"department": manifest().department.model_copy(
        update={"parent_team_id": uuid4()})})
    with pytest.raises(script.ProvisionConflict, match="do not match"):
        script.preflight(bad, {"departments": [PARENT], "accounts": []})
