"""Real PG/HTTP amount reminders: unchanged facts, tenant boundaries and atomic retries."""

from decimal import Decimal
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.opportunities import require_amount_confirmation
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_opportunity_lifecycle import setup_customer
from tests.integration.test_profile_scores import business_login
from tests.integration.test_visit_attendance import review_attendance

pytestmark = pytest.mark.asyncio


def payload(amount="10000000", **changes):
    return {"name": "大额隔离商机-" + uuid4().hex, "amount": amount, "probability": 10,
            "sales_channel": "direct", "expected_close_date": "2026-12-01", **changes}


async def effects(connection):
    await actor(connection, "ADMIN001")
    return tuple(await connection.fetchrow("""SELECT
        (SELECT count(*) FROM crm.opportunity), (SELECT count(*) FROM crm.business_change),
        (SELECT count(*) FROM workflow.notification), (SELECT count(*) FROM ops.job),
        (SELECT count(*) FROM ops.outbox_event), (SELECT count(*) FROM agent.confirmation),
        (SELECT count(*) FROM activity.visit), (SELECT count(*) FROM ops.mutation_receipt)"""))


@pytest.mark.parametrize("amount,confirmation,status", [
    ("9999999.99", None, 201), ("10000000", None, 422), ("10000000", "1000", 422),
    ("10000000", "10000000.00", 201), ("1000000000", "1000000000", 201),
])
async def test_manual_boundary_exact_confirmation_and_idempotent_retry(
    connection, sales_actor, amount, confirmation, status,
):
    _, customer = await setup_customer(connection, sales_actor)
    body = payload(amount, amount_confirmed_value=confirmation)
    before = await effects(connection)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        key = {"Idempotency-Key": str(uuid4())}
        path = f"/api/v1/customers/{customer['id']}/opportunities"
        response = await client.post(path, json=body, headers=key)
        assert response.status_code == status, response.text
        if status == 422:
            assert "确认该金额" in response.text
            assert await effects(connection) == before
            body["amount_confirmed_value"] = amount
            # The failed attempt left neither a business write nor a success receipt.
            response = await client.post(path, json=body, headers=key)
            assert response.status_code == 201, response.text
        assert Decimal(str(response.json()["amount"])) == Decimal(amount)
        assert "amount_confirmed_value" not in response.json()
        after = await effects(connection)
        replay = await client.post(path, json=body, headers=key)
        assert replay.status_code == 201 and replay.json() == response.json()
        assert await effects(connection) == after
        for confirmation in (None, "1"):
            retried = {**body, "amount_confirmed_value": confirmation}
            if confirmation is None:
                retried.pop("amount_confirmed_value")
            replay = await client.post(path, json=retried, headers=key)
            assert replay.status_code == 201 and replay.json() == response.json(), replay.text
            assert await effects(connection) == after
        changed = await client.post(path, json={**body, "amount": "10000001"}, headers=key)
        assert changed.status_code == 409
        assert await effects(connection) == after
        await actor(connection, "ADMIN001")
        saved = await connection.fetchrow("SELECT amount,attributes,import_meta FROM crm.opportunity WHERE id=$1::uuid",
                                          response.json()["id"])
        assert saved["amount"] == Decimal(amount)
        assert "amount_confirmed_value" not in saved["attributes"]
        assert "amount_confirmed_value" not in saved["import_meta"]


