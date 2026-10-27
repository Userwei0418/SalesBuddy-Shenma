"""Closed-company SQL facade: restricted role, same RBAC, fixed projections only."""

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.services.tenant_export import json_bytes, table_query
from sales_backend.services.closed_company_export import ClosedExportSnapshot, export_actor
from sales_backend.services.material_storage import MaterialStorage
from sales_backend.services.tenant_export_catalog import SECTIONS
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[3]
PASSWORD_HASH = "synthetic-sql-credential-v1"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalized_json(value):
    """Compare timestamp instants; PostgreSQL omits insignificant fractional zeroes."""
    if isinstance(value, list):
        return [normalized_json(item) for item in value]
    if isinstance(value, dict):
        return {key: normalized_json(item) for key, item in value.items()}
    if value == "9999-12-31T23:59:59.999999":
        return "infinity"
    if value == "0001-01-01T00:00:00":
        return "-infinity"
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).isoformat()
    return value


async def call(connection, name, *args):
    return await connection.fetchval(
        f"SELECT security.closed_export_{name}({','.join('$' + str(i) for i in range(1, len(args) + 1))})", *args
    )


async def seed_company(connection, *, status="closed", code=None, content=b"synthetic original bytes", chunk_size=None):
    ws, user, other = uuid4(), uuid4(), uuid4()
    code = code or "closed-" + uuid4().hex[:12]
    async with fixture_owner(connection):
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'合成关闭公司')", ws, code
        )
        await connection.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
        for identity, role, account in ((user, "administrator", "EXPORTADMIN"), (other, "operations", "OPERATOR")):
            await connection.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name,phone_number) VALUES($1,$2,$3,$4,$4,$5)",
                identity,
                ws,
                str(identity),
                account,
                "13900000123" if identity == user else None,
            )
            await connection.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,'workspace')",
                ws,
                identity,
                role,
            )
            await connection.execute(
                "INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password) VALUES($1,$2,$3,false)",
                identity,
                ws,
                PASSWORD_HASH,
            )
        await connection.execute(
            "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2),($1,$3)", ws, user, other
        )
        customer = await connection.fetchval(
            "INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id) VALUES($1,'同名合成客户','同名合成客户',$2) RETURNING id",
            ws,
            user,
        )
        deleted = await connection.fetchval(
            "INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,deleted_at) VALUES($1,'已删除合成客户','已删除合成客户',$2,clock_timestamp()) RETURNING id",
            ws,
            user,
        )
        await connection.execute(
            "INSERT INTO crm.contact(workspace_id,customer_id,name) VALUES($1,$2,'不应导出的已删除父项联系人')",
            ws,
            deleted,
        )
        opportunity = await connection.fetchval(
            """INSERT INTO crm.opportunity(workspace_id,customer_id,name,amount,owner_user_ref_id,created_by_user_ref_id)
            VALUES($1,$2,'合成精度商机',123456789012.123456789,$3,$3) RETURNING id""",
            ws,
            customer,
            user,
        )
        definition = await connection.fetchval(
            "INSERT INTO config.form_definition(workspace_id,form_code,name,object_type) VALUES($1,'closed-export','合成关闭导出','visit') RETURNING id",
            ws,
        )
        form = await connection.fetchval(
            "INSERT INTO config.form_version(form_definition_id,version_no,status) VALUES($1,1,'active') RETURNING id",
            definition,
        )
        imported, visits = uuid4(), []
        await connection.execute(
            """INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,file_path,file_size,status,storage_profile,storage_driver,storage_key,content_sha256)
            VALUES($1,$2,$3,'synthetic.txt','',$4,'succeeded','postgres','postgres',$5,$6)""",
            imported,
            ws,
            user,
            len(content),
            str(ws) + "/" + str(imported),
            hashlib.sha256(content).hexdigest(),
        )
        chunk_size = chunk_size or len(content)
        await connection.executemany(
            "INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,$3,$4)",
            [
                (imported, ws, i, content[start : start + chunk_size])
                for i, start in enumerate(range(0, len(content), chunk_size))
            ],
        )
        for linked in (None, opportunity):
            visits.append(
                await connection.fetchval(
                    """INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,form_version_id,status,interaction_at,follow_up_record,source_import_id,confirmed_at,confirmed_by_user_ref_id)
                VALUES($1,$2,$3,$4,$5,'confirmed',clock_timestamp(),'合成已归档跟进',$6,clock_timestamp(),$4) RETURNING id""",
                    ws,
                    customer,
                    linked,
                    user,
                    form,
                    imported,
                )
            )
        await connection.execute(
            """INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,form_version_id,status,interaction_at,follow_up_record)
            VALUES($1,$2,$3,$4,'pending_confirm',clock_timestamp(),'PRIVATE DRAFT EXCLUDED')""",
            ws,
            customer,
            user,
            form,
        )
        await connection.execute("UPDATE platform.workspace SET status=$2 WHERE id=$1", ws, status)
    return {
        "workspace": ws,
        "user": user,
        "other": other,
        "code": code,
        "customer": customer,
        "opportunity": opportunity,
        "import": imported,
        "content": content,
        "visits": visits,
    }


