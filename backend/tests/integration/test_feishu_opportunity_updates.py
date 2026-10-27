"""Human opportunity update snapshots and deliveries in a disposable restricted DB.

No Feishu network or credential access: the real outbox/repository is exercised
with a fake send_card transport. All bootstrap writes use explicit owner seeds.
"""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.feishu_sync.config import SyncConfig
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.services.feishu_sync import FeishuSyncService
from sales_backend.services.opportunities import save_opportunity
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.test_feishu_storage import setup as other_company
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_opportunity_lifecycle import setup_customer
from tests.integration.test_profile_scores import business_login
from tests.test_feishu_sync_policy import opportunity_payload

pytestmark = pytest.mark.asyncio


async def configured(connection, sales_actor):
    _, customer = await setup_customer(connection, sales_actor)
    admin = await actor(connection, "ADMIN001")
    data = opportunity_payload()
    data.update(workspace_id=sales_actor.workspace_id, connection_id=str(uuid4()), credential_ref=str(uuid4()))
    config = SyncConfig.model_validate(data)
    # Persist a legacy configuration with no on_update key: V188 defaults it on.
    settings = config.model_dump(mode="json")
    settings["notification"].pop("on_update")
    async with fixture_owner(connection):
        await connection.execute(
            "INSERT INTO config.feishu_connection(id,workspace_id,revision,settings,updated_by,"
            "validated_revision,enabled) VALUES($1,$2,1,$3,$4,1,true)",
            config.connection_id, config.workspace_id, settings, UUID(admin.user_id),
        )
    await assert_restricted(connection)
    return SimpleNamespace(customer=customer, config=config, admin=admin, sales=sales_actor)


def body(**changes):
    return {"name": "更新通知隔离商机-" + uuid4().hex, "amount": "500000", "probability": 10,
            "expected_close_date": "2026-12-01", "sales_channel": "direct", **changes}


async def create_business(connection, fixture):
    data = body()
    await set_request_context(connection, fixture.sales)
    result = await save_opportunity(connection, fixture.sales, customer_id=fixture.customer["id"],
                                    data={**data, "expected_close_date": date.fromisoformat(data["expected_close_date"])})
    return data, result


async def snapshot_events(connection, fixture):
    await set_request_context(connection, fixture.admin)
    await assert_restricted(connection)
    return await connection.fetch(
        "SELECT * FROM ops.feishu_event WHERE connection_id=$1 AND notification_snapshot IS NOT NULL ORDER BY sequence_no",
        fixture.config.connection_id,
    )


async def insert_audit(connection, fixture, opportunity, *, changes=None, change_id=None, workspace=None,
                       customer=None, actor_id=None):
    event_id = change_id or uuid4()
    await connection.execute(
        "INSERT INTO crm.business_change(id,workspace_id,customer_id,opportunity_id,actor_user_ref_id,version_no,changes) "
        "VALUES($1,$2,$3::uuid,$4::uuid,$5::uuid,2,$6)", event_id,
        workspace or fixture.config.workspace_id, customer or fixture.customer["id"], opportunity["id"],
        actor_id or fixture.sales.user_id,
        changes if changes is not None else [{"label": "跟进计划", "before": "安排评审", "after": "确认评审时间"}],
    )
    return event_id


