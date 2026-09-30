"""Real PostgreSQL maintenance safety: all fixtures are synthetic and rolled back."""
import copy
import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tests.integration.feishu_fixtures import fixture_owner
from tests.integration.test_weekly_feishu_publish import _settings

SPEC = importlib.util.spec_from_file_location(
    "synthetic_trial_data", Path(__file__).resolve().parents[3] / "scripts" / "synthetic_trial_data.py"
)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)
pytestmark = pytest.mark.asyncio


async def fixture(connection):
    ws, admin, team = uuid4(), uuid4(), uuid4()
    await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离维护工具验收')", ws, str(ws))
    await connection.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'test-sales','销售团队')", team, ws)
    owners = [uuid4(), uuid4()]
    for user, role in [(admin, "administrator"), *[(u, "sales") for u in owners]]:
        await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)", user, ws, user.hex)
        await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)", ws, user, role, "workspace" if role == "administrator" else "self", team)
        if role == "sales":
            await connection.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, user, team)
    batch = "trial-" + uuid4().hex
    connection_id = uuid4()
    settings = _settings(ws, connection_id)
    settings["mappings"].update({"customer": {"enabled": True}, "opportunity": {"enabled": True}})
    await connection.execute("""INSERT INTO config.feishu_connection
        (id,workspace_id,revision,enabled,settings,validated_revision,updated_by)
        VALUES($1,$2,1,true,$3,1,$4)""", connection_id, ws, settings, admin)
    doc = {"workspace_id": str(ws), "batch_id": batch, "scenarios": [
        {"owner_user_ref_id": str(owner), "team_id": str(team),
         "customer": {"name": "验证客户" + str(index), "main_business": "设备研发", "industry": "制造"},
         "opportunity": {"name": "网络建设", "stage_label": "商机识别" if index == 0 else "方案设计",
                         "amount_cny": 860000, "expected_close_date": "2027-01-29", "follow_up_plan": "核对首期范围"}}
        for index, owner in enumerate(owners)
    ]}
    plan = tool.normalize(doc, workspace=ws, batch=batch, actor_user_id=admin, expected_sales=2)
    return doc, plan


async def test_complete_seed_is_idempotent_and_cleanup_soft_archives(connection):
    async with fixture_owner(connection):
        _, plan = await fixture(connection)
        initial = await tool.execute(connection, plan)
        assert initial["state"] == "absent" and not initial["changed"]
        applied = await tool.execute(connection, plan, "apply")
        assert applied["expected_counts"] == {"customer": 2, "opportunity": 2, "forecast": 1}
        assert applied["changed"] and applied["cleanup_allowed"]
        replay = await tool.execute(connection, plan, "apply")
        assert replay["result"] == "already_applied" and not replay["changed"]
        ws = plan["workspace"]
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE workspace_id=$1::uuid", ws) == 0
        assert await connection.fetchval("SELECT count(*) FROM workflow.task WHERE workspace_id=$1::uuid", ws) == 0
        assert await connection.fetchval("SELECT count(*) FROM crm.customer_actual WHERE workspace_id=$1::uuid", ws) == 0
        assert await connection.fetchval("SELECT count(*) FROM ops.job WHERE workspace_id=$1::uuid", ws) == 0
        assert await connection.fetchval("SELECT sum(recognized_amount)+sum(collection_amount) FROM crm.opportunity_forecast WHERE workspace_id=$1::uuid", ws) == 0
        assert (await tool.execute(connection, plan, "cleanup"))["cleanup_allowed"]
        with pytest.raises(tool.Refused, match="exact"):
            await tool.execute(connection, plan, "cleanup", apply_cleanup=True, confirm_batch="wrong")
        retired = await tool.execute(connection, plan, "cleanup", apply_cleanup=True, confirm_batch=plan["batch"])
        assert retired["state"] == "archived" and retired["changed"]
        assert (await tool.execute(connection, plan, "cleanup", apply_cleanup=True, confirm_batch=plan["batch"]))["result"] == "already_archived"
        assert await connection.fetchval("SELECT count(*) FROM crm.customer WHERE workspace_id=$1::uuid AND deleted_at IS NOT NULL", ws) == 2
        assert await connection.fetchval("SELECT count(*) FROM crm.opportunity_forecast WHERE workspace_id=$1::uuid", ws) == 1
        with pytest.raises(tool.Refused, match="archived"):
            await tool.execute(connection, plan, "apply")