async def issue(connection, company, *, identifier="EXPORTADMIN"):
    token = digest(uuid4().hex)
    candidate = await call(connection, "candidate", company["code"], identifier)
    assert candidate and candidate["user_id"] == str(company["user"])
    profile = await call(
        connection,
        "issue",
        company["workspace"],
        company["user"],
        identifier,
        PASSWORD_HASH,
        token,
        digest("closed-export-account:" + company["code"] + ":EXPORTADMIN"),
    )
    return token, profile


@pytest_asyncio.fixture
async def closed(connection):
    await assert_restricted(connection)
    return await seed_company(connection)


async def test_native_admin_login_scopes_expiry_and_logout_without_ordinary_authority(connection, closed):
    token, profile = await issue(connection, closed, identifier="13900000123")
    assert profile["workspace_id"] == str(closed["workspace"]) and profile["role"] == "administrator"
    assert UUID(profile["session_id"]) and profile["team_ids"] == []
    assert 0 < (datetime.fromisoformat(profile["expires_at"]) - datetime.now(UTC)).total_seconds() <= 900
    assert "password" not in json.dumps(profile) and "token" not in json.dumps(profile)
    assert await call(connection, "status", token) == profile
    assert await call(connection, "candidate", closed["code"], "OPERATOR") is None
    assert await call(connection, "candidate", None, "EXPORTADMIN") is None
    assert await call(connection, "candidate", "unknown-company", "EXPORTADMIN") is None
    actor = ActorContext(
        workspace_id=str(closed["workspace"]),
        user_id=str(closed["user"]),
        role=RoleCode.ADMINISTRATOR,
        data_scope=DataScope.WORKSPACE,
    )
    with pytest.raises(PermissionError):
        async with connection.transaction():
            await set_request_context(connection, actor)
    assert not await connection.fetchval("SELECT security.workspace_is_active($1)", closed["workspace"])
    await call(connection, "logout", token)
    await call(connection, "logout", token)
    with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_AUTH_REQUIRED"):
        async with connection.transaction():
            await call(connection, "status", token)
    async with fixture_owner(connection):
        events = await connection.fetch(
            "SELECT action FROM security.closed_export_event WHERE session_id=$1 ORDER BY id",
            UUID(profile["session_id"]),
        )
        assert [row["action"] for row in events] == ["login", "logout"]
        assert not await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform.auth_session WHERE workspace_id=$1)", closed["workspace"]
        )


@pytest.mark.parametrize("status", ["active", "suspended", "provisioning"])
async def test_other_lifecycle_states_are_not_closed_export_logins(connection, status):
    company = await seed_company(connection, status=status)
    assert await call(connection, "candidate", company["code"], "EXPORTADMIN") is None


