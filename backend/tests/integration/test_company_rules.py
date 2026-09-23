from uuid import uuid4

import asyncpg
import pytest

from sales_backend.config import get_settings
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.services.battle_map_reviews import BattleMapReviewHandler
from tests.integration.test_operations_api import TransactionDatabase, client_for, sign_in
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio
URL = "/api/v1/console/company-rules"


async def test_policy_http_roles_conflicts_restore_and_immutability(connection):
    async with await client_for(connection) as client:
        await sign_in(client)
        item = next(i for i in (await client.get(URL)).json()["items"] if i["code"] == "customer_quadrant")
        original = item["current"]
        definition = {**original["definition"], "potential_threshold": 75}
        data = {"definition": definition, "reason": "隔离测试阈值", "base_id": original["id"]}
        preview = await client.post(URL + "/customer_quadrant/preview", json=data)
        sample = next(s for s in preview.json()["samples"] if s["potential"] == 70)
        assert sample["before"] == "customer_asset" and sample["after"] == "customer_resource"
        key = {"Idempotency-Key": str(uuid4())}
        draft = await client.post(URL + "/customer_quadrant/drafts", json=data, headers=key)
        assert draft.status_code == 200, draft.text
        assert (await client.post(URL + "/customer_quadrant/drafts", json=data, headers=key)).json() == draft.json()
        identifier = draft.json()["id"]
        assert (await client.post(URL + f"/versions/{identifier}/publish", json={"revision": 1})).status_code == 403
        await sign_in(client, "ADMIN001")
        assert (await client.post(URL + f"/versions/{identifier}/publish", json={"revision": 2})).status_code == 409
        response = await client.post(URL + f"/versions/{identifier}/publish", json={"revision": 1})
        assert response.status_code == 200, response.text
        assert next(i for i in (await client.get(URL)).json()["items"] if i["code"] == "customer_quadrant")["current"]["definition"]["potential_threshold"] == 75
        assert (await client.post(URL + "/customer_quadrant/drafts", json=data)).status_code == 409
        # Published content is append-only even for the runtime administrator.
        assert (
            await connection.execute("UPDATE config.rule_set SET definition='{}' WHERE id=$1::uuid", identifier)
            == "UPDATE 0"
        )
        restored = await client.post(
            URL + f"/versions/{original['id']}/restore", json={"base_id": identifier, "reason": "隔离回退"}
        )
        assert restored.status_code == 200, restored.text
        restore_id = restored.json()["id"]
        assert next(i for i in (await client.get(URL)).json()["items"] if i["code"] == "customer_quadrant")["current"]["id"] == identifier
        response = await client.post(URL + f"/versions/{restore_id}/publish", json={"revision": 1})
        assert response.status_code == 200, response.text
        final = next(i for i in (await client.get(URL)).json()["items"] if i["code"] == "customer_quadrant")
        assert final["current"]["definition"]["potential_threshold"] == 70
        assert len(final["versions"]) == 3 and final["versions"][0]["restored_from_id"] == original["id"]
        assert (
            await client.post(
                URL + "/customer_quadrant/drafts",
                json={**data, "definition": {**definition, "potential_threshold": 100}},
            )
        ).status_code == 422
    await actor(connection, "XS001")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval("SELECT security.publish_company_rule($1::uuid,1)", identifier)


async def test_quadrant_persistence_uses_policy_loaded_before_concurrent_publish(connection):
    sales = await actor(connection, "XS001")
    customer = await connection.fetchval("SELECT id::text FROM crm.customer WHERE deleted_at IS NULL LIMIT 1")
    handler = BattleMapReviewHandler(TransactionDatabase(connection), get_settings())
    old_facts = await handler._load_facts(customer, sales)
    policy = old_facts["company_policy"]
    await actor(connection, "ADMIN001")
    repo = CompanyRulesRepository()
    new_id = await repo.save(
        connection,
        "customer_quadrant",
        {
            "definition": {**policy["definition"], "potential_threshold": 75},
            "reason": "并发发布测试",
            "base_id": policy["id"],
        },
    )
    await repo.publish(connection, new_id, 1)
    await actor(connection, "XS001")
    answer = {"potential_score": 72, "relationship_score": 72, "evidence": [], "summary": "合成评估", "rationale": {"potential": "已提供事实", "relationship": "已有跟进"}}
    await handler._persist(customer, sales, old_facts, handler._normalize_result(answer, old_facts), {})
    old = await connection.fetchrow(
        "SELECT * FROM insight.quadrant_score WHERE customer_id=$1::uuid AND valid_to='infinity'", customer
    )
    assert old["quadrant_code"] == "customer_asset" and str(old["rule_set_id"]) == policy["id"]
    new_facts = await handler._load_facts(customer, sales)
    await handler._persist(customer, sales, new_facts, handler._normalize_result(answer, new_facts), {})
    new = await connection.fetchrow(
        "SELECT * FROM insight.quadrant_score WHERE customer_id=$1::uuid AND valid_to='infinity'", customer
    )
    assert new["quadrant_code"] == "customer_resource" and str(new["rule_set_id"]) == new_id
    assert (
        await connection.fetchval("SELECT quadrant_code FROM insight.quadrant_score WHERE id=$1", old["id"])
        == "customer_asset"
    )


