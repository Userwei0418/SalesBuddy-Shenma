"""V108 adversarial checks. Run only against an explicitly local disposable DB.

Run with backend/tests/system/run_feishu_archive_v108_postgres.py. The runner
creates the exact V108 schema and a matching non-bypass application role.

Fixture business rows and roles are rolled back after every test. No production
DSN fallback, provider call, runtime migration or deployment is performed here.
"""
import json
import os
import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from tests.integration.test_feishu_test_archive import batch

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def connection():
    dsn = os.environ.get("SALES_TEST_DATABASE_URL", "")
    parts = urlsplit(dsn)
    role = os.environ.get("SALES_TEST_ROLE", "")
    role_match = re.fullmatch(r"salegent_verify_role_([a-f0-9]{12})", role)
    database_match = re.fullmatch(r"/salegent_verify_integration_([a-f0-9]{12})", parts.path)
    query = parse_qs(parts.query)
    socket = query.get("host", [""])[0]
    local = parts.hostname in {"127.0.0.1", "localhost", "::1"} or (
        parts.hostname is None and set(query) == {"host"} and len(query["host"]) == 1
        and socket.startswith("/") and socket == os.environ.get("PGHOST")
    )
    if not (local and role_match and database_match and role_match[1] == database_match[1]):
        pytest.skip("V108 adversarial checks require the local disposable V108 runner and its matching test role")
    conn = await asyncpg.connect(dsn, command_timeout=10)
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(typename, schema="pg_catalog", encoder=json.dumps, decoder=json.loads)
    transaction = conn.transaction()
    await transaction.start()
    try:
        assert await conn.fetchval("SELECT current_database()") == parts.path[1:]
        latest = await conn.fetchval(
            "SELECT max(substring(version FROM 2)::int) FROM ops.schema_migration WHERE version ~ '^V[0-9]+$'"
        )
        if latest != 108:
            pytest.skip("V108 adversarial success checks require the exact historical V108 schema")
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        assert not await conn.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        yield conn
    finally:
        await transaction.rollback()
        await conn.close()


async def call(conn, manifest, plan=None):
    body = manifest if isinstance(manifest, dict) else manifest.model_dump(mode="json")
    return await conn.fetchval("SELECT security.feishu_test_archive($1::jsonb,$2::text)", body, plan)


async def seed_hidden_advice(conn, manifest, *, status="queued", suggestion=False):
    role = await conn.fetchval("SELECT current_user")
    await conn.execute("RESET ROLE")
    other, advice = uuid4(), uuid4()
    await conn.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) "
                       "VALUES($1::uuid,$2,$1::uuid::text,'other advice owner')", other, manifest.workspace_id)
    await conn.execute("""INSERT INTO insight.business_advice
      (id,workspace_id,actor_user_ref_id,actor_role_code,subject_kind,subject_id,customer_id,section,
       cache_key,facts_fingerprint,configuration_fingerprint,identity_snapshot,facts_snapshot,
       configuration_snapshot,status,summary)
      VALUES($1,$2,$3,'sales','customer',$4,$4,'overview',$5,$5,$5,'{}','{}','{}',$6,'DO_NOT_EXPORT_ADVICE_BODY')""",
      advice, manifest.workspace_id, other, manifest.records[0].id, "a" * 64, status)
    if suggestion:
        await conn.execute("""INSERT INTO insight.business_suggestion
          (workspace_id,advice_id,ordinal,title,evidence,action,evidence_refs)
          VALUES($1,$2,1,'DO_NOT_EXPORT_TITLE','DO_NOT_EXPORT_EVIDENCE','DO_NOT_EXPORT_ACTION','[]')""",
          manifest.workspace_id, advice)
    await conn.execute(f'SET LOCAL ROLE "{role}"')
    return advice


async def test_other_actor_force_rls_advice_is_seen_without_exporting_content(connection):
    _, manifest = await batch(connection)
    advice = await seed_hidden_advice(connection, manifest)
    assert not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM insight.business_advice WHERE id=$1)", advice)
    result = await call(connection, manifest)
    assert result == {"code": "DEPENDENCY_REQUIRES_REVIEW:insight.business_advice"}
    assert await connection.fetchval("SELECT deleted_at IS NULL FROM crm.customer WHERE id=$1", manifest.records[0].id)


async def test_pending_suggestion_without_direct_customer_column_is_in_graph(connection):
    _, manifest = await batch(connection)
    await seed_hidden_advice(connection, manifest, status="succeeded", suggestion=True)
    result = await call(connection, manifest)
    assert result == {"code": "DEPENDENCY_REQUIRES_REVIEW:insight.business_suggestion"}


async def test_terminal_advice_metadata_excludes_business_text(connection):
    _, manifest = await batch(connection)
    await seed_hidden_advice(connection, manifest, status="succeeded")
    result = await call(connection, manifest)
    assert result["dry_run"]
    advice = next(x for x in result["retained"] if x["table"] == "insight.business_advice")
    assert set(advice) == {"table", "id", "state", "fingerprint"}
    assert "DO_NOT_EXPORT" not in json.dumps(result)


