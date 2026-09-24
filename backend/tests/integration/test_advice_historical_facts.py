"""Imported financial facts retain source precision and invalidate old advice."""
from uuid import uuid4

import pytest

from sales_backend.domain.advice import AdviceError, AdviceRequest, evidence_references
from sales_backend.repositories.advice_facts import AdviceFactsRepository
from sales_backend.services.advice import AdviceHandler, AdviceService
from sales_backend.services.agent_platform.inference import InferenceService
from tests.integration.test_business_advice import database, model
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def test_history_preserves_units_tax_source_and_invalidates_cached_analysis(connection, monkeypatch):
    monkeypatch.setattr(InferenceService, "evaluate", model)
    op = await opportunity(connection, "XS001", 100)
    who = await actor(connection, "XS001")
    repo = AdviceFactsRepository()
    before = await repo.load(connection, who, "opportunity", op["id"])
    service = AdviceService(database(connection))
    request = AdviceRequest(subject_kind="opportunity", subject_id=op["id"])
    old = await service.request(who, request)
    await AdviceHandler(service.database).handle(old["id"], who)
    manager = await actor(connection, "OPS001")
    ws = manager.workspace_id
    batch = await connection.fetchval(
        "INSERT INTO ops.crm_import_batch(workspace_id,source_system,source_base_id,manifest_sha256,source_snapshot_at,status) "
        "VALUES($1::uuid,'synthetic','advice-history',repeat('a',64),clock_timestamp(),'approved') RETURNING id", ws)
    await connection.execute("SELECT set_config('app.feishu_historical_import','on',true)")
    for kind, amount, unit in [("recognized", 54, "wan_cny"), ("collection", 1200, "cny")]:
        snapshot = uuid4()
        source = await connection.fetchval(
            "INSERT INTO ops.crm_import_record(workspace_id,batch_id,source_table_id,source_record_id,object_kind,"
            "source_sha256,raw_fields,status,target_id) VALUES($1::uuid,$2,'synthetic',$3,'period_actual_snapshot',"
            "repeat('b',64),'{}','approved',$4) RETURNING id", ws, batch, str(uuid4()), snapshot)
        await connection.execute(
            "INSERT INTO crm.opportunity_period_actual_snapshot(workspace_id,opportunity_id,year,quarter,kind,"
            "source_field,raw_amount,source_unit,tax_basis,source_record_id,import_batch_id,id) "
            "VALUES($1::uuid,$2::uuid,2026,2,$3,$4,$5,$6,'unknown',$7,$8,$9)",
            ws, op["id"], kind, "Q2 raw " + kind, amount, unit, source, batch, snapshot)
    await actor(connection, "XS001")
    after = await repo.load(connection, who, "opportunity", op["id"])
    rows = after["facts"]["records"]["historical_actuals"]
    by_kind = {r["kind"]: r for r in rows}
    assert float(by_kind["recognized"]["amount_cny"]) == 540000
    assert float(by_kind["collection"]["amount_cny"]) == 1200
    assert float(by_kind["recognized"]["raw_amount"]) == 54
    assert all(r["year"] == 2026 and r["quarter"] == 2 and r["tax_basis"] == "unknown" for r in rows)
    assert all("occurred_on" not in r and r["source_field"] for r in rows)
    assert after["facts"]["subject"]["actuals"]["recognized_amount"] is None
    assert after["fingerprint"] != before["fingerprint"]
    assert all("historical_actuals:" + r["id"] in evidence_references(after["facts"]) for r in rows)
    assert (await service.get(who, old["id"]))["status"] == "superseded"
    fresh = await service.request(who, request)
    assert fresh["id"] != old["id"]
    assert (await service.request(who, request))["id"] == fresh["id"]
    other = await actor(connection, "XS002")
    with pytest.raises(AdviceError):
        await repo.load(connection, other, "opportunity", op["id"])
