"""Real PostgreSQL whole-121 bridge verification; only creates owned disposable DBs.

No DATABASE_URL, credentials, production socket or pre-existing database is read.
Use --socket /absolute/private/socket --port <local port>. New randomized test
DBs/NOLOGIN NOBYPASSRLS roles are removed in finally. Synthetic fixture only.
"""

import argparse
import asyncio
import getpass
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from migrate import MigrationError, migrate
from verify_fde_postgres import context, fixture_before_upgrade


async def through(conn, version):
    with tempfile.TemporaryDirectory(prefix="shenma-whole-history-") as folder:
        root = Path(folder) / "database"
        shutil.copytree(ROOT, root)
        for path in (root / "migrations").glob("V*.sql"):
            if int(path.name[1:4]) > version:
                path.unlink()
        return await migrate(conn, root)


async def seed(conn, runtime_role=None):
    await through(conn, 68)
    ws, team, sales, customer, visit, _ = await fixture_before_upgrade(conn)
    if runtime_role is not None:
        # Production has used its restricted runtime since the V125 bootstrap.
        # ACL migrations must observe that role, not a post-V159 synthetic role.
        await through(conn, 125)
        await runtime(conn, runtime_role)
    await through(conn, 159)
    await conn.execute(
        "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
        ws,
        sales,
    )
    await conn.execute(
        "INSERT INTO config.account_permission_role(workspace_id,user_ref_id,role_id,scope_code) SELECT $1,$2,id,'self' FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='sales' ON CONFLICT DO NOTHING",
        ws,
        sales,
    )
    await conn.execute(
        "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'account.unlock','deny')",
        ws,
        sales,
    )
    await conn.execute(
        "UPDATE security.deployment_account_quota SET max_active_accounts=65 WHERE singleton"
    )
    await conn.execute(
        "INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password) VALUES($1,$2,'synthetic-fixture-hash-do-not-use',false)",
        sales,
        ws,
    )
    # Named operation/admin accounts and one disabled account: no migration may
    # replace passwords, reactivate a disabled user, or assign cross-company IDs.
    admin, disabled = uuid.uuid4(), uuid.uuid4()
    for ident, name, status in (
        (admin, "fixture-admin", "active"),
        (disabled, "disabled-fixture", "inactive"),
    ):
        await conn.execute(
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name,status) VALUES($1,$2,$3,$3,$3,$4)",
            ident,
            ws,
            name,
            status,
        )
    await conn.execute(
        "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'administrator','workspace')",
        ws,
        admin,
    )
    await conn.execute(
        "INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password) VALUES($1,$2,'admin-fixture-hash-do-not-use',false)",
        admin,
        ws,
    )
    demo = uuid.uuid4()
    await conn.execute(
        "INSERT INTO platform.workspace(id,external_workspace_id,name,attributes) VALUES($1,$2,'isolated fixture demo','{\"kind\":\"demo\"}')",
        demo,
        str(demo),
    )
    await conn.execute("SELECT security.register_isolated_demo_quota($1,7)", demo)
    demo_user = uuid.uuid4()
    await conn.execute(
        "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,'demo-fixture','demo fixture')",
        demo_user,
        demo,
        str(demo_user),
    )
    await context(conn, ws, admin, "administrator")
    opportunity = uuid.uuid4()
    await conn.execute(
        "INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,amount) VALUES($1,$2,$3,'Existing real fixture opportunity',$4,$5,123456.78)",
        opportunity,
        ws,
        customer,
        sales,
        team,
    )
    trial, trial_opp = uuid.uuid4(), uuid.uuid4()
    marker = {
        "synthetic_trial": {
            "batch_id": "bridge-test",
            "root_customer_id": str(trial),
            "source": "test_fixture",
        }
    }
    await conn.execute(
        "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id,data_kind,import_meta) VALUES($1,$2,'Synthetic trial fixture','synthetic trial fixture',$3,$3,$4,'demo',$5::jsonb)",
        trial,
        ws,
        sales,
        team,
        json.dumps(marker),
    )
    await conn.execute(
        "INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,amount) VALUES($1,$2,$3,'Synthetic trial opportunity',$4,$5,99)",
        trial_opp,
        ws,
        trial,
        sales,
        team,
    )
    weekly = uuid.uuid4()
    snapshot = {
        "records": [{"id": str(visit)}],
        "context": {
            "customers": [{"id": str(customer)}],
            "opportunities": [{"id": str(opportunity)}],
        },
    }
    await conn.execute(
        """INSERT INTO insight.weekly_report(id,workspace_id,author_id,request_id,status,result_status,input_snapshot,input_sha256,binding,original_result,draft_markdown,draft_version,report_week,source_cutoff_at)
     VALUES($1,$2,$3,$4,'succeeded','ready',$5,$6,'{}','{"title":"Existing weekly"}','Manual draft preserved',1,'2026-10-12',clock_timestamp())""",
        weekly,
        ws,
        sales,
        uuid.uuid4(),
        json.dumps(snapshot),
        hashlib.sha256(json.dumps(snapshot).encode()).hexdigest(),
    )
    await conn.execute(
        "INSERT INTO insight.weekly_report_revision(workspace_id,author_id,report_id,version_no,body_markdown,source) VALUES($1,$2,$3,1,'Manual draft preserved','manual')",
        ws,
        sales,
        weekly,
    )
    connection = uuid.uuid4()
    settings = {
        "workspace_id": str(ws),
        "connection_id": str(connection),
        "direction": "system_to_base",
        "mappings": {"weekly_report": {"enabled": True}},
        "notification": {
            "enabled": True,
            "routing_mode": "single_group",
            "default_chat_id": "synthetic-chat-fixture",
            "on_create": ["weekly_report"],
        },
    }
    await conn.execute(
        "INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,validated_revision,settings,updated_by) VALUES($1,$2,1,true,1,$3::jsonb,$4)",
        connection,
        ws,
        json.dumps(settings),
        admin,
    )
    await conn.execute(
        "INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id) VALUES($1,$2,decode('66697874757265','hex'),'fixture-no-real-key')",
        connection,
        ws,
    )
    return {
        "ws": ws,
        "team": team,
        "sales": sales,
        "admin": admin,
        "disabled": disabled,
        "demo": demo,
        "demo_user": demo_user,
        "customer": customer,
        "opportunity": opportunity,
        "visit": visit,
        "trial": trial,
        "trial_opp": trial_opp,
        "weekly": weekly,
        "connection": connection,
    }


