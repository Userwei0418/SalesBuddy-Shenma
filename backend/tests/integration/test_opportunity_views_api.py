# ruff: noqa: S608 -- SQL identifiers/fragments are internal constants or validated catalog identifiers.
"""Real HTTP, auth, transactions and owner-only PostgreSQL RLS for saved views."""

from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import create_customer
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login, opportunity_payload

pytestmark = pytest.mark.asyncio
PATH = "/api/v1/opportunity-views"


def key():
    return {"Idempotency-Key": str(uuid4())}


async def create_view(client, *, payload=None, headers=None):
    result = await client.post(PATH, json=payload or {"name": "我的重点项目"}, headers=headers or key())
    assert result.status_code == 201, result.text
    return result.json()


async def test_personal_view_crud_readback_rename_replay_and_versions(connection):
    account, _, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as client:
        await native_login(client, account)
        options = await client.get(PATH + "/options")
        assert options.status_code == 200, options.text
        assert options.json()["definition_version"] == "shenma_opportunity_view_v1"
        assert options.json()["default_columns"] == ["name", "stage", "signal", "money", "close"]
        payload = {
            "name": "我的重点项目",
            "config": {
                "columns": ["name", "owner", "amount"],
                "order": "close_date",
                "group_by": "none",
                "filters": {
                    "q": "软件",
                    "owner_id": account["id"],
                    "year": 2026,
                    "quarters": [4],
                    "stages": ["proposal"],
                },
            },
        }
        headers = key()
        saved = await create_view(client, payload=payload, headers=headers)
        replay = await client.post(PATH, json=payload, headers=headers)
        assert replay.status_code == 201 and replay.json() == saved, replay.text
        assert saved["owner_user_ref_id"] == account["id"] and saved["version_no"] == 1
        page = await client.get(PATH)
        assert page.status_code == 200 and page.json()["total"] == 1, page.text
        assert page.json()["items"] == [saved]
        detail = await client.get(PATH + "/" + saved["id"])
        assert detail.status_code == 200 and detail.json() == saved
        rename_key = key()
        renamed = await client.put(
            PATH + "/" + saved["id"], json={"name": "本季度项目", "version_no": 1}, headers=rename_key
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["config"] == saved["config"] and renamed.json()["version_no"] == 2
        rename_replay = await client.put(
            PATH + "/" + saved["id"], json={"name": "本季度项目", "version_no": 1}, headers=rename_key
        )
        assert rename_replay.status_code == 200 and rename_replay.json() == renamed.json()
        stale = await client.put(PATH + "/" + saved["id"], json={"name": "旧版本覆盖", "version_no": 1}, headers=key())
        assert stale.status_code == 409, stale.text
        changed = await client.put(
            PATH + "/" + saved["id"],
            json={"name": "当前项目", "version_no": 2, "config": {"columns": ["name", "close"], "group_by": "quarter"}},
            headers=key(),
        )
        assert changed.status_code == 200 and changed.json()["version_no"] == 3, changed.text
        delete_key = key()
        deleted = await client.request("DELETE", PATH + "/" + saved["id"], json={"version_no": 3}, headers=delete_key)
        assert deleted.status_code == 200 and deleted.json() == {"id": saved["id"], "deleted": True, "version_no": 4}, (
            deleted.text
        )
        replay = await client.request("DELETE", PATH + "/" + saved["id"], json={"version_no": 3}, headers=delete_key)
        assert replay.status_code == 200 and replay.json() == deleted.json()
        assert (await client.get(PATH + "/" + saved["id"])).status_code == 404
        assert (await client.get(PATH)).json()["total"] == 0


async def test_cross_user_and_workspace_are_hidden_even_from_company_admin(connection):
    first, _, admin = await account_fixture(connection, ["sales"])
    second, _, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as owner, await client_for(connection) as other:
        await native_login(owner, first)
        saved = await create_view(owner)
        await native_login(other, second)
        assert (await other.get(PATH)).json()["total"] == 0
        assert (await other.get(PATH + "/" + saved["id"])).status_code == 404
        denied = await other.put(PATH + "/" + saved["id"], json={"name": "越权", "version_no": 1}, headers=key())
        assert denied.status_code == 404, denied.text
        denied = await other.request("DELETE", PATH + "/" + saved["id"], json={"version_no": 1}, headers=key())
        assert denied.status_code == 404, denied.text
        identity = await IdentityRepository().find_actor_by_id(
            connection, workspace_id=admin.workspace_id, user_id=second["id"], role="sales"
        )
        await set_request_context(connection, identity.context)
        assert (
            await connection.fetchval("SELECT count(*) FROM crm.opportunity_saved_view WHERE id=$1::uuid", saved["id"])
            == 0
        )
        assert (
            await connection.fetchval(
                "UPDATE crm.opportunity_saved_view SET name='越权' WHERE id=$1::uuid RETURNING id", saved["id"]
            )
            is None
        )
        await set_request_context(connection, identity.context.model_copy(update={"workspace_id": str(uuid4())}))
        assert await connection.fetchval("SELECT count(*) FROM crm.opportunity_saved_view") == 0
        await sign_in(other, code="ADMIN001")
        assert (await other.get(PATH)).json()["total"] == 0
        assert (await other.get(PATH + "/" + saved["id"])).status_code == 404


async def test_view_does_not_grant_query_scope_or_expose_owner_options(connection):
    customer = await create_customer(connection)
    first, teams, _ = await account_fixture(connection, ["sales"])
    second, other_teams, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as one, await client_for(connection) as two:
        await native_login(one, first)
        made = await one.post(f"/api/v1/customers/{customer['id']}/opportunities", json=opportunity_payload())
        assert made.status_code == 201, made.text
        first_opp = made.json()["id"]
        await native_login(two, second)
        made = await two.post(f"/api/v1/customers/{customer['id']}/opportunities", json=opportunity_payload())
        assert made.status_code == 201, made.text
        hidden_opp = made.json()["id"]
        # Settings can contain stale/foreign IDs. Applying them must still use
        # the ordinary scoped query; saved preferences are never credentials.
        await create_view(
            one,
            payload={
                "name": "失效视图",
                "config": {"filters": {"scope": "team", "team_id": other_teams["sales"], "owner_id": second["id"]}},
            },
        )
        unfiltered = await one.get("/api/v1/opportunities", params={"include_closed": "true"})
        assert unfiltered.status_code == 200, unfiltered.text
        assert first_opp in {row["id"] for row in unfiltered.json()["items"]}
        assert hidden_opp not in {row["id"] for row in unfiltered.json()["items"]}
        assert {row["id"] for row in unfiltered.json()["facets"]["owner_options"]} == {first["id"]}
        precise = await one.get("/api/v1/opportunities", params={"owner_id": second["id"]})
        assert precise.status_code == 200 and precise.json()["summary"]["total"] == 0, precise.text
        own = await one.get("/api/v1/opportunities", params={"owner_id": first["id"]})
        assert own.status_code == 200 and {row["id"] for row in own.json()["items"]} == {first_opp}, own.text
        denied = await one.get("/api/v1/opportunities", params={"team_id": other_teams["sales"]})
        assert denied.status_code == 403, denied.text
        denied = await one.get("/api/v1/opportunities", params={"scope": "team"})
        assert denied.status_code == 403, denied.text


async def test_invalid_payloads_keys_and_raw_sql_identity_protection(connection):
    account, _, admin = await account_fixture(connection, ["sales"])
    other, _, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as client:
        assert (await client.get(PATH)).status_code == 401
        await native_login(client, account)
        assert (await client.post(PATH, json={"name": "无幂等键"})).status_code == 422
        for payload in ({"visibility": "team"}, {"workspace_id": str(uuid4())}, {"config": {"sql": "SELECT 1"}}):
            invalid = await client.post(PATH, json={"name": "非法配置", **payload}, headers=key())
            assert invalid.status_code == 422, invalid.text
        saved = await create_view(client)
        for payload in ({"name": "空配置", "version_no": 1, "config": None}, {"name": "无版本"}):
            invalid = await client.put(PATH + "/" + saved["id"], json=payload, headers=key())
            assert invalid.status_code == 422, invalid.text
        identity = await IdentityRepository().find_actor_by_id(
            connection, workspace_id=admin.workspace_id, user_id=account["id"], role="sales"
        )
        await set_request_context(connection, identity.context)
        for field, value in (("owner_user_ref_id", other["id"]), ("workspace_id", str(uuid4()))):
            with pytest.raises(asyncpg.InvalidParameterValueError):
                async with connection.transaction():
                    await connection.execute(
                        f"UPDATE crm.opportunity_saved_view SET {field}=$2::uuid WHERE id=$1::uuid", saved["id"], value
                    )
        role = await connection.fetchval("SELECT current_user")
        assert not await connection.fetchval(
            "SELECT has_table_privilege($1,'crm.opportunity_saved_view','DELETE')", role
        )
        assert not await connection.fetchval(
            "SELECT has_table_privilege('salegent_feishu_worker','crm.opportunity_saved_view','SELECT')"
        )
        # A reused key with a different mutation intent cannot overwrite the receipt.
        headers = key()
        await create_view(client, headers=headers)
        conflict = await client.post(PATH, json={"name": "不同意图"}, headers=headers)
        assert conflict.status_code == 409, conflict.text


async def test_current_read_permission_is_required_even_for_receipt_replay(connection):
    from tests.integration.test_authorization_opportunities import grant

    account, _, admin = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as client:
        await native_login(client, account)
        headers = key()
        saved = await create_view(client, headers=headers)
        await grant(connection, admin, account["id"], [{"permission": "opportunity.read", "effect": "deny"}])
        for path in (PATH, PATH + "/options", PATH + "/" + saved["id"]):
            denied = await client.get(path)
            assert denied.status_code == 403, denied.text
        denied = await client.post(PATH, json={"name": "我的重点项目"}, headers=headers)
        assert denied.status_code == 403, denied.text
        denied = await client.put(PATH + "/" + saved["id"], json={"name": "撤权后修改", "version_no": 1}, headers=key())
        assert denied.status_code == 403, denied.text
        identity = await IdentityRepository().find_actor_by_id(
            connection, workspace_id=admin.workspace_id, user_id=account["id"], role="sales"
        )
        await set_request_context(connection, identity.context)
        assert await connection.fetchval("SELECT count(*) FROM crm.opportunity_saved_view") == 0


async def test_owner_limit_and_page_bounds(connection):
    account, _, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as client:
        await native_login(client, account)
        saved = await create_view(client)
        await connection.execute(
            "INSERT INTO crm.opportunity_saved_view(workspace_id,owner_user_ref_id,name,config) "
            "SELECT common.current_workspace_id(),common.current_user_ref_id(),'批量偏好-'||n,$1::jsonb "
            "FROM generate_series(1,98) n",
            saved["config"],
        )
        await create_view(client, payload={"name": "第100个"})
        overflow = await client.post(PATH, json={"name": "第101个"}, headers=key())
        assert overflow.status_code == 422, overflow.text
        page = await client.get(PATH, params={"limit": 1, "offset": 99})
        assert page.status_code == 200 and page.json()["total"] == 100 and len(page.json()["items"]) == 1
        assert (await client.get(PATH, params={"limit": 101})).status_code == 422
        assert (await client.get(PATH, params={"offset": -1})).status_code == 422


@pytest.mark.parametrize(
    "bad_config",
    [
        {"schema_version": 99, "columns": ["name"]},
        {"schema_version": 1, "columns": ["unknown_private_field"]},
        {"schema_version": 1, "filters": {"team_id": "obsolete-id"}},
    ],
)
async def test_invalid_stored_config_falls_back_per_item_and_rename_preserves_raw(connection, bad_config):
    account, _, _ = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as client:
        await native_login(client, account)
        valid = await create_view(
            client, payload={"name": "正常视图", "config": {"columns": ["name", "owner"], "filters": {"q": "正常筛选"}}}
        )
        invalid = await create_view(client, payload={"name": "过期视图"})
        # Real PostgreSQL simulates a preference saved by an older schema or
        # damaged outside the current API. RLS and version/audit triggers still run.
        await connection.execute(
            "UPDATE crm.opportunity_saved_view SET config=$2::jsonb WHERE id=$1::uuid", invalid["id"], bad_config
        )
        page = await client.get(PATH)
        assert page.status_code == 200 and page.json()["total"] == 2, page.text
        indexed = {row["id"]: row for row in page.json()["items"]}
        assert indexed[valid["id"]] == valid
        broken = indexed[invalid["id"]]
        assert broken["config_valid"] is False
        assert broken["config_error"] == "视图配置已失效，已恢复默认列和筛选，请重新保存。"
        assert broken["config"]["columns"] == ["name", "stage", "signal", "money", "close"]
        assert broken["config"]["filters"]["q"] == "" and broken["config"]["filters"]["team_id"] is None
        detail = await client.get(PATH + "/" + invalid["id"])
        assert detail.status_code == 200 and detail.json() == broken, detail.text
        renamed = await client.put(
            PATH + "/" + invalid["id"], json={"name": "仅改名仍过期", "version_no": broken["version_no"]}, headers=key()
        )
        assert renamed.status_code == 200 and renamed.json()["config_valid"] is False, renamed.text
        assert (
            await connection.fetchval("SELECT config FROM crm.opportunity_saved_view WHERE id=$1::uuid", invalid["id"])
            == bad_config
        )
        # The public write contract remains strict: read fallback does not make
        # unsupported input valid, and repair requires an explicit full config.
        rejected = await client.put(
            PATH + "/" + invalid["id"],
            json={"name": "拒绝旧配置", "version_no": renamed.json()["version_no"], "config": bad_config},
            headers=key(),
        )
        assert rejected.status_code == 422, rejected.text
        fixed = await client.put(
            PATH + "/" + invalid["id"],
            json={
                "name": "已修复",
                "version_no": renamed.json()["version_no"],
                "config": {"columns": ["name", "close"], "filters": {"q": "修复后"}},
            },
            headers=key(),
        )
        assert (
            fixed.status_code == 200 and fixed.json()["config_valid"] is True and fixed.json()["config_error"] is None
        ), fixed.text
        assert fixed.json()["config"]["filters"]["q"] == "修复后"