@pytest.mark.parametrize("endpoint", ["manual", "console"])
async def test_http_updates_freeze_actor_amount_and_retry_once_while_noop_stays_quiet(connection, sales_actor, endpoint):
    fixture = await configured(connection, sales_actor)
    data = body()
    path = (f"/api/v1/customers/{fixture.customer['id']}/opportunities" if endpoint == "manual"
            else "/api/v1/console/opportunities")
    if endpoint == "console":
        data.update(customer_id=fixture.customer["id"], owner_user_ref_id=sales_actor.user_id)
    async with await client_for(connection, auth_mode="demo" if endpoint == "manual" else "password") as client:
        if endpoint == "manual":
            await business_login(client, "XS001")
        else:
            await sign_in(client, "ADMIN001")
        created = await client.post(path, json=data)
        assert created.status_code == 201, created.text
        assert not await snapshot_events(connection, fixture)
        update = {**data, "action": "update", "opportunity_id": created.json()["id"],
                  "version_no": created.json()["version_no"], "amount": "12345.67"}
        key = {"Idempotency-Key": str(uuid4())}
        saved = await client.post(path, json=update, headers=key)
        assert saved.status_code == 201, saved.text
        events = await snapshot_events(connection, fixture)
        assert len(events) == 1
        event = events[0]
        snapshot = event["notification_snapshot"]
        change = await connection.fetchrow("SELECT * FROM crm.business_change WHERE id=$1", event["id"])
        assert str(event["id"]) == saved.json()["event_id"] == snapshot["change_id"]
        assert snapshot["workspace_id"] == sales_actor.workspace_id
        assert snapshot["opportunity_id"] == created.json()["id"]
        assert snapshot["opportunity_name"] == data["name"]
        assert snapshot["customer_name"] == fixture.customer["name"]
        assert snapshot["actor_name"] == ("XS001" if endpoint == "manual" else "ADMIN001")
        assert snapshot["owner_name"] == "XS001"
        assert datetime.fromisoformat(snapshot["changed_at"]) == change["created_at"]
        assert snapshot["changes"] == [{"label": "ACV（万元）", "before": "50.00", "after": "1.234567"}]
        assert snapshot["currency"] == "CNY"
        assert not event["historical"] and not event["first_formal_create"]
        replay = await client.post(path, json=update, headers=key)
        assert replay.status_code == 201 and replay.json() == saved.json(), replay.text
        assert len(await snapshot_events(connection, fixture)) == 1
        noop = await client.post(path, json={**update, "version_no": saved.json()["version_no"]})
        assert noop.status_code == 201 and not noop.json()["changed"], noop.text
        assert len(await snapshot_events(connection, fixture)) == 1


async def test_snapshot_is_frozen_minimal_and_immutable_but_queue_state_can_progress(connection, sales_actor):
    fixture = await configured(connection, sales_actor)
    _, opportunity = await create_business(connection, fixture)
    changes = [{"label": "跟进计划", "before": "前", "after": "后", "private_note": "不许外传"},
               {"label": "未变化", "before": "同", "after": "同"},
               {"label": "嵌套内容", "before": {"secret": "不许外传"}, "after": "后"},
               {"label": "", "before": "前", "after": "后"}]
    await set_request_context(connection, sales_actor)
    change_id = await insert_audit(connection, fixture, opportunity, changes=changes)
    events = await snapshot_events(connection, fixture)
    assert len(events) == 1
    frozen = deepcopy(events[0]["notification_snapshot"])
    assert frozen["changes"] == [{"label": "跟进计划", "before": "前", "after": "后"}]
    assert "private_note" not in str(frozen) and "不许外传" not in str(frozen)
    await connection.execute("UPDATE crm.customer SET name='后续客户名称' WHERE id=$1::uuid", fixture.customer["id"])
    await connection.execute("UPDATE crm.opportunity SET name='后续商机名称' WHERE id=$1::uuid", opportunity["id"])
    await connection.execute("UPDATE platform.user_ref SET display_name='后续人员名称' WHERE id=$1::uuid", sales_actor.user_id)
    assert (await snapshot_events(connection, fixture))[0]["notification_snapshot"] == frozen
    with pytest.raises(asyncpg.CheckViolationError, match="immutable"):
        async with connection.transaction():
            await connection.execute("UPDATE ops.feishu_event SET notification_snapshot=NULL WHERE id=$1", change_id)
    await connection.execute("UPDATE ops.feishu_event SET status='failed',error_code='SYNTHETIC_RETRY' WHERE id=$1", change_id)
    stored = await connection.fetchrow("SELECT * FROM ops.feishu_event WHERE id=$1", change_id)
    assert stored["status"] == "failed" and stored["notification_snapshot"] == frozen