async def preserved_snapshot(conn):
    tables = [
        "config.account_authorization",
        "config.account_permission_role",
        "config.account_permission_override",
        "platform.workspace",
        "platform.user_ref",
        "platform.password_credential",
        "platform.role_binding",
        "platform.team",
        "platform.team_membership",
        "security.password_policy",
        "security.isolated_demo_account_quota",
        "security.deployment_account_quota",
        "config.feishu_connection",
        "security.feishu_credential",
        "insight.weekly_report",
        "insight.weekly_report_revision",
    ]
    return {
        table: await conn.fetchval(
            f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text FROM {table} t"
        )
        for table in tables
    }


async def runtime(conn, role):
    await conn.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
    await conn.execute(
        (ROOT.parent / "deployment/runtime-grants.sql")
        .read_text()
        .replace("shenma_runtime", role)
    )


async def report_document(conn, f, subject, *, extra=None):
    snapshot, document, revision = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    payload = {
        "sources": [{"source_type": "opportunity", "record_id": str(subject)}],
        "coverage": {},
        "metrics": {},
    }
    payload.update(extra or {})
    sha = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    async with conn.transaction():
        await conn.execute(
            """INSERT INTO agent.work_report_snapshot(id,workspace_id,owner_user_ref_id,selection,period,scope,status,snapshot_hash,payload,data_as_of)
         VALUES($1,$2,$3,'{"sources":["opportunity"]}','{"start":"2026-10-12T00:00:00+08:00","end_exclusive":"2026-10-19T00:00:00+08:00","cutoff":"2026-10-18T12:00:00+08:00"}','{"type":"self"}','ready',$4,$5::jsonb,clock_timestamp())""",
            snapshot,
            f["ws"],
            f["sales"],
            sha,
            json.dumps(payload),
        )
        await conn.execute(
            """INSERT INTO agent.work_report_document(id,workspace_id,owner_user_ref_id,snapshot_id,snapshot_hash,context,source_manifest,selected_sources,latest_revision_id)
         VALUES($1,$2,$3,$4,$5,'{}','[]','[]',$6)""",
            document,
            f["ws"],
            f["sales"],
            snapshot,
            sha,
            revision,
        )
        await conn.execute(
            """INSERT INTO agent.work_report_revision(id,workspace_id,document_id,revision_no,origin,content,scheme,created_by_user_ref_id)
         VALUES($1,$2,$3,1,'manual','{"title":"Frozen fixture report"}',$4::jsonb,$5)""",
            revision,
            f["ws"],
            document,
            json.dumps(
                {
                    "scheme_id": str(uuid.uuid4()),
                    "version_no": 1,
                    "name": "fixture",
                    "config_hash": "a" * 64,
                }
            ),
            f["sales"],
        )
    return document, revision


