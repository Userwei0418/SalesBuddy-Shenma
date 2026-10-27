"""Real PostgreSQL/RLS coverage; no mocked storage or authorization evaluator."""

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.api.knowledge import ACLWrite, CatalogWrite
from sales_backend.services.idempotency import IdempotencyConflict

from .knowledge_fixtures import catalog_values, selection
from .knowledge_fixtures import kb as kb


@pytest.mark.asyncio
async def test_file_roundtrip_versions_and_revoke(kb):
    owner, reader = kb.actors["owner"], kb.actors["reader"]
    await kb.configure()
    content = "真实归档\nSynthetic test".encode()
    file = await kb.service.upload(owner, uuid4(), "材料.txt", "text/plain", content)
    created = await kb.service.save(owner, uuid4(), selection(files=[file["file_id"]]))
    mid, vid = UUID(created["material_id"]), UUID(created["version_id"])
    assert created["state"] == "saved" and created["active_version_id"] is None
    reopened = await kb.service.read(owner, "detail", mid)
    assert reopened["versions"][0]["files"][0]["sha256"] == file["sha256"]
    assert (await kb.service.download(owner, UUID(file["file_id"]), vid))["content"] == content
    with pytest.raises(LookupError):
        await kb.service.download(reader, UUID(file["file_id"]), vid)
    await kb.service.save_acl(
        owner,
        uuid4(),
        mid,
        ACLWrite(
            expected_acl_revision=1,
            grants=[
                {
                    "subject_kind": "user",
                    "subject_id": reader.user_id,
                    "actions": ["read"],
                }
            ],
        ),
    )
    assert (await kb.service.download(reader, UUID(file["file_id"]), vid))["content"] == content
    new = await kb.service.save(owner, uuid4(), selection(files=[file["file_id"]], name="新版本"), mid, 1)
    assert new["version_no"] == 2 and new["active_version_id"] is None
    assert (await kb.service.download(reader, UUID(file["file_id"]), vid))["content"] == content
    await kb.service.save_acl(owner, uuid4(), mid, ACLWrite(expected_acl_revision=2))
    with pytest.raises(LookupError):
        await kb.service.download(reader, UUID(file["file_id"]), vid)
    with pytest.raises(LookupError):
        await kb.service.download(kb.actors["other"], UUID(file["file_id"]), vid)
    async with kb.database.transaction(reader, readonly=True) as c:
        assert await c.fetchval("SELECT security.knowledge_projection($1)", mid) is None
        for table in ["material", "version", "file", "version_file", "material_acl"]:
            assert await c.fetchval(f"SELECT count(*) FROM knowledge.{table}") == 0  # noqa: S608


@pytest.mark.asyncio
async def test_idempotency_collision_and_current_authority(kb):
    owner = kb.actors["owner"]
    await kb.configure()
    key = uuid4()
    receipt = await kb.service.save(owner, key, selection())
    assert await kb.service.save(owner, key, selection()) == receipt
    with pytest.raises(IdempotencyConflict):
        await kb.service.save(owner, key, selection(name="different"))
    with pytest.raises(asyncpg.SerializationError, match="KNOWLEDGE_NAME_CONFLICT"):
        await kb.service.save(owner, uuid4(), selection())
    mid = UUID(receipt["material_id"])
    with pytest.raises(asyncpg.SerializationError, match="KNOWLEDGE_VERSION_CONFLICT"):
        await kb.service.save(owner, uuid4(), selection(name="next"), mid, 7)
    await kb.service.save_acl(owner, uuid4(), mid, ACLWrite(expected_acl_revision=1, denied_user_ids=[owner.user_id]))
    with pytest.raises(LookupError):
        await kb.service.save(owner, key, selection())


@pytest.mark.asyncio
async def test_nonowner_sql_cannot_mutate_or_bypass_rls(kb):
    await kb.configure()
    async with kb.database.transaction(kb.actors["owner"]) as c:
        assert await c.fetchval("SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user")
        for sql in [
            "DELETE FROM knowledge.catalog",
            "UPDATE knowledge.material SET content_name='tampered'",
            "INSERT INTO knowledge.file(workspace_id) VALUES(gen_random_uuid())",
        ]:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with c.transaction():
                    await c.execute(sql)