async def test_rollback_removes_change_snapshot_and_generic_event_together(connection, sales_actor):
    fixture = await configured(connection, sales_actor)
    data, opportunity = await create_business(connection, fixture)
    before = await connection.fetchval("SELECT count(*) FROM crm.business_change WHERE opportunity_id=$1::uuid", opportunity["id"])
    await set_request_context(connection, fixture.admin)
    event_count = await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1", fixture.config.connection_id)
    transaction = connection.transaction()
    await transaction.start()
    try:
        await set_request_context(connection, sales_actor)
        updated = await save_opportunity(connection, sales_actor, customer_id=fixture.customer["id"], data={
            **data, "expected_close_date": date.fromisoformat(data["expected_close_date"]), "amount": Decimal("250000"),
            "action": "update", "opportunity_id": opportunity["id"], "version_no": opportunity["version_no"],
        })
        assert updated["changed"] and len(await snapshot_events(connection, fixture)) == 1
    finally:
        await transaction.rollback()
    assert not await snapshot_events(connection, fixture)
    assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1", fixture.config.connection_id) == event_count
    assert await connection.fetchval("SELECT count(*) FROM crm.business_change WHERE opportunity_id=$1::uuid", opportunity["id"]) == before
    assert await connection.fetchval("SELECT amount FROM crm.opportunity WHERE id=$1::uuid", opportunity["id"]) == Decimal("500000")


@pytest.mark.parametrize("suppression", ["historical", "job", "connection", "notification", "empty_updates", "mapping", "module", "noop"])
async def test_nonhuman_disabled_or_noop_audits_do_not_capture_notifications(connection, sales_actor, suppression):
    fixture = await configured(connection, sales_actor)
    _, opportunity = await create_business(connection, fixture)
    await set_request_context(connection, fixture.admin)
    if suppression == "historical":
        await connection.execute("SET LOCAL app.feishu_historical_import='on'")
    elif suppression == "connection":
        await connection.execute("UPDATE config.feishu_connection SET enabled=false WHERE id=$1", fixture.config.connection_id)
    elif suppression in ("notification", "empty_updates", "mapping"):
        paths = {"notification": "{notification,enabled}", "empty_updates": "{notification,on_update}",
                 "mapping": "{mappings,opportunity,enabled}"}
        value = [] if suppression == "empty_updates" else False
        await connection.execute("UPDATE config.feishu_connection SET settings=jsonb_set(settings,$2::text[],$3::jsonb) WHERE id=$1",
                                 fixture.config.connection_id, paths[suppression].strip('{}').split(','), value)
    elif suppression == "module":
        async with fixture_owner(connection):
            await connection.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='feishu'",
                                     fixture.config.workspace_id)
    await set_request_context(connection, sales_actor)
    if suppression == "job":
        await connection.execute("SELECT set_config('app.job_id',$1,true)", str(uuid4()))
    changes = ([{"label": "计划", "before": "不变", "after": "不变"}] if suppression == "noop" else None)
    await insert_audit(connection, fixture, opportunity, changes=changes)
    # Inspect by fixture owner only when the disabled module intentionally denies management visibility.
    if suppression == "module":
        async with fixture_owner(connection):
            count = await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND notification_snapshot IS NOT NULL",
                                              fixture.config.connection_id)
        assert count == 0
    else:
        assert not await snapshot_events(connection, fixture)
    await assert_restricted(connection)


