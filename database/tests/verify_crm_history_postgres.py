"""V120 -> V122 historical CRM contract in a disposable PostgreSQL database.

Only a randomized local database and NOLOGIN/NOBYPASSRLS test role are created.
No DATABASE_URL, production database, secrets, backups or customer data are read.
"""
import asyncio
from datetime import date, timezone
import json
import os
from pathlib import Path
import sys
import uuid

import asyncpg

from verify_fde_postgres import context
from verify_runtime_grants_postgres import migrate_through

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from migrate import migrate


async def rejected(connection, sql, *args, errors=(asyncpg.CheckViolationError, asyncpg.InsufficientPrivilegeError)):
    try:
        async with connection.transaction():
            await connection.execute(sql, *args)
    except errors:
        return
    raise AssertionError(f"unexpectedly accepted: {sql}")


async def main():
    config = {"host": os.environ.get("PGHOST", "/tmp"), "port": int(os.environ.get("PGPORT", "5432")),
              "user": os.environ.get("PGUSER", "postgres")}
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid.uuid4().hex[:16]
    database, runtime = "salegent_history_verify_" + suffix, "salegent_history_runtime_" + suffix
    connection = None
    checks = []
    try:
        assert await admin.fetchval("SHOW server_encoding") == "UTF8", "isolated PostgreSQL must use UTF8 for real Chinese CRM JSON"
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        connection = await asyncpg.connect(database=database, **config)
        assert json.loads(await connection.fetchval("SELECT $1::jsonb", json.dumps({"企业": "历史跟进"}))) == {"企业": "历史跟进"}
        await migrate_through(connection, 120)
        ws, other_ws, operator, sales_a, sales_b, outsider, customer_a, customer_b, foreign_customer, opp_a, opp_b, old_visit, sync = [uuid.uuid4() for _ in range(13)]
        await connection.executemany("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)",
                                     [(ws, str(ws)), (other_ws, str(other_ws))])
        for person, workspace, code, role in [(operator, ws, "OPS", "administrator"), (sales_a, ws, "SA", "sales"),
                                              (sales_b, ws, "SB", "sales"), (outsider, other_ws, "OTHER", "administrator")]:
            await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,$3)", person, workspace, code)
            await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,$4)",
                                     workspace, person, role, "workspace" if role == "administrator" else "self")
        await context(connection, ws, operator, "administrator")
        for customer, workspace, owner, name in [(customer_a, ws, sales_a, "A"), (customer_b, ws, sales_b, "B"), (foreign_customer, other_ws, outsider, "foreign")]:
            await connection.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,data_kind) VALUES($1,$2,$3,$3,$4,$4,'production')", customer, workspace, name, owner)
        for opportunity, customer, owner, name in [(opp_a, customer_a, sales_a, "A opportunity"), (opp_b, customer_b, sales_b, "B opportunity")]:
            await connection.execute("INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id) VALUES($1,$2,$3,$4,$5)", opportunity, ws, customer, name, owner)
        await connection.execute("UPDATE crm.opportunity SET import_meta='{\"import_type\":\"crm_history\"}' WHERE id=$1", opp_b)
        form = await connection.fetchval("SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1")
        await connection.execute("INSERT INTO activity.visit(id,workspace_id,customer_id,opportunity_id,recorder_user_ref_id,created_by_user_ref_id,form_version_id,status,interaction_at,archived_at,follow_up_record) VALUES($1,$2,$3,$4,$5,$5,$6,'archived','2026-03-22+08','2026-03-22+08','immutable original')", old_visit, ws, customer_a, opp_a, sales_a, form)
        settings = json.dumps({"workspace_id": str(ws), "connection_id": str(sync), "notification": {"enabled": True},
                               "mappings": {"period_actual_snapshot": {"enabled": True}}})
        await connection.execute("INSERT INTO config.feishu_connection(id,workspace_id,revision,enabled,validated_revision,settings,updated_by) VALUES($1,$2,1,true,1,$3::jsonb,$4)", sync, ws, settings, operator)
        old_snapshot = await connection.fetchval("SELECT to_jsonb(v)::text FROM activity.visit v WHERE id=$1", old_visit)
        event_count = await connection.fetchval("SELECT count(*) FROM ops.feishu_event")
        await migrate(connection)
        after = json.loads(await connection.fetchval("SELECT to_jsonb(v)::text FROM activity.visit v WHERE id=$1", old_visit))
        before = json.loads(old_snapshot)
        assert all(after[key] == value for key, value in before.items())
        assert await connection.fetchval("SELECT count(*) FROM ops.feishu_event") == event_count
        assert await connection.fetchval("SELECT count(*) FROM activity.visit_opportunity WHERE visit_id=$1 AND opportunity_id=$2", old_visit, opp_a) == 1
        checks.append("upgrade_preserves_every_existing_visit_field_and_emits_no_mass_sync")

        await connection.execute(f'CREATE ROLE "{runtime}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        for schema in ("platform", "crm", "activity", "ops", "common", "security", "config", "workflow"):
            await connection.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{runtime}"')
            await connection.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO "{runtime}"')
            await connection.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{runtime}"')
        await connection.execute(f'REVOKE EXECUTE ON FUNCTION security.crm_history_visit_form_valid(uuid) FROM "{runtime}"')
        await connection.execute('SELECT security.reconcile_runtime_grants()')
        assert await connection.fetchval("SELECT has_function_privilege($1,'security.crm_history_visit_form_valid(uuid)','EXECUTE')", runtime)
        await connection.execute("SET row_security=on")
        await connection.execute(f'SET ROLE "{runtime}"')
        await context(connection, ws, operator, "administrator")
        assert await connection.fetchval("SELECT security.crm_history_visit_form_valid($1)", form)
        partner = await connection.fetchval("INSERT INTO crm.partner(workspace_id,name,principal_name,channel_manager_user_ref_id) VALUES($1,'Partner','External Person',$2) RETURNING id", ws, sales_a)
        await connection.execute("SELECT set_config('app.feishu_historical_import','on',false)")
        historical_visit = await connection.fetchval("INSERT INTO activity.visit(workspace_id,partner_id,original_recorder_name,manager_user_ref_id,form_version_id,status,interaction_at,archived_at,follow_up_record,import_meta,created_by_user_ref_id) VALUES($1,$2,'Former colleague',$3,$4,'archived','2025-12-12+08',clock_timestamp(),'original historical text','{\"import_type\":\"crm_history\"}',$3) RETURNING id", ws, partner, operator, form)
        row = await connection.fetchrow("SELECT customer_id,recorder_user_ref_id,original_recorder_name,interaction_at FROM activity.visit WHERE id=$1", historical_visit)
        assert row["customer_id"] is None and row["recorder_user_ref_id"] is None
        assert row["original_recorder_name"] == "Former colleague"
        assert row["interaction_at"].astimezone(timezone.utc).date() == date(2025, 12, 11)
        assert await connection.fetchval("SELECT recording_role_code_snapshot IS NULL AND recording_team_id_snapshot IS NULL FROM activity.visit WHERE id=$1", historical_visit)
        assert await connection.fetchval("SELECT historical FROM ops.feishu_event WHERE object_id=$1 ORDER BY sequence_no DESC LIMIT 1", historical_visit)
        await connection.execute("UPDATE activity.visit SET manager_user_ref_id=$2 WHERE id=$1", historical_visit, sales_a)
        await rejected(connection, "UPDATE activity.visit SET original_recorder_name='New author' WHERE id=$1", historical_visit)
        await rejected(connection, "UPDATE activity.visit SET recorder_user_ref_id=$2 WHERE id=$1", historical_visit, sales_a)
        await rejected(connection, "INSERT INTO activity.visit(workspace_id,original_recorder_name,form_version_id,import_meta) VALUES($1,'Former colleague',$2,'{\"import_type\":\"crm_history\"}')", ws, form)
        await rejected(connection, "INSERT INTO activity.visit(workspace_id,customer_id,form_version_id,original_recorder_name) VALUES($1,$2,$3,'fake author')", ws, customer_a, form)
        checks.append("partner_history_has_no_fake_customer_or_recorder_and_handover_preserves_author")

        history_opp = await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,original_owner_name,owner_user_ref_id,ownership_resolution,expected_close_year,expected_close_quarter,import_meta) VALUES($1,$2,'Inherited opportunity','Former sales',$3,'provisional',2026,4,'{\"import_type\":\"crm_history\"}') RETURNING id", ws, customer_a, sales_a)
        await rejected(connection, "UPDATE crm.opportunity SET original_owner_name='Current sales' WHERE id=$1", history_opp)
        assert await connection.fetchval("SELECT expected_close_date IS NULL FROM crm.opportunity WHERE id=$1", history_opp)
        await rejected(connection, "UPDATE crm.opportunity SET expected_close_date='2026-03-31' WHERE id=$1", history_opp)
        unknown_opp = await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,ownership_resolution,import_meta) VALUES($1,$2,'Unknown owner','unassigned','{\"import_type\":\"crm_history\"}') RETURNING id", ws, customer_a)
        await rejected(connection, "UPDATE crm.opportunity SET original_owner_name='Inferred from customer' WHERE id=$1", unknown_opp)
        await rejected(connection, "UPDATE crm.opportunity SET ownership_resolution='confirmed' WHERE id=$1", unknown_opp)
        checks.append("original_sales_empty_stays_empty_and_quarter_does_not_invent_close_date")

        await connection.execute("SELECT set_config('app.feishu_historical_import','off',false)")
        await rejected(connection, "UPDATE crm.opportunity SET original_owner_name='Legacy source owner' WHERE id=$1", opp_b)
        await connection.execute("SELECT set_config('app.feishu_historical_import','on',false)")
        await connection.execute("UPDATE crm.opportunity SET original_owner_name='Legacy source owner' WHERE id=$1", opp_b)
        assert await connection.fetchval("SELECT original_owner_captured FROM crm.opportunity WHERE id=$1", opp_b)
        await rejected(connection, "UPDATE crm.opportunity SET original_owner_captured=false WHERE id=$1", opp_b)
        await rejected(connection, "UPDATE crm.opportunity SET original_owner_name='Second source owner' WHERE id=$1", opp_b)
        await connection.execute("UPDATE activity.visit SET original_recorder_name='SA' WHERE id=$1", old_visit)
        await rejected(connection, "UPDATE activity.visit SET original_recorder_name='SB' WHERE id=$1", old_visit)
        checks.append("legacy_original_identity_can_be_captured_once_only_in_controlled_management_import")

        batch = await connection.fetchval("INSERT INTO ops.crm_import_batch(workspace_id,source_system,source_base_id,manifest_sha256,status,source_snapshot_at,created_by_user_ref_id) VALUES($1,'feishu','base',repeat('a',64),'approved',clock_timestamp(),$2) RETURNING id", ws, operator)
        record = await connection.fetchval("INSERT INTO ops.crm_import_record(workspace_id,batch_id,source_table_id,source_record_id,object_kind,source_sha256,raw_fields,status,expected_target_version) VALUES($1,$2,'table','record','opportunity',repeat('b',64),'{\"Q2真实确收\":0}','approved',1) RETURNING id", ws, batch)
        await rejected(connection, "UPDATE ops.crm_import_record SET raw_fields='{}' WHERE id=$1", record)
        await connection.execute("UPDATE ops.crm_import_record SET decision='{\"operator\":\"reviewed\"}' WHERE id=$1", record)
        await rejected(connection, "UPDATE ops.crm_import_batch SET source_base_id='changed' WHERE id=$1", batch)
        period = await connection.fetchval("INSERT INTO crm.opportunity_period_actual_snapshot(workspace_id,opportunity_id,year,quarter,kind,source_field,raw_amount,source_unit,tax_basis,source_record_id,import_batch_id) VALUES($1,$2,2026,2,'recognized','Q2真实确收',0,'wan_cny','unknown',$3,$4) RETURNING id", ws, opp_a, record, batch)
        forecast = await connection.fetchval("INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,recognized_amount,collection_amount,collection_confidence) VALUES($1,$2,2026,3,NULL,0,'low') RETURNING id", ws, opp_a)
        await rejected(connection, "UPDATE crm.opportunity_forecast SET collection_confidence='medium' WHERE id=$1", forecast)
        for key in ("forecast:2026:3", "forecast:2026:4"):
            await connection.execute("INSERT INTO ops.crm_import_record(workspace_id,batch_id,source_table_id,source_record_id,source_item_key,object_kind,source_sha256,raw_fields) VALUES($1,$2,'table','record',$3,'forecast',repeat('f',64),'{}')", ws, batch, key)
        assert await connection.fetchval("SELECT count(*) FROM crm.customer_actual WHERE workspace_id=$1", ws) == 0
        assert await connection.fetchval("SELECT raw_amount=0 AND tax_basis='unknown' FROM crm.opportunity_period_actual_snapshot WHERE id=$1", period)
        await rejected(connection, "UPDATE crm.opportunity_period_actual_snapshot SET raw_amount=100 WHERE id=$1", period, errors=(asyncpg.InsufficientPrivilegeError,))
        checks.append("quarterly_zero_and_unknown_tax_retained_without_inventing_actual_transaction")

        # Import preflight locks and compares reviewed target versions before changes.
        decision = json.loads(await connection.fetchval("SELECT security.check_crm_import_target($1,$2)", record, opp_a))
        assert decision["action"] == "update"
        await connection.execute("INSERT INTO ops.crm_import_binding(workspace_id,source_system,source_base_id,source_table_id,source_record_id,object_kind,target_id,target_version,last_source_sha256,last_imported_snapshot,applied_batch_id) VALUES($1,'feishu','base','table','record','opportunity',$2,1,repeat('b',64),'{}',$3)", ws, opp_a, batch)
        assert json.loads(await connection.fetchval("SELECT security.check_crm_import_target($1,$2)", record, opp_a))["action"] == "unchanged"
        await rejected(connection, "SELECT security.check_crm_import_target($1,$2)", record, opp_b)
        batch2 = await connection.fetchval("INSERT INTO ops.crm_import_batch(workspace_id,source_system,source_base_id,manifest_sha256,status,source_snapshot_at) VALUES($1,'feishu','base',repeat('c',64),'approved',clock_timestamp()) RETURNING id", ws)
        record2 = await connection.fetchval("INSERT INTO ops.crm_import_record(workspace_id,batch_id,source_table_id,source_record_id,object_kind,source_sha256,raw_fields,status) VALUES($1,$2,'table','record','opportunity',repeat('d',64),'{\"changed\":true}','approved') RETURNING id", ws, batch2)
        await connection.execute("UPDATE crm.opportunity SET amount=12345 WHERE id=$1", opp_a)
        await rejected(connection, "SELECT security.check_crm_import_target($1,$2)", record2, opp_a, errors=(asyncpg.SerializationError,))
        await context(connection, ws, sales_a, "sales")
        assert not await connection.fetchval("SELECT security.crm_history_visit_form_valid($1)", form)
        assert await connection.fetchval("SELECT count(*) FROM ops.crm_import_record") == 0
        await rejected(connection, "SELECT security.check_crm_import_target($1,$2)", record, opp_a, errors=(asyncpg.InsufficientPrivilegeError,))
        await rejected(connection, "INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,form_version_id,created_by_user_ref_id,import_meta) VALUES($1,$2,$3,$4,$3,'{\"import_type\":\"crm_history\"}')", ws, customer_a, sales_a, form, errors=(asyncpg.InsufficientPrivilegeError,))
        checks.append("stable_binding_skips_repeat_source_and_blocks_overwriting_later_system_edit")

        await context(connection, ws, operator, "administrator")
        multi = await connection.fetchval("INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,created_by_user_ref_id,form_version_id,status,interaction_at,archived_at,follow_up_record,manager_user_ref_id) VALUES($1,$2,$3,$3,$4,'archived','2026-06-01+08',clock_timestamp(),'contains both opportunities',$5) RETURNING id", ws, customer_a, operator, form, sales_a)
        async with connection.transaction():
            await connection.execute("UPDATE activity.visit SET customer_id=NULL WHERE id=$1", multi)
            await connection.executemany("INSERT INTO activity.visit_opportunity(visit_id,workspace_id,opportunity_id) VALUES($1,$2,$3)", [(multi, ws, opp_a), (multi, ws, opp_b)])
        assert await connection.fetchval("SELECT opportunity_id IS NULL FROM activity.visit WHERE id=$1", multi)
        assert await connection.fetchval("SELECT version_no FROM activity.visit WHERE id=$1", multi) == 4
        await context(connection, ws, sales_a, "sales")
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", multi) == 0
        assert await connection.fetchval("SELECT count(*) FROM activity.visit_opportunity WHERE visit_id=$1", multi) == 0
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", historical_visit) == 1
        await context(connection, ws, sales_b, "sales")
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", multi) == 0
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", historical_visit) == 0
        await context(connection, ws, operator, "administrator")
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", multi) == 1
        assert await connection.fetchval("SELECT count(*) FROM activity.visit_opportunity WHERE visit_id=$1", multi) == 2
        await rejected(connection, "INSERT INTO activity.visit_opportunity(visit_id,workspace_id,opportunity_id) VALUES($1,$2,$3)", old_visit, ws, opp_b)
        checks.append("multi_opportunity_narrative_requires_all_read_permissions_even_for_manager_field")

        # The legacy single relation takes the old permission path, but never
        # caches ownership: moving the opportunity revokes the former author.
        await context(connection, ws, sales_a, "sales")
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", old_visit) == 1
        await context(connection, ws, operator, "administrator")
        await connection.execute("UPDATE crm.opportunity SET owner_user_ref_id=$2 WHERE id=$1", opp_a, sales_b)
        await context(connection, ws, sales_a, "sales")
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", old_visit) == 0
        assert await connection.fetchval("SELECT count(*) FROM activity.visit_opportunity WHERE visit_id=$1", old_visit) == 0
        await context(connection, ws, operator, "administrator")
        await connection.execute("UPDATE crm.opportunity SET owner_user_ref_id=$2 WHERE id=$1", opp_a, sales_a)
        await context(connection, ws, sales_a, "sales")
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", old_visit) == 1
        await context(connection, ws, operator, "administrator")
        checks.append("single_opportunity_fast_path_revokes_original_author_after_owner_transfer")

        await connection.execute("RESET ROLE")
        await rejected(connection, "INSERT INTO activity.visit_opportunity(visit_id,workspace_id,opportunity_id) VALUES($1,$2,$3)", old_visit, other_ws, opp_b, errors=(asyncpg.ForeignKeyViolationError,))
        await rejected(connection, "INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,form_version_id) VALUES($1,$2,$3,$4)", ws, foreign_customer, sales_a, form, errors=(asyncpg.ForeignKeyViolationError,))
        foreign_opp = await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id) VALUES($1,$2,'foreign opportunity',$3) RETURNING id", other_ws, foreign_customer, outsider)
        fresh = await connection.fetchval("INSERT INTO ops.crm_import_record(workspace_id,batch_id,source_table_id,source_record_id,object_kind,source_sha256,raw_fields,status) VALUES($1,$2,'table','fresh','opportunity',repeat('e',64),'{}','approved') RETURNING id", ws, batch2)
        await connection.execute(f'SET ROLE "{runtime}"')
        await rejected(connection, "SELECT security.check_crm_import_target($1,$2)", fresh, foreign_opp, errors=(asyncpg.InsufficientPrivilegeError,))
        await context(connection, other_ws, outsider, "administrator")
        assert await connection.fetchval("SELECT count(*) FROM ops.crm_import_record WHERE id=$1", record) == 0
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", multi) == 0
        assert await connection.fetchval("SELECT count(*) FROM crm.opportunity_period_actual_snapshot WHERE id=$1", period) == 0
        await context(connection, ws, operator, "administrator")
        checks.append("native_rls_and_composite_foreign_keys_reject_cross_workspace_sources_and_targets")

        await connection.execute("RESET ROLE")
        await connection.execute("SELECT set_config('app.feishu_historical_import','off',false)")
        normal_visit = await connection.fetchval("INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,created_by_user_ref_id,form_version_id,status,interaction_at,archived_at,follow_up_record) VALUES($1,$2,$3,$4,$4,$5,'archived',clock_timestamp(),clock_timestamp(),'normal new event') RETURNING id", ws, customer_a, opp_a, sales_a, form)
        events = await connection.fetch("SELECT first_formal_create,historical FROM ops.feishu_event WHERE object_kind='visit' AND object_id=$1", normal_visit)
        assert len(events) == 1 and events[0]["first_formal_create"] and not events[0]["historical"]
        assert await connection.fetchval("SELECT version_no FROM activity.visit WHERE id=$1", normal_visit) == 1
        checks.append("legacy_single_visit_create_emits_exactly_one_first_formal_event_without_version_drift")
        await connection.execute("SET ROLE salegent_feishu_worker")
        exposed = await connection.fetch("""SELECT p.oid::regprocedure::text AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND has_schema_privilege(current_user,n.oid,'USAGE') AND has_function_privilege(current_user,p.oid,'EXECUTE')
          AND p.oid NOT IN ('ops.feishu_source(uuid,text,uuid)'::regprocedure,'ops.feishu_reconcile(uuid)'::regprocedure,
                           'security.has_active_role(text)'::regprocedure)""")
        assert not exposed, [row["name"] for row in exposed]
        await connection.execute("RESET ROLE")
        checks.append("feishu_worker_has_no_new_business_definer_entrypoint_or_private_source_helper")
        await connection.execute("INSERT INTO crm.opportunity_related_partner(workspace_id,opportunity_id,partner_id) VALUES($1,$2,$3)", ws, opp_a, partner)
        await rejected(connection, "INSERT INTO crm.opportunity_related_partner(workspace_id,opportunity_id,partner_id) VALUES($1,$2,$3)", other_ws, opp_b, partner, errors=(asyncpg.ForeignKeyViolationError,))
        associated = json.loads(await connection.fetchval("SELECT ops.feishu_source($1,'opportunity',$2)", sync, opp_a))
        assert associated["associated_partner_ids"] == [str(partner)]
        assert associated["associated_partner_names"] == ["Partner"]
        assert associated["partner_id"] is None and associated["sales_channel"] == "unknown"
        await rejected(connection, "INSERT INTO crm.opportunity(workspace_id,customer_id,name,sales_channel) VALUES($1,$2,'invalid reseller','partner')", ws, customer_a)
        checks.append("ordinary_partner_relations_preserve_reseller_semantics_tenant_scope_and_v4_projection")
        projection = json.loads(await connection.fetchval("SELECT ops.feishu_source($1,'visit',$2)", sync, multi))
        assert set(projection["opportunity_ids"]) == {str(opp_a), str(opp_b)}
        assert set(projection["customer_ids"]) == {str(customer_a), str(customer_b)}
        assert set(projection["customer_names"]) == {"A", "B"}
        projection = json.loads(await connection.fetchval("SELECT ops.feishu_source($1,'period_actual_snapshot',$2)", sync, period))
        assert projection["raw_amount"] == 0 and projection["tax_basis"] == "unknown"
        assert projection["source_external_record_id"] == "record"
        forecast_projection = json.loads(await connection.fetchval("SELECT ops.feishu_source($1,'forecast',$2)", sync, forecast))
        assert forecast_projection["recognized_amount"] is None and forecast_projection["collection_amount"] == 0
        assert forecast_projection["collection_confidence"] == "low"
        assert await connection.fetchval("SELECT bool_and(historical AND NOT first_formal_create AND queue_origin='reconcile') FROM ops.feishu_event WHERE object_kind='period_actual_snapshot'")
        assert await connection.fetchval("SELECT count(*) FROM ops.job") == 0
        assert await connection.fetchval("SELECT count(*) FROM workflow.notification") == 0
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE recorder_user_ref_id=$1 AND original_recorder_name='Former colleague'", sales_a) == 0
        checks.append("complete_relations_and_quarter_source_project_without_jobs_or_historical_notifications")
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE interaction_at >= '2026-03-22 00:00:00+08' AND interaction_at < '2026-09-23 00:00:00+08' AND id=ANY($1::uuid[])", [old_visit, historical_visit, multi]) == 2
        checks.append("historical_activity_uses_original_calendar_date_not_archive_or_import_time")
        assert all(step["status"] == "unchanged" for step in await migrate(connection))
        checks.append("repeat_deployment_remains_noop_after_real_business_fixture")
        print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False))
    finally:
        if connection:
            await connection.close()
        assert database.startswith("salegent_history_verify_")
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{runtime}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