@pytest.mark.asyncio
async def test_function_scope_intersects_acl_and_deny(kb):
    await kb.configure()
    owner, reader = kb.actors["owner"], kb.actors["reader"]
    receipt = await kb.service.save(owner, uuid4(), selection())
    mid = UUID(receipt["material_id"])
    # Company ACL does not widen a personal or assigned platform grant.
    await kb.service.save_acl(
        owner,
        uuid4(),
        mid,
        ACLWrite(
            expected_acl_revision=1,
            grants=[
                {
                    "subject_kind": "company",
                    "actions": ["read"],
                }
            ],
        ),
    )
    for scope in ["self", "assigned"]:
        await kb.grant_scope("reader", scope)
        assert (await kb.service.read(reader, "list_materials"))["items"] == []
    await kb.service.save_acl(
        owner,
        uuid4(),
        mid,
        ACLWrite(
            expected_acl_revision=2,
            grants=[
                {
                    "subject_kind": "user",
                    "subject_id": reader.user_id,
                    "actions": ["read"],
                }
            ],
        ),
    )
    assert (await kb.service.read(reader, "list_materials"))["total"] == 1
    await kb.grant_scope("reader", "self")
    assert (await kb.service.read(reader, "list_materials"))["items"] == []
    await kb.membership("owner")
    await kb.grant_scope("reader", "teams", [kb.team])
    # Explicit platform team scope may refer to a team the reader manages without membership.
    assert (await kb.service.read(reader, "list_materials"))["total"] == 1
    await kb.admin.execute("UPDATE platform.team SET status='inactive' WHERE id=$1", kb.team)
    with pytest.raises(PermissionError):
        await kb.service.read(reader, "list_materials")
    await kb.grant_scope("reader", "workspace")
    assert (await kb.service.read(reader, "list_materials"))["total"] == 1
    await kb.grant_scope("reader", None, effect="deny")
    with pytest.raises(PermissionError):
        await kb.service.read(reader, "list_materials")


@pytest.mark.asyncio
async def test_missing_catalog_module_and_inactive_user_fail_closed(kb):
    owner = kb.actors["owner"]
    with pytest.raises(LookupError, match="KNOWLEDGE_CATALOG_UNCONFIGURED"):
        await kb.service.read(owner, "catalog")
    assert (await kb.service.count(owner)).model_dump()["count"] is None
    await kb.configure()
    await kb.admin.execute(
        "UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='knowledge'", kb.workspace
    )
    with pytest.raises(PermissionError):
        await kb.service.save(owner, uuid4(), selection())
    await kb.admin.execute(
        "UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='knowledge'", kb.workspace
    )
    await kb.admin.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", UUID(owner.user_id))
    with pytest.raises(PermissionError):
        await kb.service.read(owner, "catalog")


@pytest.mark.asyncio
async def test_files_cannot_be_reused_to_evade_source_revocation(kb):
    await kb.configure()
    owner, reader = kb.actors["owner"], kb.actors["reader"]
    uploaded = await kb.service.upload(owner, uuid4(), "source.txt", "text/plain", b"source")
    source = await kb.service.save(owner, uuid4(), selection(files=[uploaded["file_id"]]))
    mid = UUID(source["material_id"])
    await kb.service.save_acl(
        owner,
        uuid4(),
        mid,
        ACLWrite(
            expected_acl_revision=1,
            grants=[
                {
                    "subject_kind": "user",
                    "subject_id": reader.user_id,
                    "actions": ["read", "edit"],
                }
            ],
        ),
    )
    for actor in [owner, reader]:
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="KNOWLEDGE_FILE_DENIED"):
            await kb.service.save(actor, uuid4(), selection(files=[uploaded["file_id"]], name="copy"))
    with pytest.raises(LookupError):
        await kb.service.download(reader, UUID(uploaded["file_id"]), uuid4())
    await kb.admin.execute("UPDATE knowledge.version SET state='deleted' WHERE id=$1", UUID(source["version_id"]))
    with pytest.raises(LookupError):
        await kb.service.download(owner, UUID(uploaded["file_id"]), UUID(source["version_id"]))


