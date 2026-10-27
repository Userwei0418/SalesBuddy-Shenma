"""Object-authorized ACL choices under the real API role and shared gate."""

from uuid import UUID, uuid4

import asyncpg
import pytest

from .knowledge_fixtures import kb as kb
from .knowledge_fixtures import selection
from .test_knowledge_shared_wiring import gated_client, key


async def material(kb):
    await kb.configure()
    return await kb.service.save(kb.actors["owner"], uuid4(), selection())


async def person(kb, label, *, status="active", deleted=False, workspace=None):
    uid = uuid4()
    await kb.admin.execute(
        "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,status,deleted_at) "
        "VALUES($1,$2,$3,$4,$5,CASE WHEN $6 THEN clock_timestamp() ELSE NULL END)",
        uid, workspace or kb.workspace, str(uid), label, status, deleted,
    )
    # Intentionally no role_binding, sales appointment, team or business grant.
    return uid


@pytest.mark.asyncio
async def test_self_scoped_material_manager_can_select_any_valid_tenant_subject(kb, monkeypatch):
    saved = await material(kb)
    mid = saved["material_id"]
    for action in ("read", "manage"):
        await kb.grant_scope("owner", "self", action=action)
    first = await person(kb, "ACL choice same")
    second = await person(kb, "ACL choice same")
    await person(kb, "ACL choice inactive", status="inactive")
    await person(kb, "ACL choice deleted", deleted=True)
    await person(kb, "ACL choice foreign", workspace=UUID(kb.actors["other"].workspace_id))
    path = f"/api/v1/knowledge/materials/{mid}"
    async with gated_client(kb, monkeypatch) as client:
        pages = []
        for offset in (0, 1):
            response = await client.get(path + "/acl-candidates", params={
                "subject_kind": "user", "q": "ACL choice", "limit": 1, "offset": offset,
            })
            assert response.status_code == 200, response.text
            page = response.json()
            assert page["total"] == 2 and page["offset"] == offset and page["limit"] == 1
            assert set(page) == {"material_id", "subject_kind", "items", "total", "limit", "offset"}
            assert response.headers["cache-control"] == "no-store"
            assert set(page["items"][0]) == {"subject_id", "display_name"}
            pages.extend(page["items"])
        assert [row["subject_id"] for row in pages] == sorted([str(first), str(second)])
        teams = await client.get(path + "/acl-candidates", params={"subject_kind": "team"})
        assert teams.status_code == 200, teams.text
        assert str(kb.team) in [row["subject_id"] for row in teams.json()["items"]]
        updated = await client.put(path + "/acl", headers=key(), json={
            "expected_acl_revision": 1,
            "grants": [
                {"subject_kind": "user", "subject_id": str(first), "actions": ["read"]},
                {"subject_kind": "team", "subject_id": str(kb.team), "actions": ["read"]},
                {"subject_kind": "company", "subject_id": None, "actions": ["read"]},
            ],
        })
        assert updated.status_code == 200, updated.text


@pytest.mark.asyncio
async def test_candidates_require_current_read_and_manage_on_exact_material(kb, monkeypatch):
    saved = await material(kb)
    mid = saved["material_id"]
    await kb.admin.execute(
        "UPDATE knowledge.material_acl SET grants=$2::jsonb WHERE material_id=$1",
        UUID(str(mid)), [{"subject_kind": "user", "subject_id": kb.actors["reader"].user_id, "actions": ["read"]}],
    )
    path = f"/api/v1/knowledge/materials/{mid}/acl-candidates?subject_kind=user"
    async with gated_client(kb, monkeypatch, "reader") as client:
        denied = await client.get(path)
        assert denied.status_code == 403 and denied.json()["detail"] == "KNOWLEDGE_ACCESS_DENIED"
    async with gated_client(kb, monkeypatch, "other") as client:
        assert (await client.get(path)).status_code == 404
    async with gated_client(kb, monkeypatch) as client:
        missing = await client.get(f"/api/v1/knowledge/materials/{uuid4()}/acl-candidates?subject_kind=user")
        assert missing.status_code == 404
        assert (await client.get(path)).status_code == 200
        await kb.grant_scope("owner", None, effect="deny", action="manage")
        assert (await client.get(path)).status_code == 403
    async with kb.database.transaction(kb.actors["owner"]) as connection:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.fetchval(
                "SELECT security.knowledge_acl_candidates($1,'user','',20,0)", UUID(str(mid)),
            )