async def test_json_matching_uses_exact_typed_ids_not_body_substrings(connection):
    _, manifest = await batch(connection)
    role = await connection.fetchval("SELECT current_user")
    await connection.execute("RESET ROLE")
    cid = str(manifest.records[0].id)
    job = await connection.fetchval(
        "INSERT INTO ops.job(workspace_id,job_type,payload) VALUES($1,'fixture',$2) RETURNING id",
        manifest.workspace_id, {"customer_id": cid + "-not-an-id", "body": cid})
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    assert (await call(connection, manifest))["dry_run"]
    await connection.execute("RESET ROLE")
    await connection.execute("UPDATE ops.job SET payload=$2 WHERE id=$1", job, {"nested": {"customer_id": cid}})
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    assert (await call(connection, manifest))["code"] == "DEPENDENCY_REQUIRES_REVIEW:ops.job"


async def test_snapshot_object_arrays_and_source_ids_use_known_reference_contract(connection):
    visit, task, unrelated = uuid4(), uuid4(), uuid4()
    result = await connection.fetchval("SELECT security.feishu_test_reference_ids($1)", {
        "visits": [{"id": str(visit)}], "candidates": [{"source_id": str(task)}],
        "people": [{"id": str(unrelated)}], "body": str(unrelated),
    })
    assert set(result) == {visit, task}


async def test_cross_workspace_reference_fails_without_exposing_other_tenant(connection):
    _, manifest = await batch(connection)
    role = await connection.fetchval("SELECT current_user")
    await connection.execute("RESET ROLE")
    other = await connection.fetchval(
        "INSERT INTO platform.workspace(external_workspace_id,name) "
        "VALUES($1,'DO_NOT_EXPORT_TENANT') RETURNING id", str(uuid4()))
    await connection.execute("INSERT INTO ops.job(workspace_id,job_type,payload) VALUES($1,'fixture',$2)",
                             other, {"customer_id": str(manifest.records[0].id)})
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    assert await call(connection, manifest) == {"code": "CROSS_WORKSPACE_DEPENDENCY"}


async def test_changed_ownership_cannot_use_updated_at_as_creation_evidence(connection):
    _, manifest = await batch(connection)
    role = await connection.fetchval("SELECT current_user")
    await connection.execute("RESET ROLE")
    await connection.execute(
        "UPDATE crm.customer_ownership SET version_no=version_no+1 WHERE customer_id=$1", manifest.records[0].id)
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    assert await call(connection, manifest) == {"code": "OWNERSHIP_CREATION_RECEIPT_NOT_PROVEN"}


async def test_business_role_cannot_invoke_privileged_plan_by_faking_role_name(connection):
    _, manifest = await batch(connection)
    await connection.execute("SET LOCAL app.role_code='sales'")
    assert await call(connection, manifest) == {"code": "WORKSPACE_OR_ROLE_MISMATCH"}


async def test_worker_acl_and_helper_have_no_public_execution(connection):
    for signature in ("security.feishu_test_archive(jsonb,text)", "security.feishu_test_reference_ids(jsonb)"):
        assert not await connection.fetchval(
            "SELECT has_function_privilege('salegent_feishu_worker',$1,'EXECUTE')", signature)
        assert not await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a "
            "WHERE p.oid=$1::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE')", signature)


async def test_apply_nonwaiting_lock_guard_prevents_dependency_race(connection):
    _, manifest = await batch(connection)
    plan = await call(connection, manifest)
    blocker = await asyncpg.connect(os.environ["SALES_TEST_DATABASE_URL"])
    try:
        await blocker.execute("BEGIN; LOCK TABLE workflow.notification IN ROW EXCLUSIVE MODE")
        assert await call(connection, manifest, plan["plan_hash"]) == {"code": "ARCHIVE_BUSY_RETRY"}
        assert await connection.fetchval(
            "SELECT deleted_at IS NULL FROM crm.customer WHERE id=$1", manifest.records[0].id)
    finally:
        await blocker.execute("ROLLBACK")
        await blocker.close()


async def test_root_scope_is_still_required_when_directly_calling_sql(connection):
    _, manifest = await batch(connection)
    body = manifest.model_dump(mode="json")
    body["workspace_id"] = str(uuid4())
    assert await call(connection, body) == {"code": "WORKSPACE_OR_ROLE_MISMATCH"}
    body = manifest.model_dump(mode="json")
    body["records"][0]["table"] = "crm.customer"
    assert await call(connection, body) == {"code": "INVALID_ARCHIVE_MANIFEST"}


async def test_shared_pending_competency_without_visit_snapshot_cannot_race_archive(connection):
    _, manifest = await batch(connection, full=True)
    role = await connection.fetchval("SELECT current_user")
    visit = next(r for r in manifest.records if r.kind == "visit")
    await connection.execute("RESET ROLE")
    await connection.execute("""INSERT INTO insight.sales_competency_review
      (workspace_id,subject_user_ref_id,review_date,framework_version,status)
      VALUES($1,$2,timezone('Asia/Shanghai',clock_timestamp())::date,1,'queued')""",
      manifest.workspace_id, visit.creator_id)
    await connection.execute(f'SET LOCAL ROLE "{role}"')
    assert await call(connection, manifest) == {"code": "SHARED_COMPETENCY_REVIEW_REQUIRES_REVIEW"}