async def test_catalog_typed_rows_match_active_rls_exports_and_other_company_isolated(connection):
    company = await seed_company(connection, status="active")
    foreign = await seed_company(connection)
    actor = ActorContext(
        workspace_id=str(company["workspace"]),
        user_id=str(company["user"]),
        role=RoleCode.ADMINISTRATOR,
        data_scope=DataScope.WORKSPACE,
    )
    await set_request_context(connection, actor)
    await connection.execute("SET LOCAL TIME ZONE 'UTC'")
    expected = {}
    for section in SECTIONS.values():
        for table in section.tables:
            rows = await connection.fetch(table_query(table))
            expected[table.name] = [json.loads(json_bytes(dict(row))) for row in rows]
    async with fixture_owner(connection):
        catalog = await connection.fetchval("SELECT security._closed_export_catalog()")
        for code, section in SECTIONS.items():
            for table in section.tables:
                assert catalog[table.name]["section"] == code
                assert catalog[table.name]["columns"] == table.columns.split(",")
                assert catalog[table.name]["predicate"] == table.predicate
        await connection.execute("UPDATE platform.workspace SET status='closed' WHERE id=$1", company["workspace"])
        workspace_table = SECTIONS["organization"].tables[0]
        expected["platform.workspace"] = [
            json.loads(json_bytes(dict(row))) for row in await connection.fetch(table_query(workspace_table))
        ]
    token, _ = await issue(connection, company)
    for section in SECTIONS.values():
        for table in section.tables:
            result = await call(connection, "rows", token, table.name, 0, 200)
            actual = normalized_json(result["items"])
            expected[table.name] = normalized_json(expected[table.name])
            if table.name == "platform.workspace":
                expected[table.name][0]["status"] = "closed"
            assert sorted(actual, key=lambda x: json.dumps(x, sort_keys=True)) == sorted(
                expected[table.name], key=lambda x: json.dumps(x, sort_keys=True)
            ), table.name
            assert all(str(foreign["workspace"]) not in json.dumps(row) for row in actual)
    opportunity = (await call(connection, "rows", token, "crm.opportunity", 0, 200))["items"][0]
    assert isinstance(opportunity["amount"], str) and Decimal(opportunity["amount"]) == Decimal(
        expected["crm.opportunity"][0]["amount"]
    )
    # Context is restored; even forged current tenant settings cannot remove the native closed RLS guard.
    assert await connection.fetchval("SELECT common.current_workspace_id()") == company["workspace"]
    assert await connection.fetchval("SELECT count(*) FROM crm.customer") == 0


async def test_original_metadata_bytes_privacy_and_cross_company_object_denial(connection, closed):
    foreign = await seed_company(connection)
    token, _ = await issue(connection, closed)
    metadata = (await call(connection, "originals", token, False, 0, 100))["items"]
    assert len(metadata) == 1 and not metadata[0]["storage_key"] and not metadata[0]["file_path"]
    originals = (await call(connection, "originals", token, True, 0, 100))["items"]
    assert originals[0]["storage_key"] == str(closed["workspace"]) + "/" + str(closed["import"])
    assert set(originals[0]["visit_ids"]) == {str(value) for value in closed["visits"]}
    row = await connection.fetchrow(
        "SELECT * FROM security.closed_export_content_chunk($1,$2,0)", token, closed["import"]
    )
    assert row["chunk_no"] == 0 and bytes(row["content"]) == closed["content"]
    assert (
        await connection.fetchrow(
            "SELECT * FROM security.closed_export_content_chunk($1,$2,1)", token, closed["import"]
        )
        is None
    )
    with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_DENIED"):
        async with connection.transaction():
            await connection.fetch(
                "SELECT * FROM security.closed_export_content_chunk($1,$2,0)", token, foreign["import"]
            )
    async with fixture_owner(connection):
        await connection.execute(
            "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'opportunity.read','deny')",
            closed["workspace"],
            closed["user"],
        )
    visible = (await call(connection, "rows", token, "activity.visit", 0, 100))["items"]
    assert [v["id"] for v in visible] == [str(closed["visits"][0])]
    assert (await call(connection, "originals", token, True, 0, 100))["items"][0]["visit_ids"] == [
        str(closed["visits"][0])
    ]


