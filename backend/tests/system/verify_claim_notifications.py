"""Check existing-account claim receipts under RLS; all business writes roll back.

Run explicitly as the local PostgreSQL administrator, using a named database.
No accounts, migrations, model calls or permanent business records are created.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sales_backend.db import set_request_context
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.notifications import NotificationRepository

PG_SOCKET = "/tmp"  # noqa: S108 — the managed PostgreSQL Unix socket, not a temporary file


async def verify(connection, operator_account):
    async def login(account):
        row = await connection.fetchrow(
            "SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,NULL)", account
        )
        assert row, "Existing verification account is unavailable"
        context = IdentityRepository._actor(row).context
        await set_request_context(connection, context)
        return context

    repository = NotificationRepository()
    operator = await login(operator_account)
    customers = []
    for index in range(2):
        customers.append(await CustomerMutationRepository().create(connection, operator, data={
            "name": "认领通知回滚核验-" + uuid4().hex,
            "industry": "软件", "customer_type": "潜在客户", "level_code": "Tier-2",
            "source": "销售线索", "target_team": "南区", "partner_name": "",
            "contact_name": "核验联系人", "contact_title": "经理", "contact_role": "决策者",
            "company_reference": "ROLLBACK-" + str(index) + "-" + uuid4().hex,
        }))
    first = await login("XS001")
    requests = [await connection.fetchval("SELECT security.claim_customer($1::uuid)", c["id"]) for c in customers]
    assert all(r["status"] == "pending" and not r["claimed"] for r in requests)
    await login("XS002")
    await connection.fetchval("SELECT security.claim_customer($1::uuid)", customers[0]["id"])
    await login(operator_account)
    for request, decision in zip(requests, ("approved", "rejected"), strict=True):
        await connection.fetchval(
            "SELECT security.review_customer_claim($1::uuid,$2,$3)",
            request["request_id"], decision, "请核实公司客户编号",
        )
    await login("XS001")
    notes = await repository.list(connection, unread_only=False, limit=100)
    own = [n for n in notes if n["object_id"] in {c["id"] for c in customers}]
    assert len(own) == 2
    assert {n["title"] for n in own} == {"客户认领已通过", "客户认领未通过"}
    for customer in customers:
        note = next(n for n in own if n["object_id"] == customer["id"])
        assert note["payload"]["customer_name"] == customer["name"]
        assert note["payload"]["customer_id"] == customer["id"] and note["payload"]["event_id"]
        assert note["body"] == "请核实公司客户编号"
        assert note["status"] == "pending" and note["read_at"] is None
    assert await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customers[0]["id"])
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customers[1]["id"])
    # Other notification contracts and the page limit are unchanged.
    for note in notes:
        if note["template_code"] != "customer_claim":
            assert note["payload"] == await connection.fetchval(
                "SELECT payload FROM workflow.notification WHERE id=$1::uuid", note["id"]
            )
    assert len(await repository.list(connection, unread_only=False, limit=1)) == 1
    own_id = own[0]["id"]
    await login("XS002")
    competing = [n for n in await repository.list(connection, unread_only=True, limit=100)
                 if n["object_id"] == customers[0]["id"]]
    assert len(competing) == 1 and competing[0]["title"] == "客户认领未通过"
    assert competing[0]["payload"]["customer_name"] == customers[0]["name"]
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customers[0]["id"])
    assert not await repository.mark_read(connection, notification_id=own_id)
    await login("XS001")
    assert await repository.mark_read(connection, notification_id=own_id)
    assert own_id not in {n["id"] for n in await repository.list(connection, unread_only=True, limit=100)}
    await set_request_context(connection, first.model_copy(update={"workspace_id": str(uuid4())}))
    assert not await repository.list(connection, unread_only=False, limit=100)
    return [c["id"] for c in customers]


async def main(args):
    connection = await asyncpg.connect(host=PG_SOCKET, database=args.database, user="postgres", command_timeout=20)
    for name in ("json", "jsonb"):
        await connection.set_type_codec(name, schema="pg_catalog", encoder=json.dumps, decoder=json.loads)
    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute("SET LOCAL ROLE sales_runtime")
        assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        customer_ids = await verify(connection, args.operator_account)
    finally:
        await transaction.rollback()
        await connection.close()
    # Separate transaction confirms that verification did not leave customers.
    connection = await asyncpg.connect(host=PG_SOCKET, database=args.database, user="postgres", command_timeout=20)
    try:
        assert not await connection.fetchval("SELECT count(*) FROM crm.customer WHERE id=ANY($1::uuid[])", customer_ids)
    finally:
        await connection.close()
    print(json.dumps({"status": "passed", "runtime_role": "sales_runtime", "rolled_back": True,
                      "checks": ["approval_receipt", "rejection_reason", "customer_reference_without_history_access",
                                 "competing_claim_rejected", "recipient_and_workspace_isolation",
                                 "explicit_mark_read", "bounded_page", "other_payloads_unchanged"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--operator-account", default="OPS001")
    asyncio.run(main(parser.parse_args()))
