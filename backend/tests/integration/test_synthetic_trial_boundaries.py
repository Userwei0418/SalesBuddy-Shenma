# ruff: noqa: S608 -- Table names are a fixed test-only literal allowlist.
"""V159: trial provenance and external-report boundaries on real non-bypass PG."""

from uuid import uuid4

import asyncpg
import pytest

from sales_backend.services.weekly_source import build_snapshot
from tests.integration.feishu_fixtures import fixture_owner, seed_execute, seed_fetchval
from tests.integration.test_weekly_feishu_publish import _fixture

pytestmark = pytest.mark.asyncio


async def customer(c, actor, *, synthetic=True, workspace=None):
    ident = uuid4()
    marker = {"batch_id": "acceptance-trial", "root_customer_id": str(ident), "source": "trial_seed"}
    await seed_execute(
        c,
        """INSERT INTO crm.customer(id,workspace_id,name,normalized_name,
        owner_user_ref_id,owner_team_id,created_by_user_ref_id,data_kind,import_meta,created_at,updated_at)
        VALUES($1,$2::uuid,$3,$3,$4::uuid,$5::uuid,$4::uuid,$6,$7,
        transaction_timestamp()-interval '2 hours',transaction_timestamp()-interval '2 hours')""",
        ident,
        workspace or actor.workspace_id,
        "场景客户" + ident.hex[:8],
        actor.user_id,
        actor.team_ids[0],
        "demo" if synthetic else "production",
        {"synthetic_trial": marker} if synthetic else {},
    )
    return ident, marker


async def opportunity(c, actor, cid):
    return await seed_fetchval(
        c,
        """INSERT INTO crm.opportunity(workspace_id,customer_id,name,
        owner_user_ref_id,owner_team_id,created_by_user_ref_id)
        VALUES($1::uuid,$2,'服务器集群更新',$3::uuid,$4::uuid,$3::uuid) RETURNING id""",
        actor.workspace_id,
        cid,
        actor.user_id,
        actor.team_ids[0],
    )


async def visit(c, actor, cid, oid=None):
    return await seed_fetchval(
        c,
        """INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,
        recorder_user_ref_id,recorder_team_id,created_by_user_ref_id,form_version_id,status,created_at,
        interaction_at,follow_up_record,next_action,archived_at)
        VALUES($1::uuid,$2,$3,$4::uuid,$5::uuid,$4::uuid,
        (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
        'archived',transaction_timestamp()-interval '1 hour',transaction_timestamp()-interval '1 day',
        '已完成需求梳理，计划评审容量清单','下周确认预算和技术测试安排',clock_timestamp()) RETURNING id""",
        actor.workspace_id,
        cid,
        oid,
        actor.user_id,
        actor.team_ids[0],
    )


async def test_marker_inheritance_and_immutable_origin_allow_business_edits(connection, sales_actor):
    cid, marker = await customer(connection, sales_actor)
    oid = await opportunity(connection, sales_actor, cid)
    vid = await visit(connection, sales_actor, cid, oid)
    tid = await seed_fetchval(
        connection,
        """INSERT INTO workflow.task(workspace_id,title,description,
        customer_id,opportunity_id,source_visit_id,creator_user_ref_id,creator_team_id,due_at,association_kind)
        VALUES($1::uuid,'确认方案','确认方案',$2,$3,$4,$5::uuid,$6::uuid,
        clock_timestamp()+interval '3 days','customer') RETURNING id""",
        sales_actor.workspace_id,
        cid,
        oid,
        vid,
        sales_actor.user_id,
        sales_actor.team_ids[0],
    )
    for kind, table, ident in [
        ("customer", "crm.customer", cid),
        ("opportunity", "crm.opportunity", oid),
        ("visit", "activity.visit", vid),
        ("task", "workflow.task", tid),
    ]:
        assert await connection.fetchval("SELECT security.is_synthetic_trial_subject($1,$2)", kind, ident)
        assert (
            await seed_fetchval(connection, f"SELECT import_meta->'synthetic_trial' FROM {table} WHERE id=$1", ident)
            == marker
        )
        with pytest.raises(asyncpg.InvalidParameterValueError, match="SYNTHETIC_TRIAL_ORIGIN_IMMUTABLE"):
            async with connection.transaction():
                await seed_execute(
                    connection, f"UPDATE {table} SET import_meta=import_meta-'synthetic_trial' WHERE id=$1", ident
                )
    await connection.execute("UPDATE crm.customer SET name='已核对容量需求的客户' WHERE id=$1", cid)
    assert await connection.fetchval("SELECT name FROM crm.customer WHERE id=$1", cid) == "已核对容量需求的客户"
    with pytest.raises(asyncpg.InvalidParameterValueError, match="SYNTHETIC_TRIAL_CUSTOMER_KIND_REQUIRED"):
        async with connection.transaction():
            await seed_execute(connection, "UPDATE crm.customer SET data_kind='production' WHERE id=$1", cid)