async def test_new_human_edit_of_imported_opportunity_notifies_without_reclassifying_history(connection, sales_actor):
    fixture = await configured(connection, sales_actor)
    await set_request_context(connection, fixture.admin)
    historical_id = uuid4()
    async with fixture_owner(connection):
        await connection.execute("SET LOCAL app.feishu_historical_import='on'")
        await connection.execute(
            "INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,amount,probability,stage_code,"
            "owner_user_ref_id,owner_team_id,expected_close_date,expected_close_year,expected_close_quarter,"
            "sales_channel,import_meta) VALUES($1,$2,$3::uuid,'历史导入商机',500000,10,'identified',$4::uuid,$5::uuid,"
            "'2026-12-01',2026,4,'direct','{\"import_type\":\"crm_history\"}')",
            historical_id, fixture.config.workspace_id, fixture.customer["id"], sales_actor.user_id, sales_actor.team_ids[0],
        )
    # A successful savepoint keeps LOCAL settings; the normal human request clears import context explicitly.
    await connection.execute("SET LOCAL app.feishu_historical_import='off'")
    await set_request_context(connection, sales_actor)
    version = await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1", historical_id)
    updated = await save_opportunity(connection, sales_actor, customer_id=fixture.customer["id"], data={
        "name": "历史导入商机", "amount": Decimal("400000"), "probability": 10,
        "expected_close_date": date(2026, 12, 1), "sales_channel": "direct",
        "action": "update", "opportunity_id": str(historical_id), "version_no": version,
    })
    events = await snapshot_events(connection, fixture)
    assert len(events) == 1 and str(events[0]["id"]) == updated["event_id"]
    assert not events[0]["historical"]
    assert await connection.fetchval("SELECT import_meta->>'import_type' FROM crm.opportunity WHERE id=$1", historical_id) == "crm_history"
    assert await connection.fetchval("SELECT bool_and(historical) FROM ops.feishu_event WHERE connection_id=$1 AND object_id=$2 AND notification_snapshot IS NULL",
                                     fixture.config.connection_id, historical_id)


async def test_snapshot_visibility_and_capture_remain_tenant_bound(connection, sales_actor):
    fixture = await configured(connection, sales_actor)
    _, opportunity = await create_business(connection, fixture)
    await set_request_context(connection, sales_actor)
    event_id = await insert_audit(connection, fixture, opportunity)
    assert len(await snapshot_events(connection, fixture)) == 1
    second_workspace, second_connection = await other_company(connection)
    await assert_restricted(connection)
    assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE id=$1", event_id) == 0
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await insert_audit(connection, fixture, opportunity, workspace=second_workspace)
    assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND notification_snapshot IS NOT NULL",
                                     second_connection) == 0
    assert len(await snapshot_events(connection, fixture)) == 1


async def test_worker_replays_frozen_update_delivery_once_without_real_feishu(connection, sales_actor):
    fixture = await configured(connection, sales_actor)
    _, opportunity = await create_business(connection, fixture)
    await set_request_context(connection, sales_actor)
    change_id = await insert_audit(connection, fixture, opportunity)
    await set_request_context(connection, fixture.admin)
    # Finish unrelated generic sync events; leave the real snapshot event for the native lease queue.
    await connection.execute("UPDATE ops.feishu_event SET status='succeeded' WHERE connection_id=$1 AND notification_snapshot IS NULL",
                             fixture.config.connection_id)
    await connection.execute("SET LOCAL ROLE salegent_feishu_worker")
    await assert_restricted(connection)
    repository = FeishuRepository()
    event = await repository.claim(connection)
    assert event and event["id"] == change_id
    transport = SimpleNamespace(send_card=AsyncMock(return_value="om_isolated_update"))
    service = FeishuSyncService(None)
    await service.notify(connection, dict(event), fixture.config, {"record_status": "有效"}, {}, transport, record_id="recIsolated")
    deliveries = await repository.deliveries(connection, change_id)
    assert len(deliveries) == 1 and deliveries[0]["status"] == "sent"
    assert deliveries[0]["payload"]["header"]["title"]["content"] == "商机更新"
    frozen = deepcopy(deliveries[0]["payload"])
    await service.notify(connection, dict(event), fixture.config,
                         {"record_status": "有效", "name": "后来更新的名字"}, {}, transport, record_id="recChanged")
    transport.send_card.assert_awaited_once()
    assert (await repository.deliveries(connection, change_id))[0]["payload"] == frozen
    await repository.finish(connection, event)
    assert await connection.fetchval("SELECT status FROM ops.feishu_event WHERE id=$1", change_id) == "succeeded"
