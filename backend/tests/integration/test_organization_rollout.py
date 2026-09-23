"""A full disposable transaction rehearsal of the explicit organization plan."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.services.operations_accounts import OperationsAccountService
from sales_backend.request_metadata import request_metadata, RequestMetadata
from tests.integration.test_operations_claims_sql import actor

spec = importlib.util.spec_from_file_location(
    "org_rollout", Path(__file__).parents[2] / "scripts/adjust_organization_20260915.py"
)
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)


@pytest.mark.asyncio
async def test_confirmed_organization_plan_is_atomic_and_preserves_business(connection):
    admin = await actor(connection, "ADMIN001")
    await connection.execute("RESET ROLE")
    await connection.execute("UPDATE platform.user_ref SET account_code='OPSADMIN' WHERE id=$1::uuid", admin.user_id)
    repo = OperationsAccountRepository()
    service = OperationsAccountService()
    org = await repo.organization(connection)
    for t in org["departments"]:
        if t["name"] == "南区":
            await repo.save_department(connection, admin, t["id"], {**t, "code": "SOUTH"})
    created = {}
    for code, name in [
        ("ALL", "全部团队"),
        ("NORTH-EAST", "北区东区"),
        ("FDE", "FDE"),
        ("accept0913", "【演示验收0913】验收组"),
    ]:
        created[code] = (
            await repo.save_department(
                connection, admin, None, dict(code=code, name=name, parent_team_id=None, status="active")
            )
        )["id"]
    for code, name, role in [("ZJ002", "蒋磊", "supervisor"), ("XS003", "周莹", "sales")]:
        await service.create(
            connection,
            admin,
            dict(account_code=code, display_name=name, roles=[role], team_id=created["NORTH-EAST"]),
            "Only-Test-2026",
        )
    token = request_metadata.set(RequestMetadata(request_id=str(uuid4()), user_agent="isolated-org-test"))
    try:
        before = await connection.fetchval("SELECT jsonb_agg(jsonb_build_array(id,owner_team_id)) FROM crm.customer")
        plan = await rollout.adjust(connection)
        with pytest.raises(rollout.Conflict, match="changed"):
            await rollout.adjust(connection, "OnlyLettersTest", apply=True, expected="stale", allow_letters=True)
        result = await rollout.adjust(
            connection, "OnlyLettersTest", apply=True, expected=plan["plan_sha256"], allow_letters=True
        )
        assert result["applied"]
        org = await repo.organization(connection)
        teams = {t["code"]: t for t in org["departments"]}
        users = {a["account_code"]: a for a in org["accounts"]}
        assert "accept0913" not in teams and "NORTH-EAST" not in teams
        assert teams["EAST"]["id"] == created["NORTH-EAST"]
        assert (
            await connection.fetchval("SELECT jsonb_agg(jsonb_build_array(id,owner_team_id)) FROM crm.customer")
            == before
        )
        jiang = users["ZJ002"]
        assert jiang["team_id"] == teams["EAST"]["id"]
        assert len(jiang["memberships"]) == 2
        north = [m for u in org["accounts"] for m in u["memberships"] if m["team_id"] == teams["NORTH"]["id"]]
        assert len(north) == 1 and north[0]["acting"]
        for code in ["CPXS001", "CPXS002"]:
            record = await IdentityRepository().find_actor_by_id(
                connection, workspace_id=admin.workspace_id, user_id=users[code]["id"], role="supervisor"
            )
            assert record.role_title == "产品销售主管" and len(record.context.team_ids) == 1
            candidate = await connection.fetchval(
                "SELECT security.password_candidate('demo-sales-workspace',$1,'supervisor')", code
            )
            assert candidate["must_change_password"]
        with pytest.raises(rollout.Conflict):
            await rollout.adjust(
                connection, "OnlyLettersTest", apply=True, expected=plan["plan_sha256"], allow_letters=True
            )
        await set_request_context(connection, admin)
        assert (
            await connection.fetchval("SELECT count(*) FROM ops.audit_log WHERE action_code='organization.adjust'") == 1
        )
    finally:
        request_metadata.reset(token)