async def test_mixed_real_and_different_trial_roots_are_rejected(connection, sales_actor):
    cid, _ = await customer(connection, sales_actor)
    real, _ = await customer(connection, sales_actor, synthetic=False)
    other, _ = await customer(connection, sales_actor)
    oid = await opportunity(connection, sales_actor, cid)
    real_oid = await opportunity(connection, sales_actor, real)
    other_oid = await opportunity(connection, sales_actor, other)
    vid = await visit(connection, sales_actor, cid, oid)
    for bad_oid in (real_oid, other_oid):
        with pytest.raises(asyncpg.InvalidParameterValueError, match="SYNTHETIC_TRIAL_MIXED_ROOTS"):
            async with connection.transaction():
                await seed_execute(
                    connection,
                    """INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id)
                    VALUES($1::uuid,$2,$3)""",
                    sales_actor.workspace_id,
                    vid,
                    bad_oid,
                )
    with pytest.raises(asyncpg.InvalidParameterValueError, match="SYNTHETIC_TRIAL_MIXED_ROOTS"):
        async with connection.transaction():
            await seed_execute(connection, "UPDATE crm.opportunity SET customer_id=$2 WHERE id=$1", oid, real)
    assert not await connection.fetchval("SELECT security.is_synthetic_trial_subject('customer',$1)", real)


async def test_private_resolver_and_cross_company_probe_remain_inaccessible(connection, sales_actor):
    cid, _ = await customer(connection, sales_actor)
    assert await connection.fetchval("SELECT security.is_synthetic_trial_visit($1)", uuid4()) is False
    for fn in (
        "security.synthetic_trial_marker(uuid,text,uuid)",
        "security.synthetic_trial_weekly_source(uuid,uuid)",
        "security.protect_synthetic_trial_origin()",
        "security.protect_synthetic_trial_visit_link()",
    ):
        assert not await connection.fetchval("SELECT has_function_privilege(current_user,$1,'EXECUTE')", fn)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval(
                "SELECT security.synthetic_trial_marker($1::uuid,'customer',$2)", sales_actor.workspace_id, cid
            )
    other = uuid4()
    await seed_execute(
        connection,
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'另一公司')",
        other,
        str(other),
    )
    # A valid ID in another workspace returns the same false as an absent ID.
    await connection.execute("SELECT set_config('app.workspace_id',$1,true)", str(other))
    assert not await connection.fetchval("SELECT security.is_synthetic_trial_subject('customer',$1)", cid)


async def test_formal_weekly_count_and_snapshot_ignore_trial_but_preserve_real(connection, sales_actor):
    cid, _ = await customer(connection, sales_actor)
    real, _ = await customer(connection, sales_actor, synthetic=False)
    trial_visit = await visit(connection, sales_actor, cid)
    real_visit = await visit(connection, sales_actor, real)
    source, _raw, _sha = await build_snapshot(connection, sales_actor, str(uuid4()))
    assert [r["id"] for r in source["records"]] == [str(real_visit)]
    assert str(trial_visit) not in str(source)
    assert source["statistics"] == {"record_count": 1, "customer_count": 1, "opportunity_count": 0}


async def test_publish_and_worker_recheck_trial_reclassification_including_replay(connection, sales_actor):
    config, report = await _fixture(connection, sales_actor)
    event = await connection.fetchval("SELECT security.publish_weekly_report_feishu($1,1)", report)
    cid = await seed_fetchval(
        connection,
        "SELECT (input_snapshot::jsonb->'context'->'customers'->0->>'id')::uuid FROM insight.weekly_report WHERE id=$1",
        report,
    )
    await seed_execute(connection, "UPDATE crm.customer SET data_kind='demo' WHERE id=$1", cid)
    with pytest.raises(asyncpg.InvalidParameterValueError, match="WEEKLY_SYNTHETIC_TRIAL_SOURCE"):
        async with connection.transaction():
            await connection.fetchval("SELECT security.publish_weekly_report_feishu($1,1)", report)
    # Worker has no author context; the explicit connection tenant governs lookup.
    async with fixture_owner(connection):
        await connection.execute("SET LOCAL ROLE salegent_feishu_worker")
        result = await connection.fetchval("SELECT ops.feishu_weekly_source($1,$2)", config, report)
    assert result == {"id": str(report), "excluded": True}
    assert await seed_fetchval(connection, "SELECT count(*) FROM ops.feishu_event WHERE id=$1", event) == 1


