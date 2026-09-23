"""Apply a reviewed, immutable CRM history plan through native tenant RLS.

This is an operations tool, not an HTTP endpoint. No environment, credentials,
files, network, model calls, notifications, or production connection are opened
here. The caller supplies an explicit connection with standard JSON codecs.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

import asyncpg

from sales_backend.services.crm_history_preflight import canonical_hash

TABLES = {
    "customer": "crm.customer",
    "partner": "crm.partner",
    "opportunity": "crm.opportunity",
    "visit": "activity.visit",
    "forecast": "crm.opportunity_forecast",
    "period_actual_snapshot": "crm.opportunity_period_actual_snapshot",
}
KINDS = {"customers": "customer", "partners": "partner", "opportunities": "opportunity", "visits": "visit"}
ORDER = {"partner": 0, "customer": 1, "opportunity": 2, "visit": 3}
PARTNER_FIELDS = {
    "name",
    "short_name",
    "principal_name",
    "original_channel_manager_name",
    "priority",
    "progress",
    "grade",
    "signed_on",
    "partner_type",
    "region",
    "province",
    "note",
    "channel_manager_account",
}
UPDATE_FIELDS = {
    "customer": {"name", "current_owner_account"},
    "partner": PARTNER_FIELDS,
    "opportunity": {
        "name",
        "amount",
        "stage_code",
        "status",
        "probability",
        "product_line",
        "sales_channel",
        "original_owner_name",
        "current_owner_account",
        "ownership_resolution",
        "ownership_resolution_evidence",
        "expected_close_year",
        "expected_close_quarter",
        "relations.partners",
    },
    # Archived prose, actual author, dates and existing subjects never change.
    "visit": {"manager_account", "original_recorder_name", "relations.partners"},
}


class ImportBlocked(ValueError):
    """A reviewed record cannot be written without inventing or replacing facts."""


def _uuid(value):
    return UUID(str(value)) if value is not None else None


def _decimal(value):
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ImportBlocked("invalid_nonnegative_amount")
    return result


def _decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def _day(value):
    return date.fromisoformat(value) if value else None


def intent_hash(entry, contract):
    """No inventory versions, action/before values or review timestamps in dedup."""
    return canonical_hash(
        {"raw_hash": entry["source_hash"], "normalized": entry["normalized"], "contract_version": contract["version"]}
    )


def validate_manifest(manifest, *, workspace_id, approved_sha256):
    if canonical_hash(manifest) != approved_sha256:
        raise ImportBlocked("manifest_hash_does_not_match_approval")
    plan, contract = manifest.get("plan", {}), manifest.get("execution_contract", {})
    if manifest.get("schema_version") != 1 or plan.get("schema_version") != 1:
        raise ImportBlocked("unsupported_manifest_schema")
    if _uuid(plan.get("workspace_id")) != _uuid(workspace_id):
        raise ImportBlocked("manifest_workspace_mismatch")
    if not contract.get("version") or contract.get("data_kind") not in {"production", "demo", "test"}:
        raise ImportBlocked("explicit_execution_contract_required")
    snapshot_at = datetime.fromisoformat(manifest["source_snapshot_at"].replace("Z", "+00:00"))
    if snapshot_at.tzinfo is None:
        raise ImportBlocked("source_snapshot_timezone_required")
    _uuid(contract["visit_form_version_id"])
    seen, bases = set(), set()
    for entry in plan.get("records", []):
        if entry.get("kind") not in KINDS:
            raise ImportBlocked("unsupported_source_kind")
        parts = entry["source_key"].split(":")
        if len(parts) != 4 or any(not part.strip() for part in parts):
            raise ImportBlocked("stable_source_identity_required")
        if parts[-1] != entry["source_record_id"] or canonical_hash(entry["raw_fields"]) != entry["source_hash"]:
            raise ImportBlocked("source_evidence_hash_mismatch")
        identity = (entry["kind"], entry["source_record_id"])
        if identity in seen:
            raise ImportBlocked("duplicate_source_identity")
        seen.add(identity)
        bases.add(tuple(parts[:2]))
        if entry["action"] not in {"create", "update", "noop", "quarantine", "conflict"}:
            raise ImportBlocked("unknown_plan_action")
        if entry.get("can_execute") and entry["action"] in {"conflict", "quarantine"}:
            raise ImportBlocked("contradictory_execution_flag")
        if entry["action"] in {"update", "noop"} and not entry.get("target_id"):
            raise ImportBlocked("existing_target_identity_required")
    if len(bases) != 1:
        raise ImportBlocked("one_source_base_per_batch_required")
    return plan, contract, snapshot_at, next(iter(bases))


class CrmHistoryApply:
    """Stage raw evidence, then apply eligible objects in dependency order.

    Transaction owner: each operation opens its own transaction; callers must
    not catch per-record failures inside a larger transaction and commit it.
    """

    def __init__(self, connection, *, workspace_id, actor_user_id, actor_role):
        self.connection = connection
        self.workspace = _uuid(workspace_id)
        self.actor = _uuid(actor_user_id)
        self.role = actor_role

    async def _context(self):
        if self.role not in {"operations", "administrator"}:
            raise ImportBlocked("operations_or_administrator_required")
        if await self.connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"):
            raise ImportBlocked("non_bypass_runtime_database_role_required")
        await self.connection.execute(
            """SELECT set_config('app.workspace_id',$1,true),
          set_config('app.user_ref_id',$2,true),set_config('app.role_code',$3,true),
          set_config('app.team_ids','',true),set_config('row_security','on',true),
          set_config('app.feishu_historical_import','on',true)""",
            str(self.workspace),
            str(self.actor),
            self.role,
        )
        if not await self.connection.fetchval("SELECT security.management_actor()"):
            raise ImportBlocked("active_management_identity_required")

    def _target(self, entry):
        return _uuid(entry.get("target_id")) or uuid5(self.workspace, entry["source_key"] + ":" + entry["kind"])

    async def stage(self, manifest, *, approved_sha256):
        plan, contract, snapshot_at, (system, base) = validate_manifest(
            manifest, workspace_id=self.workspace, approved_sha256=approved_sha256
        )
        async with self.connection.transaction():
            await self._context()
            form_valid = await self.connection.fetchval(
                "SELECT security.crm_history_visit_form_valid($1)",
                _uuid(contract["visit_form_version_id"]),
            )
            if not form_valid:
                raise ImportBlocked("reviewed_visit_form_version_not_active")
            batch = await self.connection.fetchval(
                """INSERT INTO ops.crm_import_batch
              (workspace_id,source_system,source_base_id,manifest_sha256,status,source_snapshot_at,created_by_user_ref_id,summary)
              VALUES($1,$2,$3,$4,'approved',$5,$6,$7::jsonb)
              ON CONFLICT(workspace_id,source_system,source_base_id,manifest_sha256) DO UPDATE SET
                summary=ops.crm_import_batch.summary RETURNING id""",
                self.workspace,
                system,
                base,
                approved_sha256,
                snapshot_at,
                self.actor,
                {"mode": "staged", "contract_version": contract["version"], "counts": plan.get("counts", {})},
            )
            for entry in plan["records"]:
                status = (
                    "approved"
                    if entry.get("can_execute")
                    else ("conflict" if entry["action"] == "conflict" else "quarantined")
                )
                await self._stage_record(batch, entry, KINDS[entry["kind"]], "", status, self._target(entry), contract)
                if entry["kind"] == "opportunities":
                    for child in self._children(entry, contract):
                        await self._stage_record(
                            batch,
                            child,
                            child["object_kind"],
                            child["item_key"],
                            "approved" if child["can_execute"] else "quarantined",
                            self._target(child),
                            contract,
                        )
            return {"batch_id": str(batch), "manifest_sha256": approved_sha256, "business_data_written": False}

    async def _stage_record(self, batch, entry, kind, item_key, status, target, contract):
        _, _, table, record = entry["source_key"].split(":")
        decision = {
            "source_raw_sha256": entry["source_hash"],
            "planned_action": entry["action"],
            "contract_version": contract["version"],
        }
        if entry.get("expected_snapshot") is not None:
            decision["expected_target_snapshot"] = entry["expected_snapshot"]
        evidence_hash = intent_hash(entry, contract)
        row = await self.connection.fetchrow(
            """INSERT INTO ops.crm_import_record
          (workspace_id,batch_id,source_table_id,source_record_id,source_item_key,object_kind,source_sha256,
           raw_fields,normalized,anomalies,status,target_id,expected_target_version,decision)
          VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb,$11,$12,$13,$14::jsonb)
          ON CONFLICT(batch_id,source_table_id,source_record_id,object_kind,source_item_key)
          DO UPDATE SET updated_at=ops.crm_import_record.updated_at RETURNING *""",
            self.workspace,
            batch,
            table,
            record,
            item_key,
            kind,
            evidence_hash,
            entry["raw_fields"],
            entry["normalized"],
            entry.get("issues", []),
            status,
            target,
            entry.get("expected_target_version"),
            decision,
        )
        if (
            row["source_sha256"] != evidence_hash
            or _decoded(row["raw_fields"]) != entry["raw_fields"]
            or _decoded(row["normalized"]) != entry["normalized"]
        ):
            raise ImportBlocked("staged_evidence_changed")
        return row["id"]

    def _children(self, entry, contract):
        normalized = entry["normalized"]
        financials = normalized.get("financials", [])
        groups = {}
        for item in financials:
            key = (item["record_kind"], int(item["year"]), int(item["quarter"]))
            if key[0] == "period_actual_snapshot":
                key += (item["kind"], item["source_field"])
            groups.setdefault(key, []).append(item)
        for quarter, confidence in normalized.get("collection_confidence", {}).items():
            if confidence is not None:
                year = contract.get("financial_year")
                if year is None:
                    raise ImportBlocked("financial_year_required_for_confidence")
                groups.setdefault(("forecast", int(year), int(quarter)), [])
        legacy = {
            (int(x["year"]), int(x["quarter"])): x
            for x in entry.get("related_objects", {}).get("forecast_reconciliation", [])
        }
        for key, items in groups.items():
            kind, year, quarter = key[:3]
            if kind not in {"forecast", "period_actual_snapshot"} or not 2000 <= year <= 2100 or not 1 <= quarter <= 4:
                raise ImportBlocked("invalid_financial_period")
            item_key = ":".join(map(str, key))
            values = {"year": year, "quarter": quarter}
            if kind == "forecast":
                values.update(
                    recognized_amount=None,
                    collection_amount=None,
                    collection_confidence=normalized.get("collection_confidence", {}).get(str(quarter)),
                )
                for item in items:
                    values[item["kind"] + "_amount"] = item["amount_cny"]
                reconciliation = legacy.get((year, quarter))
                child_action = reconciliation.get("action", "update") if reconciliation else None
                if child_action == "preserve":
                    continue
                baseline = reconciliation if child_action == "update" else None
                if baseline and (
                    not baseline.get("target_id")
                    or not baseline.get("current_snapshot")
                    or not baseline.get("last_imported_snapshot")
                ):
                    raise ImportBlocked("legacy_forecast_baseline_evidence_missing")
                allowed = entry["action"] == "create" or (
                    child_action in {"create", "update"}
                    and contract.get("allow_verified_legacy_forecast_update") is True
                )
                allowed = allowed and child_action != "conflict" and entry.get("can_execute_children", True)
            else:
                values.update(items[0])
                baseline = None
                allowed = contract.get("allow_period_actual_snapshot_create") is True
            expected = dict(baseline["current_snapshot"]) if baseline else None
            if expected is not None:
                expected.setdefault("collection_confidence", None)
                old = baseline["last_imported_snapshot"]
                for metric in ("recognized_amount", "collection_amount"):
                    if _decimal(expected.get(metric)) != _decimal(old.get(metric)):
                        raise ImportBlocked("legacy_forecast_baseline_mismatch")
                if expected.get("collection_confidence") is not None:
                    raise ImportBlocked("legacy_forecast_confidence_has_no_baseline")
            yield {
                "kind": kind,
                "object_kind": kind,
                "source_key": entry["source_key"],
                "source_record_id": entry["source_record_id"],
                "item_key": item_key,
                "source_hash": canonical_hash({"items": items, "confidence": values.get("collection_confidence")}),
                "raw_fields": {"items": items, "confidence": values.get("collection_confidence")},
                "normalized": values,
                "target_id": baseline["target_id"]
                if baseline
                else str(uuid5(self.workspace, entry["source_key"] + ":" + item_key)),
                "expected_snapshot": expected,
                "action": "update" if baseline else "create",
                "can_execute": bool(entry.get("can_execute") and allowed),
                "issues": [] if allowed else ["existing_forecast_requires_reviewed_import_baseline"],
            }

    async def apply(self, manifest, *, approved_sha256):
        plan, contract, _, _ = validate_manifest(manifest, workspace_id=self.workspace, approved_sha256=approved_sha256)
        staged = await self.stage(manifest, approved_sha256=approved_sha256)
        batch = _uuid(staged["batch_id"])
        outcomes = []
        lookup = {(row["kind"], row["source_record_id"]): row for row in plan["records"]}
        async with self.connection.transaction():
            await self._context()
            await self.connection.execute(
                "UPDATE ops.crm_import_batch SET status='applying' WHERE id=$1 AND workspace_id=$2",
                batch,
                self.workspace,
            )
        for entry in sorted(plan["records"], key=lambda row: ORDER[KINDS[row["kind"]]]):
            kind = KINDS[entry["kind"]]
            children = list(self._children(entry, contract)) if kind == "opportunity" else []
            child_outcomes = []
            business_action = "unchanged"
            if not entry.get("can_execute"):
                status = "conflict" if entry["action"] == "conflict" else "quarantined"
                outcomes.append(
                    {
                        "source_key": entry["source_key"],
                        "object_kind": kind,
                        "status": status,
                        "business_action": status,
                        "children": [self._child_outcome(child, status) for child in children],
                    }
                )
                continue
            try:
                async with self.connection.transaction():
                    await self._context()
                    dependencies = (
                        ("customers", "partners")
                        if kind == "opportunity"
                        else (("customers", "partners", "opportunities") if kind == "visit" else ())
                    )
                    for dependency in dependencies:
                        await self._relations(entry, dependency, lookup, batch)
                    record = await self._ledger(batch, entry, kind, "")
                    decision = _decoded(
                        await self.connection.fetchval(
                            "SELECT security.check_crm_import_target($1,$2)", record["id"], self._target(entry)
                        )
                    )
                    target = _uuid(decision["target_id"]) or self._target(entry)
                    if decision["action"] != "unchanged":
                        if entry["action"] == "create" and decision["action"] != "insert":
                            raise ImportBlocked("create_target_already_exists")
                        if entry["action"] != "create" and decision["action"] != "update":
                            raise ImportBlocked("reviewed_existing_target_missing")
                        values, relations = await self._core_values(
                            entry, contract, lookup, batch, record["id"], target, create=decision["action"] == "insert"
                        )
                        if decision["action"] == "insert":
                            await self._insert(kind, values)
                            business_action = "inserted"
                            if kind == "visit" and len(relations) > 1:
                                for opportunity in relations:
                                    await self.connection.execute(
                                        "INSERT INTO activity.visit_opportunity"
                                        "(visit_id,workspace_id,opportunity_id,created_by_user_ref_id) "
                                        "VALUES($1,$2,$3,$4)",
                                        target,
                                        self.workspace,
                                        opportunity,
                                        self.actor,
                                    )
                        if (
                            kind == "customer"
                            and entry["normalized"].get("current_owner_account")
                            and (decision["action"] == "insert" or "current_owner_account" in entry.get("changes", {}))
                        ):
                            owner = await self._account(entry["normalized"]["current_owner_account"])
                            team = await self._owner_team(owner, entry["normalized"]["current_owner_account"], contract)
                            restored = _decoded(
                                await self.connection.fetchval(
                                    "SELECT security.restore_historical_customer_ownership($1,$2,$3)",
                                    record["id"],
                                    owner,
                                    team,
                                )
                            )
                            if restored["status"] == "history_restored" and business_action != "inserted":
                                business_action = "updated"
                        if decision["action"] != "insert" and values:
                            await self._update(kind, target, values)
                            business_action = "updated"
                        if kind == "opportunity" and (
                            decision["action"] == "insert"
                            or entry.get("related_objects", {}).get("associated_partner_source_ids")
                            or "relations.partners" in entry.get("changes", {})
                            or "relations.partners" in entry.get("additive", {})
                        ):
                            changed = await self._associated_partners(target, entry, lookup, batch)
                            if changed and business_action != "inserted":
                                business_action = "updated"
                        await self._bind(batch, record, entry, target, kind, contract)
                    else:
                        await self._mark_unchanged(record["id"], target)
                    for child in children:
                        child_outcomes.append(
                            await self._apply_child(batch, child, target, contract)
                            if child["can_execute"]
                            else self._child_outcome(child, "quarantined")
                        )
                # Deferred constraints can fail when the transaction exits. Only
                # report mutations after its parent and children commit together.
                outcomes.append(
                    {
                        "source_key": entry["source_key"],
                        "object_kind": kind,
                        "target_id": str(target),
                        "status": "unchanged" if decision["action"] == "unchanged" else "applied",
                        "business_action": business_action,
                        "children": child_outcomes,
                    }
                )
            except (ImportBlocked, asyncpg.PostgresError) as exc:
                code = str(exc) if isinstance(exc, ImportBlocked) else "database_guard:" + exc.sqlstate
                async with self.connection.transaction():
                    await self._context()
                    await self.connection.execute(
                        """UPDATE ops.crm_import_record SET status='conflict',
                      anomalies=anomalies || jsonb_build_array($1::text) WHERE batch_id=$2 AND workspace_id=$3
                      AND source_table_id=$4 AND source_record_id=$5 AND status NOT IN ('applied','unchanged')""",
                        code,
                        batch,
                        self.workspace,
                        entry["source_key"].split(":")[2],
                        entry["source_record_id"],
                    )
                outcomes.append(
                    {
                        "source_key": entry["source_key"],
                        "object_kind": kind,
                        "status": "conflict",
                        "business_action": "conflict",
                        "reason": code,
                        "children": [self._child_outcome(child, "conflict") for child in children],
                    }
                )
        counts = dict(Counter(item["status"] for item in outcomes))
        business_counts = Counter()
        for outcome in outcomes:
            for item in (outcome, *outcome["children"]):
                business_counts[(item["object_kind"], item["business_action"])] += 1
        object_counts = [
            {"object_kind": kind, "action": action, "count": count}
            for (kind, action), count in sorted(business_counts.items())
        ]
        async with self.connection.transaction():
            await self._context()
            ledger_counts = [
                dict(row)
                for row in await self.connection.fetch(
                    "SELECT object_kind,status,count(*) AS count FROM ops.crm_import_record "
                    "WHERE batch_id=$1 AND workspace_id=$2 GROUP BY object_kind,status ORDER BY object_kind,status",
                    batch,
                    self.workspace,
                )
            ]
            await self.connection.execute(
                "UPDATE ops.crm_import_batch SET status='applied',summary=summary || $2::jsonb "
                "WHERE id=$1 AND workspace_id=$3",
                batch,
                {"mode": "applied", "outcomes": counts, "ledger_counts": ledger_counts, "object_counts": object_counts},
                self.workspace,
            )
        return {
            **staged,
            "business_data_written": any(row["action"] in {"inserted", "updated"} for row in object_counts),
            "counts": counts,
            "ledger_counts": ledger_counts,
            "object_counts": object_counts,
            "records": outcomes,
        }

    async def _ledger(self, batch, entry, kind, item_key):
        row = await self.connection.fetchrow(
            """SELECT * FROM ops.crm_import_record WHERE batch_id=$1 AND workspace_id=$2
          AND source_table_id=$3 AND source_record_id=$4 AND object_kind=$5 AND source_item_key=$6 FOR UPDATE""",
            batch,
            self.workspace,
            entry["source_key"].split(":")[2],
            entry["source_record_id"],
            kind,
            item_key,
        )
        if row is None or _decoded(row["normalized"]) != entry["normalized"]:
            raise ImportBlocked("approved_ledger_record_missing_or_changed")
        return row

    async def _account(self, account):
        if account is None:
            return None
        row = await self.connection.fetchval(
            """SELECT id FROM platform.user_ref WHERE workspace_id=$1
          AND upper(account_code)=upper($2) AND status='active' AND deleted_at IS NULL""",
            self.workspace,
            account,
        )
        if row is None:
            raise ImportBlocked("reviewed_account_missing_or_inactive")
        return row

    async def _relations(self, entry, related_kind, lookup, batch):
        result = []
        for source_id in entry["normalized"].get("relations", {}).get(related_kind, []):
            parent = lookup.get((related_kind, source_id))
            if parent is None or not parent.get("can_execute"):
                raise ImportBlocked("subject_not_resolved:" + related_kind)
            system, base, table, record = parent["source_key"].split(":")
            target = await self.connection.fetchval(
                """SELECT b.target_id FROM ops.crm_import_binding b
              JOIN ops.crm_import_record r ON r.workspace_id=b.workspace_id AND r.target_id=b.target_id
               AND r.source_table_id=b.source_table_id AND r.source_record_id=b.source_record_id
               AND r.object_kind=b.object_kind AND r.source_item_key=b.source_item_key
              WHERE b.workspace_id=$1 AND b.source_system=$2 AND b.source_base_id=$3 AND b.source_table_id=$4
              AND b.source_record_id=$5 AND b.object_kind=$6 AND b.source_item_key=''
              AND r.batch_id=$7 AND r.status IN ('applied','unchanged')""",
                self.workspace,
                system,
                base,
                table,
                record,
                KINDS[related_kind],
                batch,
            )
            if target is None:
                raise ImportBlocked("subject_not_applied:" + related_kind)
            result.append(target)
        return result

    async def _owner_team(self, user_id, account, contract):
        if user_id is None:
            return None
        memberships = await self.connection.fetch(
            """SELECT DISTINCT tm.team_id,tm.is_primary FROM platform.team_membership tm
              JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
              WHERE tm.workspace_id=$1 AND tm.user_ref_id=$2 AND t.status='active' AND t.deleted_at IS NULL
              AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
              AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
              AND tm.membership_role IN ('sales','supervisor','manager')
              AND EXISTS(SELECT 1 FROM platform.role_binding rb WHERE rb.workspace_id=tm.workspace_id
                AND rb.user_ref_id=tm.user_ref_id AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
                AND rb.role_code=tm.membership_role
                AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to)""",
            self.workspace,
            user_id,
        )
        explicit = contract.get("owner_team_by_account", {}).get(account)
        if explicit:
            team = _uuid(explicit)
            if team not in {row["team_id"] for row in memberships}:
                raise ImportBlocked("reviewed_owner_team_membership_changed")
            return team
        primary = {row["team_id"] for row in memberships if row["is_primary"]}
        if len(primary) != 1:
            raise ImportBlocked("owner_requires_one_verified_primary_business_team")
        return next(iter(primary))

    async def _associated_partners(self, opportunity, entry, lookup, batch):
        partners = set(await self._relations(entry, "partners", lookup, batch))
        # check_crm_import_target already locks the parent opportunity. Bridge
        # writes also touch that parent; the bridge has no UPDATE privilege.
        existing = {
            row["partner_id"]
            for row in await self.connection.fetch(
                "SELECT partner_id FROM crm.opportunity_related_partner "
                "WHERE workspace_id=$1 AND opportunity_id=$2",
                self.workspace,
                opportunity,
            )
        }
        if not partners:
            return False  # Absence is unknown and never deletes a business relationship.
        if not existing.issubset(partners):
            raise ImportBlocked("existing_associated_partner_requires_reconciliation")
        for partner in sorted(partners - existing):
            await self.connection.execute(
                "INSERT INTO crm.opportunity_related_partner"
                "(workspace_id,opportunity_id,partner_id,created_by_user_ref_id) VALUES($1,$2,$3,$4)",
                self.workspace,
                opportunity,
                partner,
                self.actor,
            )
        return bool(partners - existing)

    async def _core_values(self, entry, contract, lookup, batch, ledger_id, target, *, create):
        kind, normalized = KINDS[entry["kind"]], entry["normalized"]
        if create:
            desired = dict(normalized)
        else:
            desired = dict(entry.get("additive", {}))
            desired.update({key: value["after"] for key, value in entry.get("changes", {}).items()})
            unsupported = set(desired) - UPDATE_FIELDS[kind]
            if unsupported:
                raise ImportBlocked("unsupported_protected_update:" + ",".join(sorted(unsupported)))
        values = {}
        direct = UPDATE_FIELDS[kind] - {
            "current_owner_account",
            "manager_account",
            "channel_manager_account",
            "relations.partners",
        }
        for key in direct:
            if key in desired:
                value = desired[key]
                if value is None and not create:
                    raise ImportBlocked("source_unknown_must_not_erase_business_field")
                if key in {"amount", "probability"}:
                    value = _decimal(value)
                elif key == "signed_on":
                    value = _day(value)
                elif key == "ownership_resolution_evidence":
                    value = value if isinstance(value, dict) else {"source": value}
                values[key] = value
        for source, target_field in (
            ("current_owner_account", "owner_user_ref_id"),
            ("manager_account", "manager_user_ref_id"),
            ("channel_manager_account", "channel_manager_user_ref_id"),
        ):
            if source in desired and not (source == "current_owner_account" and kind == "customer"):
                values[target_field] = await self._account(desired[source])
        if "current_owner_account" in desired and kind == "opportunity":
            values["owner_team_id"] = await self._owner_team(
                values["owner_user_ref_id"], desired["current_owner_account"], contract
            )
        if "name" in values and kind == "customer":
            values["normalized_name"] = values["name"].strip().lower()
        if kind == "opportunity" and (create or {"stage_code", "status", "probability"} & desired.keys()):
            valid = any(
                all(normalized.get(key) == stage.get(key) for key in ("stage_code", "status", "probability"))
                for stage in contract.get("stage_map", {}).values()
            )
            if not valid:
                raise ImportBlocked("opportunity_stage_not_in_reviewed_contract")
        if not create:
            if kind == "opportunity" and "original_owner_name" in normalized:
                captured = await self.connection.fetchval(
                    "SELECT original_owner_captured FROM crm.opportunity WHERE id=$1 AND workspace_id=$2",
                    target,
                    self.workspace,
                )
                if captured is False:
                    # The approved source may explicitly have no original sales.
                    # Freeze that absence as well; later transfer is not an author.
                    values["original_owner_captured"] = True
            if kind == "visit" and "relations.partners" in desired:
                partners = await self._relations(entry, "partners", lookup, batch)
                old = await self.connection.fetchval(
                    f"SELECT partner_id FROM {TABLES[kind]} WHERE id=$1 AND workspace_id=$2",  # noqa: S608
                    target,
                    self.workspace,
                )
                if not partners:
                    return values, []  # No new source fact: preserve any existing business relationship.
                if len(partners) != 1 or (old is not None and old != partners[0]):
                    raise ImportBlocked("existing_visit_subject_is_protected")
                values["partner_id"] = partners[0]
            return values, []
        values.update(
            id=target,
            workspace_id=self.workspace,
            created_by_user_ref_id=self.actor,
            import_meta={
                "import_type": "crm_history",
                "import_record_id": str(ledger_id),
                "import_batch_id": str(batch),
                "source_key": entry["source_key"],
                "source_raw_sha256": entry["source_hash"],
                "source_fields": entry["raw_fields"],
            },
        )
        relations = []
        if kind == "customer":
            values.update(data_kind=contract["data_kind"], data_source="crm_history", owner_user_ref_id=None)
        elif kind == "opportunity":
            customers = await self._relations(entry, "customers", lookup, batch)
            partners = await self._relations(entry, "partners", lookup, batch)
            if len(customers) != 1:
                raise ImportBlocked("opportunity_requires_one_customer")
            channel = normalized.get("sales_channel") or "unknown"
            values.update(
                customer_id=customers[0],
                partner_id=None,  # Source association is not evidence of the reseller identity.
                sales_channel=channel,
            )
        elif kind == "visit":
            customers = await self._relations(entry, "customers", lookup, batch)
            partners = await self._relations(entry, "partners", lookup, batch)
            relations = await self._relations(entry, "opportunities", lookup, batch)
            if len(partners) > 1 or (not customers and not partners and not relations):
                raise ImportBlocked("visit_subject_requires_unambiguous_customer_or_partner")
            if not normalized.get("original_recorder_name") or not normalized.get("interaction_on"):
                raise ImportBlocked("historical_author_and_business_date_required")
            interaction = datetime.combine(_day(normalized["interaction_on"]), time.min, ZoneInfo("Asia/Shanghai"))
            values.update(
                customer_id=customers[0] if len(customers) == 1 else None,
                partner_id=partners[0] if partners else None,
                opportunity_id=relations[0] if len(relations) == 1 else None,
                recorder_user_ref_id=await self._account(normalized.get("original_recorder_account")),
                form_version_id=_uuid(contract["visit_form_version_id"]),
                status="archived",
                interaction_at=interaction,
                recorded_on=_day(normalized.get("recorded_on")),
                archived_at=datetime.now(UTC),
                follow_up_record=normalized.get("follow_up_record"),
                next_action=normalized.get("next_action"),
                contact_name_snapshot=normalized.get("contact_name_snapshot"),
                archived_fields={
                    "follow_up_record": normalized.get("follow_up_record"),
                    "next_action": normalized.get("next_action"),
                    "interaction_at": normalized["interaction_on"],
                    "source_fields": entry["raw_fields"],
                },
            )
        return values, relations

    async def _insert(self, kind, values):
        columns = list(values)
        placeholders = ",".join("$" + str(index) for index in range(1, len(columns) + 1))
        await self.connection.execute(
            f"INSERT INTO {TABLES[kind]} ({','.join(columns)}) VALUES({placeholders})", *values.values()
        )

    async def _update(self, kind, target, values):
        assignments = ",".join(f"{key}=${index}" for index, key in enumerate(values, 3))
        result = await self.connection.execute(
            f"UPDATE {TABLES[kind]} SET {assignments} WHERE id=$1 AND workspace_id=$2",  # noqa: S608 -- private whitelist
            target,
            self.workspace,
            *values.values(),
        )
        if result != "UPDATE 1":
            raise ImportBlocked("reviewed_target_update_did_not_affect_one_row")

    async def _bind(self, batch, ledger, entry, target, kind, contract):
        snapshot = _decoded(
            await self.connection.fetchval(
                f"SELECT to_jsonb(t) FROM {TABLES[kind]} t WHERE id=$1 AND workspace_id=$2",  # noqa: S608 -- constant table
                target,
                self.workspace,
            )
        )
        if snapshot is None:
            raise ImportBlocked("written_target_not_visible")
        system, base, table, record = entry["source_key"].split(":")
        await self.connection.execute(
            """INSERT INTO ops.crm_import_binding
          (workspace_id,source_system,source_base_id,source_table_id,source_record_id,source_item_key,object_kind,
           target_id,target_version,last_source_sha256,last_imported_snapshot,applied_batch_id)
          VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12)
          ON CONFLICT(workspace_id,source_system,source_base_id,source_table_id,source_record_id,
            object_kind,source_item_key)
          DO UPDATE SET target_version=EXCLUDED.target_version,last_source_sha256=EXCLUDED.last_source_sha256,
            last_imported_snapshot=EXCLUDED.last_imported_snapshot,applied_batch_id=EXCLUDED.applied_batch_id,
            applied_at=clock_timestamp()""",
            self.workspace,
            system,
            base,
            table,
            record,
            ledger["source_item_key"],
            kind,
            target,
            snapshot.get("version_no"),
            intent_hash(entry, contract),
            snapshot,
            batch,
        )
        await self.connection.execute(
            """UPDATE ops.crm_import_record SET status='applied',target_id=$2,
          last_imported_snapshot=$3::jsonb,applied_at=clock_timestamp() WHERE id=$1 AND workspace_id=$4""",
            ledger["id"],
            target,
            snapshot,
            self.workspace,
        )

    async def _mark_unchanged(self, ledger_id, target):
        await self.connection.execute(
            "UPDATE ops.crm_import_record SET status='unchanged',target_id=$2,applied_at=clock_timestamp() "
            "WHERE id=$1 AND workspace_id=$3",
            ledger_id,
            target,
            self.workspace,
        )

    def _child_outcome(self, child, action):
        return {
            "object_kind": child["object_kind"],
            "source_item_key": child["item_key"],
            "target_id": str(self._target(child)),
            "business_action": action,
        }

    async def _apply_child(self, batch, child, parent, contract):
        kind = child["object_kind"]
        record = await self._ledger(batch, child, kind, child["item_key"])
        decision = _decoded(
            await self.connection.fetchval(
                "SELECT security.check_crm_import_target($1,$2)", record["id"], self._target(child)
            )
        )
        target = _uuid(decision["target_id"]) or self._target(child)
        if decision["action"] == "unchanged":
            await self._mark_unchanged(record["id"], target)
            return self._child_outcome(child, "unchanged")
        normalized = child["normalized"]
        values = {"opportunity_id": parent, "year": normalized["year"], "quarter": normalized["quarter"]}
        if kind == "forecast":
            if child.get("expected_snapshot") and _uuid(child["expected_snapshot"]["opportunity_id"]) != parent:
                raise ImportBlocked("forecast_baseline_parent_mismatch")
            for field in ("recognized_amount", "collection_amount"):
                value = normalized.get(field)
                # An unknown source value never wipes a verified legacy amount.
                if value is not None or decision["action"] == "insert":
                    values[field] = _decimal(value)
            if normalized.get("collection_confidence") is not None or decision["action"] == "insert":
                values["collection_confidence"] = normalized.get("collection_confidence")
            values["updated_by_user_ref_id"] = self.actor
            values["updated_at"] = datetime.now(UTC)
        else:
            if decision["action"] != "insert":
                raise ImportBlocked("period_source_snapshot_is_immutable")
            values.update({key: normalized[key] for key in ("kind", "source_field", "source_unit", "tax_basis")})
            values.update(
                raw_amount=_decimal(normalized["raw_amount"]), source_record_id=record["id"], import_batch_id=batch
            )
        if decision["action"] == "insert":
            await self._insert(kind, {"id": target, "workspace_id": self.workspace, **values})
        else:
            await self._update(kind, target, values)
        await self._bind(batch, record, child, target, kind, contract)
        return self._child_outcome(child, "inserted" if decision["action"] == "insert" else "updated")
