"""Contribution categories are authorized by current grants on real HTTP/PG writes."""

from uuid import UUID, uuid4

import pytest

from sales_backend.api.knowledge import CatalogWrite

from .knowledge_fixtures import catalog_values, selection
from .knowledge_fixtures import kb as kb

pytestmark = pytest.mark.asyncio
PREFIX = "/api/v1/knowledge/materials"
RESTRICTED = ("overall_solution", "product_intro", "industry_solution", "sales_support", "training_assessment")
DENIED = {"detail": "KNOWLEDGE_CONTRIBUTION_CATEGORY_DENIED"}


def headers():
    return {"Idempotency-Key": str(uuid4())}


def contribution(category, **updates):
    fields = {"industry_code": "test"} if category in {"industry_solution", "customer_solution"} else {}
    return selection(content_category=category, **fields, **updates).model_dump(mode="json")


async def catalog(kb):
    values = catalog_values()
    for field in ("industries", "reporting_positions", "reporting_levels", "decision_roles"):
        values[field] = [{"code": "test", "label": "合成测试", "active": True}]
    await kb.service.save_catalog(kb.actors["owner"], uuid4(), CatalogWrite(**values))


@pytest.mark.parametrize("category", RESTRICTED)
async def test_self_contributor_cannot_create_maintainer_categories(kb, category):
    await catalog(kb)
    member = await kb.user("member", scope="self", permissions=["read", "create", "edit", "manage"])
    key = headers()
    async with kb.client("member") as client:
        response = await client.post(PREFIX, json=contribution(category), headers=key)
    assert response.status_code == 403 and response.json() == DENIED
    assert await kb.admin.fetchval(
        "SELECT count(*) FROM knowledge.material WHERE workspace_id=$1", kb.workspace
    ) == 0
    assert await kb.admin.fetchval(
        "SELECT count(*) FROM ops.mutation_receipt WHERE workspace_id=$1 AND actor_id=$2 AND request_key=$3",
        kb.workspace, UUID(member.user_id), UUID(key["Idempotency-Key"]),
    ) == 0


@pytest.mark.parametrize("category", ("conference_speech", "customer_solution"))
async def test_self_contributor_can_create_and_version_the_two_contribution_categories(kb, category):
    await catalog(kb)
    member = await kb.user("member", scope="self", permissions=["read", "create", "edit", "manage"])
    extra = {}
    if category == "customer_solution":
        customer = uuid4()
        await kb.admin.execute(
            "UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code IN "
            "(SELECT module_code FROM control.permission_module WHERE permission_code='customer.read')",
            kb.workspace,
        )
        await kb.admin.execute(
            "INSERT INTO config.account_permission_override"
            "(workspace_id,user_ref_id,permission_code,effect,scope_code) "
            "VALUES($1,$2,'customer.read','allow','workspace')",
            kb.workspace, UUID(member.user_id),
        )
        await kb.admin.execute(
            "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id) "
            "VALUES($1,$2,'合成客户','合成客户',$3)", customer, kb.workspace, UUID(member.user_id),
        )
        extra = dict(customer_id=customer, reporting_position_code="test", reporting_level_code="test",
                     decision_role_code="test")
    body = contribution(category, **extra)
    async with kb.client("member") as client:
        response = await client.post(PREFIX, json=body, headers=headers())
        assert response.status_code == 201, response.text
        mid = response.json()["material_id"]
        changed = await client.post(
            f"{PREFIX}/{mid}/versions",
            json={"expected_material_revision": 1, "contribution": {**body, "supplementary_text": "第二版"}},
            headers=headers(),
        )
        assert changed.status_code == 201 and changed.json()["version_no"] == 2, changed.text
        denied = await client.post(
            f"{PREFIX}/{mid}/versions",
            json={"expected_material_revision": 2, "contribution": contribution("sales_support")}, headers=headers(),
        )
        assert denied.status_code == 403 and denied.json() == DENIED
        detail = (await client.get(f"{PREFIX}/{mid}")).json()
        assert detail["material_revision"] == 2 and len(detail["versions"]) == 2


@pytest.mark.parametrize("category", RESTRICTED)
async def test_catalog_maintainer_can_create_and_version_other_categories(kb, category):
    await catalog(kb)
    body = contribution(category)
    async with kb.client() as client:
        created = await client.post(PREFIX, json=body, headers=headers())
        assert created.status_code == 201, created.text
        update = await client.post(
            f"{PREFIX}/{created.json()['material_id']}/versions",
            json={"expected_material_revision": 1, "contribution": {**body, "supplementary_text": "第二版"}},
            headers=headers(),
        )
        assert update.status_code == 201 and update.json()["version_no"] == 2, update.text


async def test_removed_catalog_permission_blocks_new_versions_and_both_receipt_replays(kb):
    await catalog(kb)
    body, create_key, version_key = contribution("sales_support"), headers(), headers()
    async with kb.client() as client:
        created = await client.post(PREFIX, json=body, headers=create_key)
        assert created.status_code == 201, created.text
        mid = created.json()["material_id"]
        version_url = f"{PREFIX}/{mid}/versions"
        update = {"expected_material_revision": 1, "contribution": {**body, "supplementary_text": "第二版"}}
        assert (await client.post(version_url, json=update, headers=version_key)).status_code == 201
        await kb.grant_scope("owner", None, effect="deny", action="catalog_manage")
        for url, request, key in ((PREFIX, body, create_key), (version_url, update, version_key),
                                  (version_url, {**update, "expected_material_revision": 2}, headers())):
            denied = await client.post(url, json=request, headers=key)
            assert denied.status_code == 403 and denied.json() == DENIED
        detail = (await client.get(f"{PREFIX}/{mid}")).json()
        assert detail["material_revision"] == 2 and len(detail["versions"]) == 2


async def test_catalog_permission_does_not_replace_create_or_material_edit_authority(kb):
    await catalog(kb)
    await kb.user("catalog_only", permissions=["read", "catalog_manage"])
    async with kb.client("catalog_only") as client:
        denied = await client.post(PREFIX, json=contribution("sales_support"), headers=headers())
        assert denied.status_code == 403 and denied.json() == {"detail": "KNOWLEDGE_ACCESS_DENIED"}
    saved = await kb.service.save(kb.actors["owner"], uuid4(), selection())
    async with kb.client("reader") as client:
        denied = await client.post(
            f"{PREFIX}/{saved['material_id']}/versions",
            json={"expected_material_revision": 1, "contribution": contribution("conference_speech")},
            headers=headers(),
        )
        assert denied.status_code == 404 and denied.json() == {"detail": "KNOWLEDGE_MATERIAL_NOT_FOUND"}