async def test_unchanged_large_amount_is_quiet_but_old_confirmation_cannot_approve_new_value(connection, sales_actor):
    _, customer = await setup_customer(connection, sales_actor)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        path = f"/api/v1/customers/{customer['id']}/opportunities"
        body = payload(amount_confirmed_value="10000000")
        created = await client.post(path, json=body)
        assert created.status_code == 201, created.text
        update = {**body, "action": "update", "opportunity_id": created.json()["id"],
                  "version_no": created.json()["version_no"], "name": body["name"] + "修改名称"}
        update.pop("amount_confirmed_value")
        unchanged = await client.post(path, json=update)
        assert unchanged.status_code == 201, unchanged.text
        update.update(amount="10000001", amount_confirmed_value="10000000",
                      version_no=unchanged.json()["version_no"])
        before = await effects(connection)
        rejected = await client.post(path, json=update)
        assert rejected.status_code == 422, rejected.text
        assert await effects(connection) == before
        accepted = await client.post(path, json={**update, "amount_confirmed_value": "10000001"})
        assert accepted.status_code == 201, accepted.text
        assert Decimal(str(accepted.json()["amount"])) == Decimal("10000001")


async def test_console_amount_policy_uses_edit_permission_and_save_uses_same_guard(connection, sales_actor):
    _, customer = await setup_customer(connection, sales_actor)
    admin = await actor(connection, "ADMIN001")
    await grant(connection, admin, admin.user_id, [dict(permission="rule.read", effect="deny")])
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        policy = await client.get("/api/v1/console/opportunities/amount-policy")
        assert policy.status_code == 200, policy.text
        assert policy.json()["definition"]["warning_threshold_wan"] == 1000
        body = payload(customer_id=customer["id"], owner_user_ref_id=sales_actor.user_id)
        key = {"Idempotency-Key": str(uuid4())}
        response = await client.post("/api/v1/console/opportunities", json=body, headers=key)
        assert response.status_code == 422, response.text
        response = await client.post("/api/v1/console/opportunities", json={**body, "amount_confirmed_value": "10000000"}, headers=key)
        assert response.status_code == 201, response.text
        after = await effects(connection)
        for retry in (body, {**body, "amount_confirmed_value": "1"}):
            replay = await client.post("/api/v1/console/opportunities", json=retry, headers=key)
            assert replay.status_code == 201 and replay.json() == response.json(), replay.text
            assert await effects(connection) == after
        conflict = await client.post("/api/v1/console/opportunities", json={**body, "amount": "10000001"}, headers=key)
        assert conflict.status_code == 409, conflict.text
        assert await effects(connection) == after
        await grant(connection, admin, admin.user_id, [dict(permission="opportunity.create", effect="deny")], version=1)
        assert (await client.get("/api/v1/console/opportunities/amount-policy")).status_code == 200
        await grant(connection, admin, admin.user_id, [dict(permission=code, effect="deny")
                    for code in ("opportunity.create", "opportunity.update")], version=2)
        assert (await client.get("/api/v1/console/opportunities/amount-policy")).status_code == 403


async def test_company_threshold_publication_preview_and_tenant_isolation(connection):
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        catalog = await client.get("/api/v1/console/company-rules")
        assert catalog.status_code == 200, catalog.text
        item = next(row for row in catalog.json()["items"] if row["code"] == "opportunity_amount")
        assert item["management"]["related_agents"] == []
        base = item["current"]
        body = {"definition": {"schema_version": 1, "warning_threshold_wan": 2000},
                "reason": "隔离公司阈值测试", "base_id": base["id"]}
        path = "/api/v1/console/company-rules/opportunity_amount"
        preview = await client.post(path + "/preview", json=body)
        assert preview.status_code == 200, preview.text
        assert "金额上限" in preview.json()["note"]
        draft = await client.post(path + "/drafts", json=body)
        assert draft.status_code == 200, draft.text
        result = await client.post(f"/api/v1/console/company-rules/versions/{draft.json()['id']}/publish", json={"revision": 1})
        assert result.status_code == 200, result.text
        presentation = await client.get("/api/v1/company-rules/presentation")
        assert presentation.status_code == 200, presentation.text
        assert presentation.json()["opportunity_amount"]["definition"]["warning_threshold_wan"] == 2000
    await actor(connection, "XS001")
    await require_amount_confirmation(connection, {"amount": Decimal("15000000")})
    workspace, user = uuid4(), uuid4()
    external = "amount-other-" + uuid4().hex
    async with fixture_owner(connection):
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离阈值公司')", workspace, external)
        await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) "
                                 "VALUES($1,$2,'OTHERADMIN','隔离管理员','OTHERADMIN')", user, workspace)
        await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
                                 "VALUES($1,$2,'administrator','workspace')", workspace, user)
    row = await connection.fetchrow("SELECT * FROM security.resolve_account_actor($1,'OTHERADMIN',NULL)", external)
    other = IdentityRepository._actor(row).context
    await set_request_context(connection, other)
    await assert_restricted(connection)
    assert (await CompanyRulesRepository().active(connection, "opportunity_amount"))["id"] == base["id"]
    with pytest.raises(ValueError, match="1,000 万元"):
        await require_amount_confirmation(connection, {"amount": Decimal("15000000")})


