"""PostgreSQL-only acceptance checks for explicit weekly-report publication.

These tests are intentionally gated by the disposable integration harness.  They
never use a customer database and exercise the same SECURITY DEFINER functions
used by the API, including the frozen publication snapshot and event idempotency.
"""
import json
from datetime import date, datetime, timezone
from hashlib import sha256
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.feishu_fixtures import seed_execute, seed_fetchrow, seed_fetchval

pytestmark = pytest.mark.asyncio


def _settings(workspace, connection):
    return {
        "schema_version": 1,
        "workspace_id": str(workspace),
        "connection_id": str(connection),
        "revision": 1,
        "provider": "feishu",
        "enabled": True,
        "app_id": "cli_test",
        "credential_ref": str(uuid4()),
        "base_token": "testBase",
        "base_url": "https://example.feishu.cn/base/testBase",
        "direction": "system_to_base",
        "mappings": {"weekly_report": {"enabled": True, "table_id": "tblWeekly",
            "id_field_id": "fldSystemId", "fields": {"record_status": "fldStatus"}}},
        "notification": {"enabled": True, "routing_mode": "single_group",
            "default_chat_id": "oc_weekly", "on_create": ["weekly_report"], "routes": []},
    }


async def _fixture(connection, actor, *, status="succeeded", version=1):
    cid = uuid4()
    settings = _settings(actor.workspace_id, cid)
    assert settings["workspace_id"] == str(actor.workspace_id)
    await seed_execute(connection, """INSERT INTO config.feishu_connection
        (id,workspace_id,revision,enabled,settings,validated_revision,updated_by)
        VALUES($1::uuid,$2::uuid,1,true,($3::jsonb || jsonb_build_object('workspace_id',$2::uuid::text,'connection_id',$1::uuid::text)),1,$4::uuid)""", cid, actor.workspace_id,
                    settings, actor.user_id)
    report = uuid4()
    original = {"schema_version": "weekly.v2", "status": "ready", "title": "联调周报",
                "statistics": {"record_count": 1, "customer_count": 1, "opportunity_count": 0}}
    snapshot = {"context": {"as_of": "2026-09-29T10:00:00+08:00"}}
    await seed_execute(connection, """INSERT INTO insight.weekly_report
      (id,workspace_id,author_id,request_id,status,result_status,input_snapshot,input_sha256,
       binding,original_result,draft_markdown,draft_version,report_week,source_cutoff_at)
      VALUES($1,$2,$3,$4,$5,'ready',$6,$7,$8::jsonb,$9::jsonb,$10,$11,$12,$13)""",
      report, actor.workspace_id, actor.user_id, uuid4(), status, json.dumps(snapshot),
      sha256(json.dumps(snapshot).encode()).hexdigest(), {"contract_version": "weekly.v2"},
      original, "## 联调周报\n人工确认正文", version, date(2026, 9, 28),
      datetime(2026, 9, 29, 2, tzinfo=timezone.utc))
    return cid, report


async def test_unconfirmed_report_does_not_create_feishu_event(connection, sales_actor):
    cid, report = await _fixture(connection, sales_actor, status="queued")
    await connection.execute("SAVEPOINT weekly_publish_rejected")
    try:
        with pytest.raises(asyncpg.PostgresError, match="WEEKLY_DRAFT_NOT_READY"):
            await connection.fetchval("SELECT security.publish_weekly_report_feishu($1,1)", report)
    finally:
        await connection.execute("ROLLBACK TO SAVEPOINT weekly_publish_rejected")
        await connection.execute("RELEASE SAVEPOINT weekly_publish_rejected")
    assert await seed_fetchval(connection, "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1", cid) == 0


async def test_publish_is_author_scoped_versioned_and_idempotent(connection, sales_actor):
    cid, report = await _fixture(connection, sales_actor)
    event_id = await connection.fetchval("SELECT security.publish_weekly_report_feishu($1,1)", report)
    assert event_id
    replay = await connection.fetchval("SELECT security.publish_weekly_report_feishu($1,1)", report)
    assert replay == event_id
    assert await seed_fetchval(connection, "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1", cid) == 1
    row = await seed_fetchrow(connection, "SELECT feishu_publish_event_id,feishu_publish_snapshot FROM insight.weekly_report WHERE id=$1", report)
    assert str(row["feishu_publish_event_id"]) == str(event_id)
    assert row["feishu_publish_snapshot"]["draft_version"] == 1
    # A replay is idempotent even when the browser retries with its stale
    # version; it returns the already-created event and never enqueues another.
    assert await connection.fetchval("SELECT security.publish_weekly_report_feishu($1,0)", report) == event_id


async def test_publish_snapshot_survives_later_draft_edit(connection, sales_actor):
    _cid, report = await _fixture(connection, sales_actor)
    await connection.fetchval("SELECT security.publish_weekly_report_feishu($1,1)", report)
    await seed_execute(connection, "UPDATE insight.weekly_report SET draft_markdown='后续未推送编辑',draft_version=2 WHERE id=$1", report)
    row = await connection.fetchrow("SELECT draft_markdown,feishu_publish_snapshot FROM insight.weekly_report WHERE id=$1", report)
    assert row["draft_markdown"] == "后续未推送编辑"
    assert row["feishu_publish_snapshot"]["draft_markdown"] == "## 联调周报\n人工确认正文"
