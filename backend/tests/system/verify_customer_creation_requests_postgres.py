"""S2 customer-request regression on a fresh database and real non-bypass role.

Run with PGHOST/PGPORT/PGUSER for a local maintenance PostgreSQL instance:
    PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python \
        backend/tests/system/verify_customer_creation_requests_postgres.py

The shared runner owns and drops the database and role. Maintenance access is
used only for fixture setup. Every business action and verification uses the
runtime role inside a transaction, including each independent concurrent action.
No HTTP client, model, worker or external service is started.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch
from uuid import uuid4

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_integration_postgres import main as disposable_database

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.customer_creation_requests import CustomerCreationRequestRepository
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.identity import IdentityRepository


from sales_backend.domain.concurrency import VersionConflict


class InjectedApprovalFailure(RuntimeError):
    """Failure after real writes, used to prove transaction rollback."""


async def run(config: dict, name: str, role: str) -> None:
    assert re.fullmatch(r"salegent_verify_integration_[a-f0-9]+", name), name
    assert re.fullmatch(r"salegent_verify_role_[a-f0-9]+", role), role
    pg = {**config, "port": int(os.environ.get("PGPORT", "5432"))}
    checks: list[str] = []
    repo = CustomerCreationRequestRepository()
    foreign_external = "s2-foreign-" + uuid4().hex
    foreign_workspace, foreign_team = uuid4(), uuid4()
    owner = await asyncpg.connect(database=name, **pg, command_timeout=20)
    try:
        # The only maintenance writes are the second company's fixture identities.
        async with owner.transaction():
            await owner.execute(
                "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'S2 foreign fixture')",
                foreign_workspace, foreign_external,
            )
            await owner.execute(
                "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'S2_FOREIGN','S2 foreign team')",
                foreign_team, foreign_workspace,
            )
            for code, builtin, scope in (("S2_OTHER_SALES", "sales", "self"),
                                          ("S2_OTHER_OPS", "operations", "workspace")):
                user = uuid4()
                await owner.execute(
                    "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) "
                    "VALUES($1,$2,$3,$3,$3)", user, foreign_workspace, code,
                )
                await owner.execute(
                    "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
                    "VALUES($1,$2,$3,$4,$5)", foreign_workspace, user, builtin, scope, foreign_team,
                )
                await owner.execute(
                    "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
                    "VALUES($1,$2,$3,$4)", foreign_workspace, user, foreign_team, builtin,
                )
    finally:
        await owner.close()

    @asynccontextmanager
    async def session(code: str):
        connection = await asyncpg.connect(database=name, **pg, command_timeout=20)
        await _initialize_connection(connection)
        try:
            async with connection.transaction():
                await connection.execute(f'SET LOCAL ROLE "{role}"')
                await connection.execute("SET LOCAL row_security=on")
                await connection.execute("SET LOCAL statement_timeout='12s'")
                await connection.execute("SET LOCAL lock_timeout='8s'")
                identity = await connection.fetchrow(
                    "SELECT current_database() AS db,current_user AS role,rolsuper,rolbypassrls "
                    "FROM pg_roles WHERE rolname=current_user"
                )
                assert identity["db"] == name and identity["role"] == role
                assert not identity["rolsuper"] and not identity["rolbypassrls"], dict(identity)
                workspace = foreign_external if code.startswith("S2_OTHER_") else "demo-sales-workspace"
                record = await connection.fetchrow(
                    "SELECT * FROM security.resolve_account_actor($1,$2,NULL)", workspace, code,
                )
                assert record, (workspace, code)
                actor = IdentityRepository._actor(record).context
                await set_request_context(connection, actor)
                assert await connection.fetchval("SELECT common.current_workspace_id()::text") == actor.workspace_id
                yield connection, actor
        finally:
            await connection.close()

    async def action(code, operation):
        async with session(code) as (connection, actor):
            return await operation(connection, actor)

    async def denied(code, operation, expected=(PermissionError, asyncpg.InsufficientPrivilegeError)):
        try:
            await action(code, operation)
        except expected as exc:
            return type(exc).__name__
        raise AssertionError(f"{code} unexpectedly completed a forbidden operation")

    def passed(label):
        checks.append(label)
        print("S2_CHECK_OK " + label, flush=True)

    async with session("XS001") as (connection, sales):
        main_workspace = sales.workspace_id
        main_team = sales.team_ids[0]
        team_name = await connection.fetchval("SELECT name FROM platform.team WHERE id=$1::uuid", main_team)
        assert await connection.fetchval(
            "SELECT has_table_privilege(current_user,'crm.customer_creation_request','SELECT')"
        )
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not await connection.fetchval(
                "SELECT has_table_privilege(current_user,'crm.customer_creation_request',$1)", privilege,
            ), privilege
    passed("runtime_is_non_bypass_and_request_writes_require_controlled_functions")

    def payload(label: str, *, reference=None, customer_name=None, foreign=False):
        return {
            "company_reference": reference or "S2-" + uuid4().hex,
            "name": customer_name or "S2 regression " + label + " " + uuid4().hex,
            "industry": "软件", "customer_type": "潜在客户", "level_code": "Tier-2",
            "source": "销售自拓", "target_team": "S2 foreign team" if foreign else team_name,
            "target_team_id": str(foreign_team) if foreign else main_team,
            "partner_name": "", "contact_name": "S2 fixture contact", "contact_title": "经理",
            "contact_role": "决策者", "contact_phone": "", "contact_email": "",
        }

    async def submit(code: str, body: dict):
        return await action(code, lambda connection, actor: repo.submit(connection, actor, body))

    async def decide(code: str, request_id: str, decision="approved", reason=""):
        return await action(code, lambda connection, actor: repo.decide(
            connection, actor, request_id, decision, reason,
        ))

    async def request_state(request_id: str, code="OPS001"):
        return await action(code, lambda connection, actor: connection.fetchrow(
            "SELECT id::text,status,created_customer_id::text,version_no FROM crm.customer_creation_request "
            "WHERE id=$1::uuid", request_id,
        ))

    async def customer_count(body: dict, *, reference=False):
        return await action("OPS001", lambda connection, actor: connection.fetchval(
            "SELECT count(*) FROM crm.customer WHERE workspace_id=$1::uuid AND deleted_at IS NULL AND "
            + ("lower(btrim(company_reference))=lower(btrim($2))" if reference else
               "normalized_name=lower(regexp_replace($2,'\\s+','','g'))"),
            main_workspace, body["company_reference" if reference else "name"],
        ))

    # Request permission is independent of direct customer creation and review.
    requests = {}
    for code in ("XS001", "ZJ001", "ZJL001"):
        body = payload("role-" + code)
        result = await submit(code, body)
        assert result["status"] == "pending" and await customer_count(body) == 0
        requests[code] = (result, body)
        await denied(code, lambda connection, actor: CustomerMutationRepository().create(
            connection, actor, data=payload("direct-forbidden"),
        ))
        await denied(code, lambda connection, actor: repo.decide(connection, actor, result["id"], "approved"))
        await denied(code, lambda connection, actor: repo.queue(connection, actor))
        mine = await action(code, lambda connection, actor: repo.mine(connection, actor))
        assert result["id"] in {item["id"] for item in mine["items"]}
    for code in ("OPS001", "ADMIN001"):
        await denied(code, lambda connection, actor: repo.submit(connection, actor, payload("reviewer-request")))
        queue = await action(code, lambda connection, actor: repo.queue(connection, actor, limit=1))
        assert queue["total"] == 3 and len(queue["items"]) == 1 and queue["has_more"]
        empty = await action(code, lambda connection, actor: repo.queue(connection, actor, offset=100))
        assert empty["total"] == 3 and not empty["items"] and not empty["has_more"]
    passed("request_and_review_role_matrix_mine_queue_pagination")

    xs_request, xs_body = requests["XS001"]
    other_mine = await action("XS002", lambda connection, actor: repo.mine(connection, actor))
    assert not other_mine["items"]
    not_found = (PermissionError, LookupError, asyncpg.InsufficientPrivilegeError,
                 asyncpg.NoDataFoundError, asyncpg.RaiseError)
    await denied("XS002", lambda connection, actor: repo.withdraw(connection, actor, xs_request["id"]), not_found)
    async with session("XS002") as (connection, _):
        assert await connection.fetchval(
            "SELECT count(*) FROM crm.customer_creation_request WHERE id=$1::uuid", xs_request["id"],
        ) == 0
    assert (await request_state(xs_request["id"]))["status"] == "pending"
    passed("other_sales_cannot_read_or_withdraw_request")

    # Raw SQL and repository paths both remain within the current company.
    foreign_request = await submit("S2_OTHER_SALES", payload("foreign", foreign=True))
    for code in ("S2_OTHER_SALES", "S2_OTHER_OPS"):
        async with session(code) as (connection, _):
            assert await connection.fetchval(
                "SELECT count(*) FROM crm.customer_creation_request WHERE id=$1::uuid", xs_request["id"],
            ) == 0
        await denied(code, lambda connection, actor: connection.execute(
            "UPDATE crm.customer_creation_request SET decision_reason='forbidden' WHERE id=$1::uuid",
            xs_request["id"],
        ), (asyncpg.InsufficientPrivilegeError,))
    await denied("S2_OTHER_OPS", lambda connection, actor: repo.decide(
        connection, actor, xs_request["id"], "approved",
    ), not_found)
    await denied("S2_OTHER_SALES", lambda connection, actor: repo.withdraw(
        connection, actor, xs_request["id"],
    ), not_found)
    await denied("XS001", lambda connection, actor: repo.submit(connection, actor, payload("wrong-team", foreign=True)))
    await denied("OPS001", lambda connection, actor: repo.decide(
        connection, actor, foreign_request["id"], "approved",
    ), not_found)
    async with session("OPS001") as (connection, actor):
        assert await connection.fetchval(
            "SELECT count(*) FROM crm.customer_creation_request WHERE id=$1::uuid", foreign_request["id"],
        ) == 0
        assert foreign_request["id"] not in {
            item["id"] for item in (await repo.queue(connection, actor))["items"]
        }
    passed("cross_company_read_update_review_withdraw_and_team_selection_denied")

    await denied("XS001", lambda connection, actor: repo.submit(connection, actor, xs_body),
                 (FileExistsError, asyncpg.UniqueViolationError))
    assert (await request_state(xs_request["id"]))["status"] == "pending"
    passed("duplicate_pending_request_rejected_without_customer")

    approved = await decide("OPS001", xs_request["id"])
    assert approved["status"] == "approved" and approved["customer"]["status"] == "unclaimed"
    async with session("OPS001") as (connection, _):
        customer = await connection.fetchrow(
            "SELECT c.data_kind,c.company_reference,c.owner_team_id::text,c.owner_user_ref_id,o.state "
            "FROM crm.customer c JOIN crm.customer_ownership o ON o.customer_id=c.id "
            "WHERE c.id=$1::uuid", approved["customer"]["id"],
        )
        assert customer["data_kind"] == "production" and customer["state"] == "unclaimed"
        assert customer["owner_user_ref_id"] is None and customer["owner_team_id"] == main_team
        assert customer["company_reference"] == xs_body["company_reference"]
    admin_approved = await decide("ADMIN001", requests["ZJ001"][0]["id"])
    assert admin_approved["status"] == "approved"
    await denied("XS002", lambda connection, actor: repo.submit(connection, actor, xs_body),
                 (FileExistsError, asyncpg.UniqueViolationError))
    await denied("OPS001", lambda connection, actor: repo.decide(
        connection, actor, xs_request["id"], "approved",
    ), (VersionConflict, ValueError, asyncpg.RaiseError))
    assert await customer_count(xs_body) == 1
    passed("operations_and_admin_approval_create_one_production_unclaimed_customer")

    reject_id = requests["ZJL001"][0]["id"]
    await denied("OPS001", lambda connection, actor: repo.decide(connection, actor, reject_id, "rejected", " "),
                 (ValueError, asyncpg.InvalidParameterValueError))
    rejected = await decide("ADMIN001", reject_id, "rejected", "资料需补充")
    assert rejected["status"] == "rejected" and rejected["decision_reason"] == "资料需补充"
    assert await customer_count(requests["ZJL001"][1]) == 0
    withdrawal_body = payload("withdraw")
    withdrawal = await submit("XS001", withdrawal_body)
    cancelled = await action("XS001", lambda connection, actor: repo.withdraw(connection, actor, withdrawal["id"]))
    assert cancelled["status"] == "cancelled" and await customer_count(withdrawal_body) == 0
    await denied("OPS001", lambda connection, actor: repo.decide(
        connection, actor, withdrawal["id"], "approved",
    ), (VersionConflict, ValueError, asyncpg.RaiseError))
    mine = await action("XS001", lambda connection, actor: repo.mine(connection, actor, status="cancelled"))
    assert withdrawal["id"] in {item["id"] for item in mine["items"]}
    passed("rejection_reason_and_self_withdraw_terminal_states")

    async def race(ids):
        ready = 0
        start = asyncio.Event()

        async def contender(code, request_id):
            nonlocal ready
            async with session(code) as (connection, actor):
                ready += 1
                if ready == len(ids):
                    start.set()
                await start.wait()
                return await repo.decide(connection, actor, request_id, "approved")

        tasks = [asyncio.create_task(contender(code, request_id))
                 for code, request_id in zip(("OPS001", "ADMIN001"), ids)]
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=25)
        finally:
            start.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        winners = [result for result in results if isinstance(result, dict)]
        losers = [result for result in results if isinstance(result, BaseException)]
        assert len(winners) == len(losers) == 1, [repr(item) for item in results]
        assert winners[0]["status"] == "approved"
        assert isinstance(losers[0], (VersionConflict, ValueError, FileExistsError, asyncpg.UniqueViolationError, asyncpg.RaiseError)), results
        return winners[0]

    same_body = payload("concurrent-same-request")
    same = await submit("XS001", same_body)
    await race((same["id"], same["id"]))
    assert await customer_count(same_body) == 1
    assert (await request_state(same["id"]))["version_no"] == 2
    passed("concurrent_same_request_creates_one_customer")

    name_body = payload("concurrent-name")
    same_name_body = {**payload("concurrent-name-2"), "name": name_body["name"].upper().replace(" ", "  ")}
    first = await submit("XS001", name_body)
    second = await submit("XS002", same_name_body)
    await race((first["id"], second["id"]))
    assert await customer_count(name_body) == 1
    states = [(await request_state(item["id"]))["status"] for item in (first, second)]
    assert sorted(states) == ["approved", "pending"], states
    passed("concurrent_different_requests_normalized_same_name_create_one_customer")

    reference_body = payload("concurrent-reference")
    same_reference_body = payload("concurrent-reference-2", reference=reference_body["company_reference"])
    first = await submit("XS001", reference_body)
    second = await submit("XS002", same_reference_body)
    await race((first["id"], second["id"]))
    assert await customer_count(reference_body, reference=True) == 1
    states = [(await request_state(item["id"]))["status"] for item in (first, second)]
    assert sorted(states) == ["approved", "pending"], states
    passed("concurrent_different_requests_same_company_reference_create_one_customer")

    async def rollback_snapshot(request_id):
        async with session("OPS001") as (connection, _):
            counts = await connection.fetchrow(
                "SELECT (SELECT count(*) FROM crm.customer) AS customers,"
                "(SELECT count(*) FROM crm.contact) AS contacts,"
                "(SELECT count(*) FROM crm.customer_ownership) AS ownerships"
            )
            state = await connection.fetchrow(
                "SELECT status,created_customer_id,version_no,reviewed_at FROM crm.customer_creation_request "
                "WHERE id=$1::uuid", request_id,
            )
            return dict(counts), dict(state)

    real_create = CustomerMutationRepository.create

    async def fail_after_customer(self, connection, actor, *, data):
        await real_create(self, connection, actor, data=data)
        raise InjectedApprovalFailure("after real customer/contact/ownership creation")

    rollback_body = payload("rollback-customer-write")
    rollback_request = await submit("XS001", rollback_body)
    before = await rollback_snapshot(rollback_request["id"])
    with patch.object(CustomerMutationRepository, "create", fail_after_customer):
        await denied("OPS001", lambda connection, actor: repo.decide(
            connection, actor, rollback_request["id"], "approved",
        ), (InjectedApprovalFailure,))
    assert await rollback_snapshot(rollback_request["id"]) == before
    assert await customer_count(rollback_body) == 0
    passed("failure_after_customer_write_rolls_back_customer_contact_ownership_and_request")

    async def fail_after_completed_action(connection, actor):
        result = await repo.decide(connection, actor, rollback_request["id"], "approved")
        assert result["status"] == "approved"
        raise InjectedApprovalFailure("after complete action before transaction commit")

    await denied("OPS001", fail_after_completed_action, (InjectedApprovalFailure,))
    assert await rollback_snapshot(rollback_request["id"]) == before
    assert await customer_count(rollback_body) == 0
    assert (await decide("OPS001", rollback_request["id"]))["status"] == "approved"
    passed("failure_after_complete_action_rolls_back_and_retry_succeeds")

    # Preserve the existing separate claim/approval workflow after customer approval.
    customer_id = approved["customer"]["id"]
    claim = await action("XS001", lambda connection, actor: connection.fetchval(
        "SELECT security.claim_customer($1::uuid)", customer_id,
    ))
    assert claim["status"] == "pending" and not claim["claimed"]
    await denied("XS001", lambda connection, actor: connection.fetchval(
        "SELECT security.review_customer_claim($1::uuid,'approved','forbidden')", claim["request_id"],
    ))
    await action("OPS001", lambda connection, actor: connection.fetchval(
        "SELECT security.review_customer_claim($1::uuid,'approved','S2 regression claim review')",
        claim["request_id"],
    ))
    async with session("XS001") as (connection, actor):
        row = await connection.fetchrow(
            "SELECT state,owner_user_ref_id::text FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer_id,
        )
        assert row["state"] == "claimed" and row["owner_user_ref_id"] == actor.user_id
        assert await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer_id)
    passed("existing_customer_claim_and_operations_review_preserved")

    print(json.dumps({
        "status": "passed", "suite": "customer_creation_requests_s2",
        "database": name, "runtime_role": role, "runtime_superuser": False,
        "runtime_bypassrls": False, "external_services_started": False, "checks": checks,
    }, ensure_ascii=False, indent=2), flush=True)


async def main():
    host = os.environ.get("PGHOST", "/tmp")
    if host not in {"localhost", "127.0.0.1", "::1"} and not host.startswith(("/tmp", "/private/tmp")):
        raise RuntimeError("This verification accepts only an explicit local PostgreSQL host/socket")
    await asyncio.wait_for(disposable_database(serve=run), timeout=240)


if __name__ == "__main__":
    asyncio.run(main())
