"""Exercise the HTTP/service/Decimal/SQL boundary with real non-bypass PostgreSQL."""
from uuid import UUID, uuid4

import pytest

from sales_backend.contracts.targets import TargetBatchSave, TargetDecision
from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.targets import TargetRepository
from sales_backend.services.targets import read_targets, save_target_batch
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


def body(items, **extra):
    return TargetBatchSave(anchor_date=today(), reason="与负责人确认季度指标", items=items, **extra)


async def test_target_http_first_submit_then_atomic_change_and_decision_history(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        data=body([{"kind": "collection", "amount": "1000000.01"}]).model_dump(mode="json")
        first=await client.post("/api/v1/targets/batch", json=data, headers={"Idempotency-Key": str(uuid4())})
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "effective"
        row=first.json()["items"][0]
        data=body([{"kind": "collection", "amount": "800000.01", "version_no": row["version_no"]},
                   {"kind": "recognized", "amount": "2000000.02"}]).model_dump(mode="json")
        key=str(uuid4())
        pending=await client.post("/api/v1/targets/batch", json=data, headers={"Idempotency-Key": key})
        assert pending.status_code == 200, pending.text
        assert pending.json()["status"] == "pending"
        request_id=pending.json()["request"]["id"]
        replay=await client.post("/api/v1/targets/batch", json=data, headers={"Idempotency-Key": key})
        assert replay.json()["request"]["id"] == request_id
        read=await client.get("/api/v1/targets", params={"period_type":"quarter", "anchor_date":today().isoformat()})
        assert read.status_code == 200, read.text
        assert len(read.json()["items"]) == 1
        assert read.json()["pending_batches"][0]["items"][0]["proposed_amount"] == 800000.01
        assert read.json()["items"][0]["amount"] == 1000000.01
        assert read.json()["items"][0]["amount_text"] == "1000000.01"
        assert (await client.post(f"/api/v1/console/target-batches/{request_id}/decision",
            json={"decision":"approved"},headers={"Idempotency-Key":str(uuid4())})).status_code == 403
    await actor(connection,"OPS001")
    approved=await TargetRepository().decide_batch(
        connection,UUID(request_id),TargetDecision(decision="approved",reason="负责人已确认"))
    assert approved["status"] == "approved" and len(approved["items"]) == 2
    person=await actor(connection,"XS001")
    read=await read_targets(connection,person,period_type="quarter",anchor_date=today())
    assert read["pending_batches"] == []
    assert read["recent_batches"][0]["decision_reason"] == "负责人已确认"
    assert {r["kind"]:str(r["amount"]) for r in read["items"]} == {
        "collection":"800000.01","recognized":"2000000.02"}


async def test_operator_first_is_existing_and_department_targets_are_independent(connection):
    person=await actor(connection,"XS001")
    operator=await actor(connection,"OPS001")
    configured=await save_target_batch(connection,operator,body(
        [{"kind":"collection","amount":"1000"}],scope="person",user_id=UUID(person.user_id)))
    person=await actor(connection,"XS001")
    change=await save_target_batch(connection,person,body(
        [{"kind":"collection","amount":"800","version_no":configured["items"][0]["version_no"]}]))
    assert change["status"] == "pending"
    operator=await actor(connection,"OPS001")
    for family, amount in (("sales","5000"),("fde","2000")):
        await save_target_batch(connection,operator,body(
            [{"kind":"collection","amount":amount}],scope="department",department_code=family))
        read=await read_targets(connection,operator,scope="department",department_code=family,
                                period_type="quarter",anchor_date=today())
        assert str(read["items"][0]["amount"]) == amount+".00"
    manager=await actor(connection,"ZJL001")
    with pytest.raises(PermissionError):
        await read_targets(connection,manager,scope="department",department_code="fde",
                           period_type="quarter",anchor_date=today())


async def test_http_rejects_missing_reason_and_old_annual_self_writes(connection):
    async with await client_for(connection,auth_mode="demo") as client:
        await business_login(client,"XS001")
        data=body([{"kind":"collection","amount":1}]).model_dump(mode="json")
        del data["reason"]
        result=await client.post("/api/v1/targets/batch",json=data,headers={"Idempotency-Key":str(uuid4())})
        assert result.status_code == 422
        data=body([{"kind":"collection","amount":1}],period_type="year").model_dump(mode="json")
        result=await client.post("/api/v1/targets/batch",json=data,headers={"Idempotency-Key":str(uuid4())})
        assert result.status_code == 409, result.text
        assert "当前季度" in result.json()["detail"]