async def rejected(conn, sql, args, code):
    try:
        async with conn.transaction():
            await conn.fetchval(sql, *args)
    except asyncpg.PostgresError as error:
        assert error.sqlstate == code, (error.sqlstate, str(error))
        return str(error)
    raise AssertionError("Expected database rejection")


async def report_checks(conn, role, f, results):
    await context(conn, f["ws"], f["sales"], "sales", f["team"])
    document, revision = await report_document(conn, f, f["opportunity"])
    bad_document, bad_revision = await report_document(conn, f, f["trial_opp"])
    aggregate, aggregate_revision = await report_document(
        conn,
        f,
        f["opportunity"],
        extra={
            "overview_facts": {"facts": {}},
            "overview_dependencies": [
                {"source_type": "meeting_customer", "record_id": str(f["customer"])}
            ],
        },
    )
    mixed, mixed_revision = await report_document(
        conn,
        f,
        f["opportunity"],
        extra={
            "overview_facts": {"facts": {}},
            "overview_dependencies": [
                {"source_type": "meeting_customer", "record_id": str(f["trial"])}
            ],
        },
    )
    # Nothing is queued by merely creating/reading a new draft.
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM ops.feishu_event WHERE object_kind='weekly_report' AND object_id=$1",
            document,
        )
        == 0
    )
    await conn.execute(f'SET ROLE "{role}"')
    receipt = json.loads(
        await conn.fetchval("SELECT security.work_report_feishu_status($1)", document)
    )
    assert receipt["status"] == "not_published" and receipt["can_publish"] is True
    assert await conn.fetchval("SELECT security.authorization_has('work_report.share')")
    assert not await conn.fetchval("SELECT security.authorization_has('report.export')")
    publish = "SELECT security.publish_work_report_feishu($1,$2,$3,$4)"
    error = await rejected(
        conn, publish, (document, 2, revision, "Frozen fixture markdown"), "22023"
    )
    assert "VERSION_CONFLICT" in error
    error = await rejected(
        conn,
        publish,
        (bad_document, 1, bad_revision, "Synthetic must not publish"),
        "22023",
    )
    assert "SOURCE_EXCLUDED" in error
    receipt = json.loads(
        await conn.fetchval(
            "SELECT security.work_report_feishu_status($1)", bad_document
        )
    )
    assert (
        receipt["can_publish"] is False
        and receipt["blocked_reason"] == "source_excluded"
    )
    error = await rejected(
        conn,
        publish,
        (mixed, 1, mixed_revision, "Hidden aggregate synthetic must not publish"),
        "22023",
    )
    assert "SOURCE_EXCLUDED" in error
    aggregate_eid = await conn.fetchval(
        publish, aggregate, 1, aggregate_revision, "Enhanced report real sources"
    )
    assert aggregate_eid is not None
    eid = await conn.fetchval(publish, document, 1, revision, "Frozen fixture markdown")
    assert (
        await conn.fetchval(
            publish, document, 1, revision, "Replayed text must not overwrite"
        )
        == eid
    )
    receipt = json.loads(
        await conn.fetchval("SELECT security.work_report_feishu_status($1)", document)
    )
    assert (
        receipt["status"] == "pending"
        and receipt["can_publish"] is False
        and receipt["blocked_reason"] == "already_published"
    )
    await conn.execute("RESET ROLE")
    await context(conn, f["ws"], f["admin"], "administrator")
    await conn.execute(f'SET ROLE "{role}"')
    await rejected(
        conn, publish, (document, 1, revision, "Not document owner"), "42501"
    )
    await conn.execute("RESET ROLE")
    await conn.execute(
        "SELECT set_config('app.workspace_id','',false),set_config('app.user_ref_id','',false)"
    )
    await conn.execute("SET ROLE salegent_feishu_worker")
    try:
        projection = json.loads(
            await conn.fetchval(
                "SELECT ops.feishu_source($1,'weekly_report',$2)",
                f["connection"],
                document,
            )
        )
    finally:
        await conn.execute("RESET ROLE")
    assert (
        projection["body_markdown"] == "Frozen fixture markdown"
        and projection["report_week"] == "2026-10-12"
    )
    assert not any(
        key in projection for key in ("payload", "context", "source_manifest")
    )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM ops.feishu_event WHERE object_kind='weekly_report' AND object_id=$1",
            document,
        )
        == 1
    )
    # Same object may not silently issue a revision notification swallowed by
    # existing per-object notification dedupe.
    await context(conn, f["ws"], f["sales"], "sales", f["team"])
    assert "ALREADY_PUBLISHED" in await rejected(
        conn, publish, (document, 1, uuid.uuid4(), "Revision2"), "22023"
    )
    results.append(
        "work_report_explicit_freeze_idempotency_owner_version_synthetic_aggregate_guards_and_narrow_worker_projection"
    )


