#!/usr/bin/env python3
"""Maintenance-only synthetic customer/opportunity seed and conservative retirement.

Input JSON is kept outside Git. Nothing connects unless the CLI is invoked and
SYNTHETIC_TRIAL_DATABASE_URL is set. `plan` and `cleanup` are read-only; writes
require `apply` or `cleanup --apply --confirm-batch <exact batch>`. All mutations
run in one transaction. Cleanup soft-deletes only an untouched, complete seed;
any business edits, extra forecasts, descendants or associations block it.

Examples (UUIDs and local files must come from the reviewed environment):
  python scripts/synthetic_trial_data.py plan --dataset /secure/dataset.json \
    --workspace WORKSPACE_UUID --batch SM-FORMAL-TRIAL-20260930 \
    --actor-user-id ADMIN_UUID --expected-sales 47
  # Use the same arguments with apply, or cleanup [--apply --confirm-batch ...].

This tool deliberately does not insert visits, tasks, scores, actuals, contacts,
user accounts, role grants, quotas or external messages. It never hard-deletes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

SOURCE = "trial_seed"
STAGES = {
    "商机识别": ("identified", 10),
    "需求确认": ("qualified", 30),
    "方案设计": ("solution", 50),
    "技术验证": ("solution", 50),
    "商务沟通": ("proposal", 70),
}
TABLES = {"customer": "crm.customer", "opportunity": "crm.opportunity", "forecast": "crm.opportunity_forecast"}


class Refused(ValueError):
    """A reviewable, fail-closed input or state conflict."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def identifier(value):
    # Identifiers below originate from a fixed map or PostgreSQL catalogs, never
    # business input. Quote each identifier even so; IDs remain query parameters.
    return '"' + value.replace('"', '""') + '"'


def relation(value):
    return ".".join(identifier(part) for part in value.split("."))


def normalize(document, *, workspace, batch, actor_user_id, expected_sales=47):
    workspace, actor_user_id = str(UUID(str(workspace))), str(UUID(str(actor_user_id)))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,79}", batch):
        raise Refused("Invalid exact batch identifier")
    if str(document.get("workspace_id")) != workspace or document.get("batch_id") != batch:
        raise Refused("Dataset workspace/batch differs from explicit target")
    scenarios = document.get("scenarios", [])
    if not isinstance(scenarios, list) or len(scenarios) != expected_sales or expected_sales < 1:
        raise Refused("Dataset does not contain the exact expected sales count")
    rows, owners, names = [], set(), set()
    for scenario in scenarios:
        owner, team = str(UUID(scenario["owner_user_ref_id"])), str(UUID(scenario["team_id"]))
        if owner in owners:
            raise Refused("Each sales owner must have exactly one scenario")
        owners.add(owner)
        c, o = scenario["customer"], scenario["opportunity"]
        name = str(c["name"]).strip()
        normalized_name = re.sub(r"\s+", "", name).lower()
        if not name or normalized_name in names:
            raise Refused("Customer names must be nonempty and unique")
        names.add(normalized_name)
        for field in ("phone", "email", "address", "external_crm_id", "position_score", "risk_score", "potential_score", "relationship_score"):
            if c.get(field) is not None:
                raise Refused("Contact identifiers and model scores are outside this seed scope")
        if any(o.get(key, 0) != 0 for key in ("signed_contract_amount_cny", "recognized_revenue_cny", "collected_amount_cny")):
            raise Refused("Realized financial facts are forbidden in trial seed")
        if o.get("actual_close_date") or o.get("contract_number"):
            raise Refused("Closed opportunities and contract facts are forbidden")
        if o.get("currency", "CNY") != "CNY" or o["stage_label"] not in STAGES:
            raise Refused("Unsupported trial currency or stage")
        amount = Decimal(str(o["amount_cny"]))
        if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000000000") or amount.as_tuple().exponent < -2:
            raise Refused("Invalid CNY opportunity amount")
        close = date.fromisoformat(o["expected_close_date"])
        if not 2000 <= close.year <= 2100:
            raise Refused("Expected closing year is outside supported range")
        cid = str(uuid5(UUID(workspace), f"{SOURCE}:{batch}:{owner}:customer"))
        oid = str(uuid5(UUID(workspace), f"{SOURCE}:{batch}:{owner}:opportunity"))
        marker = {"source": SOURCE, "batch_id": batch, "root_customer_id": cid}
        common = {"workspace_id": workspace, "owner_user_ref_id": owner, "owner_team_id": team,
                  "created_by_user_ref_id": actor_user_id, "attributes": {}}
        customer = dict(common, id=cid, name=name, normalized_name=normalized_name,
                        industry_code=c.get("industry"), main_business=c.get("main_business"),
                        demand_summary=c.get("need_summary"), next_action=o.get("next_action"),
                        lifecycle_status="prospect", data_source=SOURCE, data_kind="demo", source_code="manual")
        stage, probability = STAGES[o["stage_label"]]
        opportunity = dict(common, id=oid, customer_id=cid, name=str(o["name"]).strip(),
                           stage_code=stage, probability=probability, amount=amount, currency="CNY",
                           expected_close_date=close, status="open", source_code="manual",
                           sales_channel="unknown", follow_up_plan=o.get("follow_up_plan"),
                           ownership_resolution="confirmed")
        if not opportunity["name"]:
            raise Refused("Opportunity name must be nonempty")
        forecast = None
        if probability >= 30:
            forecast = {"id": str(uuid5(UUID(workspace), f"{SOURCE}:{batch}:{owner}:forecast")),
                        "workspace_id": workspace, "opportunity_id": oid, "year": close.year,
                        "quarter": (close.month - 1) // 3 + 1, "recognized_amount": Decimal(0),
                        "collection_amount": Decimal(0), "updated_by_user_ref_id": actor_user_id,
                        "collection_confidence": None}
        rows.append({"owner_user_ref_id": owner, "team_id": team, "marker": marker,
                     "customer": customer, "opportunity": opportunity, "forecast": forecast})
    rows.sort(key=lambda x: x["owner_user_ref_id"])
    plan = {"workspace": workspace, "batch": batch, "actor_user_id": actor_user_id,
            "expected_sales": expected_sales, "rows": rows}
    plan["manifest_sha256"] = digest(plan)
    return plan


