"""V117 retention using synthetic records in a disposable PostgreSQL database.

The production descriptor is deliberately not loaded. The unmodified function
must first reject our synthetic descriptor. A transaction-local copy changes
only its fixed descriptor fingerprint; all authorization, dependency, update and
retention SQL remains byte-for-byte unchanged. Test rollback restores the
original function. This proves synthetic lifecycle behavior, not production
cleanup or a replay of the historical batch.
"""
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from tests.integration.feishu_fixtures import (
    assert_restricted,
    seed_execute,
    seed_fetchrow,
    seed_fetchval,
)
from tests.integration.test_feishu_storage import setup

pytestmark = pytest.mark.asyncio
FUNCTION = "security.feishu_notification_test_archive_v2(jsonb,text)"
PRODUCTION_FINGERPRINT = "d0ca45656178b3331b9f3a4de8013f0b0f7e314c0c176d7a80c127e15708963b"


async def invoke(connection, descriptor, plan=None):
    await assert_restricted(connection)
    return await connection.fetchval(
        "SELECT security.feishu_notification_test_archive_v2($1::jsonb,$2::text)", descriptor, plan
    )


async def synthetic_batch(connection, *, conversation_status="active", review_status="succeeded"):
    latest=await connection.fetchval("SELECT max(substring(version FROM 2)::int) FROM ops.schema_migration WHERE version ~ '^V[0-9]+$'")
    if latest > 117:
        pytest.skip("Historical V117 success scenarios run in the isolated V117 CI job; current-schema rejection is checked separately")
    workspace, cid = await setup(connection)
    user = await connection.fetchval("SELECT updated_by FROM config.feishu_connection WHERE id=$1", cid)
    marker = "isolated-feishu-retention-" + uuid4().hex
    descriptor = {
        "customer_name": marker, "company_reference": marker, "contact_name": marker + "-contact",
        "opportunity_name": marker + "-opportunity", "visit_marker": marker + "-visit",
        "industry": "software", "created_day": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
    }
    # Guard the real fixed fingerprint before installing the synthetic fixture.
    assert await invoke(connection, descriptor) == {"code": "NOTIFICATION_TEST_DESCRIPTOR_NOT_AUTHORIZED"}
    definition = await connection.fetchval("SELECT pg_get_functiondef($1::regprocedure)", FUNCTION)
    assert definition.count(PRODUCTION_FINGERPRINT) == 1
    fingerprint = await connection.fetchval(
        "SELECT encode(sha256(convert_to($1::jsonb::text,'UTF8')),'hex')", descriptor
    )
    fixture_definition = definition.replace(PRODUCTION_FINGERPRINT, fingerprint)
    assert fixture_definition.replace(fingerprint, PRODUCTION_FINGERPRINT) == definition
    await seed_execute(connection, fixture_definition)
    await connection.execute("SELECT set_config('app.request_id',$1,true)", str(uuid4()))
    customer = await seed_fetchval(connection,
        "INSERT INTO crm.customer(workspace_id,name,normalized_name,company_reference,industry_code,"
        "created_by_user_ref_id,data_kind) VALUES($1,$2,$2,$2,$3,$4,'production') RETURNING id",
        workspace, marker, descriptor["industry"], user)
    opportunity = await seed_fetchval(connection,
        "INSERT INTO crm.opportunity(workspace_id,customer_id,name,created_by_user_ref_id) "
        "VALUES($1,$2,$3,$4) RETURNING id", workspace, customer, descriptor["opportunity_name"], user)
    contact = await seed_fetchval(connection,
        "INSERT INTO crm.contact(workspace_id,customer_id,name,created_by_user_ref_id) "
        "VALUES($1,$2,$3,$4) RETURNING id", workspace, customer, descriptor["contact_name"], user)
    form = await seed_fetchval(connection,
        "INSERT INTO config.form_definition(workspace_id,form_code,name,object_type) "
        "VALUES($1,$2,'isolated retention','visit') RETURNING id", workspace, marker)
    version = await seed_fetchval(connection,
        "INSERT INTO config.form_version(form_definition_id,version_no,status) "
        "VALUES($1,1,'active') RETURNING id", form)
    # Historical notification follow-up had no opportunity link. Do not invent one.
    visit = await seed_fetchval(connection,
        "INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,form_version_id,status,"
        "archived_at,confirmed_at,confirmed_by_user_ref_id,created_by_user_ref_id,follow_up_record,is_first_visit) "
        "VALUES($1,$2,$3,$4,'archived',clock_timestamp(),clock_timestamp(),$3,$3,$5,false) RETURNING id",
        workspace, customer, user, version, descriptor["visit_marker"])
    review = await seed_fetchval(connection,
        "INSERT INTO insight.sales_competency_review(workspace_id,subject_user_ref_id,review_date,"
        "framework_version,status,input_snapshot,summary) "
        "VALUES($1,$2,current_date,1,$3,$4,'synthetic shared history') RETURNING id",
        workspace, user, review_status, {"visit_ids": [str(visit)]})
    conversation = await seed_fetchval(connection,
        "INSERT INTO agent.conversation(workspace_id,user_ref_id,role_code,data_scope_snapshot,status,context) "
        "VALUES($1,$2,'sales','{}',$3,$4) RETURNING id",
        workspace, user, conversation_status, {"customer_id": str(customer), "visit_id": str(visit)})
    originals = {
        "review": await seed_fetchrow(connection, "SELECT * FROM insight.sales_competency_review WHERE id=$1", review),
        "conversation": await seed_fetchrow(connection, "SELECT * FROM agent.conversation WHERE id=$1", conversation),
        "visit": await seed_fetchrow(connection,
            "SELECT status,archived_at,confirmed_at,confirmed_by_user_ref_id,archived_fields,follow_up_record "
            "FROM activity.visit WHERE id=$1", visit),
    }
    return descriptor, {"customer": customer, "opportunity": opportunity, "contact": contact,
                        "visit": visit, "review": review, "conversation": conversation}, originals