async def verify_feishu_startup(conn, runtime_role, results):
    # Exercise the actual imported process startup guard, not an approximate
    # SQL allowlist: customer weekly helpers must not make the worker refuse boot.
    sys.path.insert(0, str(ROOT.parent / "backend/src"))
    from sales_backend.feishu_worker import verify_worker_role

    await conn.execute("SET ROLE salegent_feishu_worker")
    try:
        await verify_worker_role(conn)
        for function in (
            "ops.feishu_weekly_source(uuid,uuid)",
            "ops.feishu_work_report_source(uuid,uuid)",
            "ops.feishu_source_internal_121(uuid,text,uuid)",
        ):
            assert not await conn.fetchval(
                "SELECT has_function_privilege(current_user,$1,'EXECUTE')", function
            ), function
    finally:
        await conn.execute("RESET ROLE")
    await conn.execute(f'SET ROLE "{runtime_role}"')
    try:
        try:
            await verify_worker_role(conn)
        except RuntimeError as exc:
            assert str(exc) == "Dedicated non-elevated Feishu worker role required"
        else:
            raise AssertionError(
                "Business runtime must not pass the Feishu worker guard"
            )
    finally:
        await conn.execute("RESET ROLE")
    results.append(
        "actual_python_feishu_worker_startup_guard_passes_only_dedicated_role"
    )


async def quotas_and_capture(conn, f, results):
    transaction = conn.transaction()
    await transaction.start()
    try:
        await context(conn, f["ws"], f["admin"], "administrator")
        count = await conn.fetchval(
            "SELECT active_accounts FROM security.deployment_account_quota WHERE singleton"
        )
        for i in range(65 - count):
            ident = uuid.uuid4()
            await conn.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,'Quota fixture')",
                ident,
                f["ws"],
                "quota-" + str(i),
            )
        create_user = "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,'Quota overflow fixture') RETURNING id"
        assert "65" in await rejected(
            conn, create_user, (uuid.uuid4(), f["ws"], "overflow-production"), "P0001"
        )
        for i in range(6):
            await conn.execute(
                create_user, uuid.uuid4(), f["demo"], "quota-demo-" + str(i)
            )
        assert "演示" in await rejected(
            conn, create_user, (uuid.uuid4(), f["demo"], "overflow-demo"), "P0001"
        )
        assert (
            await conn.fetchval(
                "SELECT active_accounts FROM security.deployment_account_quota WHERE singleton"
            )
            == 65
        )
        assert (
            await conn.fetchval(
                "SELECT active_accounts FROM security.isolated_demo_account_quota WHERE workspace_id=$1",
                f["demo"],
            )
            == 7
        )
        trial = uuid.uuid4()
        marker = {
            "synthetic_trial": {
                "batch_id": "post-upgrade",
                "root_customer_id": str(trial),
                "source": "test_fixture",
            }
        }
        await conn.execute(
            "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,data_kind,import_meta) VALUES($1,$2,'New synthetic fixture','new synthetic fixture',$3,'demo',$4::jsonb)",
            trial,
            f["ws"],
            f["sales"],
            json.dumps(marker),
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM ops.feishu_event WHERE object_id=$1", trial
            )
            == 0
        )
        results.append(
            "65_production_and_7_demo_limits_independent_overflow_denied_new_synthetic_insert_never_queues"
        )
    finally:
        await transaction.rollback()