async def current_roster(connection, workspace):
    # Follow the authoritative owner selection: active sales role, current active
    # primary membership, latest membership as the deterministic tie-breaker.
    records = await connection.fetch("""SELECT u.id::text AS owner, membership.team_id::text AS team
        FROM platform.user_ref u
        JOIN LATERAL (SELECT m.team_id FROM platform.team_membership m
          JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=u.workspace_id
          WHERE m.user_ref_id=u.id AND m.workspace_id=u.workspace_id
            AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
            AND t.status='active' AND t.deleted_at IS NULL
          ORDER BY m.is_primary DESC,m.created_at DESC,m.id LIMIT 1) membership ON true
        WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
          AND EXISTS(SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=u.id
            AND r.workspace_id=u.workspace_id AND r.role_code='sales'
            AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to)
        ORDER BY u.id""", workspace)
    return {r["owner"]: r["team"] for r in records}


async def preflight(connection, plan, *, require_roster=True):
    if not await connection.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"):
        raise Refused("This maintenance tool requires an explicit database owner/superuser session")
    if not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V159')"):
        raise Refused("V159 trial boundaries must be installed before seeding")
    if not await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM platform.workspace
        WHERE id=$1::uuid AND status='active' AND deleted_at IS NULL)""", plan["workspace"]):
        raise Refused("Target workspace is not active")
    if not await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM platform.user_ref u
        JOIN platform.role_binding r ON r.user_ref_id=u.id AND r.workspace_id=u.workspace_id
        WHERE u.id=$1::uuid AND u.workspace_id=$2::uuid AND u.status='active' AND u.deleted_at IS NULL
          AND r.role_code IN ('administrator','operations')
          AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to)""",
        plan["actor_user_id"], plan["workspace"]):
        raise Refused("Seed attribution requires an active same-company administrator/operations account")
    if require_roster:
        expected = {r["owner_user_ref_id"]: r["team_id"] for r in plan["rows"]}
        if await current_roster(connection, plan["workspace"]) != expected:
            raise Refused("Active sales roster or primary teams changed; regenerate and review the plan")


