"""Real HTTP/auth/SQL under managed API ACL and RLS; owner seeds are synthetic only."""

import json
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.authorization import AuthorizationRepository
from tests.integration.feishu_fixtures import seed_execute, seed_fetchval
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor, create_customer
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login, opportunity_payload

pytestmark = pytest.mark.asyncio


async def setup(connection, client):
    first, teams, admin = await account_fixture(connection, ["sales"], kind="sales")
    second, _, _ = await account_fixture(connection, ["sales"], kind="sales")
    customer = await create_customer(connection)
    await native_login(client, first)
    body = opportunity_payload()
    saved = await client.post(f"/api/v1/customers/{customer['id']}/opportunities", json=body)
    assert saved.status_code == 201, saved.text
    return first, second, teams, admin, customer, saved.json(), body


async def history(client, oid, **params):
    return await client.get(f"/api/v1/opportunities/{oid}/change-history", params=params)


async def seed_event(connection, admin, customer, opportunity, changes, *, stamp=None, version=2):
    identifier = str(uuid4())
    await seed_execute(
        connection,
        "INSERT INTO crm.business_change(id,workspace_id,customer_id,opportunity_id,actor_user_ref_id,"
        "version_no,changes,created_at) VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6,$7::jsonb,"
        "COALESCE($8::timestamptz,clock_timestamp()))",
        identifier,
        admin.workspace_id,
        customer["id"],
        opportunity["id"],
        admin.user_id,
        version,
        changes,
        stamp,
    )
    return identifier


async def override(connection, admin, account, permissions):
    await set_request_context(connection, admin)
    current = await AuthorizationRepository().account(connection, account["id"])
    await grant(connection, admin, account["id"], permissions, version=current["version_no"])


async def test_real_save_history_remains_readable_without_customer_or_ai_permissions(connection):
    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
    async with await client_for(connection) as client:
        first, _, _, admin, customer, saved, body = await setup(connection, client)
        response = await client.post(
            f"/api/v1/customers/{customer['id']}/opportunities",
            json={
                **body,
                "action": "update",
                "opportunity_id": saved["id"],
                "version_no": saved["version_no"],
                "probability": 30,
                "amount": 2000,
                "expected_close_date": "2026-11-01",
                "quarterly_forecasts": [dict(year=2026, quarter=4, recognized_amount=0, collection_amount=1000)],
            },
        )
        assert response.status_code == 201, response.text
        await override(
            connection,
            admin,
            first,
            [dict(permission=code, effect="deny") for code in ("customer.read", "agent.history", "actual.read")],
        )
        read = await history(client, saved["id"])
        assert read.status_code == 200, read.text
        page = read.json()
        assert len(page["changes"]["items"]) == 2
        labels = {field["label"] for item in page["changes"]["items"] for field in item["fields"]}
        assert {"商机阶段", "ACV（万元）", "预计关单日期", "2026 Q4 预测回款（万元）"} <= labels
        assert all(item["actor"]["name_status"] == "current" for item in page["changes"]["items"])
        # Existing narrow history remains available and does not invent intent events.
        intent = await client.get(f"/api/v1/opportunities/{saved['id']}/intent-product-line-history")
        assert intent.status_code == 200 and intent.json()["changes"]["items"] == []


async def test_stable_more_than_100_same_time_same_version_and_actual_event_conflict(connection):
    from datetime import UTC, datetime

    async with await client_for(connection) as client:
        _, _, _, admin, customer, saved, _ = await setup(connection, client)
        ids = [
            await seed_event(
                connection,
                admin,
                customer,
                saved,
                [dict(field="note", before="", after=f"合成变更{i}")],
                stamp=datetime(2100, 1, 1, tzinfo=UTC),
            )
            for i in range(105)
        ]
        first = (await history(client, saved["id"], limit=100)).json()
        assert [item["id"] for item in first["changes"]["items"]] == sorted(ids, reverse=True)[:100]
        revision = first["history_revision"]
        second = await history(client, saved["id"], limit=100, offset=100, expected_history_revision=revision)
        assert second.status_code == 200
        assert len(second.json()["changes"]["items"]) == 6
        assert not second.json()["changes"]["has_more"] and second.json()["changes"]["next_offset"] is None
        all_ids = [item["id"] for item in first["changes"]["items"] + second.json()["changes"]["items"]]
        assert len(all_ids) == len(set(all_ids)) == 106
        before = await seed_fetchval(
            connection, "SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", saved["id"]
        )
        await seed_event(
            connection,
            admin,
            customer,
            saved,
            [dict(field="operational_actual", before={"amount": "1"}, after={"amount": "2"})],
        )
        assert (
            await seed_fetchval(connection, "SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", saved["id"])
            == before
        )
        changed = await history(client, saved["id"], offset=100, expected_history_revision=revision)
        assert changed.status_code == 409
        assert changed.json()["detail"]["code"] == "OPPORTUNITY_CHANGE_HISTORY_CHANGED"


