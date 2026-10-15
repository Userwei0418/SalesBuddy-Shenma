"""Real non-bypass business selectors and writes without the organization directory."""
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_sales_opportunity_permissions import PASSWORD, account_fixture

pytestmark = pytest.mark.asyncio
ENTRY = dict(permission="access.console", effect="allow", scope="workspace")
NO_DIRECTORY = [dict(permission=p, effect="deny") for p in ("organization.read", "directory.read")]
CUSTOMER_OPTIONS = "/api/v1/console/customers/create-options"
TARGET_OPTIONS = "/api/v1/console/targets/options"


def scoped(permission, team):
    return dict(permission=permission, effect="allow", scope="teams", team_ids=[team])


def customer_body(team):
    return dict(name="隔离建档-"+uuid4().hex, industry="软件", customer_type="潜在客户", level_code="Tier-2",
                source="销售线索", target_team=team["name"], target_team_id=team["id"],
                company_reference="ISOLATED-"+uuid4().hex, contact_name="测试联系人", contact_title="经理",
                contact_role="决策者")


def target_body(user_id=None, team_id=None, department=None):
    return dict(scope="person" if user_id else "team" if team_id else "department", user_id=user_id,
                team_id=team_id, department_code=department or "sales", period_type="year",
                anchor_date="2032-03-01", reason="隔离目标授权验证", items=[dict(kind="collection", amount="1234.56")])


async def test_customer_create_selector_and_write_without_org_or_directory_and_stale_revoke(connection):
    user, teams, admin = await account_fixture(connection, ["sales"])
    other, other_teams, _ = await account_fixture(connection, ["sales"])
    permissions = [ENTRY, *NO_DIRECTORY, *[scoped(p, teams["sales"]) for p in
                   ("customer.create", "customer.read", "customer.update")]]
    await grant(connection, admin, user["id"], permissions)
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        assert (await client.get("/api/v1/console/organization")).status_code == 403
        assert (await client.get("/api/v1/directory/teams")).status_code == 403
        choices = await client.get(CUSTOMER_OPTIONS)
        assert choices.status_code == 200, choices.text
        team, = choices.json()["teams"]
        assert team["id"] == teams["sales"] and set(team) == {"id", "name"}
        # Existing name-based clients and stable-ID clients both keep working.
        for by_id in (True, False):
            payload = customer_body(team)
            if not by_id:
                del payload["target_team_id"]
            result = await client.post("/api/v1/customers", json=payload)
            assert result.status_code == 201, result.text
        cid = result.json()["id"]
        rows = (await client.get("/api/v1/console/customers")).json()["items"]
        row = next(r for r in rows if r["id"] == cid)
        assert row["can_update"] and not row["can_release"] and not row["can_resolve_owner"]
        profile = await client.get(f"/api/v1/console/customers/{cid}/overview")
        assert profile.status_code == 200 and profile.json()["can_update"]
        wrong = await client.post("/api/v1/customers", json=customer_body({"id": other_teams["sales"], "name": team["name"]}))
        assert wrong.status_code == 403, wrong.text
        await grant(connection, admin, user["id"], [ENTRY, *NO_DIRECTORY,
            scoped("customer.read", teams["sales"]), dict(permission="customer.create", effect="deny"),
            dict(permission="customer.update", effect="deny")], version=1)
        assert (await client.get(CUSTOMER_OPTIONS)).status_code == 403
        assert (await client.post("/api/v1/customers", json=customer_body(team))).status_code == 403
        assert not (await client.get(f"/api/v1/console/customers/{cid}/overview")).json()["can_update"]
    await set_request_context(connection, admin)
    assert (await connection.fetchval("SELECT count(*) FROM platform.role_binding WHERE user_ref_id=$1::uuid AND role_code='administrator'", user["id"])) == 0
    assert other["id"] != user["id"]