@pytest.mark.asyncio
async def test_concurrent_receipt_and_name_are_atomic(kb):
    await kb.configure()
    owner = kb.actors["owner"]
    key = uuid4()
    first, second = await asyncio.gather(
        kb.service.save(owner, key, selection()),
        kb.service.save(owner, key, selection()),
    )
    assert first == second
    outcomes = await asyncio.gather(
        kb.service.save(owner, uuid4(), selection(name="racing name")),
        kb.service.save(owner, uuid4(), selection(name="racing name")),
        return_exceptions=True,
    )
    assert sum(isinstance(x, dict) for x in outcomes) == 1
    assert sum(isinstance(x, asyncpg.SerializationError) for x in outcomes) == 1
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.version WHERE workspace_id=$1", kb.workspace) == 2


@pytest.mark.asyncio
async def test_unattached_file_is_private_and_upload_replay_obeys_revoke(kb):
    await kb.configure()
    await kb.service.save_catalog(kb.actors["other"], uuid4(), CatalogWrite(**catalog_values()))
    owner, reader = kb.actors["owner"], kb.actors["reader"]
    key = uuid4()
    uploaded = await kb.service.upload(owner, key, "private.txt", "text/plain", b"private upload")
    assert await kb.service.upload(owner, key, "private.txt", "text/plain", b"private upload") == uploaded
    with pytest.raises(IdempotencyConflict):
        await kb.service.upload(owner, key, "private.txt", "text/plain", b"different bytes")
    for actor in [reader, kb.actors["other"]]:
        with pytest.raises(LookupError):
            await kb.service.read(actor, "file_metadata", UUID(uploaded["file_id"]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="KNOWLEDGE_FILE_DENIED"):
            await kb.service.save(actor, uuid4(), selection(files=[uploaded["file_id"]]))
    receipt = await kb.service.save(owner, uuid4(), selection(files=[uploaded["file_id"]]))
    await kb.service.save_acl(
        owner,
        uuid4(),
        UUID(receipt["material_id"]),
        ACLWrite(
            expected_acl_revision=1,
            denied_user_ids=[owner.user_id],
        ),
    )
    with pytest.raises(LookupError):
        await kb.service.upload(owner, key, "private.txt", "text/plain", b"private upload")


@pytest.mark.asyncio
async def test_catalog_conflict_and_invalid_sql_rollback(kb):
    await kb.configure()
    owner = kb.actors["owner"]
    async with kb.database.transaction(owner) as c:
        for patch in [
            {"content_name": "spoofed"},
            {"product_code": "unknown"},
            {"content": {"links": [None]}},
            {"content": {"links": ["https://user:password@example.com/"]}},
            {"content": {"links": []}},
            {"customer_id": str(uuid4())},
        ]:
            payload = {**selection().model_dump(mode="json"), **patch}
            with pytest.raises(asyncpg.InvalidParameterValueError):
                async with c.transaction():
                    await c.fetchval("SELECT security.knowledge_save_material(NULL,NULL,$1::jsonb)", payload)
        with pytest.raises(asyncpg.SerializationError, match="KNOWLEDGE_CATALOG_CHANGED"):
            async with c.transaction():
                payload = {**selection().model_dump(mode="json"), "expected_catalog_revision": 9}
                await c.fetchval("SELECT security.knowledge_save_material(NULL,NULL,$1::jsonb)", payload)
    assert await kb.admin.fetchval("SELECT count(*) FROM knowledge.material WHERE workspace_id=$1", kb.workspace) == 0


@pytest.mark.asyncio
async def test_live_authority_ignores_cached_grants_and_no_implicit_defaults(kb):
    await kb.configure()
    ungranted = await kb.user("no-grants", permissions=[])
    with pytest.raises(PermissionError):
        await kb.service.read(ungranted, "catalog")
    owner = kb.actors["owner"]
    await kb.service.save(owner, uuid4(), selection())
    async with kb.database.transaction(owner, readonly=True) as c:
        assert await c.fetchval("SELECT security.knowledge_feature('knowledge.read')")
        await kb.grant_scope("owner", None, effect="deny")
        # This connection's authorization_refresh snapshot is now stale. New KB checks use live tables.
        assert not await c.fetchval("SELECT security.knowledge_feature('knowledge.read')")
        assert await c.fetchval("SELECT count(*) FROM knowledge.material") == 0


@pytest.mark.asyncio
async def test_team_acl_requires_current_membership_and_actions_are_independent(kb):
    await kb.configure()
    owner, reader = kb.actors["owner"], kb.actors["reader"]
    receipt = await kb.service.save(owner, uuid4(), selection())
    mid = UUID(receipt["material_id"])
    await kb.service.save_acl(
        owner,
        uuid4(),
        mid,
        ACLWrite(
            expected_acl_revision=1,
            grants=[
                {
                    "subject_kind": "team",
                    "subject_id": kb.team,
                    "actions": ["read"],
                }
            ],
        ),
    )
    assert (await kb.service.read(reader, "list_materials"))["total"] == 0

    await kb.membership("reader")
    assert (await kb.service.read(reader, "list_materials"))["total"] == 1
    with pytest.raises(PermissionError):
        await kb.service.save(reader, uuid4(), selection(name="illegal edit"), mid, 1)
    with pytest.raises(PermissionError):
        await kb.service.read(reader, "acl", mid)
    await kb.admin.execute(
        "UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE user_ref_id=$1",
        UUID(reader.user_id),
    )
    assert (await kb.service.read(reader, "list_materials"))["total"] == 0


@pytest.mark.asyncio
async def test_catalog_and_acl_sql_validation_and_manage_scope(kb):
    await kb.configure()
    owner = kb.actors["owner"]
    saved = await kb.service.save(owner, uuid4(), selection())
    mid = UUID(saved["material_id"])
    async with kb.database.transaction(owner) as c:
        for option in [{"label": "Missing code", "active": True}, {"code": 7, "label": "Number", "active": True}]:
            values = {
                k: v
                for k, v in CatalogWrite(**catalog_values()).model_dump(mode="json").items()
                if k != "expected_revision"
            }
            values["products"] = [option]
            with pytest.raises(asyncpg.InvalidParameterValueError):
                async with c.transaction():
                    await c.fetchval("SELECT security.knowledge_save_catalog($1,$2::jsonb)", 1, values)
        for grants in [
            [{"actions": ["read"]}],
            [{"subject_kind": "company", "actions": ["read", None]}],
            [{"subject_kind": "user", "subject_id": kb.actors["other"].user_id, "actions": ["read"]}],
        ]:
            with pytest.raises(asyncpg.InvalidParameterValueError):
                async with c.transaction():
                    await c.fetchval(
                        "SELECT security.knowledge_save_acl($1,$2,$3::jsonb,$4::uuid[])", mid, 1, grants, []
                    )
        assert (await kb.service.read(owner, "acl", mid))["revision"] == 1
    await kb.grant_scope("owner", "self", action="catalog_manage")
    with pytest.raises(PermissionError):
        await kb.service.save_catalog(owner, uuid4(), CatalogWrite(**{**catalog_values(), "expected_revision": 1}))


@pytest.mark.asyncio
async def test_assigned_scope_needs_explicit_user_grant_even_for_owner(kb):
    await kb.configure()
    owner = kb.actors["owner"]
    receipt = await kb.service.save(owner, uuid4(), selection())
    mid = UUID(receipt["material_id"])
    await kb.grant_scope("owner", "assigned")
    assert (await kb.service.read(owner, "list_materials"))["total"] == 0
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await kb.service.save(owner, uuid4(), selection(name="cannot create invisible material"))
    await kb.grant_scope("owner", "workspace")
    await kb.service.save_acl(
        owner,
        uuid4(),
        mid,
        ACLWrite(
            expected_acl_revision=1,
            grants=[
                {
                    "subject_kind": "user",
                    "subject_id": owner.user_id,
                    "actions": ["read"],
                }
            ],
        ),
    )
    await kb.grant_scope("owner", "assigned")
    assert (await kb.service.read(owner, "list_materials"))["total"] == 1