async def run_case(admin, cfg, pre_s2, results):
    suffix = uuid.uuid4().hex[:12]
    name = "shenma_verify_whole_" + suffix
    role = "shenma_verify_runtime_" + suffix
    conn = None
    await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
    try:
        conn = await asyncpg.connect(database=name, **cfg)
        f = await seed(conn, role)
        if pre_s2:
            await through(conn, 161)
            await context(conn, f["ws"], f["sales"], "sales", f["team"])
            request = json.loads(
                await conn.fetchval(
                    "SELECT security.submit_customer_creation_request($1::jsonb)",
                    json.dumps(
                        {
                            "name": "Pending original request",
                            "company_reference": "EXISTING-REQUEST-01",
                        }
                    ),
                )
            )
            view = uuid.uuid4()
            await conn.execute(
                'INSERT INTO crm.opportunity_saved_view(id,workspace_id,owner_user_ref_id,name,config) VALUES($1,$2,$3,\'Original private view\',\'{"schema_version":1,"columns":["name","stage"]}\')',
                view,
                f["ws"],
                f["sales"],
            )
            f.update(request=uuid.UUID(request["id"]), view=view)
        before = await preserved_snapshot(conn)
        business = {
            table: await conn.fetchval(
                f"SELECT jsonb_agg(to_jsonb(t) ORDER BY id)::text FROM {table} t"
            )
            for table in ("crm.customer", "crm.opportunity", "activity.visit")
        }
        ledger = [
            dict(row)
            for row in await conn.fetch(
                "SELECT * FROM ops.migration_checksum ORDER BY migration_key"
            )
        ]
        upgraded = await migrate(conn, ROOT)
        assert await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V399')"
        )
        after = await preserved_snapshot(conn)
        for table in before:
            old_rows = json.loads(before[table])
            new_rows = json.loads(after[table])
            assert len(old_rows) == len(new_rows), table
            if old_rows:
                keys = old_rows[0].keys()
                projected = [{key: row[key] for key in keys} for row in new_rows]
                assert sorted(
                    json.dumps(x, sort_keys=True) for x in old_rows
                ) == sorted(json.dumps(x, sort_keys=True) for x in projected), table
        for table, value in business.items():
            original = json.loads(value)
            current = json.loads(
                await conn.fetchval(
                    f"SELECT jsonb_agg(to_jsonb(t) ORDER BY id)::text FROM {table} t"
                )
            )
            for old, new in zip(original, current, strict=True):
                assert {key: new.get(key) for key in old} == old, (
                    table,
                    [key for key in old if new.get(key) != old[key]],
                )
        for old in ledger:
            assert (
                dict(
                    await conn.fetchrow(
                        "SELECT * FROM ops.migration_checksum WHERE migration_key=$1",
                        old["migration_key"],
                    )
                )
                == old
            )
        assert all(item["status"] == "unchanged" for item in await migrate(conn, ROOT))
        results.append(
            f"V{161 if pre_s2 else 159}_to_V399_all_original_identity_business_config_quota_ledger_values_preserved_repeat_noop"
        )
        assert await conn.fetchval(
            "SELECT max_active_accounts=65 FROM security.deployment_account_quota WHERE singleton"
        )
        assert await conn.fetchval(
            "SELECT shared_enabled FROM control.tenant_ai_service WHERE workspace_id=$1",
            f["ws"],
        )
        assert await conn.fetchval(
            "SELECT account_limit IS NULL FROM control.tenant_account_quota WHERE workspace_id=$1",
            f["ws"],
        )
        assert await conn.fetchval(
            "SELECT max_active_accounts=7 AND active_accounts=1 FROM security.isolated_demo_account_quota WHERE workspace_id=$1",
            f["demo"],
        )
        assert await conn.fetchval(
            "SELECT status='inactive' FROM platform.user_ref WHERE id=$1", f["disabled"]
        )
        for function in (
            "security.work_report_feishu_status(uuid)",
            "security.publish_work_report_feishu(uuid,integer,uuid,text)",
            "security.work_report_source_access(jsonb,jsonb)",
            "security.complete_customer_creation_request(uuid,text,text,uuid)",
        ):
            assert await conn.fetchval(
                "SELECT to_regprocedure($1) IS NOT NULL", function
            ), function
        await conn.execute("SET row_security=on")
        await verify_feishu_startup(conn, role, results)
        assert not await conn.fetchval(
            "SELECT has_function_privilege('salegent_feishu_worker','ops.feishu_source_internal_121(uuid,text,uuid)','EXECUTE')"
        )
        assert await conn.fetchval(
            "SELECT has_function_privilege('salegent_feishu_worker','ops.feishu_source(uuid,text,uuid)','EXECUTE')"
        )
        await conn.execute(f'SET ROLE "{role}"')
        await context(conn, f["ws"], f["sales"], "sales", f["team"])
        assert await conn.fetchval(
            "SELECT security.authorization_has('weekly_report.read')"
        )
        assert await conn.fetchval(
            "SELECT security.authorization_has('work_report.read')"
        )
        assert await conn.fetchval(
            "SELECT security.authorization_has('customer.create_request')"
        )
        assert not await conn.fetchval(
            "SELECT security.authorization_has('customer.create_review')"
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege(current_user,'platform.password_credential','SELECT')"
        )
        assert not await conn.fetchval(
            "SELECT has_function_privilege(current_user,'ops.feishu_source(uuid,text,uuid)','EXECUTE')"
        )
        assert not await conn.fetchval(
            "SELECT has_function_privilege(current_user,'security.reconcile_runtime_grants()','EXECUTE')"
        )
        # The existing business worker shares the restricted runtime login.
        # Its maintenance and queue claims must retain their precise grants.
        for function in (
            "ops.evaluate_ai_usage_alerts()",
            "ops.prune_system_events()",
            "ops.enqueue_daily_sales_competency_reviews()",
            "ops.run_priority_reminder_monitor()",
            "ops.claim_job(text,integer,text[],boolean)",
        ):
            assert await conn.fetchval(
                "SELECT has_function_privilege(current_user,$1,'EXECUTE')", function
            ), function
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM platform.user_ref WHERE workspace_id=$1",
                f["demo"],
            )
            == 0
        )
        await conn.execute("RESET ROLE")
        results.append(
            f"V{161 if pre_s2 else 159}_runtime_nobypassrls_scoped_permissions_private_credentials_and_worker_denied"
        )
        await conn.execute("SET ROLE salegent_feishu_worker")
        for kind, key in (("customer", "trial"), ("opportunity", "trial_opp")):
            projection = json.loads(
                await conn.fetchval(
                    "SELECT ops.feishu_source($1,$2,$3)", f["connection"], kind, f[key]
                )
            )
            assert projection.get("excluded") is True
        assert (
            json.loads(
                await conn.fetchval(
                    "SELECT ops.feishu_source($1,'weekly_report',$2)",
                    f["connection"],
                    f["weekly"],
                )
            ).get("deleted")
            is True
        )
        await conn.execute("RESET ROLE")
        results.append(
            "synthetic_customer_and_opportunity_excluded_unpublished_legacy_weekly_not_exported"
        )
        if pre_s2:
            row = await conn.fetchrow(
                "SELECT * FROM crm.customer_creation_request WHERE id=$1", f["request"]
            )
            assert (
                row["status"] == "pending"
                and row["version_no"] == 1
                and row["reviewed_payload"] is None
            )
            viewrow = await conn.fetchrow(
                "SELECT * FROM crm.opportunity_saved_view WHERE id=$1", f["view"]
            )
            assert viewrow["visibility"] == "private" and viewrow["version_no"] == 1
            await context(conn, f["ws"], f["admin"], "administrator")
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute(
                'UPDATE crm.customer_creation_request SET reviewed_payload=payload||\'{"name":"Reviewed original request"}\'::jsonb WHERE id=$1',
                f["request"],
            )
            await conn.fetchval(
                "SELECT security.complete_customer_creation_request($1,'rejected','Fixture review declined',NULL)",
                f["request"],
            )
            await conn.execute("RESET ROLE")
            assert await conn.fetchval(
                "SELECT status='rejected' AND version_no=2 FROM crm.customer_creation_request WHERE id=$1",
                f["request"],
            )
            results.append(
                "existing_S2_pending_request_and_S3_private_view_retained_reviewer_column_update_works"
            )
        from verify_shenma_runtime_repositories import (
            actual_repositories,
            reviewed_policy,
        )

        await reviewed_policy(conn, role)
        results.append(
            "exact_e40_API_and_worker_policy_complete_except_strict_customer_request_mutations"
        )
        await actual_repositories(conn, role, f, context)
        results.append(
            "actual_restricted_runtime_portfolio_report_scheme_snapshot_documents_and_customer_approval_repositories"
        )
        await report_checks(conn, role, f, results)
        await quotas_and_capture(conn, f, results)
        print(
            json.dumps(
                {
                    "case": 159 if not pre_s2 else 161,
                    "checks": len(results),
                    "migrations_applied": sum(
                        x["status"] == "applied" for x in upgraded
                    ),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        if conn:
            await conn.execute("RESET ROLE")
            await conn.close()
        assert name.startswith("shenma_verify_whole_")
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')


async def fresh_case(admin, cfg, results):
    suffix = uuid.uuid4().hex[:12]
    name = "shenma_verify_whole_" + suffix
    role = "shenma_verify_runtime_" + suffix
    conn = None
    await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
    try:
        conn = await asyncpg.connect(database=name, **cfg)
        # Install the versioned V125 base role before incremental ACL migrations.
        # Upstream V246 deliberately stops replaying historical reconciler chains.
        await migrate(conn, ROOT, through_version=125)
        await runtime(conn, role)
        await migrate(conn, ROOT)
        from verify_shenma_runtime_repositories import reviewed_policy

        await reviewed_policy(conn, role)
        try:
            await migrate(conn, ROOT, through_version=125)
        except MigrationError:
            pass
        else:
            raise AssertionError("Fresh boundary accepted a downgrade")
        sys.path.insert(0, str(ROOT.parent / "backend/src"))
        spec = importlib.util.spec_from_file_location(
            "shenma_bootstrap_test", ROOT.parent / "deployment/bootstrap-customer.py"
        )
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        with tempfile.TemporaryDirectory(prefix="shenma-whole-bootstrap-") as folder:
            receipt = Path(folder) / "initial-admin.json"
            await bootstrap.bootstrap(conn, receipt)
            assert receipt.stat().st_mode & 0o777 == 0o600
            from sales_backend.auth.passwords import verify_password

            secret = json.loads(receipt.read_text())
            row = await conn.fetchrow(
                "SELECT p.password_hash,p.must_change_password,u.id,u.workspace_id FROM platform.password_credential p JOIN platform.user_ref u ON u.id=p.user_ref_id"
            )
            assert (
                verify_password(secret["password"], row["password_hash"])
                and row["must_change_password"]
            )
            try:
                await bootstrap.bootstrap(conn, receipt)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Bootstrap overwrote existing company")
        for table in ("crm.customer", "crm.opportunity", "activity.visit"):
            assert await conn.fetchval(f"SELECT count(*) FROM {table}") == 0
        await conn.execute("SET row_security=on")
        await conn.execute(f'SET ROLE "{role}"')
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM security.password_login_identifier('shenzhoukuntai','CUSTOMERADMIN')"
            )
            == 1
        )
        await context(conn, row["workspace_id"], row["id"], "administrator")
        assert await conn.fetchval(
            "SELECT security.authorization_has('access.console')"
        )
        assert await conn.fetchval(
            "SELECT security.authorization_has('customer.create_review')"
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege(current_user,'platform.password_credential','SELECT')"
        )
        results.append(
            "fresh_V399_exact_runtime_grants_bootstrap_secure_password_login_empty_business_and_overwrite_denial"
        )
    finally:
        if conn:
            await conn.execute("RESET ROLE")
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--ci", action="store_true")
    parser.add_argument(
        "--port", default=int(os.environ.get("PGPORT", "5432")), type=int
    )
    args = parser.parse_args()
    if args.ci:
        if os.environ.get("CI") != "true" or os.environ.get("PGHOST") != "127.0.0.1":
            parser.error(
                "--ci only accepts explicit disposable GitHub CI loopback PostgreSQL"
            )
        cfg = {
            "host": "127.0.0.1",
            "port": args.port,
            "user": os.environ.get("PGUSER", "postgres"),
        }
    else:
        if (
            not args.socket
            or not args.socket.is_absolute()
            or not (args.socket / f".s.PGSQL.{args.port}").exists()
        ):
            parser.error("an existing absolute local PostgreSQL socket is required")
        cfg = {
            "host": str(args.socket),
            "port": args.port,
            "user": os.environ.get("PGUSER", getpass.getuser()),
        }
    admin = await asyncpg.connect(database="postgres", **cfg)
    results = []
    try:
        for pre_s2 in (False, True):
            await run_case(admin, cfg, pre_s2, results)
        await fresh_case(admin, cfg, results)
        print(
            json.dumps(
                {"passed": len(results), "checks": results},
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