async def test_exact_roster_and_explicit_workspace_fail_closed(connection):
    async with fixture_owner(connection):
        doc, plan = await fixture(connection)
        with pytest.raises(tool.Refused, match="workspace/batch"):
            tool.normalize(doc, workspace=uuid4(), batch=plan["batch"], actor_user_id=plan["actor_user_id"], expected_sales=2)
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", plan["rows"][0]["owner_user_ref_id"])
        with pytest.raises(tool.Refused, match="roster"):
            await tool.execute(connection, plan, "apply")
        assert await connection.fetchval("SELECT count(*) FROM crm.customer WHERE workspace_id=$1::uuid", plan["workspace"]) == 0


async def test_business_edit_prevents_overwrite_and_cleanup(connection):
    async with fixture_owner(connection):
        _, plan = await fixture(connection)
        await tool.execute(connection, plan, "apply")
        cid = plan["rows"][0]["customer"]["id"]
        await connection.execute("UPDATE crm.customer SET next_action='使用者已记录后续安排' WHERE id=$1::uuid", cid)
        dry = await tool.execute(connection, plan, "cleanup")
        assert dry["state"] == "conflict" and not dry["cleanup_allowed"]
        with pytest.raises(tool.Refused):
            await tool.execute(connection, plan, "apply")
        with pytest.raises(tool.Refused, match="Cleanup refused"):
            await tool.execute(connection, plan, "cleanup", apply_cleanup=True, confirm_batch=plan["batch"])
        assert await connection.fetchval("SELECT next_action FROM crm.customer WHERE id=$1::uuid", cid) == "使用者已记录后续安排"
        assert await connection.fetchval("SELECT count(*) FROM crm.customer WHERE workspace_id=$1::uuid AND deleted_at IS NOT NULL", plan["workspace"]) == 0


async def test_new_opportunity_and_detached_task_are_cleanup_conflicts(connection):
    async with fixture_owner(connection):
        _, plan = await fixture(connection)
        await tool.execute(connection, plan, "apply")
        group = plan["rows"][0]
        cid, oid = group["customer"]["id"], group["opportunity"]["id"]
        tid = await connection.fetchval("""INSERT INTO workflow.task(workspace_id,title,description,
            customer_id,opportunity_id,creator_user_ref_id,creator_team_id,due_at,association_kind)
            VALUES($1::uuid,'确认新需求','销售自行新增的内容',$2::uuid,$3::uuid,$4::uuid,$5::uuid,
            clock_timestamp()+interval '3 days','customer') RETURNING id""",
            plan["workspace"], cid, oid, group["owner_user_ref_id"], group["team_id"])
        # Provenance survives detachment; name/association-based cleanup would miss it.
        await connection.execute("UPDATE workflow.task SET opportunity_id=NULL,customer_id=NULL,association_kind='daily' WHERE id=$1", tid)
        dry = await tool.execute(connection, plan, "cleanup")
        assert any(r["table"] == "workflow.task" and str(tid) in [str(v) for v in r["ids"]] for r in dry["related_content"])
        assert not dry["cleanup_allowed"]
        with pytest.raises(tool.Refused, match="Cleanup refused"):
            await tool.execute(connection, plan, "cleanup", apply_cleanup=True, confirm_batch=plan["batch"])
        assert await connection.fetchval("SELECT deleted_at IS NULL FROM crm.customer WHERE id=$1::uuid", cid)


