"""Real restricted-role repository calls for whole-release ACL regression.

Called only by the disposable local PostgreSQL bridge verifier. No application
server, worker, model, Feishu client or external connection is started.
"""

import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "tests/fixtures/upstream_e40_runtime_permissions.json"
POLICY_SHA256 = "31d0a6b3e525cc7ca931cb6744954c793e5321834962d54f01e79906d36a0d4d"


async def reviewed_policy(conn, role):
    """Match fixed API + business-worker policy, with the one reviewed exception."""
    assert hashlib.sha256(POLICY.read_bytes()).hexdigest() == POLICY_SHA256
    policy = json.loads(POLICY.read_text())
    missing = []
    for service in ("api", "worker"):
        contract = policy["services"][service]
        for schema in contract["schemas"]:
            if not await conn.fetchval(
                "SELECT has_schema_privilege($1,$2,'USAGE')", role, schema
            ):
                missing.append((service, schema, "USAGE"))
        for signature in contract["functions"]:
            assert await conn.fetchval(
                "SELECT to_regprocedure($1) IS NOT NULL", signature
            ), signature
            if not await conn.fetchval(
                "SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature
            ):
                missing.append((service, signature, "EXECUTE"))
        for table, permissions in contract["tables"].items():
            for permission in permissions:
                actual = await conn.fetchval(
                    "SELECT has_table_privilege($1,$2,$3)", role, table, permission
                )
                if table == "crm.customer_creation_request" and permission in (
                    "INSERT",
                    "UPDATE",
                ):
                    assert not actual, (
                        table,
                        permission,
                        "strict Shenma request boundary",
                    )
                elif not actual:
                    missing.append((service, table, permission))
        for table, operations in contract.get("columns", {}).items():
            for permission, columns in operations.items():
                for column in columns:
                    if not await conn.fetchval(
                        "SELECT has_column_privilege($1,$2,$3,$4)",
                        role,
                        table,
                        column,
                        permission,
                    ):
                        missing.append((service, table, column, permission))
    assert not missing, missing
    assert await conn.fetchval(
        "SELECT has_column_privilege($1,'crm.customer_creation_request','reviewed_payload','UPDATE')",
        role,
    )
    assert not await conn.fetchval(
        "SELECT has_table_privilege($1,'ops.schema_migration','SELECT')", role
    )
    assert not await conn.fetchval(
        "SELECT has_function_privilege('salegent_feishu_worker','security.company_customer_directory_search(text,integer,integer,text,text,text[],text)','EXECUTE')"
    )
    assert not await conn.fetchval(
        "SELECT has_table_privilege('salegent_feishu_worker','agent.work_report_document','SELECT')"
    )