def expected_ids(plan, kind):
    return [UUID(row[kind]["id"]) for row in plan["rows"] if row[kind]]


async def read_batch(connection, plan, *, lock=False):
    output = {}
    for kind in ("customer", "opportunity"):
        suffix = " FOR UPDATE OF t" if lock else ""
        records = await connection.fetch(f"""SELECT to_jsonb(t) AS value FROM {TABLES[kind]} t
            WHERE (workspace_id=$1::uuid AND import_meta->'synthetic_trial'->>'batch_id'=$2)
               OR id=ANY($3::uuid[]) ORDER BY id{suffix}""",
            plan["workspace"], plan["batch"], expected_ids(plan, kind))
        output[kind] = {decoded(r["value"])["id"]: decoded(r["value"]) for r in records}
    records = await connection.fetch("""SELECT to_jsonb(f) AS value FROM crm.opportunity_forecast f
        WHERE opportunity_id=ANY($1::uuid[]) OR id=ANY($2::uuid[]) ORDER BY id""",
        expected_ids(plan, "opportunity"), expected_ids(plan, "forecast"))
    output["forecast"] = {decoded(r["value"])["id"]: decoded(r["value"]) for r in records}
    return output


def same_value(actual, expected):
    if isinstance(expected, Decimal):
        return actual is not None and Decimal(str(actual)) == expected
    if isinstance(expected, (date, datetime, UUID)):
        return str(actual) == str(expected)
    return actual == expected


def same_timestamp(actual, expected):
    try:
        return datetime.fromisoformat(actual) == datetime.fromisoformat(expected)
    except (ValueError, TypeError):
        return False


def inspect_seed(plan, existing):
    conflicts = []
    if not any(existing.values()):
        return {"state": "absent", "conflicts": []}
    archived = []
    for kind in TABLES:
        if set(existing[kind]) != {str(i) for i in expected_ids(plan, kind)}:
            conflicts.append({"kind": kind, "reason": "partial_batch_extra_rows_or_id_collision"})
    for group in plan["rows"]:
        for kind in TABLES:
            spec = group[kind]
            if not spec or spec["id"] not in existing[kind]:
                continue
            row = existing[kind][spec["id"]]
            if kind == "forecast":
                expected = spec
                parent = existing["opportunity"].get(group["opportunity"]["id"], {})
                stamp = parent.get("import_meta", {}).get("synthetic_trial_seed", {}).get("seeded_at")
                if not same_timestamp(row["updated_at"], stamp):
                    conflicts.append({"kind": kind, "id": spec["id"], "reason": "forecast_was_edited"})
            else:
                meta = row.get("import_meta", {})
                seed = meta.get("synthetic_trial_seed", {})
                retired = meta.get("synthetic_trial_cleanup")
                archived.append(bool(retired))
                expected = dict(spec)
                if retired:
                    expected["lifecycle_status" if kind == "customer" else "status"] = "archived" if kind == "customer" else "cancelled"
                    expected["version_no"] = 2
                    if retired.get("manifest_sha256") != plan["manifest_sha256"] or not row.get("deleted_at"):
                        conflicts.append({"kind": kind, "id": spec["id"], "reason": "invalid_retirement_marker"})
                else:
                    expected.update(version_no=1, deleted_at=None)
                    if not same_timestamp(row.get("created_at"), seed.get("seeded_at")) or not same_timestamp(row.get("updated_at"), seed.get("seeded_at")):
                        conflicts.append({"kind": kind, "id": spec["id"], "reason": "seed_timestamp_changed"})
                if meta.get("synthetic_trial") != group["marker"] or seed.get("manifest_sha256") != plan["manifest_sha256"] or seed.get("record_sha256") != digest(spec):
                    conflicts.append({"kind": kind, "id": spec["id"], "reason": "provenance_or_manifest_differs"})
                permitted_meta = {"synthetic_trial", "synthetic_trial_seed", "synthetic_trial_cleanup"}
                if set(meta) - permitted_meta:
                    conflicts.append({"kind": kind, "id": spec["id"], "reason": "additional_import_metadata"})
            changed = [key for key, value in expected.items() if not same_value(row.get(key), value)]
            if changed:
                conflicts.append({"kind": kind, "id": spec["id"], "reason": "user_edits_or_collision", "fields": changed})
    if any(archived) and not all(archived):
        conflicts.append({"reason": "partially_archived_batch"})
    return {"state": "conflict" if conflicts else "archived" if archived and all(archived) else "present",
            "conflicts": conflicts}


