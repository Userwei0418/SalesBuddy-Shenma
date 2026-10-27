"""Real restricted-role checks in a fresh Unix-only PG16, using synthetic rows only.
PG_BIN=/path/to/pg16/bin python database/tests/verify_collection_direct_v290_postgres.py
No existing DSN, local test data, passwords or production environment is read.
"""

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "database/scripts"),
    str(ROOT / "tools/installation"),
    str(ROOT / "backend/src"),
]
import migrate
from runtime_roles import SCHEMAS, fingerprint, load_policy
from upgrade_permissions import reconcile_service_roles
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.collection_actuals import read_collection_actual
from sales_backend.services.collection_actuals import save_collection_actuals
from sales_backend.services.opportunities import save_opportunity
from sales_backend.services.opportunity_finance import confirm_actuals
from sales_backend.repositories.opportunity_finance import OpportunityFinanceRepository
from sales_backend.services.idempotency import execute_mutation

PORT = 56590


async def verify(directory):
    args = dict(
        host=str(directory), port=PORT, user="collection_owner", database="postgres"
    )
    db = await asyncpg.connect(**args)
    await _initialize_connection(db)
    for role in (
        "salegent_feishu_worker",
        "salegent_api",
        "salegent_worker",
        "salegent_platform",
    ):
        await db.execute(f"CREATE ROLE {role} NOLOGIN")
    await migrate.migrate(db, ROOT / "database", adopt_existing=True)
    await db.execute("SET row_security=on")
    policy = load_policy()
    policy["migration_fingerprint"] = fingerprint(
        dict(
            await db.fetch(
                "SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"
            )
        )
    )
    tables = [
        dict(x)
        for x in await db.fetch(
            "SELECT n.nspname||'.'||c.relname name,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1",
            SCHEMAS,
        )
    ]
    policies = [
        dict(x)
        for x in await db.fetch(
            "SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check FROM pg_policies WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3",
            SCHEMAS,
        )
    ]
    for row in policies:
        row["roles"] = sorted(row["roles"])
    policy["rls_fingerprint"] = fingerprint({"tables": tables, "policies": policies})
    roles = {k: "collection_" + k for k in policy["services"]}
    for service, role in roles.items():
        await db.execute(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS")
        for member in policy["services"][service]["memberships"]:
            await db.execute(f"GRANT {member} TO {role} WITH INHERIT FALSE, SET FALSE")
    async with db.transaction():
        await reconcile_service_roles(db, roles, policy)
    print(
        json.dumps(
            {
                k: policy[k]
                for k in ("schema", "migration_fingerprint", "rls_fingerprint")
            }
        ),
        flush=True,
    )
    assert not await db.fetchval(
        "SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version IN ('V288','V289'))"
    )

    async def user(ws, name, role, permissions):
        uid = await db.fetchval(
            "INSERT INTO platform.user_ref(workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$2,$2) RETURNING id",
            ws,
            name,
        )
        team = await db.fetchval(
            "INSERT INTO platform.team(workspace_id,code,name) VALUES($1,$2,$2) RETURNING id",
            ws,
            name,
        )
        await db.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)",
            ws,
            uid,
            role,
            "self" if role == "sales" else "workspace",
            team,
        )
        await db.execute(
            "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role,is_primary) VALUES($1,$2,$3,$4,true)",
            ws,
            team,
            uid,
            role,
        )
        await db.execute(
            "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)",
            ws,
            uid,
        )
        for permission in permissions:
            await db.execute(
                "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,$3,'allow',$4)",
                ws,
                uid,
                permission,
                "self"
                if role == "sales"
                and not permission.startswith("access.")
                and permission != "customer.reference"
                else "workspace",
            )
        return ActorContext(
            workspace_id=str(ws),
            user_id=str(uid),
            role=role,
            data_scope="self" if role == "sales" else "workspace",
            team_ids=(str(team),),
        )

    ws = await db.fetchval(
        "INSERT INTO platform.workspace(external_workspace_id,name) VALUES('collection-synthetic','Synthetic collection') RETURNING id"
    )
    ws2 = await db.fetchval(
        "INSERT INTO platform.workspace(external_workspace_id,name) VALUES('collection-foreign','Synthetic foreign') RETURNING id"
    )
    await db.execute(
        "UPDATE control.tenant_module SET enabled=true WHERE workspace_id=ANY($1::uuid[])",
        [ws, ws2],
    )
    await db.execute(
        "DELETE FROM config.permission_role_grant WHERE workspace_id=ANY($1::uuid[])",
        [ws, ws2],
    )
    allowed = [
        "access.mini_program",
        "opportunity.read",
        "opportunity.update",
        "customer.read",
        "customer.reference",
        "directory.read",
    ]
    sales = await user(ws, "COLLECTION_SALES", "sales", allowed)
    other = await user(ws, "COLLECTION_OTHER", "sales", allowed)
    foreign = await user(ws2, "COLLECTION_FOREIGN", "sales", allowed)
    admin = await user(
        ws,
        "COLLECTION_ADMIN",
        "administrator",
        [*allowed, "access.console", "actual.read", "actual.create", "actual.void"],
    )
    async with db.transaction():
        await set_request_context(db, sales)
        cid = await db.fetchval(
            "INSERT INTO crm.customer(workspace_id,name,owner_user_ref_id,created_by_user_ref_id) VALUES($1,'Synthetic customer',$2,$2) RETURNING id",
            ws,
            UUID(sales.user_id),
        )
        oid = await db.fetchval(
            "INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,created_by_user_ref_id,trade_relation,amount,probability,expected_close_date) VALUES($1,$2,'Synthetic opportunity',$3,$3,'direct',100000,10,'2026-12-31') RETURNING id",
            ws,
            cid,
            UUID(sales.user_id),
        )
        await db.execute(
            "UPDATE crm.opportunity SET owner_team_id=$2 WHERE id=$1",
            oid,
            UUID(sales.team_ids[0]),
        )
    customer = {"id": str(cid), "name": "Synthetic customer"}
    checks = []

    @asynccontextmanager
    async def as_actor(actor):
        async with db.transaction():
            await db.execute("SET LOCAL ROLE collection_api")
            assert not await db.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            await set_request_context(db, actor)
            yield db

    async def current(actor=sales, q=4):
        async with as_actor(actor):
            return await read_collection_actual(db, oid, 2026, q)

    async def write(value, revision=None, actor=sales, q=4, replace=False):
        state = await current(actor, q)
        data = {
            "action": "update",
            "collection_actuals": [
                dict(
                    year=2026,
                    quarter=q,
                    amount=Decimal(value),
                    source_ref="Synthetic bank basis",
                    note="",
                    expected_actual_revision=revision or state["actual"]["revision"],
                    replace_source=replace,
                )
            ],
        }
        async with as_actor(actor):
            return await save_collection_actuals(db, actor, str(oid), customer, data)

    async def deny(
        awaitable,
        errors=(PermissionError, LookupError, asyncpg.InsufficientPrivilegeError),
    ):
        try:
            await awaitable
        except errors:
            return
        raise AssertionError("Expected denial/conflict")

    initial = await current()
    assert initial["actual"]["amount"] is None and initial["can_edit"]
    async with as_actor(sales):
        assert not await db.fetchval(
            "SELECT security.authorization_has('actual.create') OR security.authorization_has('actual.read') OR security.authorization_has('access.console')"
        )
    await write("100000")
    assert Decimal((await current())["actual"]["amount"]) == 100000
    await write("150000")
    assert Decimal((await current())["actual"]["amount"]) == 150000
    assert (
        await db.fetchval(
            "SELECT count(*) FROM crm.opportunity_actual_confirmation WHERE opportunity_id=$1 AND kind='collection'",
            oid,
        )
        == 1
    )
    revision = (await current())["actual"]["revision"]
    await write("150000")
    assert (await current())["actual"]["revision"] == revision
    checks.append(
        "sales_without_actual_create_read_or_console_cumulative_10_then_15_is_15_and_identical_noop"
    )
    await deny(write("170000", initial["actual"]["revision"]), (VersionConflict,))
    await deny(current(other))
    await deny(current(foreign))

    async def direct_forbidden(actor, query, *values):
        async with as_actor(actor):
            result = await db.execute(query, *values)
            if result.endswith(" 0"):
                raise PermissionError("RLS filtered all matching rows")
            raise AssertionError(result)

    await deny(
        direct_forbidden(
            sales,
            "UPDATE crm.opportunity_actual_confirmation SET amount=999,version_no=version_no+1 WHERE opportunity_id=$1",
            oid,
        )
    )
    async with as_actor(sales):
        assert not await db.fetchval(
            "SELECT has_function_privilege(current_user,'security.opportunity_collection_edit_scope(uuid)','EXECUTE')"
        )
    for denied_actor in (other, foreign):
        await deny(
            direct_forbidden(
                denied_actor,
                "SELECT security.save_opportunity_collection_actual($1,2026,4,999,'Forged','',$2,false)",
                oid,
                revision,
            )
        )
    await deny(
        direct_forbidden(
            sales,
            """INSERT INTO crm.opportunity_actual_confirmation
      (workspace_id,opportunity_id,year,quarter,kind,amount,tax_basis,source_ref,note,reason,material_snapshot,confirmed_by_user_ref_id)
      VALUES($1,$2,2026,4,'recognized',999,'exclusive','Forbidden','','Forbidden','{}',$3)""",
            ws,
            oid,
            UUID(sales.user_id),
        )
    )
    checks.append(
        "stale_revision_foreign_tenant_and_nonowner_denied_raw_formal_and_recognized_write_blocked"
    )
    # Legacy direct operations registration remains callable and invalidates a sales revision.
    prior = await current()
    async with as_actor(admin):
        overview = await OpportunityFinanceRepository().read(
            db, oid, year=2026, quarter=4
        )
        period = next(
            p
            for p in overview["periods"]
            if p["kind"] == "collection" and p["quarter"] == 4
        )
        await confirm_actuals(
            db,
            admin,
            oid,
            dict(
                year=2026,
                quarter=4,
                kind="collection",
                expected_opportunity_version=overview["opportunity_version"],
                expected_version=period["version_no"],
                expected_fingerprint=period["source_fingerprint"],
                replace_source=False,
                reason="Synthetic direct operations",
                entries=[
                    dict(
                        material_line_id=None,
                        expected_version=period["confirmations"][0]["version_no"],
                        action="confirm",
                        amount=Decimal("160000"),
                        source_ref="Operations basis",
                        note="",
                    )
                ],
            ),
        )
    await deny(write("180000", prior["actual"]["revision"]), (VersionConflict,))
    assert Decimal((await current())["actual"]["amount"]) == 160000
    checks.append("existing_operations_path_preserved_and_blocks_stale_sales_overwrite")
    # Source replacement is explicit and leaves original entry evidence intact.
    async with as_actor(admin):
        entry = await db.fetchval(
            """INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,
          occurred_on,source_ref,request_id,confirmed_by_user_ref_id) VALUES($1,$2,$3,'collection',100,
          '2026-01-20','Synthetic legacy entry',$4,$5) RETURNING id""",
            ws,
            cid,
            oid,
            uuid4(),
            UUID(admin.user_id),
        )
    assert (await current(q=1))["actual"]["requires_source_replacement"]
    await deny(write("250000", q=1), (VersionConflict,))
    await write("250000", q=1, replace=True)
    assert Decimal((await current(q=1))["actual"]["amount"]) == 250000
    assert (
        await db.fetchval("SELECT amount FROM crm.customer_actual WHERE id=$1", entry)
        == 100
    )
    checks.append(
        "entry_source_requires_explicit_replacement_and_old_evidence_is_retained"
    )
    # Multiple quarterly edits remain one atomic save despite deferred finance checks.
    async with as_actor(sales):
        revs = [await read_collection_actual(db, oid, 2026, q) for q in (2, 3)]
        for q, state in zip((2, 3), revs):
            await save_collection_actuals(
                db,
                sales,
                str(oid),
                customer,
                {
                    "action": "update",
                    "collection_actuals": [
                        dict(
                            year=2026,
                            quarter=q,
                            amount=Decimal("0"),
                            source_ref="Explicit zero",
                            note="",
                            expected_actual_revision=state["actual"]["revision"],
                        )
                    ],
                },
            )
    assert Decimal((await current(q=2))["actual"]["amount"]) == 0
    checks.append("multiple_quarters_atomic_and_explicit_zero_preserved")

    # Exercise actual shared opportunity save, not a fake wrapper. No actual fields
    # means old clients edit normally, new fields commit/rollback with ordinary edits.
    async def payload(name, actuals=None):
        row = await db.fetchrow(
            "SELECT version_no,amount,probability,expected_close_date FROM crm.opportunity WHERE id=$1",
            oid,
        )
        result = dict(
            action="update",
            opportunity_id=str(oid),
            version_no=row["version_no"],
            name=name,
            status="open",
            amount=row["amount"],
            probability=row["probability"],
            expected_close_date=row["expected_close_date"],
        )
        if actuals is not None:
            result["collection_actuals"] = actuals
        return result

    data = await payload("Ordinary changed")
    async with as_actor(sales):
        result = await save_opportunity(db, sales, customer_id=str(cid), data=data)
    assert (
        result["name"] == "Ordinary changed"
        and Decimal((await current())["actual"]["amount"]) == 160000
    )
    before = await current()
    changed = dict(
        year=2026,
        quarter=4,
        amount=Decimal("190000"),
        source_ref="Sales direct",
        note="",
        expected_actual_revision=before["actual"]["revision"],
    )
    data = await payload("Ordinary and collection changed", [changed])
    key = uuid4()

    async def saved():
        async with as_actor(sales):
            return await execute_mutation(
                db,
                sales,
                key,
                f"opportunities.save:{cid}",
                data,
                lambda: save_opportunity(db, sales, customer_id=str(cid), data=data),
            )

    first = await saved()
    second = await saved()
    assert first == second and Decimal((await current())["actual"]["amount"]) == 190000
    assert (
        await db.fetchval("SELECT name FROM crm.opportunity WHERE id=$1", oid)
        == "Ordinary and collection changed"
    )
    bad = await payload("Must rollback", [changed])

    async def bad_save():
        async with as_actor(sales):
            await save_opportunity(db, sales, customer_id=str(cid), data=bad)

    await deny(bad_save(), (VersionConflict,))
    assert (
        await db.fetchval("SELECT name FROM crm.opportunity WHERE id=$1", oid)
        == "Ordinary and collection changed"
    )
    checks.append(
        "ordinary_edit_compatible_combined_save_immediate_idempotent_and_stale_input_rolls_back_atomically"
    )
    # Only a collection edit, with no ordinary change, must still persist and be
    # recoverable through the existing save receipt.
    state = await current()
    data = await payload(
        "Ordinary and collection changed",
        [
            {
                **changed,
                "amount": Decimal("210000"),
                "expected_actual_revision": state["actual"]["revision"],
            }
        ],
    )
    async with as_actor(sales):
        result = await save_opportunity(db, sales, customer_id=str(cid), data=data)
    assert (
        result["changed"] is False
        and result["operation"] == "unchanged"
        and result["collection_actuals"]
    )
    assert Decimal((await current())["actual"]["amount"]) == 210000
    assert (
        await db.fetchval(
            'SELECT count(*) FROM crm.business_change WHERE opportunity_id=$1 AND changes @> \'[{"field":"collection_actual"}]\'',
            oid,
        )
        > 0
    )
    checks.append("collection_only_save_has_effect_and_retains_change_history")
    print(
        json.dumps({"checks": checks, "result": "passed"}, ensure_ascii=False),
        flush=True,
    )
    await db.close()


def main():
    binaries = Path(os.environ["PG_BIN"])
    directory = Path(tempfile.mkdtemp(prefix="sb-collection-v290-"))
    subprocess.run(
        [
            str(binaries / "initdb"),
            "-D",
            str(directory / "pg"),
            "-U",
            "collection_owner",
            "--auth=trust",
            "--encoding=UTF8",
            "--no-locale",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            str(binaries / "pg_ctl"),
            "-D",
            str(directory / "pg"),
            "-l",
            str(directory / "postgres.log"),
            "-o",
            f"-k {directory} -p {PORT} -c listen_addresses=''",
            "-w",
            "start",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    try:
        asyncio.run(verify(directory))
    finally:
        subprocess.run(
            [
                str(binaries / "pg_ctl"),
                "-D",
                str(directory / "pg"),
                "-m",
                "fast",
                "-w",
                "stop",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        shutil.rmtree(directory)


if __name__ == "__main__":
    main()