async def test_forecast_edits_and_new_links_are_not_discarded(connection):
    async with fixture_owner(connection):
        _, plan = await fixture(connection)
        await tool.execute(connection, plan, "apply")
        forecast = next(r["forecast"] for r in plan["rows"] if r["forecast"])
        await connection.execute("UPDATE crm.opportunity_forecast SET recognized_amount=100000,updated_at=clock_timestamp() WHERE id=$1::uuid", forecast["id"])
        dry = await tool.execute(connection, plan, "cleanup")
        assert any(c.get("kind") == "forecast" for c in dry["conflicts"])
        assert not dry["cleanup_allowed"]


async def test_partial_batch_rolls_back_and_does_not_touch_existing_records(connection):
    async with fixture_owner(connection):
        _, plan = await fixture(connection)
        group = plan["rows"][0]
        # A pre-existing real record has the deterministic ID: never adopt it.
        spec = copy.deepcopy(group["customer"])
        spec["data_kind"] = "production"
        spec["data_source"] = "manual"
        await tool.insert_record(connection, "crm.customer", spec)
        with pytest.raises(tool.Refused, match="conflicts"):
            await tool.execute(connection, plan, "apply")
        assert await connection.fetchval("SELECT count(*) FROM crm.customer WHERE workspace_id=$1::uuid", plan["workspace"]) == 1
        assert await connection.fetchval("SELECT data_kind FROM crm.customer WHERE id=$1::uuid", spec["id"]) == "production"


async def test_nonmaintenance_runtime_cannot_use_tool(connection):
    if await connection.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"):
        pytest.skip("Requires the integration runner's non-superuser role")
    # Authorization is checked before reading any supplied UUID's company data.
    with pytest.raises(tool.Refused, match="owner/superuser"):
        await tool.preflight(connection, {"workspace": str(uuid4())})


async def test_late_insert_failure_rolls_back_the_entire_batch(connection, monkeypatch):
    async with fixture_owner(connection):
        _, plan = await fixture(connection)
        original = tool.insert_record
        calls = 0

        async def fail_late(c, table, record):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise tool.Refused("Injected late verification failure")
            await original(c, table, record)

        monkeypatch.setattr(tool, "insert_record", fail_late)
        with pytest.raises(tool.Refused, match="late"):
            await tool.execute(connection, plan, "apply")
        for table in ("crm.customer", "crm.opportunity", "crm.customer_ownership", "crm.customer_sales_member"):
            assert await connection.fetchval(f"SELECT count(*) FROM {table} WHERE workspace_id=$1::uuid", plan["workspace"]) == 0


async def test_cross_company_id_collision_cannot_be_adopted(connection):
    async with fixture_owner(connection):
        _, plan = await fixture(connection)
        _, foreign = await fixture(connection)
        foreign_spec = copy.deepcopy(foreign["rows"][0]["customer"])
        foreign_spec["id"] = plan["rows"][0]["customer"]["id"]
        await tool.insert_record(connection, "crm.customer", foreign_spec)
        with pytest.raises(tool.Refused):
            await tool.execute(connection, plan, "apply")
        assert await connection.fetchval("SELECT count(*) FROM crm.customer WHERE workspace_id=$1::uuid", plan["workspace"]) == 0
        assert await connection.fetchval("SELECT workspace_id::text FROM crm.customer WHERE id=$1::uuid", foreign_spec["id"]) == foreign["workspace"]


async def test_new_collaborator_relationship_blocks_retirement(connection):
    async with fixture_owner(connection):
        _, plan = await fixture(connection)
        await tool.execute(connection, plan, "apply")
        root, other = plan["rows"]
        await connection.execute("""INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id,added_by_user_ref_id)
            VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid)""", plan["workspace"], root["customer"]["id"], other["owner_user_ref_id"], plan["actor_user_id"])
        dry = await tool.execute(connection, plan, "cleanup")
        assert not dry["cleanup_allowed"]
        assert any(r["table"] == "crm.customer_sales_member" for r in dry["related_content"])
        with pytest.raises(tool.Refused, match="Cleanup refused"):
            await tool.execute(connection, plan, "cleanup", apply_cleanup=True, confirm_batch=plan["batch"])