async def relationships(connection, plan, existing):
    """Catalog-driven business links plus inherited markers, including detached rows.

    Audit records stay as evidence and do not block retirement. All ordinary
    associated rows do block, including already soft-deleted user content.
    Values and IDs are always parameters; catalog identifiers are quoted.
    """
    workspace, batch = plan["workspace"], plan["batch"]
    roots = expected_ids(plan, "customer")
    opportunity_ids = list(set(expected_ids(plan, "opportunity")) | {
        UUID(r["id"]) for r in existing["opportunity"].values() if r["workspace_id"] == workspace})
    inherited = {}
    for kind, table in (("visit", "activity.visit"), ("task", "workflow.task")):
        inherited[kind] = list(await connection.fetch("SELECT id FROM " + table + " WHERE workspace_id=$1::uuid AND "
            "(import_meta->'synthetic_trial'->>'batch_id'=$2 OR customer_id=ANY($3::uuid[]) OR opportunity_id=ANY($4::uuid[]))",
            workspace, batch, roots, opportunity_ids))
    visit_ids = [r["id"] for r in inherited["visit"]]
    task_ids = [r["id"] for r in inherited["task"]]
    # Both secondary visit/opportunity links and task source visits are meaningful.
    visit_ids += list(await connection.fetchval("SELECT COALESCE(array_agg(visit_id),'{}'::uuid[]) FROM activity.visit_opportunity WHERE workspace_id=$1::uuid AND opportunity_id=ANY($2::uuid[])", workspace, opportunity_ids))
    task_ids += list(await connection.fetchval("SELECT COALESCE(array_agg(id),'{}'::uuid[]) FROM workflow.task WHERE workspace_id=$1::uuid AND source_visit_id=ANY($2::uuid[])", workspace, visit_ids))
    linked = {"customer_id": roots, "candidate_customer_id": roots, "root_customer_id": roots,
              "opportunity_id": opportunity_ids, "visit_id": visit_ids, "source_visit_id": visit_ids,
              "task_id": task_ids, "source_task_id": task_ids}
    columns = await connection.fetch("""SELECT n.nspname AS schema,c.relname AS name,a.attname AS column
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE c.relkind IN ('r','p') AND n.nspname IN ('crm','activity','workflow','insight','agent','ops','ingest')
          AND a.attname=ANY($1::text[]) AND a.atttypid='uuid'::regtype
          AND EXISTS(SELECT 1 FROM pg_attribute w WHERE w.attrelid=c.oid AND w.attname='workspace_id' AND NOT w.attisdropped)
        ORDER BY n.nspname,c.relname,a.attname""", list(linked))
    by_table = {}
    for col in columns:
        if linked[col["column"]]:
            by_table.setdefault(col["schema"] + "." + col["name"], []).append(col["column"])
    result = []
    for table, cols in by_table.items():
        if table in ("crm.customer", "crm.opportunity", "crm.opportunity_forecast", "crm.customer_ownership", "crm.customer_sales_member"):
            continue
        params = [workspace] + [linked[col] for col in cols]
        clauses = [f"{identifier(col)}=ANY(${i + 2}::uuid[])" for i, col in enumerate(cols)]
        records = await connection.fetch(f"SELECT to_jsonb(t) AS value FROM {relation(table)} t WHERE workspace_id=$1::uuid AND (" + " OR ".join(clauses) + ")", *params)
        if records:
            ids = [decoded(r["value"]).get("id") or digest(decoded(r["value"])) for r in records]
            result.append({"table": table, "count": len(records), "ids": ids})
    for kind, table in (("visit", "activity.visit"), ("task", "workflow.task")):
        ids = sorted({str(r["id"]) for r in inherited[kind]})
        if ids and not any(r["table"] == table for r in result):
            result.append({"table": table, "count": len(ids), "ids": ids, "origin": "retained_batch_marker"})
    # Polymorphic queues, notifications and JSON-held associations need separate
    # coverage; no messages or payloads are returned in maintenance output.
    all_ids = [str(i) for i in roots + opportunity_ids + visit_ids + task_ids + expected_ids(plan, "forecast")]
    seed_ids = {kind: {str(i) for i in expected_ids(plan, kind)} for kind in TABLES}
    for table in ("ops.job", "ops.feishu_event", "workflow.notification", "insight.weekly_report", "agent.run"):
        records = await connection.fetch(f"SELECT to_jsonb(t) AS value FROM {table} t WHERE workspace_id=$1::uuid AND EXISTS(SELECT 1 FROM unnest($2::text[]) target WHERE to_jsonb(t)::text LIKE '%'||target||'%')", workspace, all_ids)
        ordinary, excluded = [], []
        for record in records:
            value = decoded(record["value"])
            if table == "ops.feishu_event" and value.get("object_id") in seed_ids.get(value.get("object_kind"), set()):
                source = decoded(await connection.fetchval("SELECT ops.feishu_source($1::uuid,$2,$3::uuid)",
                    value["connection_id"], value["object_kind"], value["object_id"]))
                if source and source.get("excluded") is True:
                    excluded.append(value["id"])
                    continue
            ordinary.append(value["id"])
        if excluded:
            result.append({"table": table, "count": len(excluded), "ids": excluded,
                           "classification": "excluded_seed_outbox_retained"})
        if ordinary and not any(r["table"] == table and not r.get("classification") for r in result):
            result.append({"table": table, "count": len(ordinary), "ids": ordinary})
    ownership = await connection.fetch("SELECT to_jsonb(o) AS value FROM crm.customer_ownership o WHERE customer_id=ANY($1::uuid[])", roots)
    expected_owners = {r["customer"]["id"]: r["owner_user_ref_id"] for r in plan["rows"]}
    bad_ownership = []
    for record in ownership:
        row = decoded(record["value"])
        if row["workspace_id"] != workspace or row["owner_user_ref_id"] != expected_owners[row["customer_id"]] or row["state"] != "claimed" or row["version_no"] != 1:
            bad_ownership.append(row["customer_id"])
    if len(ownership) != len(roots) and existing["customer"]:
        bad_ownership += [str(i) for i in roots if str(i) not in {decoded(r["value"])["customer_id"] for r in ownership}]
    if bad_ownership:
        result.append({"table": "crm.customer_ownership", "count": len(bad_ownership), "ids": bad_ownership})
    members = await connection.fetch("SELECT to_jsonb(m) AS value FROM crm.customer_sales_member m WHERE customer_id=ANY($1::uuid[])", roots)
    good_members, bad_members = set(), []
    for record in members:
        row = decoded(record["value"])
        parent = existing["customer"].get(row["customer_id"], {})
        stamp = parent.get("import_meta", {}).get("synthetic_trial_seed", {}).get("seeded_at")
        if row["workspace_id"] == workspace and row["user_ref_id"] == expected_owners.get(row["customer_id"]) and row["added_by_user_ref_id"] == plan["actor_user_id"] and same_timestamp(row["joined_at"], stamp):
            good_members.add(row["customer_id"])
        else:
            bad_members.append(row["customer_id"] + ":" + row["user_ref_id"])
    if existing["customer"]:
        bad_members += [str(i) for i in roots if str(i) not in good_members]
    if bad_members:
        result.append({"table": "crm.customer_sales_member", "count": len(bad_members), "ids": bad_members})
    return result