async def actual_repositories(conn, role, fixture, context):
    sys.path.insert(0, str(ROOT.parent / "backend/src"))
    from sales_backend.contracts.customer_creation import CustomerCreationRequestBody
    from sales_backend.contracts.customer_portfolio import CustomerPortfolioQuery
    from sales_backend.contracts.work_report import (
        DocumentQuery,
        SchemeCreate,
        SnapshotSelection,
    )
    from sales_backend.contracts.work_report_base import ResolvedScope
    from sales_backend.domain.agent import ActorContext
    from sales_backend.domain.work_report import default_scheme, resolve_period
    from sales_backend.repositories.customer_creation_requests import (
        CustomerCreationRequestRepository,
    )
    from sales_backend.repositories.customer_portfolio import (
        CustomerPortfolioRepository,
    )
    from sales_backend.repositories.partners import PartnerRepository
    from sales_backend.repositories.task_alignment import TaskAlignmentRepository
    from sales_backend.repositories.work_report import WorkReportRepository
    from sales_backend.repositories.work_report_meeting_facts import (
        _meeting_schema_applied_at,
    )
    from sales_backend.services.work_report_overview import _require_overview_schema

    f = fixture
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(
            typ,
            schema="pg_catalog",
            encoder=json.dumps,
            decoder=json.loads,
            format="text",
        )
    transaction = conn.transaction()
    await transaction.start()
    try:
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        await conn.execute("SET LOCAL row_security=on")
        await context(conn, f["ws"], f["sales"], "sales", f["team"])
        actor = ActorContext(
            workspace_id=str(f["ws"]),
            user_id=str(f["sales"]),
            role="sales",
            data_scope="self",
            team_ids=(str(f["team"]),),
        )
        portfolio = await CustomerPortfolioRepository().read(
            conn, actor, CustomerPortfolioQuery(scope="self"), as_of=date(2026, 10, 15)
        )
        assert portfolio["total"] >= 1
        assert str(f["customer"]) in {str(item["id"]) for item in portfolio["items"]}
        assert all(
            str(item["owner_user_ref_id"]) == actor.user_id
            for item in portfolio["items"]
        )

        # These are actual backend capability probes against the mapped ledger,
        # under the same restricted role used by API/worker, not maintenance.
        assert await TaskAlignmentRepository.supports_v2(conn)
        await _require_overview_schema(conn)
        assert await _meeting_schema_applied_at(conn) == await conn.fetchval(
            "SELECT applied_at FROM ops.schema_migration WHERE version='V383'"
        )

        reports = WorkReportRepository()
        scheme = await reports.create_scheme(
            conn,
            actor,
            SchemeCreate(name="Local ACL verification", config=default_scheme()),
        )
        assert scheme.can_edit and scheme.version_no == 1
        assert (await reports.get_scheme(conn, actor, scheme.id)).id == scheme.id
        assert any(
            item.id == scheme.id
            for item in (await reports.list_schemes(conn, actor)).items
        )
        selection = SnapshotSelection()
        snapshot = await reports.create_snapshot(
            conn,
            actor,
            selection,
            period=resolve_period(
                selection.period, now=datetime(2026, 10, 15, 8, tzinfo=timezone.utc)
            ),
            scope=ResolvedScope(kind="self", label="本人", member_ids=[f["sales"]]),
        )
        assert snapshot.status == "preparing"
        assert (await reports.get_snapshot(conn, actor, snapshot.id)).id == snapshot.id
        assert (await reports.list_documents(conn, actor, DocumentQuery())).items == []
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM agent.work_report_event WHERE object_id=ANY($1::uuid[])",
                [scheme.id, snapshot.id],
            )
            == 2
        )

        request_body = CustomerCreationRequestBody(
            name="Runtime approval fixture",
            business_identities=["customer", "partner"],
            level_code="Tier-2",
            source="销售自拓",
            target_team="Sales test",
            target_team_id=f["team"],
            customer_type="潜在客户",
            contact_name="Fixture contact",
            contact_title="主任",
            contact_role="决策者",
        )
        requests = CustomerCreationRequestRepository()
        request = await requests.submit(
            conn, actor, request_body.model_dump(mode="json")
        )
        assert request["status"] == "pending" and request["created_customer_id"] is None
        assert not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM crm.customer WHERE name=$1)", request_body.name
        )
        # Sales must still fail the real repository reviewer gate.
        try:
            await requests.decide(
                conn, actor, str(request["id"]), "approved", expected_version=1
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("sales acquired customer.create_review")
        await context(conn, f["ws"], f["admin"], "administrator")
        await conn.execute("SELECT security.authorization_refresh()")
        admin = ActorContext(
            workspace_id=str(f["ws"]),
            user_id=str(f["admin"]),
            role="administrator",
            data_scope="workspace",
        )
        decision = await requests.decide(
            conn, admin, str(request["id"]), "approved", expected_version=1
        )
        assert (
            decision["status"] == "approved"
            and decision["customer"]["status"] == "unclaimed"
        )
        assert await conn.fetchval(
            "SELECT owner_user_ref_id IS NULL FROM crm.customer WHERE id=$1::uuid",
            decision["created_customer_id"],
        )
        # Dual identity approval executes set_customer_business_identity; the
        # actual partner list then invokes partner_enterprise_reference.
        partners = await PartnerRepository().list(conn, q=request_body.name)
        assert partners["total"] == 1
        assert (
            partners["items"][0]["linked_customer_id"]
            == decision["created_customer_id"]
        )
        assert partners["items"][0]["business_identities"] == ["customer", "partner"]
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    finally:
        await transaction.rollback()
        for typ in ("json", "jsonb"):
            await conn.reset_type_codec(typ, schema="pg_catalog")