@pytest.mark.parametrize(
    "params",
    [
        {"offset": 1},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 100001},
        {"expected_history_revision": "not-a-revision"},
    ],
)
async def test_http_page_input_bounds(connection, params):
    async with await client_for(connection) as client:
        *_, saved, _ = await setup(connection, client)
        assert (await history(client, saved["id"], **params)).status_code == 422


async def test_financial_actual_dual_scope_and_permission_change_revision(connection):
    async with await client_for(connection) as client:
        first, _, teams, admin, customer, saved, _ = await setup(connection, client)
        identifier = await seed_event(
            connection,
            admin,
            customer,
            saved,
            [
                dict(field="operational_actual", before={"amount": "100.12"}, after={"amount": "200.34"}),
            ],
        )
        await override(connection, admin, first, [dict(permission="actual.read", effect="deny")])
        restricted = (await history(client, saved["id"])).json()
        item = next(item for item in restricted["changes"]["items"] if item["id"] == identifier)
        assert item["fields"][0]["after"]["status"] == "restricted"
        assert "200.34" not in json.dumps(restricted)
        # The opportunity belongs to first's team but the unclaimed customer does
        # not: an opportunity-only actual grant must not bypass customer scope.
        await override(
            connection,
            admin,
            first,
            [
                dict(permission="actual.read", effect="allow", scope="teams", team_ids=[teams["sales"]]),
            ],
        )
        scoped = (await history(client, saved["id"])).json()
        assert (
            next(i for i in scoped["changes"]["items"] if i["id"] == identifier)["fields"][0]["after"]["status"]
            == "restricted"
        )
        await override(connection, admin, first, [dict(permission="actual.read", effect="allow", scope="workspace")])
        assert (
            await history(client, saved["id"], expected_history_revision=restricted["history_revision"])
        ).status_code == 409
        allowed = (await history(client, saved["id"])).json()
        item = next(item for item in allowed["changes"]["items"] if item["id"] == identifier)
        assert item["fields"][0]["after"]["rows"][0]["detail"] == "200.34"


async def test_collaborator_revoke_sibling_tenant_deleted_and_action_denials(connection):
    async with await client_for(connection) as client:
        first, second, _, admin, customer, saved, _ = await setup(connection, client)
        # Historical collaboration remains authorized after its public setter retires.
        await seed_execute(
            connection,
            "INSERT INTO crm.opportunity_collaborator(workspace_id,opportunity_id,user_ref_id,added_by_user_ref_id) "
            "SELECT workspace_id,id,$2::uuid,$3::uuid FROM crm.opportunity WHERE id=$1::uuid",
            saved['id'], second['id'], first['id'],
        )
        await native_login(client, second)
        await override(connection, admin, second, [dict(permission="customer.read", effect="deny")])
        assert (await history(client, saved["id"])).status_code == 200
        sibling = await seed_fetchval(
            connection,
            "INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount,owner_user_ref_id,owner_team_id,"
            "created_by_user_ref_id) SELECT workspace_id,customer_id,'合成兄弟商机',100,"
            "owner_user_ref_id,owner_team_id,"
            "created_by_user_ref_id FROM crm.opportunity WHERE id=$1::uuid RETURNING id::text",
            saved["id"],
        )
        assert (await history(client, sibling)).status_code == 404
        await native_login(client, first)
        await seed_execute(
            connection, "DELETE FROM crm.opportunity_collaborator WHERE opportunity_id=$1::uuid AND user_ref_id=$2::uuid",
            saved['id'], second['id'],
        )
        await native_login(client, second)
        assert (await history(client, saved["id"])).status_code == 404
        await native_login(client, first)
        await override(connection, admin, first, [dict(permission="opportunity.read", effect="deny")])
        assert (await history(client, saved["id"])).status_code == 403
        await override(connection, admin, first, [])
        await seed_execute(
            connection, "UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1::uuid", saved["id"]
        )
        assert (await history(client, saved["id"])).status_code == 404
        foreign_ws, foreign_user, foreign_customer, foreign_oid = [str(uuid4()) for _ in range(4)]
        await seed_execute(
            connection,
            "INSERT INTO platform.workspace(id,external_workspace_id,name) "
            "VALUES($1::uuid,$1::uuid::text,'合成隔离公司')",
            foreign_ws,
        )
        await seed_execute(
            connection,
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) "
            "VALUES($1::uuid,$2::uuid,$1::uuid::text,'合成异公司人员')",
            foreign_user,
            foreign_ws,
        )
        await seed_execute(
            connection,
            "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id) "
            "VALUES($1::uuid,$2::uuid,'合成异公司客户','合成异公司客户',$3::uuid)",
            foreign_customer,
            foreign_ws,
            foreign_user,
        )
        await seed_execute(
            connection,
            "INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,amount,"
            "owner_user_ref_id,created_by_user_ref_id) "
            "VALUES($1::uuid,$2::uuid,$3::uuid,'合成异公司商机',100,$4::uuid,$4::uuid)",
            foreign_oid,
            foreign_ws,
            foreign_customer,
            foreign_user,
        )
        assert (await history(client, foreign_oid)).status_code == 404


