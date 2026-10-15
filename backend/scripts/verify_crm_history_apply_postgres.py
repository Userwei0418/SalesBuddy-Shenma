"""Synthetic stage/apply regression in a disposable UTF8 local PostgreSQL DB."""
# ruff: noqa: E402, E501

from __future__ import annotations

import asyncio
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend/src"), str(ROOT / "backend/tests"), str(ROOT / "database/scripts")]
from migrate import migrate
from test_crm_history_apply import entry, manifest

from sales_backend.services.crm_history_apply import CrmHistoryApply, ImportBlocked
from sales_backend.services.crm_history_preflight import canonical_hash


async def rejected(connection, statement, *args):
    try:
        async with connection.transaction():
            await connection.execute(statement, *args)
    except (asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError):
        return
    raise AssertionError("protected write was accepted")


def verification_host(host: str) -> str:
    """Allow local CI TCP or a local /tmp socket; databases stay random/disposable."""
    socket = (host == "/tmp" or host.startswith("/tmp/")) and ".." not in Path(host).parts  # noqa: S108
    if host not in {"127.0.0.1", "localhost", "::1"} and not socket:
        raise ValueError("verification requires explicit loopback or a local /tmp socket")
    return host


async def main():
    host = verification_host(os.environ.get("PGHOST", "/tmp"))  # noqa: S108 -- random disposable database only
    config = {"host": host, "port": int(os.environ.get("PGPORT", "5432")), "user": os.environ.get("PGUSER", "postgres")}
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid4().hex[:16]
    database, role = "history_apply_verify_" + suffix, "history_apply_role_" + suffix
    connection = None
    checks = []
    try:
        assert await admin.fetchval("SHOW server_encoding") == "UTF8"
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        connection = await asyncpg.connect(database=database, **config)
        await migrate(connection)
        for name in ("json", "jsonb"):
            await connection.set_type_codec(name, schema="pg_catalog", encoder=json.dumps, decoder=json.loads)
        workspace, operator, sales, other, foreign_workspace, fde_user = [uuid4() for _ in range(6)]
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic')",
            workspace,
            str(workspace),
        )
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Other tenant')",
            foreign_workspace,
            str(foreign_workspace),
        )
        await connection.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=ANY($1::uuid[]) AND module_code='history'",[workspace,foreign_workspace])
        for person, account, role_code in (
            (operator, "OPS", "operations"),
            (sales, "SA", "sales"),
            (other, "SB", "sales"),
            (fde_user, "FDE", "fde"),
        ):
            await connection.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)",
                person,
                workspace,
                account,
            )
            await connection.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,$4)",
                workspace,
                person,
                role_code,
                "workspace" if role_code == "operations" else "self",
            )
            if role_code in {"sales", "fde"}:
                team = await connection.fetchval(
                    "INSERT INTO platform.team(workspace_id,code,name) VALUES($1,$2,$2) RETURNING id",
                    workspace,
                    account,
                )
                await connection.execute(
                    "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role,is_primary) "
                    "VALUES($1,$2,$3,$4,true)",
                    workspace,
                    team,
                    person,
                    role_code,
                )
        form = await connection.fetchval(
            "SELECT v.id FROM config.form_version v JOIN config.form_definition d ON d.id=v.form_definition_id "
            "WHERE v.status='active' AND d.object_type='visit' AND d.workspace_id IS NULL "
            "AND clock_timestamp()>=v.effective_from AND clock_timestamp()<v.effective_to "
            "ORDER BY v.version_no DESC LIMIT 1"
        )
        invalid_forms = []
        for scope, object_type, start, end in (
            (foreign_workspace, "visit", "2000-01-01", "infinity"),
            (None, "customer", "2000-01-01", "infinity"),
            (None, "visit", "2000-01-01", "2001-01-01"),
            (None, "visit", "2999-01-01", "infinity"),
        ):
            definition = await connection.fetchval(
                "INSERT INTO config.form_definition(workspace_id,form_code,name,object_type) "
                "VALUES($1,$2,'invalid for this import',$3) RETURNING id",
                scope,
                str(uuid4()),
                object_type,
            )
            invalid_forms.append(
                await connection.fetchval(
                    "INSERT INTO config.form_version(form_definition_id,version_no,status,effective_from,effective_to) "
                    "VALUES($1,1,'active',$2::text::timestamptz,$3::text::timestamptz) RETURNING id",
                    definition,
                    start,
                    end,
                )
            )
        await connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        for schema in ("platform", "crm", "activity", "ops", "common", "security", "config", "workflow"):
            await connection.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
            await connection.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO "{role}"')
            await connection.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{role}"')
        # Production deliberately grants no UPDATE on the insert/delete bridge.
        # SELECT FOR UPDATE would need that privilege even for zero matching rows.
        await connection.execute(f'REVOKE UPDATE ON crm.opportunity_related_partner FROM "{role}"')
        await connection.execute(f'SET ROLE "{role}"')
        assert not await connection.fetchval(
            "SELECT has_table_privilege(current_user,'crm.opportunity_related_partner','UPDATE')"
        )
        service = CrmHistoryApply(connection, workspace_id=workspace, actor_user_id=operator, actor_role="operations")
        opp = {
            "name": "合成商机",
            "relations": {"customers": ["c"], "partners": []},
            "stage_code": "won",
            "status": "won",
            "probability": 100,
            "amount": None,
            "original_owner_name": "Former",
            "current_owner_account": "SA",
            "ownership_resolution": "provisional",
            "ownership_resolution_evidence": "approved synthetic transfer",
            "expected_close_year": 2026,
            "expected_close_quarter": 4,
            "sales_channel": "direct",
            "financials": [],
            "collection_confidence": {},
        }
        first_opp = deepcopy(opp)
        first_opp["relations"]["partners"] = ["p"]
        first_opp["financials"] = [
            {
                "record_kind": "forecast",
                "year": 2026,
                "quarter": 3,
                "kind": "collection",
                "source_field": "Q3",
                "raw_amount": "0",
                "amount_cny": "0",
                "source_unit": "wan_cny",
                "tax_basis": "unknown",
            },
            {
                "record_kind": "period_actual_snapshot",
                "year": 2026,
                "quarter": 2,
                "kind": "recognized",
                "source_field": "Q2",
                "raw_amount": "1.2",
                "amount_cny": "12000",
                "source_unit": "wan_cny",
                "tax_basis": "unknown",
            },
        ]
        first_opp["collection_confidence"] = {"3": "low"}
        opp["name"] = "第二商机"
        opp["relations"]["customers"] = ["d"]
        opp["sales_channel"] = "partner"
        visit = {
            "relations": {"customers": ["c", "d"], "partners": [], "opportunities": ["o", "o2"]},
            "original_recorder_name": "Former colleague",
            "original_recorder_account": None,
            "manager_account": "SA",
            "interaction_on": "2025-12-12",
            "recorded_on": None,
            "follow_up_record": "真实已知原文",
            "next_action": None,
        }
        partner_visit = deepcopy(visit)
        partner_visit["relations"] = {"partners": ["p"], "opportunities": [], "customers": []}
        doc = manifest(
            workspace,
            form,
            [
                entry(
                    "customers",
                    "c",
                    {"name": "合成客户", "relations": {}, "source_owner_names": ["SA"], "current_owner_account": "SA"},
                ),
                entry(
                    "partners",
                    "p",
                    {
                        "name": "合成伙伴",
                        "principal_name": "外部负责人",
                        "channel_manager_account": "SA",
                        "relations": {},
                    },
                ),
                entry("opportunities", "o", first_opp),
                entry("opportunities", "o2", opp),
                entry("visits", "v", visit),
                entry("visits", "vp", partner_visit),
                entry(
                    "visits",
                    "unknown",
                    {"relations": {}},
                    action="quarantine",
                    can_execute=False,
                    issues=["missing_subject"],
                ),
                entry("customers", "d", {"name": "未认领客户", "relations": {}, "source_owner_names": []}),
            ],
        )
        staged = await service.stage(doc, approved_sha256=canonical_hash(doc))
        assert staged["business_data_written"] is False
        for invalid_form in invalid_forms:
            invalid_doc = deepcopy(doc)
            invalid_doc["execution_contract"]["visit_form_version_id"] = str(invalid_form)
            try:
                await service.stage(invalid_doc, approved_sha256=canonical_hash(invalid_doc))
            except ImportBlocked as error:
                assert str(error) == "reviewed_visit_form_version_not_active"
            else:
                raise AssertionError("wrong tenant, object type or effective period form was accepted")
        checks.append("reviewed_form_requires_visit_type_correct_tenant_and_current_effective_period")
        async with connection.transaction():
            await service._context()
            assert await connection.fetchval("SELECT count(*) FROM crm.customer WHERE workspace_id=$1", workspace) == 0
            assert (
                await connection.fetchval("SELECT count(*) FROM ops.crm_import_record WHERE workspace_id=$1", workspace)
                == 10
            )
            customer_id = service._target(doc["plan"]["records"][0])
            ledger = await connection.fetchval(
                "SELECT id FROM ops.crm_import_record WHERE batch_id=$1::uuid AND object_kind='customer'",
                staged["batch_id"],
            )
            sql = "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,import_meta) VALUES($1,$2,'Not verified','not verified',$3,$4::jsonb)"
            await rejected(
                connection,
                sql,
                customer_id,
                workspace,
                operator,
                {"import_type": "crm_history", "import_record_id": str(uuid4())},
            )
            await connection.execute(
                "UPDATE ops.crm_import_batch SET status='reviewing' WHERE id=$1::uuid", staged["batch_id"]
            )
            await rejected(
                connection,
                sql,
                customer_id,
                workspace,
                operator,
                {"import_type": "crm_history", "import_record_id": str(ledger)},
            )
            await connection.execute(
                "UPDATE ops.crm_import_batch SET status='approved' WHERE id=$1::uuid", staged["batch_id"]
            )
            await rejected(
                connection,
                sql,
                customer_id,
                foreign_workspace,
                operator,
                {"import_type": "crm_history", "import_record_id": str(ledger)},
            )
            await connection.execute("SELECT set_config('app.feishu_historical_import','off',true)")
            await rejected(
                connection,
                sql,
                customer_id,
                workspace,
                operator,
                {"import_type": "crm_history", "import_record_id": str(ledger)},
            )
            await connection.execute(
                "SELECT set_config('app.feishu_historical_import','on',true),set_config('app.role_code','sales',true),set_config('app.user_ref_id',$1,true)",
                str(sales),
            )
            await rejected(
                connection,
                sql,
                customer_id,
                workspace,
                sales,
                {"import_type": "crm_history", "import_record_id": str(ledger)},
            )
        checks.append("stage_writes_evidence_only_and_customer_guard_requires_real_approved_ledger")
        result = await service.apply(doc, approved_sha256=canonical_hash(doc))
        assert result["counts"] == {"applied": 7, "quarantined": 1}, result
        assert result["business_data_written"] is True
        assert sum(item["count"] for item in result["object_counts"] if item["action"] == "inserted") == 9
        async with connection.transaction():
            await service._context()
            assert (
                await connection.fetchval("SELECT count(*) FROM crm.customer_actual WHERE workspace_id=$1", workspace)
                == 0
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM crm.customer WHERE workspace_id=$1 AND owner_user_ref_id IS NULL AND company_reference IS NULL AND company_verified_at IS NULL",
                    workspace,
                )
                == 1
            )
            assert (
                await connection.fetchval("SELECT state FROM crm.customer_ownership WHERE customer_id=$1", customer_id)
                == "claimed"
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM activity.visit WHERE workspace_id=$1 AND recorder_user_ref_id IS NULL AND manager_user_ref_id=$2",
                    workspace,
                    sales,
                )
                == 2
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM crm.customer_ownership_event WHERE workspace_id=$1 AND event_type='history_restored'",
                    workspace,
                )
                == 1
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM crm.customer_claim_request WHERE workspace_id=$1", workspace
                )
                == 0
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM crm.opportunity_related_partner WHERE workspace_id=$1", workspace
                )
                == 1
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM crm.opportunity WHERE workspace_id=$1 AND partner_id IS NULL AND sales_channel='partner'",
                    workspace,
                )
                == 1
            )
            await rejected(
                connection,
                "INSERT INTO crm.opportunity(workspace_id,customer_id,name,sales_channel) VALUES($1,$2,'invalid manual','partner')",
                workspace,
                customer_id,
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM activity.visit WHERE workspace_id=$1 AND opportunity_id IS NULL", workspace
                )
                == 2
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM activity.visit_opportunity WHERE workspace_id=$1", workspace
                )
                == 2
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM crm.opportunity WHERE workspace_id=$1 AND amount IS NULL AND expected_close_date IS NULL",
                    workspace,
                )
                == 2
            )
            assert await connection.fetchval(
                "SELECT raw_amount=1.2 AND tax_basis='unknown' FROM crm.opportunity_period_actual_snapshot WHERE workspace_id=$1",
                workspace,
            )
            assert await connection.fetchval(
                "SELECT collection_amount=0 AND recognized_amount IS NULL AND collection_confidence='low' FROM crm.opportunity_forecast WHERE workspace_id=$1",
                workspace,
            )
            assert await connection.fetchval("SELECT count(*) FROM ops.job WHERE workspace_id=$1", workspace) == 0
            assert (
                await connection.fetchval("SELECT count(*) FROM workflow.notification WHERE workspace_id=$1", workspace)
                == 0
            )
            assert (
                await connection.fetchval(
                    "SELECT bool_and(historical AND NOT first_formal_create) FROM ops.feishu_event WHERE workspace_id=$1",
                    workspace,
                )
                is not False
            )
        checks.append("apply_preserves_unknowns_original_author_multiple_relations_and_quarterly_evidence")
        checks.append("ordinary_association_does_not_invent_reseller_and_multi_customer_visit_has_no_fake_primary")
        checks.append("unique_active_source_owner_restores_claimed_with_provenance_without_fake_claim_approval")
        repeat = await service.apply(doc, approved_sha256=canonical_hash(doc))
        assert repeat["counts"] == {"unchanged": 7, "quarantined": 1}, repeat
        assert repeat["business_data_written"] is False
        assert all(item["action"] not in {"inserted", "updated"} for item in repeat["object_counts"])
        checks.append("same_approved_manifest_is_idempotent_for_core_and_financial_children")
        deferred_doc = deepcopy(doc)
        invalid_subject = deepcopy(visit)
        invalid_subject["relations"] = {"customers": ["c", "d"], "partners": [], "opportunities": []}
        deferred_doc["plan"]["records"].append(entry("visits", "invalid-subject", invalid_subject))
        deferred_result = await service.apply(deferred_doc, approved_sha256=canonical_hash(deferred_doc))
        assert deferred_result["business_data_written"] is False, deferred_result
        assert deferred_result["counts"] == {"unchanged": 7, "quarantined": 1, "conflict": 1}, deferred_result
        assert (
            len([item for item in deferred_result["records"] if item["source_key"].endswith(":invalid-subject")]) == 1
        )
        checks.append("failed_transactions_and_deferred_subject_constraints_never_count_as_business_writes")

        # A real pre-existing child baseline is distinct from the desired V3 values.
        legacy_doc = deepcopy(doc)
        second = legacy_doc["plan"]["records"][3]
        legacy_parent = service._target(second)
        async with connection.transaction():
            await service._context()
            legacy_forecast = await connection.fetchval(
                "INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,recognized_amount,collection_amount) VALUES($1,$2,2026,3,5,2) RETURNING id",
                workspace,
                legacy_parent,
            )
            baseline_snapshot = await connection.fetchval(
                "SELECT to_jsonb(f) FROM crm.opportunity_forecast f WHERE id=$1", legacy_forecast
            )
            parent_version = await connection.fetchval(
                "SELECT version_no FROM crm.opportunity WHERE id=$1", legacy_parent
            )
        second.update(action="noop", target_id=str(legacy_parent), expected_target_version=parent_version)
        second["normalized"]["financials"] = [
            {
                "record_kind": "forecast",
                "year": 2026,
                "quarter": 3,
                "kind": "recognized",
                "source_field": "Q3 recognized",
                "raw_amount": "0.0006",
                "amount_cny": "6",
                "source_unit": "wan_cny",
                "tax_basis": "inclusive",
            }
        ]
        second["related_objects"]["forecast_reconciliation"] = [
            {
                "year": 2026,
                "quarter": 3,
                "target_id": str(legacy_forecast),
                "current_snapshot": baseline_snapshot,
                "last_imported_snapshot": {"recognized_amount": 5, "collection_amount": 2},
            }
        ]
        legacy_result = await service.apply(legacy_doc, approved_sha256=canonical_hash(legacy_doc))
        assert not legacy_result["counts"].get("conflict"), legacy_result
        async with connection.transaction():
            await service._context()
            assert await connection.fetchval(
                "SELECT recognized_amount=6 AND collection_amount=2 FROM crm.opportunity_forecast WHERE id=$1",
                legacy_forecast,
            )
        assert any(
            row["object_kind"] == "forecast" and row["status"] == "applied" for row in legacy_result["ledger_counts"]
        )
        assert legacy_result["business_data_written"] is True
        assert {"object_kind": "forecast", "action": "updated", "count": 1} in legacy_result["object_counts"]
        checks.append("legacy_forecast_uses_real_baseline_and_preserves_unknown_source_collection")
        changed = deepcopy(legacy_doc)
        target_opp = service._target(changed["plan"]["records"][2])
        async with connection.transaction():
            await service._context()
            await connection.execute("UPDATE crm.opportunity SET amount=4321 WHERE id=$1", target_opp)
            version = await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1", target_opp)
        row = changed["plan"]["records"][2]
        row.update(action="update", target_id=str(target_opp), expected_target_version=version)
        row["normalized"]["current_owner_account"] = "SB"
        row["changes"] = {"current_owner_account": {"before": "SA", "after": "SB"}}
        inherited = deepcopy(visit)
        changed["plan"]["records"].append(entry("visits", "new-v", inherited))
        conflict = await service.apply(changed, approved_sha256=canonical_hash(changed))
        assert conflict["counts"].get("conflict") == 3, conflict
        async with connection.transaction():
            await service._context()
            assert await connection.fetchval(
                "SELECT amount=4321 AND owner_user_ref_id=$2 FROM crm.opportunity WHERE id=$1", target_opp, sales
            )
            assert (
                await connection.fetchval("SELECT count(*) FROM activity.visit WHERE workspace_id=$1", workspace) == 2
            )
        checks.append("later_system_edit_protected_and_old_binding_cannot_bypass_failed_parent_in_current_batch")
        try:
            await CrmHistoryApply(connection, workspace_id=workspace, actor_user_id=sales, actor_role="sales").stage(
                doc, approved_sha256=canonical_hash(doc)
            )
        except ImportBlocked as error:
            assert str(error) == "operations_or_administrator_required"
        else:
            raise AssertionError("sales imported history")
        try:
            await CrmHistoryApply(
                connection, workspace_id=uuid4(), actor_user_id=operator, actor_role="operations"
            ).stage(doc, approved_sha256=canonical_hash(doc))
        except ImportBlocked as error:
            assert str(error) == "manifest_workspace_mismatch"
        else:
            raise AssertionError("cross-workspace manifest accepted")
        checks.append("explicit_actor_and_workspace_cannot_be_replaced_by_manifest_or_history_flag")
        fde_doc = manifest(
            workspace,
            form,
            [
                entry(
                    "customers",
                    "fde-owned",
                    {
                        "name": "FDE不可恢复为销售",
                        "relations": {},
                        "source_owner_names": ["FDE"],
                        "current_owner_account": "FDE",
                    },
                )
            ],
        )
        fde_result = await service.apply(fde_doc, approved_sha256=canonical_hash(fde_doc))
        assert fde_result["counts"] == {"conflict": 1}
        assert fde_result["business_data_written"] is False
        assert fde_result["records"][0]["reason"] == "owner_requires_one_verified_primary_business_team"
        checks.append("fde_account_exists_but_is_not_a_commercial_owner")
        # An existing claim is preserved without requiring the source person's team.
        preserved_doc = deepcopy(legacy_doc)
        first_customer = preserved_doc["plan"]["records"][0]
        async with connection.transaction():
            await service._context()
            current_version = await connection.fetchval("SELECT version_no FROM crm.customer WHERE id=$1", customer_id)
        first_customer.update(action="noop", target_id=str(customer_id), expected_target_version=current_version)
        first_customer["normalized"].update(source_owner_names=["FDE"], current_owner_account="FDE")
        # The changed opportunity above remains conflicted; this customer itself must be accepted.
        preserved_result = await service.apply(preserved_doc, approved_sha256=canonical_hash(preserved_doc))
        customer_result = next(
            row for row in preserved_result["records"] if row["source_key"] == first_customer["source_key"]
        )
        assert customer_result["status"] == "applied", customer_result
        assert customer_result["business_action"] == "unchanged", customer_result
        assert preserved_result["business_data_written"] is False, preserved_result
        async with connection.transaction():
            await service._context()
            assert await connection.fetchval(
                "SELECT owner_user_ref_id=$2 FROM crm.customer_ownership WHERE customer_id=$1", customer_id, sales
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM crm.customer_ownership_event WHERE workspace_id=$1 AND event_type='history_restored'",
                    workspace,
                )
                == 1
            )
        checks.append("existing_claim_preserved_without_resolving_or_reassigning_source_owner")
        associated_doc = deepcopy(legacy_doc)
        associated_doc["plan"]["records"] = [
            row for row in associated_doc["plan"]["records"]
            if (row["kind"], row["source_record_id"]) in {("customers", "d"), ("partners", "p"), ("opportunities", "o2")}
        ]
        associated_parent = next(row for row in associated_doc["plan"]["records"] if row["kind"] == "opportunities")
        async with connection.transaction():
            await service._context()
            existing_version = await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1", legacy_parent)
        associated_parent.update(action="update", target_id=str(legacy_parent), expected_target_version=existing_version)
        associated_parent["normalized"]["relations"]["partners"] = ["p"]
        associated_parent["related_objects"]["associated_partner_source_ids"] = ["p"]
        associated_result = await service.apply(associated_doc, approved_sha256=canonical_hash(associated_doc))
        assert associated_result["counts"] == {"unchanged": 2, "applied": 1}, associated_result
        async with connection.transaction():
            await service._context()
            assert await connection.fetchval(
                "SELECT count(*) FROM crm.opportunity_related_partner WHERE opportunity_id=$1", legacy_parent
            ) == 1
        checks.append("production_bridge_without_update_grant_supports_zero_partner_create_partner_create_and_existing_association")
        print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False))
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