@pytest.mark.asyncio
async def test_team_candidates_match_active_interval_and_tenant_save_rules(kb, monkeypatch):
    saved = await material(kb)
    mid = saved["material_id"]
    excluded = []
    for label, status, deleted, start, end, workspace in (
        ("inactive", "inactive", False, -2, 2, kb.workspace),
        ("deleted", "active", True, -2, 2, kb.workspace),
        ("expired", "active", False, -2, -1, kb.workspace),
        ("future", "active", False, 1, 2, kb.workspace),
        ("foreign", "active", False, -2, 2, UUID(kb.actors["other"].workspace_id)),
    ):
        tid = uuid4()
        await kb.admin.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name,status,deleted_at,valid_from,valid_to) "
            "VALUES($1,$2,$3,$3,$4,CASE WHEN $5 THEN clock_timestamp() ELSE NULL END,"
            "clock_timestamp()+$6*interval '1 day',clock_timestamp()+$7*interval '1 day')",
            tid, workspace, label, status, deleted, start, end,
        )
        excluded.append(tid)
    path = f"/api/v1/knowledge/materials/{mid}"
    async with gated_client(kb, monkeypatch) as client:
        response = await client.get(path + "/acl-candidates", params={"subject_kind": "team"})
        assert response.status_code == 200, response.text
        assert {row["subject_id"] for row in response.json()["items"]} == {str(kb.team)}
        for tid in excluded:
            denied = await client.put(path + "/acl", headers=key(), json={
                "expected_acl_revision": 1,
                "grants": [{"subject_kind": "team", "subject_id": str(tid), "actions": ["read"]}],
            })
            assert denied.status_code == 422, denied.text


@pytest.mark.asyncio
async def test_candidate_result_does_not_authorize_later_save_and_existing_denies_survive(kb, monkeypatch):
    saved = await material(kb)
    mid = saved["material_id"]
    target = await person(kb, "Later inactive")
    path = f"/api/v1/knowledge/materials/{mid}"
    async with gated_client(kb, monkeypatch) as client:
        choices = await client.get(path + "/acl-candidates", params={"subject_kind": "user", "q": "Later"})
        assert choices.status_code == 200 and choices.json()["total"] == 1
        await kb.admin.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", target)
        denied = await client.put(path + "/acl", headers=key(), json={
            "expected_acl_revision": 1,
            "grants": [{"subject_kind": "user", "subject_id": str(target), "actions": ["read"]}],
        })
        assert denied.status_code == 422
        kept = await client.put(path + "/acl", headers=key(), json={
            "expected_acl_revision": 1, "denied_user_ids": [str(target)],
        })
        assert kept.status_code == 200
        reread = await client.get(path + "/acl")
        assert reread.json()["denied_user_ids"] == [str(target)]
        remaining = await client.get(path + "/acl-candidates", params={"subject_kind": "user", "q": "Later"})
        assert remaining.json()["total"] == 0


@pytest.mark.asyncio
async def test_candidate_pagination_validation_and_module_revocation(kb, monkeypatch):
    saved = await material(kb)
    path = f"/api/v1/knowledge/materials/{saved['material_id']}/acl-candidates"
    async with gated_client(kb, monkeypatch) as client:
        for params in ({}, {"subject_kind": "company"}, {"subject_kind": "user", "limit": 101},
                       {"subject_kind": "user", "offset": -1}, {"subject_kind": "user", "q": "x" * 101}):
            assert (await client.get(path, params=params)).status_code == 422
        empty = await client.get(path, params={"subject_kind": "user", "q": "%_unique_no_match"})
        assert empty.status_code == 200 and empty.json()["total"] == 0
        await kb.admin.execute(
            "UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='knowledge'",
            kb.workspace,
        )
        assert (await client.get(path, params={"subject_kind": "user"})).status_code == 403