async def test_existing_fde_endpoint_records_only_changes_and_names_are_not_backfilled(connection):
    async with await client_for(connection) as client:
        first, _, _, admin, _, saved, _ = await setup(connection, client)
        fde, _, _ = await account_fixture(connection, ["fde"], kind="fde")
        await native_login(client, first)
        path = f"/api/v1/opportunities/{saved['id']}/fde-members"
        response = await client.put(path, json={"version_no": saved["version_no"], "member_ids": [fde["id"]]})
        assert response.status_code == 200, response.text
        count = await seed_fetchval(
            connection, "SELECT count(*) FROM crm.business_change WHERE opportunity_id=$1::uuid", saved["id"]
        )
        assert count == 2
        replay = await client.put(path, json={"version_no": response.json()["version_no"], "member_ids": [fde["id"]]})
        assert replay.status_code == 200 and not replay.json()["changed"]
        assert (
            await seed_fetchval(
                connection, "SELECT count(*) FROM crm.business_change WHERE opportunity_id=$1::uuid", saved["id"]
            )
            == count
        )
        read = (await history(client, saved["id"])).json()
        field = next(f for item in read["changes"]["items"] for f in item["fields"] if f["field"] == "fde_members")
        assert field["after"]["rows"][0]["id"] == fde["id"]
        assert field["after"]["rows"][0]["name"] is None and field["after"]["rows"][0]["missing_name"]
        await seed_execute(
            connection, "UPDATE platform.user_ref SET display_name='变更后的当前姓名' WHERE id=$1::uuid", first["id"]
        )
        assert (
            await history(client, saved["id"], expected_history_revision=read["history_revision"])
        ).status_code == 409
        current = (await history(client, saved["id"])).json()
        assert current["changes"]["items"][0]["actor"]["name_status"] == "current"


async def test_fde_only_permission_saves_history_without_general_update_permission(connection):
    async with await client_for(connection) as client:
        first, _, _, admin, customer, saved, body = await setup(connection, client)
        fde, _, _ = await account_fixture(connection, ["fde"], kind="fde")
        # A newly created row in this outer test transaction would still allow
        # create-side effects. Model an existing opportunity to exercise the gap.
        await seed_execute(
            connection,
            "UPDATE crm.opportunity SET created_at=transaction_timestamp()-interval '1 day' WHERE id=$1::uuid",
            saved["id"],
        )
        denied = [dict(permission=code, effect="deny") for code in ("opportunity.update", "history.import")]
        await override(connection, admin, first, [
            *denied, dict(permission="opportunity.fde_members", effect="allow", scope="self"),
        ])
        await native_login(client, first)
        await actor(connection, first["account_code"])
        assert not await connection.fetchval(
            "SELECT security.authorization_opportunity_mutation($1::uuid)", saved["id"],
        )
        assert await connection.fetchval("SELECT security.can_manage_fde_members($1::uuid)", saved["id"])
        version = await seed_fetchval(
            connection, "SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", saved["id"],
        )
        path = f"/api/v1/opportunities/{saved['id']}/fde-members"
        response = await client.put(path, json={"version_no": version, "member_ids": [fde["id"]]})
        assert response.status_code == 200, response.text
        version = response.json()["version_no"]
        read = await history(client, saved["id"])
        assert read.status_code == 200, read.text
        entries = [
            field for item in read.json()["changes"]["items"] for field in item["fields"]
            if field["field"] == "fde_members"
        ]
        assert len(entries) == 1 and entries[0]["after"]["rows"][0]["id"] == fde["id"]
        count = await seed_fetchval(
            connection, "SELECT count(*) FROM crm.business_change WHERE opportunity_id=$1::uuid", saved["id"],
        )
        replay = await client.put(path, json={"version_no": version, "member_ids": [fde["id"]]})
        assert replay.status_code == 200 and not replay.json()["changed"]
        update = await client.post(f"/api/v1/customers/{customer['id']}/opportunities", json={
            **body, "action": "update", "opportunity_id": saved["id"], "version_no": version, "amount": 2000,
        })
        assert update.status_code == 403, update.text
        await override(connection, admin, first, [
            *denied, dict(permission="opportunity.fde_members", effect="deny"),
        ])
        rejected = await client.put(path, json={"version_no": version, "member_ids": []})
        assert rejected.status_code == 403, rejected.text
        assert await seed_fetchval(
            connection, "SELECT count(*) FROM crm.business_change WHERE opportunity_id=$1::uuid", saved["id"],
        ) == count