@pytest.mark.parametrize(
    "change",
    [
        "credential",
        "user",
        "role",
        "template",
        "template-expiry",
        "grant-expiry",
        "deny",
        "module",
        "platform-managed",
        "expired",
        "revoked",
    ],
)
async def test_rechecks_current_authority_for_every_call(connection, closed, change):
    token, _ = await issue(connection, closed)
    async with fixture_owner(connection):
        ws, u = closed["workspace"], closed["user"]
        if change == "credential":
            await connection.execute(
                "UPDATE platform.password_credential SET password_hash='changed-synthetic' WHERE user_ref_id=$1", u
            )
        elif change == "user":
            await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", u)
        elif change == "role":
            await connection.execute(
                "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1", u
            )
        elif change == "template":
            await connection.execute(
                "UPDATE config.permission_role SET status='inactive' WHERE workspace_id=$1 AND builtin_role_code='administrator'",
                ws,
            )
        elif change == "template-expiry":
            await connection.execute(
                "UPDATE config.permission_role SET expires_at=clock_timestamp()-interval '1 second' WHERE workspace_id=$1 AND builtin_role_code='administrator'",
                ws,
            )
        elif change == "grant-expiry":
            await connection.execute(
                "DELETE FROM config.permission_role_grant WHERE workspace_id=$1 AND permission_code='company.export'",
                ws,
            )
            await connection.execute(
                "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code,expires_at) VALUES($1,$2,'company.export','allow','workspace',clock_timestamp()-interval '1 second')",
                ws,
                u,
            )
        elif change == "deny":
            await connection.execute(
                "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'company.export','deny')",
                ws,
                u,
            )
        elif change == "module":
            await connection.execute(
                "UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='customer'", ws
            )
        elif change == "platform-managed":
            await connection.execute(
                "UPDATE platform.user_ref SET attributes=attributes||'{\"platform_managed\":true}' WHERE id=$1", u
            )
        elif change == "expired":
            # Both boundaries share the statement's instant: independently read
            # wall clocks can exceed the strict 15-minute lifetime by 1 microsecond.
            await connection.execute(
                "UPDATE security.closed_export_session SET created_at=statement_timestamp()-interval '30 minutes',expires_at=statement_timestamp()-interval '15 minutes' WHERE token_hash=$1",
                token,
            )
        else:
            await connection.execute(
                "UPDATE security.closed_export_session SET revoked_at=clock_timestamp() WHERE token_hash=$1", token
            )
    with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_"):
        async with connection.transaction():
            await call(connection, "rows", token, "crm.customer", 0, 100)


async def test_fixed_functions_reject_extra_tables_pages_sections_and_keep_helpers_private(connection, closed):
    token, _ = await issue(connection, closed)
    for name, args in [
        ("rows", (token, "platform.password_credential", 0, 100)),
        ("rows", (token, "crm.customer", -1, 100)),
        ("rows", (token, "crm.customer", 0, 201)),
        ("authorize", (token, ["customers", "customers"], False)),
        ("authorize", (token, ["customers"], True)),
        ("authorize", (token, [], False)),
    ]:
        with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_INVALID_REQUEST"):
            async with connection.transaction():
                await call(connection, name, *args)
    for sql in [
        "SELECT * FROM security.closed_export_session",
        "SELECT * FROM security.closed_export_event",
        "SELECT security._closed_export_catalog()",
        "SELECT * FROM security._authorization_grants_for_state(NULL,NULL,'closed')",
    ]:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(sql)


async def test_audit_whitelist_and_revoked_session_failure_receipt(connection, closed):
    token, profile = await issue(connection, closed)
    export = uuid4()
    detail = {
        "sections": ["customers"],
        "include_originals": False,
        "reason": "合成验收导出",
        "sha256": "0" * 64,
        "record_count": 1,
    }
    await call(connection, "event", token, export, "prepared", detail)
    with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_INVALID_REQUEST"):
        async with connection.transaction():
            await call(connection, "event", token, export, "failed", {"customer_notes": "DO NOT STORE"})
    await call(connection, "logout", token)
    await call(connection, "event", token, export, "cancelled", {"error_code": "SESSION_REVOKED"})
    with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_AUTH_REQUIRED"):
        async with connection.transaction():
            await call(connection, "event", token, export, "sent", detail)
    async with fixture_owner(connection):
        events = await connection.fetch(
            "SELECT action,detail FROM security.closed_export_event WHERE session_id=$1 ORDER BY id",
            UUID(profile["session_id"]),
        )
    assert [r["action"] for r in events] == ["login", "prepared", "logout", "cancelled"]
    assert "DO NOT STORE" not in str(events)