async def test_self_target_management_is_usable_without_org_and_never_exposes_other_subjects(connection):
    user, _, admin = await account_fixture(connection, ["sales"])
    await grant(connection, admin, user["id"], [ENTRY, *NO_DIRECTORY,
        dict(permission="target.manage", effect="allow", scope="self"), dict(permission="target.history", effect="deny")])
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        options = await client.get(TARGET_OPTIONS)
        assert options.status_code == 200, options.text
        choices = options.json()
        assert choices["teams"] == choices["departments"] == []
        assert choices["people"] == [{"id": user["id"], "name": "隔离权限账号", "account_code": user["account_code"]}]
        assert (await client.get("/api/v1/console/organization")).status_code == 403
        payload = target_body(user_id=user["id"])
        saved = await client.post("/api/v1/console/targets/batch", json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "effective"
        row = saved.json()["items"][0]
        listing = await client.get("/api/v1/console/targets?period_type=year&anchor_date=2032-03-01")
        assert listing.status_code == 200, listing.text
        listed, = listing.json()["items"]
        assert listed["can_manage"] and not listed["can_history"]
        assert listed["amount_text"] == "1234.56"
        assert (await client.get(f"/api/v1/console/targets/{row['id']}/history")).status_code == 403
        await grant(connection, admin, user["id"], [ENTRY, *NO_DIRECTORY,
            dict(permission="target.manage", effect="deny")], version=1)
        assert (await client.get(TARGET_OPTIONS)).status_code == 403
        payload["items"][0].update(amount="2000.01", version_no=row["version_no"])
        assert (await client.post("/api/v1/console/targets/batch", json=payload)).status_code == 403


async def test_team_target_options_intersect_manage_read_and_valid_subjects_not_self_submit(connection):
    user, own_teams, admin = await account_fixture(connection, ["sales"])
    colleague, teams, _ = await account_fixture(connection, ["sales"])
    permissions = [ENTRY, *NO_DIRECTORY, scoped("target.manage", teams["sales"]),
                   dict(permission="target.read", effect="allow", scope="workspace")]
    await grant(connection, admin, user["id"], permissions)
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        choices = (await client.get(TARGET_OPTIONS)).json()
        assert {p["id"] for p in choices["people"]} == {colleague["id"]}
        assert {p["id"] for p in choices["teams"]} == {teams["sales"]}
        assert choices["departments"] == []
        assert own_teams["sales"] not in {p["id"] for p in choices["teams"]}
        for payload in (target_body(user_id=colleague["id"]), target_body(team_id=teams["sales"])):
            result = await client.post("/api/v1/console/targets/batch", json=payload)
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "effective"
        # Native self submit does not let console management maintain other years for self.
        rejected = await client.post("/api/v1/console/targets/batch", json=target_body(user_id=user["id"]))
        assert rejected.status_code == 409, rejected.text
        await grant(connection, admin, user["id"], [ENTRY, *NO_DIRECTORY, scoped("target.manage", teams["sales"])], version=1)
        assert (await client.get(TARGET_OPTIONS)).json() == {"people": [], "teams": [], "departments": []}
        await grant(connection, admin, user["id"], permissions, version=2)
        await set_request_context(connection, admin)
        await connection.execute("UPDATE platform.team SET valid_to=clock_timestamp() WHERE id=$1::uuid", teams["sales"])
        # Expiring the sole managed team invalidates the effective grant itself.
        assert (await client.get(TARGET_OPTIONS)).status_code == 403


async def test_department_target_options_keep_fde_permission_separate(connection):
    user, _, admin = await account_fixture(connection, ["sales"])
    permissions = [ENTRY, *NO_DIRECTORY, *[dict(permission=p, effect="allow", scope="workspace")
        for p in ("target.manage", "target.read")]]
    await grant(connection, admin, user["id"], permissions)
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        choices = (await client.get(TARGET_OPTIONS)).json()
        assert [d["id"] for d in choices["departments"]] == ["sales"]
        denied = await client.post("/api/v1/console/targets/batch", json=target_body(department="fde"))
        assert denied.status_code == 403, denied.text
        await grant(connection, admin, user["id"], [*permissions,
            dict(permission="target.fde_department", effect="allow", scope="workspace")], version=1)
        choices = (await client.get(TARGET_OPTIONS)).json()
        assert {d["id"] for d in choices["departments"]} == {"sales", "fde"}
        allowed = await client.post("/api/v1/console/targets/batch", json=target_body(department="fde"))
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["status"] == "effective"


async def test_claim_reviewer_without_customer_read_sees_requests_but_not_full_customer_details(connection):
    user, teams, admin = await account_fixture(connection, ["sales"])
    initial = [ENTRY, *NO_DIRECTORY, *[scoped(p, teams["sales"]) for p in ("customer.create", "customer.read")]]
    await grant(connection, admin, user["id"], initial)
    async with await client_for(connection) as client:
        await sign_in(client, user["account_code"], PASSWORD)
        team, = (await client.get(CUSTOMER_OPTIONS)).json()["teams"]
        created = await client.post("/api/v1/customers", json=customer_body(team))
        assert created.status_code == 201, created.text
        cid = created.json()["id"]
        await actor(connection, "XS001")
        request = await connection.fetchval("SELECT security.claim_customer($1::uuid)", cid)
        permissions = [ENTRY, *NO_DIRECTORY, scoped("customer.claim_review", teams["sales"]),
                       dict(permission="customer.read", effect="deny")]
        await grant(connection, admin, user["id"], permissions, version=1)
        listing = await client.get("/api/v1/console/claims", params={"customer_id": cid})
        assert listing.status_code == 200, listing.text
        row, = listing.json()["items"]
        assert row["id"] == request["request_id"] and row["can_review"] and not row["can_read_customer"]
        assert row["customer_name"] == created.json()["name"]
        assert (await client.get(f"/api/v1/console/customers/{cid}/overview")).status_code == 403
        await grant(connection, admin, user["id"], [*permissions, dict(permission="customer.reference", effect="deny")], version=2)
        row, = (await client.get("/api/v1/console/claims", params={"customer_id": cid})).json()["items"]
        assert row["customer_name"] is None and row["can_review"] and not row["can_read_customer"]
        decision = await client.post(f"/api/v1/console/claims/{request['request_id']}/decision",
                                     json={"decision": "rejected", "reason": "隔离审批测试"})
        assert decision.status_code == 200, decision.text
