"""Disposable V259 PostgreSQL/ASGI proof; no saved DSNs or real credentials.

The API role is NOSUPERUSER/NOBYPASSRLS. Production runtime inventory is owned by
Backend; this script explicitly adds only the three V259 EXECUTE grants.
"""

# ruff: noqa: E402, E501, S608, S108
import argparse
import asyncio
import hashlib
import io
import json
import sys
from contextlib import asynccontextmanager, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/scripts"))
from verify_review_targets_postgres import apply, grants, migrate_through
from verify_review_targets_postgres import item as migration_item

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.api.opportunity_finance import router as finance_router
from sales_backend.api.opportunity_transfer import router
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext


async def verify(args):
    socket = str(Path(args.socket).resolve())
    if not socket.startswith(("/tmp/sb122-transfer-pg.", "/private/tmp/sb122-transfer-pg.")):
        raise ValueError("Dedicated private socket required")
    config = dict(host=socket, port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    name, role = "sb122_transfer_" + uuid4().hex[:10], "sb122_transfer_api_" + uuid4().hex[:10]
    report = dict(
        status="started",
        schema="V258+V259",
        production_accessed=False,
        http_auth="synthetic injected identities; real ASGI/database/RLS",
        runtime_inventory="base V256 runtime grants plus explicit V259 signatures; complete shared V258/V259 provisioning is a separate integration check",
        checks=[],
        samples={},
    )
    c = None
    try:
        await admin.execute(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8'")
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 258)
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        ws, foreign_ws, east, south = [uuid4() for _ in range(4)]
        for tenant in (ws, foreign_ws):
            await c.execute(
                "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic transfer')",
                tenant,
                str(tenant),
            )
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", tenant)
            await c.execute(
                """UPDATE config.permission_role_grant g SET scope_code='self',team_ids='{}'
             FROM config.permission_role r WHERE r.id=g.role_id AND r.workspace_id=$1 AND r.builtin_role_code='sales'
             AND g.permission_code IN ('customer.reference','customer.read','opportunity.read','opportunity.update','visit.read')""",
                tenant,
            )
        for team in (east, south):
            await c.execute(
                "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)", team, ws, str(team)
            )
        actors = {}
        for label, job, team, scope, tenant in (
            ("manager", "operations", east, "workspace", ws),
            ("limited", "supervisor", east, "team", ws),
            ("old", "sales", east, "self", ws),
            ("new", "sales", south, "self", ws),
            ("peer", "sales", east, "self", ws),
            ("collaborator", "sales", south, "self", ws),
            ("foreign", "manager", None, "workspace", foreign_ws),
        ):
            uid = uuid4()
            await c.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)",
                uid,
                tenant,
                label,
            )
            await c.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)",
                tenant,
                uid,
                job,
                scope,
                team,
            )
            if team:
                await c.execute(
                    "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)",
                    tenant,
                    uid,
                    team,
                    "supervisor" if job == "supervisor" else "sales",
                )
            actors[label] = ActorContext(
                workspace_id=str(tenant),
                user_id=str(uid),
                role=job,
                data_scope=scope,
                team_ids=[str(team)] if team else [],
            )
        await c.execute(
            "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)",
            ws,
            actors["limited"].user_id,
        )
        await c.execute(
            "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'actual.read','deny',NULL)",
            ws,
            actors["limited"].user_id,
        )
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
        await grants(c, role)
        await c.execute("SET row_security=on")
        customer = uuid4()
        await c.execute(
            "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,'Transfer customer','Transfer customer',$3,$3,$4)",
            customer,
            ws,
            actors["old"].user_id,
            east,
        )

        async def new_opportunity(label):
            oid = uuid4()
            async with c.transaction():
                await set_request_context(c, actors["manager"])
                await c.execute("SELECT set_config('app.native_opportunity_create',$1,true)", str(oid))
                await c.execute(
                    """INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
                 amount,currency,probability,stage_code,status,sales_channel,created_by_user_ref_id,trade_relation)
                 VALUES($1,$2,$3,$4,$5,$6,2003000,'CNY',70,'qualified','open','direct',$5,'direct')""",
                    oid,
                    ws,
                    customer,
                    label,
                    actors["old"].user_id,
                    east,
                )
            return oid

        @asynccontextmanager
        async def transaction(actor, connection=c, **kwargs):
            async with connection.transaction(**kwargs):
                await connection.execute(f'SET LOCAL ROLE "{role}"')
                await set_request_context(connection, actor)
                assert not await connection.fetchval(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
                yield connection

        year = datetime.now(UTC).year
        quarter = (datetime.now(UTC).month - 1) // 3 + 1

        async def operations(oid, amount=700000, period_year=None):
            async with transaction(actors["manager"]) as tx:
                await tx.execute("SELECT security.finance_write_lock()")
                aid = await tx.fetchval(
                    """INSERT INTO crm.opportunity_actual_confirmation(workspace_id,opportunity_id,year,quarter,kind,amount,
                 tax_basis,source_ref,reason,material_snapshot,confirmed_by_user_ref_id)
                 VALUES($1,$2,$3,$4,'collection',$5,'not_applicable','synthetic','verified','{}',$6) RETURNING id""",
                    ws,
                    oid,
                    period_year or year,
                    quarter,
                    amount,
                    actors["manager"].user_id,
                )
                await tx.execute(
                    """INSERT INTO crm.opportunity_actual_basis(workspace_id,opportunity_id,year,quarter,kind,source,source_fingerprint,reason,decided_by_user_ref_id)
                 VALUES($1,$2,$3,$4,'collection','operations',security.finance_reference_fingerprint($1,$2,$3,$4,'collection'),'verified',$5)
                 ON CONFLICT(workspace_id,opportunity_id,year,quarter,kind) DO UPDATE SET source='operations',selected_history_id=NULL,source_fingerprint=EXCLUDED.source_fingerprint,version_no=crm.opportunity_actual_basis.version_no+1,reason='switch source',decided_by_user_ref_id=EXCLUDED.decided_by_user_ref_id""",
                    ws,
                    oid,
                    period_year or year,
                    quarter,
                    actors["manager"].user_id,
                )
                return aid

        async def entry(oid, amount=50000):
            async with transaction(actors["manager"]) as tx:
                return await tx.fetchval(
                    """INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,source_ref,request_id,confirmed_by_user_ref_id,tax_basis)
                 VALUES($1,$2,$3,'collection',$4,current_date,'synthetic:'||($5::uuid)::text,$5,$6,'not_applicable') RETURNING id""",
                    ws,
                    customer,
                    oid,
                    amount,
                    uuid4(),
                    actors["manager"].user_id,
                )

        async def adopt(oid, source="entries", historical_id=None):
            async with transaction(actors["manager"]) as tx:
                await tx.execute("SELECT security.finance_write_lock()")
                await tx.execute(
                    """INSERT INTO crm.opportunity_actual_basis(workspace_id,opportunity_id,year,quarter,kind,source,source_fingerprint,reason,decided_by_user_ref_id,selected_history_id)
                 VALUES($1,$2,$3,$4,'collection',$6,security.finance_reference_fingerprint($1,$2,$3,$4,'collection'),'verified',$5,$7)
                 ON CONFLICT(workspace_id,opportunity_id,year,quarter,kind) DO UPDATE SET source=EXCLUDED.source,selected_history_id=EXCLUDED.selected_history_id,
                 source_fingerprint=EXCLUDED.source_fingerprint,version_no=crm.opportunity_actual_basis.version_no+1,reason='readopted',decided_by_user_ref_id=EXCLUDED.decided_by_user_ref_id""",
                    ws,
                    oid,
                    year,
                    quarter,
                    actors["manager"].user_id,
                    source,
                    historical_id,
                )

        async def historical(oid):
            async with c.transaction():
                await set_request_context(c, actors["manager"])
                batch, record = uuid4(), uuid4()
                await c.execute(
                    "INSERT INTO ops.crm_import_batch(id,workspace_id,source_system,source_base_id,manifest_sha256,source_snapshot_at) VALUES($1,$2,'synthetic',$3,$4,clock_timestamp())",
                    batch,
                    ws,
                    str(batch),
                    "a" * 64,
                )
                await c.execute(
                    "INSERT INTO ops.crm_import_record(id,workspace_id,batch_id,source_table_id,source_record_id,object_kind,source_sha256,raw_fields) VALUES($1,$2,$3,'synthetic',$4,'period_actual_snapshot',$5,'{}')",
                    record,
                    ws,
                    batch,
                    str(record),
                    "b" * 64,
                )
                return await c.fetchval(
                    "INSERT INTO crm.opportunity_period_actual_snapshot(workspace_id,opportunity_id,year,quarter,kind,source_field,raw_amount,source_unit,tax_basis,source_record_id,import_batch_id) VALUES($1,$2,$3,$4,'collection','synthetic',7,'wan_cny','not_applicable',$5,$6) RETURNING id",
                    ws,
                    oid,
                    year,
                    quarter,
                    record,
                    batch,
                )

        main = await new_opportunity("Main")
        sibling = await new_opportunity("Sibling")
        adopted = await new_opportunity("Adopted")
        unsafe = await new_opportunity("Unsafe")
        aid = await operations(main)
        entry_id = await entry(adopted)
        await adopt(adopted)
        await entry(unsafe)
        history_opp = await new_opportunity("History adopted")
        history_id = await historical(history_opp)
        await adopt(history_opp, "historical", history_id)
        dynamic_history = await new_opportunity("History dynamic")
        await historical(dynamic_history)
        shadowed = await new_opportunity("Raw reference shadowed by operations")
        await entry(shadowed)
        await operations(shadowed)
        partial = await new_opportunity("Partial legacy snapshot")
        partial_id = await operations(partial)
        # Synthetic pre-migration corrupt legacy evidence only. Runtime tests below
        # never disable triggers or bypass RLS.
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role=replica")
            await c.execute(
                "UPDATE crm.opportunity_actual_confirmation SET material_snapshot=material_snapshot-'owner_team_id' WHERE id=$1",
                partial_id,
            )
        partial_sources = []
        for source in ("entries", "historical"):
            for missing in (("owner_team_id",), ("owner_user_ref_id",), ("owner_team_id", "owner_user_ref_id")):
                oid = await new_opportunity(f"Legacy partial {source} {missing}")
                fid = await entry(oid) if source == "entries" else await historical(oid)
                await adopt(oid, source, fid if source == "historical" else None)
                async with c.transaction():
                    await c.execute("SET LOCAL session_replication_role=replica")
                    await c.execute(
                        """UPDATE crm.opportunity_actual_basis SET adopted_snapshot=jsonb_set(adopted_snapshot,
                        '{facts,0}',(adopted_snapshot#>'{facts,0}')-$2::text[]) WHERE opportunity_id=$1""",
                        oid,
                        list(missing),
                    )
                original = await c.fetchval(
                    "SELECT adopted_snapshot#>'{facts,0}' FROM crm.opportunity_actual_basis WHERE opportunity_id=$1",
                    oid,
                )
                partial_sources.append((oid, fid, source, missing, original))
        migration = ROOT / "database/migrations/V259__opportunity_owner_transfer.sql"
        await apply(c, migration_item("V259", migration), ROOT / "database")
        report["migration_sha256"] = hashlib.sha256(migration.read_bytes()).hexdigest()
        if args.with_owner_followup:
            owner_migration = ROOT / "database/migrations/V260__opportunity_owner_follow_up.sql"
            await apply(c, migration_item("V260", owner_migration), ROOT / "database")
            report["schema"] = "V258+V259+V260"
            report["v260_sha256"] = hashlib.sha256(owner_migration.read_bytes()).hexdigest()
        report["postgres_version"] = await c.fetchval("SHOW server_version")
        ledger = [
            dict(row)
            for row in await c.fetch(
                "SELECT migration_key,checksum_sha256 FROM ops.migration_checksum ORDER BY migration_key"
            )
        ]
        report["ledger_count"] = len(ledger)
        report["ledger_sha256"] = hashlib.sha256(
            json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert any(
            row["migration_key"] == "V259" and row["checksum_sha256"] == report["migration_sha256"] for row in ledger
        )
        for signature in (
            "security.opportunity_transfer_capability(uuid)",
            "security.opportunity_transfer_candidates(uuid,text,integer,integer)",
            "security.transfer_opportunity_owner(uuid,integer,uuid,uuid,text,uuid)",
        ):
            await c.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')
        assert not await c.fetchval("SELECT has_table_privilege($1,'crm.opportunity_transfer_request','SELECT')", role)
        assert not await c.fetchval(
            "SELECT has_function_privilege($1,'security.transfer_attribution_safe(uuid)','EXECUTE')", role
        )
        report["checks"].append(
            "full V258 then V259; first complete and incomplete financial evidence indexed; private tables/helpers closed"
        )
        for signature in (
            "security.transfer_capture_attribution(uuid,uuid,text,text,integer,integer,text,jsonb)",
            "crm.capture_transferred_actual_attribution()",
        ):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)
        assert not await c.fetchval(
            "SELECT has_table_privilege($1,'crm.opportunity_actual_attribution','SELECT')", role
        )
        who = actors["manager"]
        app = FastAPI()
        app.include_router(router)
        app.include_router(finance_router)
        app.dependency_overrides[get_management_identity] = lambda: SimpleNamespace(actor=who)
        app.dependency_overrides[get_database] = lambda: SimpleNamespace(transaction=transaction)
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=who)

        async def version(oid):
            return await c.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1", oid)

        def payload(target="new", team=south, version_no=1):
            return dict(
                version_no=version_no,
                new_owner_user_ref_id=actors[target].user_id,
                new_owner_team_id=str(team),
                reason="verified handover",
            )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:

            async def transfer(oid, body=None, key=None, actor="manager", expected=200):
                nonlocal who
                who = actors[actor]
                res = await client.post(
                    f"/api/v1/opportunities/{oid}/transfer",
                    json=body or payload(version_no=await version(oid)),
                    headers={"Idempotency-Key": str(key or uuid4())},
                )
                assert res.status_code == expected, (expected, res.status_code, res.text)
                return res.json()

            async def assert_unresolved(oid):
                async with transaction(actors["manager"], readonly=True) as tx:
                    cap = await tx.fetchval("SELECT security.opportunity_transfer_capability($1)", oid)
                assert cap == {"can_transfer": False, "reason": "TRANSFER_ATTRIBUTION_UNRESOLVED"}, cap
                denied = await transfer(oid, expected=409)
                assert denied["code"] == "TRANSFER_ATTRIBUTION_UNRESOLVED"
                assert await version(oid) == 1
                return cap

            async def attribution(oid, source, fid):
                return dict(
                    await c.fetchrow(
                        "SELECT * FROM crm.opportunity_actual_attribution WHERE opportunity_id=$1 AND source_kind=$2 AND fact_id=$3",
                        oid,
                        source,
                        str(fid),
                    )
                )

            # SQL upserts and the actual HTTP service use different write paths.
            # Exercise both for entries/history x missing team/owner/both.
            partial_evidence = []
            for oid, fid, source, missing, original in partial_sources:
                first = await attribution(oid, source, fid)
                assert first["owner_user_ref_id"] is None and first["owner_team_id"] is None
                assert first["evidence"] == original
                await assert_unresolved(oid)
                await adopt(oid, source, fid if source == "historical" else None)
                await assert_unresolved(oid)
                await operations(oid, 250000)
                # Active operations cannot hide unresolved adopted history.
                await assert_unresolved(oid)
                await adopt(oid, source, fid if source == "historical" else None)
                fact = await c.fetchval(
                    "SELECT adopted_snapshot#>'{facts,0}' FROM crm.opportunity_actual_basis WHERE opportunity_id=$1",
                    oid,
                )
                for field in ("owner_user_ref_id", "owner_team_id"):
                    assert fact.get(field) == original.get(field), (field, fact, original)
                assert await attribution(oid, source, fid) == first
                await assert_unresolved(oid)
                partial_evidence.append(
                    {
                        "source": source,
                        "missing": missing,
                        "original": original,
                        "after_sql_upserts": fact,
                        "first_ledger": first,
                    }
                )
            report["checks"].append(
                "DATA-V259-001 SQL: 6 partial entries/history variants survive same-source upsert and operations/source roundtrip; hidden unresolved evidence also blocks transfer"
            )

            async def read_period(oid):
                response = await client.get(
                    f"/api/v1/console/opportunities/{oid}/financial-overview", params={"year": year, "quarter": quarter}
                )
                assert response.status_code == 200, response.text
                current = response.json()
                return current, next(p for p in current["periods"] if p["kind"] == "collection")

            async def confirm_source_http(oid, source, fid):
                _, period = await read_period(oid)
                body = dict(
                    year=year,
                    quarter=quarter,
                    kind="collection",
                    source=source,
                    expected_fingerprint=period["source_fingerprint"],
                    expected_version=period["version_no"],
                    reason="Verify first incomplete legacy evidence",
                )
                if source == "historical":
                    body["selected_history_id"] = str(fid)
                response = await client.post(
                    f"/api/v1/console/opportunities/{oid}/financial-sources/confirm",
                    json=body,
                    headers={"Idempotency-Key": str(uuid4())},
                )
                assert response.status_code == 200, response.text
                return next(p for p in response.json()["periods"] if p["kind"] == "collection")

            for evidence, (oid, fid, source, _missing, original) in zip(partial_evidence, partial_sources, strict=True):
                same = await confirm_source_http(oid, source, fid)
                await assert_unresolved(oid)
                current, period = await read_period(oid)
                confirmation = period["confirmations"][0]
                body = dict(
                    year=year,
                    quarter=quarter,
                    kind="collection",
                    expected_version=period["version_no"],
                    expected_opportunity_version=current["opportunity_version"],
                    expected_fingerprint=period["source_fingerprint"],
                    replace_source=True,
                    reason="Verify real source switch keeps unknown attribution",
                    entries=[
                        dict(
                            expected_version=confirmation["version_no"],
                            action="confirm",
                            amount="300000",
                            source_ref="synthetic",
                        )
                    ],
                )
                response = await client.post(
                    f"/api/v1/console/opportunities/{oid}/actual-confirmations",
                    json=body,
                    headers={"Idempotency-Key": str(uuid4())},
                )
                assert response.status_code == 200, response.text
                await assert_unresolved(oid)
                back = await confirm_source_http(oid, source, fid)
                fact = back["decision"]["adopted_snapshot"]["facts"][0]
                for field in ("owner_user_ref_id", "owner_team_id"):
                    assert fact.get(field) == original.get(field)
                assert await attribution(oid, source, fid) == evidence["first_ledger"]
                cap = await assert_unresolved(oid)
                evidence.update(
                    same_source_http=same,
                    operations_http=response.json(),
                    back_to_source_http=back,
                    final_capability=cap,
                    transfer_status=409,
                )
            report["samples"]["data_v259_001"] = partial_evidence
            report["checks"].append(
                "DATA-V259-001 real HTTP: 6 variants financial-sources/confirm -> actual-confirmations replace_source -> financial-sources/confirm preserve unknown fields and immutable evidence; transfer409"
            )

            # Rejected INSERT candidates must not establish an orphan confirmation
            # identity or adopt new raw facts that the persisted basis never chose.
            discarded = uuid4()
            async with transaction(actors["manager"]) as tx:
                await tx.execute(
                    """INSERT INTO crm.opportunity_actual_confirmation(id,workspace_id,opportunity_id,year,quarter,kind,amount,
                    tax_basis,source_ref,reason,material_snapshot,confirmed_by_user_ref_id)
                    VALUES($1,$2,$3,$4,$5,'collection',900000,'not_applicable','synthetic','discarded','{}',$6)
                    ON CONFLICT DO NOTHING""",
                    discarded,
                    ws,
                    main,
                    year,
                    quarter,
                    actors["manager"].user_id,
                )
            assert not await c.fetchval(
                "SELECT EXISTS(SELECT 1 FROM crm.opportunity_actual_attribution WHERE fact_id=$1)", str(discarded)
            )
            unadopted = await new_opportunity("Discarded candidate must not adopt")
            await entry(unadopted)
            await adopt(unadopted)
            fresh_entry = await entry(unadopted, 60000)
            async with transaction(actors["manager"]) as tx:
                await tx.execute(
                    """INSERT INTO crm.opportunity_actual_basis(workspace_id,opportunity_id,year,quarter,kind,source,
                    source_fingerprint,reason,decided_by_user_ref_id)
                    VALUES($1,$2,$3,$4,'collection','entries',security.finance_reference_fingerprint($1,$2,$3,$4,'collection'),
                    'discarded',$5) ON CONFLICT DO NOTHING""",
                    ws,
                    unadopted,
                    year,
                    quarter,
                    actors["manager"].user_id,
                )
            assert not await c.fetchval(
                "SELECT EXISTS(SELECT 1 FROM crm.opportunity_actual_attribution WHERE fact_id=$1)", str(fresh_entry)
            )
            await assert_unresolved(unadopted)
            await adopt(unadopted)
            assert (
                str((await attribution(unadopted, "entries", fresh_entry))["owner_user_ref_id"])
                == actors["old"].user_id
            )
            await transfer(unadopted)
            report["checks"].append(
                "DATA-V259-001 rejected ON CONFLICT candidates create no confirmation/fact ledger; actual adoption captures only persisted facts"
            )

            # Complete graph remains visible to the guard even when financial RLS hides it.
            async with transaction(actors["limited"]) as tx:
                assert (
                    await tx.fetchval("SELECT count(*) FROM crm.customer_actual WHERE opportunity_id=$1", unsafe) == 0
                )
                cap = await tx.fetchval("SELECT security.opportunity_transfer_capability($1)", unsafe)
                assert cap["reason"] == "TRANSFER_ATTRIBUTION_UNRESOLVED", cap
            for unsafe_id in (dynamic_history, shadowed, partial):
                denied = await transfer(unsafe_id, expected=409)
                assert denied["code"] == "TRANSFER_ATTRIBUTION_UNRESOLVED"
            report["checks"].append(
                "dynamic historical references, unadopted references shadowed by operations, and partial original snapshots are rejected without guessing/adopting"
            )
            unsafe_result = await transfer(unsafe, expected=409)
            assert unsafe_result["code"] == "TRANSFER_ATTRIBUTION_UNRESOLVED"
            for actor in ("old", "collaborator", "foreign"):
                assert (await transfer(main, actor=actor, expected=403))["code"] == "OPPORTUNITY_ACCESS_DENIED"
            await transfer(main, actor="limited", expected=403)  # source only, target outside scope
            report["checks"].append(
                "source/target scope, edit-only/collaborator/foreign rejection; RLS-hidden legacy financial rows cannot become empty-safe"
            )
            async with transaction(actors["manager"], readonly=True) as tx:
                choices = await tx.fetchval("SELECT security.opportunity_transfer_candidates($1,$2,20,0)", main, "new")
                assert choices["total"] == 1 and choices["items"][0]["teams"][0]["team_id"] == str(south)
            await c.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", actors["new"].user_id)
            await transfer(main, expected=422)
            await c.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", actors["new"].user_id)
            await c.execute(
                "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)",
                ws,
                actors["new"].user_id,
            )
            await c.execute(
                "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'opportunity.update','deny',NULL)",
                ws,
                actors["new"].user_id,
            )
            async with transaction(actors["manager"], readonly=True) as tx:
                denied_choices = await tx.fetchval(
                    "SELECT security.opportunity_transfer_candidates($1,$2,20,0)", main, "new"
                )
                assert denied_choices["total"] == 0
            await transfer(main, expected=422)
            await c.execute(
                "DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code='opportunity.update'",
                ws,
                actors["new"].user_id,
            )
            report["checks"].append(
                "inactive and explicitly denied future owner is excluded by both live candidate and final write eligibility"
            )
            noop = await transfer(main, payload("old", east, await version(main)))
            assert noop["changed"] is False and noop["event_id"] is None
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM crm.opportunity_owner_transfer_event WHERE opportunity_id=$1", main
                )
                == 0
            )
            await c.execute(
                "INSERT INTO crm.opportunity_collaborator(workspace_id,opportunity_id,user_ref_id,added_by_user_ref_id) VALUES($1,$2,$3,$4),($1,$2,$5,$4)",
                ws,
                main,
                actors["new"].user_id,
                actors["old"].user_id,
                actors["collaborator"].user_id,
            )
            before_customer = dict(await c.fetchrow("SELECT * FROM crm.customer WHERE id=$1", customer))
            before_sibling = dict(await c.fetchrow("SELECT * FROM crm.opportunity WHERE id=$1", sibling))
            before_money = [
                dict(r)
                for r in await c.fetch(
                    "SELECT id,amount,owner_user_ref_id,owner_team_id FROM crm.effective_actual WHERE opportunity_id=$1 ORDER BY id",
                    main,
                )
            ]
            key = uuid4()
            body = payload(version_no=await version(main))
            saved = await transfer(main, body, key)
            assert (
                saved["changed"]
                and saved["version_no"] == body["version_no"] + 1
                and saved["sync_status"] == "not_configured"
            )
            assert {x["user_id"] for x in saved["collaborators"]} == {actors["collaborator"].user_id}
            assert await transfer(main, body, key) == saved
            assert (await transfer(main, {**body, "reason": "different"}, key, expected=409))[
                "code"
            ] == "IDEMPOTENCY_CONFLICT"
            assert (await transfer(main, body, expected=409))["code"] == "VERSION_CONFLICT"
            assert before_customer == dict(await c.fetchrow("SELECT * FROM crm.customer WHERE id=$1", customer))
            assert before_sibling == dict(await c.fetchrow("SELECT * FROM crm.opportunity WHERE id=$1", sibling))
            assert before_money == [
                dict(r)
                for r in await c.fetch(
                    "SELECT id,amount,owner_user_ref_id,owner_team_id FROM crm.effective_actual WHERE opportunity_id=$1 ORDER BY id",
                    main,
                )
            ]
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM crm.opportunity_owner_transfer_event WHERE opportunity_id=$1", main
                )
                == 1
            )
            report["samples"]["transfer"] = saved
            report["checks"].append(
                "atomic real transfer; whole version/no-op/idempotency; redundant collaborator removed, old owner not added; customer/sibling and frozen historical money unchanged"
            )
            for label, can_read in [("old", False), ("new", True), ("collaborator", True)]:
                async with transaction(actors[label], readonly=True) as tx:
                    assert bool(await tx.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1", main)) == can_read
                    if label == "new":
                        assert await tx.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1", sibling) == 0
                        ref = await tx.fetchval(
                            "SELECT security.opportunity_follow_up_reference($1,$2)", customer, main
                        )
                        if ref is None and not args.with_owner_followup:
                            report.setdefault("integration_gaps", []).append(
                                "base V256 follow-up reference returns null for new opportunity owner without customer access; Backend-owned integration required"
                            )
                        else:
                            assert set(ref) == {"id", "name"} and ref["id"] == str(customer)
                            assert not await tx.fetchval(
                                "SELECT security.authorization_customer('customer.read',$1)", customer
                            )
                            for action in ("visit.read", "visit.create", "visit.structure", "visit.quality_review"):
                                assert await tx.fetchval(
                                    "SELECT security.authorization_visit_target($1,$2,$3,$4::uuid,$5)",
                                    action,
                                    customer,
                                    main,
                                    actors[label].user_id,
                                    south,
                                )
                            report["checks"].append(
                                "V259 transfer plus V260: new owner without customer read gets exact id/name follow-up reference and all four scoped visit actions; sibling remains unreadable"
                            )
            report["checks"].append(
                "new owner and explicit collaborator read target; prior owner loses only owner access; new owner cannot read sibling; customer reference inspected separately in integration_gaps"
            )
            for status in ("won", "lost", "cancelled"):
                async with transaction(actors["manager"]) as tx:
                    await tx.execute("UPDATE crm.opportunity SET status=$2 WHERE id=$1", sibling, status)
                assert (await transfer(sibling, expected=409))["code"] == "TRANSFER_STATE_NOT_ALLOWED"
            async with transaction(actors["manager"]) as tx:
                await tx.execute("UPDATE crm.opportunity SET status='open' WHERE id=$1", sibling)
            # Direct write cannot acquire the function-owner identity by setting flags.
            try:
                async with transaction(actors["new"]) as tx:
                    await tx.execute("SELECT set_config('app.opportunity_transfer','true',true)")
                    await tx.execute(
                        "UPDATE crm.opportunity SET owner_user_ref_id=$2 WHERE id=$1", main, actors["old"].user_id
                    )
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise AssertionError("ordinary UPDATE bypassed transfer")
            # Same confirmation ID: 70 -> 100 and withdraw/restore never split/reassign.
            for state, amount in [("confirmed", 1000000), ("withdrawn", 1000000), ("confirmed", 1000000)]:
                async with transaction(actors["manager"]) as tx:
                    await tx.execute("SELECT security.finance_write_lock()")
                    await tx.execute(
                        "UPDATE crm.opportunity_actual_confirmation SET status=$2,amount=$3,version_no=version_no+1,reason='correction',confirmed_by_user_ref_id=$4 WHERE id=$1",
                        aid,
                        state,
                        amount,
                        actors["manager"].user_id,
                    )
                snapshot = await c.fetchval(
                    "SELECT material_snapshot FROM crm.opportunity_actual_confirmation WHERE id=$1", aid
                )
                assert snapshot["owner_user_ref_id"] == actors["old"].user_id and snapshot["owner_team_id"] == str(east)
            await transfer(adopted)
            await adopt(adopted)
            adopted_fact = await c.fetchrow(
                "SELECT amount,owner_user_ref_id,owner_team_id FROM crm.effective_actual WHERE opportunity_id=$1 AND id=$2",
                adopted,
                str(entry_id),
            )
            assert (
                str(adopted_fact["owner_user_ref_id"]) == actors["old"].user_id
                and adopted_fact["owner_team_id"] == east
            )
            assert str(adopted_fact["amount"]) == "50000.00"
            report["checks"].append(
                "same period collection 70->100 stays original attribution; withdrawal/restoration and same evidence re-adoption preserve first owner/team"
            )
            await transfer(history_opp)
            await adopt(history_opp, "historical", history_id)
            frozen_history = await c.fetchrow(
                "SELECT amount,owner_user_ref_id,owner_team_id FROM crm.effective_actual WHERE opportunity_id=$1",
                history_opp,
            )
            assert (
                frozen_history["amount"] == 70000
                and str(frozen_history["owner_user_ref_id"]) == actors["old"].user_id
                and frozen_history["owner_team_id"] == east
            )
            # Switch away and re-adopt the same raw evidence: first attribution ledger survives.
            await operations(adopted, 250000)
            await adopt(adopted)
            assert (
                str(
                    await c.fetchval(
                        "SELECT owner_user_ref_id FROM crm.effective_actual WHERE opportunity_id=$1 AND id=$2",
                        adopted,
                        str(entry_id),
                    )
                )
                == actors["old"].user_id
            )
            fresh = await new_opportunity("First new fact after transfer")
            await transfer(fresh)
            fresh_id = await operations(fresh)
            assert (
                await c.fetchval(
                    "SELECT material_snapshot FROM crm.opportunity_actual_confirmation WHERE id=$1", fresh_id
                )
            )["owner_user_ref_id"] == actors["new"].user_id
            try:
                await operations(fresh, period_year=year - 1)
            except asyncpg.SerializationError as exc:
                assert str(exc) == "TRANSFER_ATTRIBUTION_UNRESOLVED"
            else:
                raise AssertionError("backfilled old period guessed new owner")
            report["checks"].append(
                "historical wan_cny converts once and re-adoption/source switches preserve first attribution; genuinely new current-period confirmation uses new owner; missing prior-period evidence rejects"
            )

            # Membership change for the same user still uses guarded transfer.
            await c.execute(
                "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')",
                ws,
                actors["old"].user_id,
                south,
            )
            same_person = await new_opportunity("Same person")
            same_aid = await operations(same_person)
            await transfer(same_person, payload("old", south, await version(same_person)))
            assert (
                await c.fetchval(
                    "SELECT material_snapshot FROM crm.opportunity_actual_confirmation WHERE id=$1", same_aid
                )
            )["owner_team_id"] == str(east)
            report["checks"].append(
                "same user/new explicit team transfers current pipeline but preserves historical team"
            )

            # The existing outbound queue and current projection remain the only CRM write path.
            await c.execute(
                "UPDATE crm.customer SET data_kind='production' WHERE id=$1", customer
            )  # Synthetic local exportable fixture only.
            connection_id = uuid4()
            settings = {
                "workspace_id": str(ws),
                "connection_id": str(connection_id),
                "mappings": {"opportunity": {"enabled": True, "direction": "system_to_base"}},
            }
            await c.execute(
                "INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,validated_revision,settings,updated_by) VALUES($1,$2,1,true,1,$3,$4)",
                connection_id,
                ws,
                settings,
                actors["manager"].user_id,
            )
            mirrored = await new_opportunity("Outbound projection")
            queue_before = await c.fetchval("SELECT count(*) FROM ops.feishu_event WHERE object_id=$1", mirrored)
            mirror_result = await transfer(mirrored)
            assert mirror_result["sync_status"] == "pending"
            assert (
                await c.fetchval("SELECT count(*) FROM ops.feishu_event WHERE object_id=$1", mirrored)
                == queue_before + 1
            )
            projection = await c.fetchval("SELECT ops.feishu_source($1,'opportunity',$2)", connection_id, mirrored)
            assert (
                projection["owner_user_ref_id"] == actors["new"].user_id
                and projection["owner_team_id"] == str(south)
                and projection["version_no"] == mirror_result["version_no"]
            )
            await transfer(mirrored, payload("peer", east, await version(mirrored)))
            projection = await c.fetchval("SELECT ops.feishu_source($1,'opportunity',$2)", connection_id, mirrored)
            assert (
                projection["owner_user_ref_id"] == actors["peer"].user_id
            )  # Reads latest state even for older queued event.
            failed = await new_opportunity("Atomic rollback")
            before_failed = dict(await c.fetchrow("SELECT * FROM crm.opportunity WHERE id=$1", failed))
            failed_queue = await c.fetchval("SELECT count(*) FROM ops.feishu_event WHERE object_id=$1", failed)
            await c.execute(
                "CREATE FUNCTION public.transfer_test_fail_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='40001'; END $$"
            )
            await c.execute(
                "CREATE TRIGGER transfer_test_fail_event BEFORE INSERT ON crm.opportunity_owner_transfer_event FOR EACH ROW EXECUTE FUNCTION public.transfer_test_fail_event()"
            )
            await transfer(failed, expected=409)
            await c.execute("DROP TRIGGER transfer_test_fail_event ON crm.opportunity_owner_transfer_event")
            await c.execute("DROP FUNCTION public.transfer_test_fail_event()")
            assert before_failed == dict(await c.fetchrow("SELECT * FROM crm.opportunity WHERE id=$1", failed))
            assert await c.fetchval("SELECT count(*) FROM ops.feishu_event WHERE object_id=$1", failed) == failed_queue
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM crm.opportunity_transfer_request WHERE opportunity_id=$1", failed
                )
                == 0
            )
            report["checks"].append(
                "existing system_to_base outbox and current projection reflect transfer; stale queued IDs read latest owner; injected post-update failure rolls back owner/version/outbox/event/receipt atomically; no external service invoked"
            )

            async def concurrent(oid, target, team, expected_version, connection=None):
                conn = connection or await asyncpg.connect(database=name, **config)
                try:
                    async with transaction(actors["manager"], connection=conn) as tx:
                        return await tx.fetchval(
                            "SELECT security.transfer_opportunity_owner($1,$2,$3,$4,$5,$6)",
                            oid,
                            expected_version,
                            actors[target].user_id,
                            team,
                            "race",
                            uuid4(),
                        )
                except asyncpg.PostgresError as exc:
                    return str(exc).split("\n", 1)[0]
                finally:
                    if connection is None:
                        await conn.close()

            race = await new_opportunity("Concurrent")
            v = await version(race)
            outcomes = await asyncio.gather(concurrent(race, "new", south, v), concurrent(race, "peer", east, v))
            assert outcomes.count("VERSION_CONFLICT") == 1, outcomes
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM crm.opportunity_owner_transfer_event WHERE opportunity_id=$1", race
                )
                == 1
            )
            report["checks"].append(
                "two restricted connections competing on same overall version: one transfer, one conflict, one event"
            )
            # A real financial confirmation and transfer serialize on the same lock.
            financial_race = await new_opportunity("Finance confirmation races transfer")
            finance_tx = c.transaction()
            await finance_tx.start()
            await set_request_context(c, actors["manager"])
            await c.execute("SELECT security.finance_write_lock()")
            financial_id = await operations(financial_race)
            await c.execute("RESET ROLE")
            financial_waiter = asyncio.create_task(
                concurrent(financial_race, "new", south, await version(financial_race))
            )
            for _ in range(100):
                waiting = await admin.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=$1 AND wait_event='advisory'", name
                )
                if waiting:
                    break
                await asyncio.sleep(0.02)
            assert waiting
            await finance_tx.commit()
            financial_response = await financial_waiter
            if isinstance(financial_response, str):
                financial_response = json.loads(financial_response)
            assert financial_response["changed"] is True
            finance_result = await c.fetchval(
                "SELECT material_snapshot FROM crm.opportunity_actual_confirmation WHERE id=$1", financial_id
            )
            assert finance_result["owner_user_ref_id"] == actors["old"].user_id and finance_result[
                "owner_team_id"
            ] == str(east)
            assert await version(financial_race) == 2
            report["checks"].append(
                "real financial confirmation holds finance lock ahead of transfer; transfer waits, re-reads committed evidence and preserves original financial attribution"
            )

            # A transaction waiting for finance lock must re-evaluate revoked grant.
            blocked = await new_opportunity("Wait and revoke")
            other = await asyncpg.connect(database=name, **config)
            lock_tx = c.transaction()
            await lock_tx.start()
            await set_request_context(c, actors["manager"])
            await c.execute("SELECT security.finance_write_lock()")
            waiter = asyncio.create_task(concurrent(blocked, "new", south, await version(blocked), other))
            try:
                for _ in range(50):
                    waiting = await admin.fetchval(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=$1 AND wait_event='advisory'", name
                    )
                    if waiting:
                        break
                    await asyncio.sleep(0.02)
                assert waiting
                await c.execute(
                    "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)",
                    ws,
                    actors["manager"].user_id,
                )
                await c.execute(
                    "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'opportunity.transfer','deny',NULL)",
                    ws,
                    actors["manager"].user_id,
                )
                await lock_tx.commit()
                assert await waiter == "OPPORTUNITY_ACCESS_DENIED"
            finally:
                await other.close()
            assert (await transfer(main, body, key, expected=403))["code"] == "OPPORTUNITY_ACCESS_DENIED"
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM crm.opportunity_owner_transfer_event WHERE opportunity_id=$1", blocked
                )
                == 0
            )
            report["checks"].append(
                "permission revocation during lock wait and successful-request replay is rechecked; no receipt authority bypass"
            )
            report["status"] = "passed"
            report["integration_status"] = (
                "V259+V260 private integration passed"
                if args.with_owner_followup
                else "V259 owned slice passed; V260 follow-up reference remains required"
            )
    except asyncpg.PostgresError as exc:
        print("POSTGRES_CONTEXT:", exc.context)
        raise
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
        report["disposable_database_and_role_removed"] = True
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, default=55439)
    parser.add_argument("--output", required=True)
    parser.add_argument("--with-owner-followup", action="store_true")
    asyncio.run(verify(parser.parse_args()))