async def test_home_policy_is_consumed_by_real_home_response_and_can_restore(connection):
    async with await client_for(connection) as client:
        await sign_in(client, "ADMIN001")
        original = (await client.get("/api/v1/assistant/home")).json()["display_policy"]
        assert original["definition"]["message_order"] == "desc"
        draft = await client.post(
            URL + "/home_display/drafts",
            json={
                "base_id": original["id"],
                "definition": {"message_order": "asc"},
                "reason": "隔离顺序验证",
            },
        )
        assert draft.status_code == 200, draft.text
        identifier = draft.json()["id"]
        await client.post(URL + f"/versions/{identifier}/publish", json={"revision": 1})
        home = (await client.get("/api/v1/assistant/home")).json()
        assert home["display_policy"]["definition"]["message_order"] == "asc"
        assert "chatbi_enabled" not in home["display_policy"]["definition"]
        restored = await client.post(
            URL + f"/versions/{original['id']}/restore",
            json={
                "base_id": identifier,
                "reason": "恢复倒序",
            },
        )
        await client.post(URL + f"/versions/{restored.json()['id']}/publish", json={"revision": 1})
        restored_home = (await client.get("/api/v1/assistant/home")).json()
        assert restored_home["display_policy"]["definition"]["message_order"] == "desc"


async def test_execution_configuration_admin_boundary_and_safe_projection(connection):
    async with await client_for(connection) as client:
        await sign_in(client)
        view = (await client.get("/api/v1/console/ai/execution")).json()
        assert len(view["items"]) == 12 and view["can_publish"] is False
        assert all(item["runtime"]["configuration_scope"] == "api_process" for item in view["items"])
        assert "api_key" not in str(view)
        assert view["management"]["capability_count"] == 12
        assert all(item["business_rule"]["code"].startswith("agent_business.") for item in view["items"])
        item = next(i for i in view["items"] if i["code"] == "agent_execution.chatbi")
        code = item["code"]
        body = {
            "base_id": item["current"]["id"],
            "definition": {**item["current"]["definition"], "strategy": "direct_only"},
            "reason": "隔离管理员控制",
        }
        denied = await client.post(URL + f"/{code}/drafts", json=body)
        assert denied.status_code == 403
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await CompanyRulesRepository().save(connection, code, body)
        await sign_in(client, "ADMIN001")
        invalid = {**body, "definition": {**body["definition"], "platform_seconds": 60, "total_seconds": 65}}
        assert (await client.post(URL + f"/{code}/drafts", json=invalid)).status_code == 422
        draft = (await client.post(URL + f"/{code}/drafts", json=body)).json()["id"]
        assert (await client.post(URL + f"/versions/{draft}/publish", json={"revision": 1})).status_code == 200
        await sign_in(client)
        denied_restore = await client.post(
            URL + f"/versions/{item['current']['id']}/restore",
            json={"base_id": draft, "reason": "不得由运营恢复技术配置"},
        )
        assert denied_restore.status_code == 403
        business = (await client.get(URL)).json()
        assert {item["code"] for item in business["items"]} == {
            "fde_capabilities",
            "customer_quadrant", "visit_admission", "home_display", "task_schedule",
            "score.maturity", "score.efficiency", "score.competency",
        }


async def test_business_guidance_draft_publish_restore_and_runtime_snapshot(connection):
    from sales_backend.services.runtime_config import load_runtime_configuration

    async with await client_for(connection) as client:
        await sign_in(client)
        items = (await client.get("/api/v1/console/ai/execution")).json()["items"]
        entry = next(item for item in items if item["code"] == "agent_execution.battle_map_review")
        rule = entry["business_rule"]
        original = rule["current"]
        definition = {**original["definition"], "guidance": "合成验证：关注客户已经确认的实施计划。"}
        body = {"base_id": original["id"], "definition": definition, "reason": "隔离业务指引发布验证"}
        code = rule["code"]
        preview = await client.post(URL + f"/{code}/preview", json=body)
        assert preview.status_code == 200
        assert preview.json()["validation_kind"] == "configuration_only"
        draft = await client.post(URL + f"/{code}/drafts", json=body)
        assert draft.status_code == 200, draft.text
        identifier = draft.json()["id"]
        assert (await client.post(URL + f"/versions/{identifier}/publish", json={"revision": 1})).status_code == 403
        await sign_in(client, "ADMIN001")
        assert (await client.post(URL + f"/versions/{identifier}/publish", json={"revision": 1})).status_code == 200
        sales = await actor(connection, "XS001")
        runtime = await load_runtime_configuration(
            TransactionDatabase(connection), sales, get_settings(), capability="battle_map_review"
        )
        assert runtime.business_policy["id"] == identifier
        assert runtime.business_policy["definition"] == definition
        assert runtime.business_policy["code"] == code
        await sign_in(client, "ADMIN001")
        restored = await client.post(URL + f"/versions/{original['id']}/restore",
                                    json={"base_id": identifier, "reason": "清空补充规则回到基线"})
        assert restored.status_code == 200, restored.text
        assert (await client.post(URL + f"/versions/{restored.json()['id']}/publish",
                                  json={"revision": 1})).status_code == 200
        data = (await client.get("/api/v1/console/ai/execution")).json()
        final = next(item["business_rule"] for item in data["items"] if item["code"] == entry["code"])
        assert final["current"]["definition"]["guidance"] == ""
        assert len(final["versions"]) == 3