async def inspect(connection, plan, *, lock=False):
    existing = await read_batch(connection, plan, lock=lock)
    state = inspect_seed(plan, existing)
    if state["state"] == "absent":
        duplicates = await connection.fetch("""SELECT id::text FROM crm.customer WHERE workspace_id=$1::uuid
            AND deleted_at IS NULL AND lower(regexp_replace(name,'\\s+','','g'))=ANY($2::text[])""",
            plan["workspace"], [r["customer"]["normalized_name"] for r in plan["rows"]])
        if duplicates:
            state = {"state": "conflict", "conflicts": [{"reason": "existing_customer_name_collision", "ids": [r["id"] for r in duplicates]}]}
    relationships_found = await relationships(connection, plan, existing)
    related = [r for r in relationships_found if not r.get("classification")]
    retained = [r for r in relationships_found if r.get("classification")]
    return dict(state, workspace=plan["workspace"], batch=plan["batch"], manifest_sha256=plan["manifest_sha256"],
                expected_counts={k: len(expected_ids(plan, k)) for k in TABLES},
                existing_counts={k: len(v) for k, v in existing.items()}, related_content=related,
                retained_system_records=retained,
                customer_ids=[str(i) for i in expected_ids(plan, "customer")],
                opportunity_ids=[str(i) for i in expected_ids(plan, "opportunity")],
                cleanup_allowed=state["state"] in ("present", "archived") and not related)