async def test_unpublished_snapshot_reclassified_as_trial_never_queues(connection, sales_actor):
    config, report = await _fixture(connection, sales_actor)
    cid = await seed_fetchval(
        connection,
        "SELECT (input_snapshot::jsonb->'context'->'customers'->0->>'id')::uuid FROM insight.weekly_report WHERE id=$1",
        report,
    )
    await seed_execute(connection, "UPDATE crm.customer SET data_kind='test' WHERE id=$1", cid)
    with pytest.raises(asyncpg.InvalidParameterValueError, match="WEEKLY_SYNTHETIC_TRIAL_SOURCE"):
        async with connection.transaction():
            await connection.fetchval("SELECT security.publish_weekly_report_feishu($1,1)", report)
    assert (
        await seed_fetchval(
            connection,
            "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_kind='weekly_report'",
            config,
        )
        == 0
    )


async def test_unlinked_trial_task_retains_provenance_and_stays_out_of_feishu(connection, sales_actor):
    config, _report = await _fixture(connection, sales_actor)
    cid, marker = await customer(connection, sales_actor)
    oid = await opportunity(connection, sales_actor, cid)
    tid = await seed_fetchval(
        connection,
        """INSERT INTO workflow.task(workspace_id,title,description,
        customer_id,opportunity_id,creator_user_ref_id,creator_team_id,due_at,association_kind)
        VALUES($1::uuid,'下一步事项','核对技术规格',$2,$3,$4::uuid,$5::uuid,
        clock_timestamp()+interval '3 days','customer') RETURNING id""",
        sales_actor.workspace_id,
        cid,
        oid,
        sales_actor.user_id,
        sales_actor.team_ids[0],
    )
    await seed_execute(
        connection,
        """UPDATE workflow.task SET customer_id=NULL,opportunity_id=NULL,
        association_kind='daily' WHERE id=$1""",
        tid,
    )
    assert await connection.fetchval("SELECT security.is_synthetic_trial_subject('task',$1)", tid)
    assert (
        await seed_fetchval(connection, "SELECT import_meta->'synthetic_trial' FROM workflow.task WHERE id=$1", tid)
        == marker
    )
    async with fixture_owner(connection):
        await connection.execute("SET LOCAL ROLE salegent_feishu_worker")
        for kind, ident in [("customer", cid), ("opportunity", oid), ("task", tid)]:
            assert await connection.fetchval("SELECT ops.feishu_source($1,$2,$3)", config, kind, ident) == {
                "id": str(ident),
                "excluded": True,
            }


async def test_v158_upgrade_preserves_existing_rows_acl_and_rolls_back_busy_or_late_failure(connection):
    from tests.system.verify_synthetic_trial_upgrade import verify

    await verify()


async def test_new_trial_capture_skips_queue_but_real_and_legacy_demo_keep_old_behavior(connection, sales_actor):
    config, _report = await _fixture(connection, sales_actor)
    real, _ = await customer(connection, sales_actor, synthetic=False)
    trial, _ = await customer(connection, sales_actor)
    legacy = await seed_fetchval(
        connection,
        """INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,data_kind)
        VALUES($1::uuid,'旧演示分类','旧演示分类',$2::uuid,'demo') RETURNING id""",
        sales_actor.workspace_id,
        sales_actor.user_id,
    )
    legacy_opportunity = await opportunity(connection, sales_actor, legacy)
    for ident, expected in [(real, 1), (legacy, 1), (legacy_opportunity, 1), (trial, 0)]:
        assert (
            await seed_fetchval(
                connection,
                "SELECT count(*) FROM ops.feishu_event WHERE connection_id=$1 AND object_id=$2",
                config,
                ident,
            )
            == expected
        )
    async with fixture_owner(connection):
        await connection.execute("SET LOCAL ROLE salegent_feishu_worker")
        source = await connection.fetchval("SELECT ops.feishu_source($1,'customer',$2)", config, real)
        assert source["data_kind"] == "production" and source["name"].startswith("场景客户")
        assert not source.get("excluded")