async def test_active_grants_match_v159_oracle_without_a_duplicate_runtime_algorithm(connection):
    source = (ROOT / "database/migrations/V159__bounded_authorization_delegation.sql").read_text()
    start = source.index("CREATE OR REPLACE FUNCTION security.authorization_grants_for(")
    end = source.index("CREATE FUNCTION security.authorization_entry(", start)
    definition = source[start:end].replace(
        "security.authorization_grants_for(", "security.authorization_before_closed_export(", 1
    )
    company = await seed_company(connection, status="active")
    async with fixture_owner(connection):
        await connection.execute(definition)
        await connection.execute(
            "REVOKE ALL ON FUNCTION security.authorization_before_closed_export(uuid,uuid) FROM PUBLIC"
        )
        people = await connection.fetch("SELECT id,workspace_id FROM platform.user_ref WHERE deleted_at IS NULL")
        for change in ("base", "deny", "module", "expiry"):
            if change == "deny":
                await connection.execute(
                    "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'customer.read','deny')",
                    company["workspace"],
                    company["user"],
                )
            elif change == "module":
                await connection.execute(
                    "UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='opportunity'",
                    company["workspace"],
                )
            elif change == "expiry":
                await connection.execute(
                    "UPDATE config.permission_role SET expires_at=clock_timestamp()-interval '1 second' WHERE workspace_id=$1 AND builtin_role_code='administrator'",
                    company["workspace"],
                )
            for person in people:
                old = await connection.fetch(
                    "SELECT * FROM security.authorization_before_closed_export($1,$2) ORDER BY 1,2,3,4,5,6",
                    person["workspace_id"],
                    person["id"],
                )
                new = await connection.fetch(
                    "SELECT * FROM security.authorization_grants_for($1,$2) ORDER BY 1,2,3,4,5,6",
                    person["workspace_id"],
                    person["id"],
                )
                assert old == new, (change, person["id"])


async def test_read_only_snapshot_and_historical_small_chunks(connection):
    closed = await seed_company(connection, content=b"x" * 120, chunk_size=1)
    token, _ = await issue(connection, closed)
    await connection.execute("SET LOCAL transaction_read_only=on")
    assert (await call(connection, "rows", token, "crm.customer", 0, 1))["next_offset"] == 1
    assert (await call(connection, "rows", token, "crm.customer", 1, 1))["items"] == []
    assert (await call(connection, "originals", token, True, 0, 1))["items"][0]["file_size"] == 120
    chunks = []
    for i in (0, 100, 101, 119):
        chunk = await connection.fetchrow(
            "SELECT * FROM security.closed_export_content_chunk($1,$2,$3)", token, closed["import"], i
        )
        assert chunk["chunk_no"] == i
        chunks.append(bytes(chunk["content"]))
    assert b"".join(chunks) == b"xxxx"
    assert (
        await connection.fetchrow(
            "SELECT * FROM security.closed_export_content_chunk($1,$2,120)", token, closed["import"]
        )
        is None
    )
    with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_INVALID_REQUEST"):
        async with connection.transaction():
            await connection.fetch(
                "SELECT * FROM security.closed_export_content_chunk($1,$2,121)", token, closed["import"]
            )


@pytest.mark.parametrize(
    "detail",
    [
        None,
        [],
        {"sections": "customers"},
        {"sections": [None]},
        {"include_originals": "true"},
        {"sha256": 1},
        {"record_count": "NaN"},
        {"record_count": -1},
        {"record_count": 1000001},
        {"reason": None},
        {"error_code": "a secret string"},
    ],
)
async def test_malformed_audit_payload_fails_as_validation(connection, closed, detail):
    token, _ = await issue(connection, closed)
    with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_INVALID_REQUEST") as error:
        async with connection.transaction():
            await call(connection, "event", token, uuid4(), "failed", detail)
    assert error.value.sqlstate == "P7422"