async def insert_record(connection, table, record):
    columns = list(record)
    values = [record[key] for key in columns]
    casts = ["::uuid" if key == "id" or key.endswith("_id") else "" for key in columns]
    placeholders = ",".join(f"${i + 1}{casts[i]}" for i in range(len(columns)))
    status = await connection.execute(f"INSERT INTO {relation(table)} (" + ",".join(identifier(c) for c in columns) + f") VALUES({placeholders})", *values)
    if status != "INSERT 0 1":
        raise Refused("Unexpected insert row count")


async def execute(connection, plan, command="plan", *, apply_cleanup=False, confirm_batch=None):
    if command not in ("plan", "apply", "cleanup"):
        raise Refused("Unsupported command")
    writing = command == "apply" or command == "cleanup" and apply_cleanup
    if apply_cleanup and (command != "cleanup" or confirm_batch != plan["batch"]):
        raise Refused("Cleanup apply requires the exact --confirm-batch value")
    # Nested transactions support disposable PostgreSQL test fixtures. The CLI
    # owns a fresh connection and uses SERIALIZABLE for the whole maintenance run.
    options = {} if connection.is_in_transaction() else {"isolation": "serializable", "readonly": not writing}
    async with connection.transaction(**options):
        await connection.execute("SET LOCAL lock_timeout='2s'; SET LOCAL statement_timeout='30s'")
        await preflight(connection, plan, require_roster=command != "cleanup")
        if writing:
            if not await connection.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended($1,0))", SOURCE + ":" + plan["workspace"] + ":" + plan["batch"]):
                raise Refused("Another maintenance operation owns this exact batch")
            # A deleted/moved owner cannot race between roster validation and insert.
            owner_ids = [UUID(r["owner_user_ref_id"]) for r in plan["rows"]]
            await connection.fetch("SELECT id FROM platform.user_ref WHERE workspace_id=$1::uuid AND id=ANY($2::uuid[]) FOR SHARE", plan["workspace"], owner_ids)
            if command == "apply":
                for table in ("platform.role_binding", "platform.team_membership"):
                    await connection.fetch(f"SELECT id FROM {table} WHERE workspace_id=$1::uuid AND user_ref_id=ANY($2::uuid[]) FOR SHARE", plan["workspace"], owner_ids)
                await connection.fetch("SELECT id FROM platform.team WHERE workspace_id=$1::uuid AND id=ANY($2::uuid[]) FOR SHARE", plan["workspace"], [UUID(r["team_id"]) for r in plan["rows"]])
                await preflight(connection, plan)
        report = await inspect(connection, plan, lock=writing)
        report.update(command=command, changed=False, dry_run=not writing)
        if command == "plan" or command == "cleanup" and not apply_cleanup:
            return report
        if command == "apply":
            if report["state"] == "present":
                report["result"] = "already_applied"
                return report
            if report["state"] != "absent":
                raise Refused("Existing trial batch is partial, archived, edited or conflicts; no writes made")
            seeded_at = await connection.fetchval("SELECT transaction_timestamp()")
            # PostgreSQL JSON timestamp formatting is used in both metadata and
            # later row comparisons so timezone spelling does not create drift.
            stamp = await connection.fetchval("SELECT to_jsonb($1::timestamptz)#>>'{}'", seeded_at)
            for row in plan["rows"]:
                for kind in ("customer", "opportunity"):
                    spec = row[kind]
                    record = dict(spec, created_at=seeded_at, updated_at=seeded_at,
                                  import_meta={"synthetic_trial": row["marker"], "synthetic_trial_seed": {
                                      "schema_version": 1, "manifest_sha256": plan["manifest_sha256"],
                                      "record_sha256": digest(spec), "seeded_at": stamp}})
                    await insert_record(connection, TABLES[kind], record)
                    if kind == "customer":
                        # The ownership trigger creates precisely this projection.
                        # Set its initial clock to the seed transaction so a later
                        # delete/re-add of even the same owner is detectable.
                        status = await connection.execute("""UPDATE crm.customer_sales_member SET joined_at=$5
                            WHERE workspace_id=$1::uuid AND customer_id=$2::uuid AND user_ref_id=$3::uuid
                              AND added_by_user_ref_id=$4::uuid""", plan["workspace"], spec["id"],
                            row["owner_user_ref_id"], plan["actor_user_id"], seeded_at)
                        if status != "UPDATE 1":
                            raise Refused("Initial ownership projection differs; rolled back")
                if row["forecast"]:
                    await insert_record(connection, TABLES["forecast"], dict(row["forecast"], updated_at=seeded_at))
            after = await inspect(connection, plan, lock=True)
            if after["state"] != "present" or after["related_content"]:
                raise Refused("Post-insert verification failed; rolled back: " + canonical({
                    "state": after["state"], "conflicts": after["conflicts"], "related_content": after["related_content"]}))
            return dict(after, command=command, changed=True, dry_run=False, result="applied")
        if not report["cleanup_allowed"]:
            raise Refused("Cleanup refused: batch incomplete/changed or user content/associations exist; use dry-run report")
        if report["state"] == "archived":
            return dict(report, result="already_archived")
        retired_at = await connection.fetchval("SELECT transaction_timestamp()")
        cleanup = {"archived_at": retired_at.isoformat(), "manifest_sha256": plan["manifest_sha256"], "method": "soft_archive"}
        for kind in ("opportunity", "customer"):
            assignment = "status='cancelled'" if kind == "opportunity" else "lifecycle_status='archived'"
            status = await connection.execute(f"UPDATE {TABLES[kind]} SET {assignment},deleted_at=$4,import_meta=import_meta||jsonb_build_object('synthetic_trial_cleanup',$5::jsonb) WHERE workspace_id=$1::uuid AND import_meta->'synthetic_trial'->>'batch_id'=$2 AND id=ANY($3::uuid[]) AND deleted_at IS NULL",
                plan["workspace"], plan["batch"], expected_ids(plan, kind), retired_at, cleanup)
            if status != "UPDATE " + str(len(plan["rows"])):
                raise Refused("Archive row count changed; rolled back")
        after = await inspect(connection, plan, lock=True)
        if after["state"] != "archived":
            raise Refused("Post-archive verification failed; rolled back")
        return dict(after, command=command, changed=True, dry_run=False, result="archived")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("plan", "apply", "cleanup"))
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--actor-user-id", required=True)
    parser.add_argument("--expected-sales", type=int, default=47)
    parser.add_argument("--apply", action="store_true", help="Only for cleanup; otherwise read-only")
    parser.add_argument("--confirm-batch")
    return parser.parse_args(argv)


async def main(argv=None):
    args = arguments(argv)
    document = json.loads(args.dataset.read_text())
    plan = normalize(document, workspace=args.workspace, batch=args.batch,
                     actor_user_id=args.actor_user_id, expected_sales=args.expected_sales)
    dsn = os.environ.get("SYNTHETIC_TRIAL_DATABASE_URL")
    if not dsn:
        raise Refused("Set SYNTHETIC_TRIAL_DATABASE_URL in the maintenance environment; never pass credentials as flags")
    import asyncpg
    connection = await asyncpg.connect(dsn)
    try:
        for type_name in ("json", "jsonb"):
            await connection.set_type_codec(type_name, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        report = await execute(connection, plan, args.command, apply_cleanup=args.apply, confirm_batch=args.confirm_batch)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Refused as exc:
        raise SystemExit("REFUSED: " + str(exc)) from None