async def test_rule_catalogs_do_not_require_model_credential_decryption(connection, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("Model key temporarily unavailable")

    monkeypatch.setattr("sales_backend.security.runtime_credentials.decrypt_credential", unavailable)
    async with await client_for(connection) as client:
        await sign_in(client)
        for path, count in [(URL, 8), ("/api/v1/console/ai/execution", 12)]:
            response = await client.get(path)
            assert response.status_code == 200, response.text
            assert len(response.json()["items"]) == count
            assert "ciphertext" not in response.text


async def test_other_workspace_administrator_cannot_read_audit_or_publish_company_rules(connection):
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from sales_backend.db import set_request_context
    from sales_backend.repositories.agent_audit import AgentAuditRepository
    from sales_backend.repositories.identity import IdentityRepository
    from tests.integration.test_agent_audit import receipt

    original_admin = await actor(connection, "ADMIN001")
    repo = CompanyRulesRepository()
    baseline = await repo.active(connection, "customer_quadrant")
    identifier = await repo.save(
        connection,
        "customer_quadrant",
        {
            "definition": {**baseline["definition"], "potential_threshold": 75},
            "reason": "Workspace isolation test",
            "base_id": baseline["id"],
        },
    )
    await repo.publish(connection, identifier, 1)
    operation_id = await receipt(connection, original_admin)

    # Only fixture provisioning uses the owner connection. All checks below use
    # the same non-superuser, non-bypass role as the business integration suite.
    runtime_role = await connection.fetchval("SELECT current_user")
    assert runtime_role.startswith("salegent_verify_role_")
    foreign_workspace, foreign_user = uuid4(), uuid4()
    external = "isolated-other-" + uuid4().hex
    await connection.execute("RESET ROLE")
    try:
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Other isolated workspace')",
            foreign_workspace,
            external,
        )
        await connection.execute(
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) "
            "VALUES($1,$2,'OTHERADMIN','Other test admin','OTHERADMIN')",
            foreign_user,
            foreign_workspace,
        )
        await connection.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
            "VALUES($1,$2,'administrator','workspace')",
            foreign_workspace,
            foreign_user,
        )
    finally:
        await connection.execute(f'SET LOCAL ROLE "{runtime_role}"')
    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
    row = await connection.fetchrow("SELECT * FROM security.resolve_account_actor($1,'OTHERADMIN',NULL)", external)
    other = IdentityRepository._actor(row).context
    await set_request_context(connection, other)
    assert await connection.fetchval("SELECT security.management_actor()")
    assert (await repo.active(connection, "customer_quadrant"))["id"] == baseline["id"]
    assert await repo.version(connection, UUID(identifier)) is None
    with pytest.raises(asyncpg.NoDataFoundError):
        async with connection.transaction():
            await repo.publish(connection, identifier, 1)
    with pytest.raises(asyncpg.NoDataFoundError):
        async with connection.transaction():
            await repo.save(
                connection,
                "customer_quadrant",
                {
                    "definition": baseline["definition"],
                    "reason": "must reject foreign restore",
                    "base_id": baseline["id"],
                },
                restored=identifier,
            )
    now = datetime.now(UTC)
    audit = AgentAuditRepository()
    result = await audit.cases(connection, start=now - timedelta(days=1), end=now + timedelta(days=1))
    assert result["total"] == 0 and result["statistics"]["eligible"] == 0
    assert (
        await connection.fetchval("SELECT count(*) FROM agent.inference_operation WHERE id=$1::uuid", operation_id) == 0
    )
    await set_request_context(connection, original_admin)
    assert (await repo.active(connection, "customer_quadrant"))["id"] == identifier
    assert (await audit.cases(connection, start=now - timedelta(days=1), end=now + timedelta(days=1)))["total"] == 1