async def test_reopening_then_closing_does_not_revive_old_export_session(connection, closed):
    token, _ = await issue(connection, closed)
    async with fixture_owner(connection):
        await connection.execute("UPDATE platform.workspace SET status='active' WHERE id=$1", closed["workspace"])
        await connection.execute("UPDATE platform.workspace SET status='closed' WHERE id=$1", closed["workspace"])
    with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_AUTH_REQUIRED"):
        async with connection.transaction():
            await call(connection, "status", token)
    fresh, _ = await issue(connection, closed)
    assert (await call(connection, "rows", fresh, "crm.customer", 0, 100))["items"]


async def test_admin_template_does_not_replace_native_identity_and_scope_is_not_widened(connection, closed):
    async with fixture_owner(connection):
        await connection.execute(
            """INSERT INTO config.account_permission_role(workspace_id,user_ref_id,role_id,scope_code)
            SELECT $1,$2,id,'workspace' FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='administrator'""",
            closed["workspace"],
            closed["other"],
        )
    assert await call(connection, "candidate", closed["code"], "OPERATOR") is None
    token, _ = await issue(connection, closed)
    async with fixture_owner(connection):
        await connection.execute(
            "DELETE FROM config.permission_role_grant WHERE workspace_id=$1 AND permission_code='customer.read'",
            closed["workspace"],
        )
        await connection.execute(
            "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'customer.read','allow','self')",
            closed["workspace"],
            closed["user"],
        )
    # Other authorized sections remain usable; the custom self grant never widens to workspace.
    assert (await call(connection, "rows", token, "platform.user_ref", 0, 100))["items"]
    with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_DENIED"):
        async with connection.transaction():
            await call(connection, "rows", token, "crm.customer", 0, 100)


async def test_issue_rechecks_observed_credential_and_canonical_throttle_key(connection, closed):
    candidate = await call(connection, "candidate", closed["code"], "EXPORTADMIN")
    assert candidate
    for password, key, expected in [
        ("stale observed hash", digest("closed-export-account:" + closed["code"] + ":EXPORTADMIN"), "AUTH_REQUIRED"),
        (PASSWORD_HASH, digest("identifier-specific-phone-bucket"), "INVALID_REQUEST"),
    ]:
        with pytest.raises(asyncpg.PostgresError, match="CLOSED_EXPORT_" + expected):
            async with connection.transaction():
                await call(
                    connection,
                    "issue",
                    closed["workspace"],
                    closed["user"],
                    "13900000123",
                    password,
                    digest(uuid4().hex),
                    key,
                )
    async with fixture_owner(connection):
        assert not await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM security.closed_export_session WHERE workspace_id=$1)", closed["workspace"]
        )
        for worker in ("salegent_feishu_worker", "salegent_agent_worker"):
            if await connection.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", worker):
                assert not await connection.fetchval(
                    "SELECT has_function_privilege($1,'security.closed_export_candidate(text,text)','EXECUTE')", worker
                )


@pytest.mark.parametrize("corruption", ["missing-middle", "trailing-after-gap"])
async def test_original_reader_does_not_mistake_missing_chunk_for_eof(connection, corruption):
    company = await seed_company(connection, content=b"abcd", chunk_size=1 if corruption == "missing-middle" else 4)
    async with fixture_owner(connection):
        if corruption == "missing-middle":
            await connection.execute(
                "DELETE FROM activity.visit_import_content WHERE import_id=$1 AND chunk_no=1", company["import"]
            )
        else:
            await connection.execute(
                "INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,2,$3)",
                company["import"],
                company["workspace"],
                b"trailing",
            )
    token, profile = await issue(connection, company)
    next_actual = await connection.fetchrow(
        "SELECT * FROM security.closed_export_content_chunk($1,$2,1)", token, company["import"]
    )
    assert next_actual["chunk_no"] == 2
    metadata = (await call(connection, "originals", token, True, 0, 100))["items"][0]
    snapshot = ClosedExportSnapshot(
        connection,
        token,
        export_actor(profile),
        True,
        MaterialStorage({"default": "postgres", "profiles": {"postgres": {"driver": "postgres"}}}),
        "synthetic",
    )
    with pytest.raises(ValueError, match="原始文件不完整"):
        await snapshot.read_original(metadata)