async def test_visit_final_acknowledgement_requires_no_second_review_and_is_atomic(connection, sales_actor):
    _, customer = await setup_customer(connection, sales_actor)
    mutation = payload()
    _, submitted = await review_attendance(connection, sales_actor, customer["id"], None, [], mutation)
    body = submitted.model_dump(mode="json")
    before = await effects(connection)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        key = {"Idempotency-Key": str(uuid4())}
        response = await client.post("/api/v1/visits", json=body, headers=key)
        assert response.status_code == 422, response.text
        assert "确认该金额" in response.text
        assert await effects(connection) == before
        # Only the human acknowledgement changes after quality review; the amount does not.
        body["fields"]["_opportunity_mutation"]["amount_confirmed_value"] = "10000000"
        accepted = await client.post("/api/v1/visits", json=body, headers=key)
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["opportunity_id"]
        after = await effects(connection)
        replay = await client.post("/api/v1/visits", json=body, headers=key)
        assert replay.status_code == 201 and replay.json() == accepted.json()
        assert await effects(connection) == after
        for confirmation in (None, "1"):
            mutation = body["fields"]["_opportunity_mutation"]
            mutation["amount_confirmed_value"] = confirmation
            if confirmation is None:
                mutation.pop("amount_confirmed_value")
            replay = await client.post("/api/v1/visits", json=body, headers=key)
            assert replay.status_code == 201 and replay.json() == accepted.json(), replay.text
            assert await effects(connection) == after
        body["fields"]["_opportunity_mutation"]["amount"] = "10000001"
        conflict = await client.post("/api/v1/visits", json=body, headers=key)
        assert conflict.status_code == 409, conflict.text
        assert await effects(connection) == after


async def test_successful_receipt_survives_changed_company_threshold(connection, sales_actor):
    _, customer = await setup_customer(connection, sales_actor)
    body = payload(amount_confirmed_value="10000000")
    key = {"Idempotency-Key": str(uuid4())}
    path = f"/api/v1/customers/{customer['id']}/opportunities"
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        saved = await client.post(path, json=body, headers=key)
        assert saved.status_code == 201, saved.text
    async with await client_for(connection) as admin_client:
        await sign_in(admin_client, "ADMIN001")
        current = await admin_client.get("/api/v1/console/opportunities/amount-policy")
        draft = await admin_client.post("/api/v1/console/company-rules/opportunity_amount/drafts", json={
            "definition": {"schema_version": 1, "warning_threshold_wan": 2000},
            "reason": "模拟保存已成功但回执丢失后阈值变化", "base_id": current.json()["id"],
        })
        assert draft.status_code == 200, draft.text
        published = await admin_client.post(
            f"/api/v1/console/company-rules/versions/{draft.json()['id']}/publish", json={"revision": 1},
        )
        assert published.status_code == 200, published.text
    after = await effects(connection)
    body.pop("amount_confirmed_value")
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        replay = await client.post(path, json=body, headers=key)
        assert replay.status_code == 201 and replay.json() == saved.json(), replay.text
        assert await effects(connection) == after