async def assert_shared_history_unchanged(connection, ids, originals):
    assert await seed_fetchrow(connection,
        "SELECT * FROM insight.sales_competency_review WHERE id=$1", ids["review"]) == originals["review"]
    assert await seed_fetchrow(connection,
        "SELECT * FROM agent.conversation WHERE id=$1", ids["conversation"]) == originals["conversation"]
    assert await seed_fetchrow(connection,
        "SELECT status,archived_at,confirmed_at,confirmed_by_user_ref_id,archived_fields,follow_up_record "
        "FROM activity.visit WHERE id=$1", ids["visit"]) == originals["visit"]


@pytest.mark.parametrize("conversation_status", ["active", "closed", "expired"])
async def test_completed_shared_review_and_all_conversation_states_are_retained(connection, conversation_status):
    descriptor, ids, originals = await synthetic_batch(connection, conversation_status=conversation_status)
    plan = await invoke(connection, descriptor)
    assert plan["dry_run"] and len(plan["records"]) == 4
    assert await invoke(connection, descriptor, "outdated-plan") == {"code": "PLAN_CHANGED"}
    result = await invoke(connection, descriptor, plan["plan_hash"])
    assert result["archived"]
    for kind, table in {"customer": "crm.customer", "opportunity": "crm.opportunity",
                        "contact": "crm.contact", "visit": "activity.visit"}.items():
        assert await seed_fetchval(connection,
            f"SELECT deleted_at IS NOT NULL FROM {table} WHERE id=$1", ids[kind])  # noqa: S608
    await assert_shared_history_unchanged(connection, ids, originals)
    replay = await invoke(connection, descriptor, plan["plan_hash"])
    assert replay["already_archived"]
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.audit_log WHERE action_code='feishu.integration_test.named_archive'"
    ) == 1


@pytest.mark.parametrize("review_status", ["queued", "running", "failed"])
async def test_unfinished_or_failed_shared_review_blocks_without_changes(connection, review_status):
    descriptor, ids, originals = await synthetic_batch(connection, review_status=review_status)
    assert await invoke(connection, descriptor) == {
        "code": "DEPENDENCY_REQUIRES_REVIEW:insight.sales_competency_review"
    }
    assert await seed_fetchval(connection,
        "SELECT deleted_at IS NULL FROM crm.customer WHERE id=$1", ids["customer"])
    await assert_shared_history_unchanged(connection, ids, originals)


async def test_current_schema_blocks_legacy_v108_archive_entry_point(connection):
    await setup(connection)
    await assert_restricted(connection)
    assert await connection.fetchval("SELECT security.feishu_test_archive('{}'::jsonb,NULL)") == {
        "code": "ARCHIVE_SCHEMA_OR_OWNER_REVIEW_REQUIRED"
    }


async def test_current_schema_blocks_v117_archive_entry_point(connection):
    latest=await connection.fetchval("SELECT max(substring(version FROM 2)::int) FROM ops.schema_migration WHERE version ~ '^V[0-9]+$'")
    if latest <= 117:
        pytest.skip("This assertion requires a schema newer than the frozen V117 entry point")
    await setup(connection)
    assert await invoke(connection, {}) == {"code":"ARCHIVE_SCHEMA_OR_OWNER_REVIEW_REQUIRED"}
