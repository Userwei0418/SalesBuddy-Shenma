"""Offline, tenant-bound CRM history planning. This module cannot write business data.

Only a frozen, complete source snapshot and an explicit source-key/legacy bridge
can identify existing records. Names are review hints, never merge keys.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

KINDS = ("customers", "opportunities", "visits", "partners")
NAME_FIELDS = {"customers": "客户名称", "opportunities": "商机名称", "partners": "伙伴名称"}
RELATIONS = {
    "customers": {"商机列表": "opportunities"},
    "opportunities": {"客户名称": "customers", "所属伙伴": "partners", "跟进记录": "visits"},
    "visits": {"商机名称": "opportunities", "伙伴名称": "partners"},
    "partners": {"商机列表": "opportunities", "跟进记录": "visits"},
}
FINANCIAL_FIELDS = (
    (3, "recognized", "Q3 预测含税确收（万元）", "inclusive", "forecast"),
    (4, "recognized", "Q4预测含税确收(万)", "inclusive", "forecast"),
    (3, "collection", "Q3 预测回款（万元）", "unknown", "forecast"),
    (4, "collection", "Q4预测回款（万元）", "unknown", "forecast"),
    (1, "recognized", "Q1含税确收（万元）", "inclusive", "period_actual_snapshot"),
    (1, "collection", "Q1回款（万元）", "unknown", "period_actual_snapshot"),
    (2, "recognized", "Q2真实确收", "unknown", "period_actual_snapshot"),
    (2, "collection", "Q2真实回款", "unknown", "period_actual_snapshot"),
)


# These fields do not replace any pre-existing business column. Allowing an
# additive write still requires frozen information_schema absence evidence.
ADDITIVE_FIELDS = {
    "opportunities": {
        "original_owner_name", "ownership_resolution", "ownership_resolution_evidence",
        "expected_close_year", "expected_close_quarter",
    },
    "visits": {"original_recorder_name", "manager_account", "relations.partners"},
    "customers": set(), "partners": set(),
}
FORWARD_RELATIONS = {"opportunities": {"customers", "partners"}, "visits": {"customers", "opportunities", "partners"}}
EVIDENCE_ONLY = {
    "raw_acv", "source_owner_field_empty", "transfer_personal_counts", "import_mode", "forecast_year",
    "source_owner_names", "source_fde_names", "source_fde_accounts", "fde_participation_status",
}
IMMUTABLE_VISIT_FIELDS = {
    "follow_up_record", "next_action", "original_recorder_account", "interaction_on", "contact_name_snapshot",
}


def persisted_projection(kind, normalized):
    """Split core writes, evidence, and independently reconciled child entities."""
    core, evidence, children = {}, {}, {}
    for field, value in normalized.items():
        if field == "relations":
            for related_kind, ids in value.items():
                field_name = f"relations.{related_kind}"
                if kind == "opportunities" and related_kind == "partners":
                    # The source association does not identify a reseller.
                    children["associated_partner_source_ids"] = ids
                elif related_kind in FORWARD_RELATIONS.get(kind, set()):
                    core[field_name] = ids
                else:
                    evidence[field_name] = ids
        elif field in {"financials", "collection_confidence"}:
            children[field] = value
        elif field in EVIDENCE_ONLY or (kind == "customers" and field == "ownership_resolution"):
            evidence[field] = value
        else:
            core[field] = value
    return core, evidence, children


def _same_business_value(field, left, right):
    if field == "amount" and left is not None and right is not None:
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except InvalidOperation:
            return False
    if field.startswith("relations."):
        return set(left or []) == set(right or [])
    return left == right


def _opportunity_business_key(normalized):
    """Match the existing customer/name uniqueness rule, never a merge key."""
    customers = normalized.get("relations", {}).get("customers", [])
    name = normalized.get("name")
    if len(customers) != 1 or not isinstance(name, str) or not name:
        return None
    return customers[0], re.sub(r"\s+", "", name).lower()


def _verified_format_preservation(snapshot, record_id, field, source_value, target, current_value):
    """Accept only a frozen, exact native-segment proof; never strip arbitrary links."""
    proof = snapshot.get("format_preservation", {}).get(record_id, {}).get(field)
    if not proof or field != "follow_up_record":
        return False
    if not isinstance(source_value, str) or not isinstance(current_value, str):
        return False
    if (proof.get("target_id") != target["id"]
            or proof.get("expected_target_version") != target["version"]
            or proof.get("source_value_sha256") != canonical_hash(source_value)
            or proof.get("current_value_sha256") != canonical_hash(current_value)
            or not proof.get("evidence_sha256") or not proof.get("evidence")):
        return False
    segments = proof.get("native_segments")
    if (not isinstance(segments, list) or not segments
            or proof.get("native_segments_sha256") != canonical_hash(segments)):
        return False
    plain, rendered = [], []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            return False
        text = segment["text"]
        kind = segment.get("type")
        if kind == "text":
            render = text
        elif kind in {"url", "mention"} and isinstance(segment.get("link"), str):
            render = f"[{text}]({segment['link']})"
        else:
            return False
        plain.append(text)
        rendered.append(render)
    # Export trimming is allowed only at the whole-field boundary. Internal
    # spaces, punctuation, words, link labels and line breaks must stay exact.
    return ("".join(plain).strip() == current_value.strip()
            and "".join(rendered).strip() == source_value.strip())


def reconcile_forecast_children(normalized, inventory_row):
    """Retain child identities/current row hashes; blanks never clear a forecast."""
    incoming = defaultdict(dict)
    for item in normalized.get("financials", []):
        if item["record_kind"] == "forecast":
            incoming[(item["year"], item["quarter"])][f"{item['kind']}_amount"] = item["amount_cny"]
    for quarter, confidence in normalized.get("collection_confidence", {}).items():
        if confidence is not None:
            # Confidence uses the same frozen financial year as the amounts.
            year = normalized.get("forecast_year")
            if year is not None:
                incoming[(year, int(quarter))]["collection_confidence"] = confidence
    existing = {(row["year"], row["quarter"]): row for row in inventory_row.get("forecast_reconciliation", [])}
    output = []
    for year, quarter in sorted(set(incoming) | set(existing)):
        row = existing.get((year, quarter), {})
        current = row.get("current", {})
        baseline = row.get("last_imported_snapshot")
        matching = baseline is not None and {"recognized_amount", "collection_amount"} <= set(baseline) and all(
            _same_business_value("amount", current.get(field), baseline.get(field))
            for field in ("recognized_amount", "collection_amount")
        )
        values = incoming.get((year, quarter), {})
        complete = inventory_row.get("forecasts_complete") is True
        if not values:
            action = "preserve"
        elif current and matching:
            action = "update"
        elif not current and complete:
            action = "create"
        else:
            action = "conflict"
        output.append({
            "year": year, "quarter": quarter, "target_id": current.get("id"),
            "current_snapshot": current, "last_imported_snapshot": baseline,
            "source_values": values, "action": action,
        })
    return output


class SnapshotError(ValueError):
    """The supplied envelope is incomplete or cannot be trusted as a snapshot."""


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def source_key(system: str, base: str, table: str, record: str) -> str:
    parts = (system, base, table, record)
    if any(not isinstance(part, str) or not part.strip() or ":" in part for part in parts):
        raise SnapshotError("Source identity requires four nonempty colon-free strings")
    return ":".join(parts)


def _text(value: Any) -> str | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, list):
        texts = [_text(item) for item in value]
        return "、".join(dict.fromkeys(text for text in texts if text)) or None
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("text"))
    return str(value).strip() or None


def _relation_ids(value: Any) -> list[str]:
    if value is None or value == "" or value == []:
        return []
    if not isinstance(value, list) or any(not isinstance(x, dict) or not x.get("id") for x in value):
        raise ValueError("relation_requires_source_record_ids")
    return list(dict.fromkeys(str(item["id"]) for item in value))


def _person_names(value: Any) -> list[str]:
    """Retain explicit source people individually; do not infer from prose."""
    if isinstance(value, list):
        return list(dict.fromkeys(name for item in value for name in _person_names(item)))
    text = _text(value)
    return [text] if text else []


def _business_date(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    # Preserve original day precision. Copy/import timestamps are never used.
    return date.fromisoformat(text[:10].replace("/", "-")).isoformat()


def _amount(value: Any) -> tuple[str | None, str | None]:
    text = _text(value)
    if text is None:
        return None, None
    amount = Decimal(text)
    if not amount.is_finite():
        raise ValueError("non_finite_amount")
    return format(amount, "f"), format(amount * Decimal(10000), "f")


def calendar_month_start(as_of: date, months: int = 6) -> date:
    """Use calendar months, clamping month-end rather than subtracting 180 days."""
    index = as_of.year * 12 + as_of.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(as_of.day, calendar.monthrange(year, month)[1]))


def normalize_record(kind, raw, *, year, accounts, owner_decision=None, execution_contract=None):
    """Project known source facts without creating authors, dates or transactions."""
    issues: list[str] = []
    warnings: list[str] = []
    normalized: dict[str, Any] = {"relations": {}}
    if kind in NAME_FIELDS:
        normalized["name"] = _text(raw.get(NAME_FIELDS[kind]))
        if not normalized["name"]:
            issues.append("missing_name")
    for field, related_kind in RELATIONS[kind].items():
        try:
            normalized["relations"][related_kind] = _relation_ids(raw.get(field))
        except ValueError:
            issues.append(f"unresolved_relation:{field}")
            normalized["relations"][related_kind] = []
    if kind == "customers":
        source_owners = _person_names(raw.get("客户所属销售"))
        owner = accounts.get(source_owners[0]) if len(source_owners) == 1 else None
        normalized.update(
            source_owner_names=source_owners, current_owner_account=owner,
            ownership_resolution="confirmed" if owner else "unassigned",
        )
        if source_owners and not owner:
            warnings.append("customer_owner_requires_assignment")
    if kind in {"opportunities", "visits"}:
        fde_names = _person_names(raw.get("FDE" if kind == "opportunities" else "参与FDE"))
        normalized.update(
            source_fde_names=fde_names,
            source_fde_accounts={name: accounts.get(name) for name in fde_names},
            fde_participation_status="pending_relationship_reconciliation" if fde_names else "source_empty",
        )
        if fde_names:
            warnings.append("source_fde_relationships_require_reconciliation")
    if kind == "opportunities":
        contract = execution_contract or {}
        source_stage = _text(raw.get("商机状态"))
        stage = contract.get("stage_map", {}).get(source_stage)
        if stage and stage.get("stage_code") and stage.get("status") in {"open", "won", "lost"}:
            normalized.update(stage_code=stage["stage_code"], status=stage["status"],
                              probability=stage.get("probability"))
        else:
            issues.append("unresolved_opportunity_stage")
        normalized["product_line"] = _text(raw.get("产品线"))
        source_channel = _text(raw.get("签单方式"))
        channel = contract.get("sales_channel_map", {}).get(source_channel)
        normalized["sales_channel"] = channel if channel in {"direct", "partner"} else None
        if source_channel and channel not in {"direct", "partner"}:
            issues.append("unknown_sales_channel")
        original = _text(raw.get("商机销售"))
        decision = owner_decision or {}
        candidate = decision.get("current_owner_account") or accounts.get(original)
        normalized.update(
            original_owner_name=original,
            source_owner_field_empty=original is None,
            current_owner_account=candidate,
            ownership_resolution="provisional" if decision.get("is_provisional") else "confirmed",
            ownership_resolution_evidence=decision.get("evidence") or ("explicit_source_owner" if original else None),
        )
        if not candidate or candidate not in set(accounts.values()):
            normalized["ownership_resolution"] = "unassigned"
            issues.append("unresolved_current_owner")
        if decision and not decision.get("evidence"):
            issues.append("owner_decision_without_evidence")
        customers = normalized["relations"].get("customers", [])
        if len(customers) != 1:
            issues.append("opportunity_requires_one_resolved_customer")
        quarter = _text(raw.get("预计关单日期"))
        normalized["expected_close_date"] = None
        normalized["expected_close_year"] = year if quarter in {"Q1", "Q2", "Q3", "Q4"} else None
        normalized["expected_close_quarter"] = int(quarter[1]) if quarter in {"Q1", "Q2", "Q3", "Q4"} else None
        if quarter and normalized["expected_close_quarter"] is None:
            issues.append("invalid_close_quarter")
        try:
            normalized["raw_acv"], normalized["amount"] = _amount(raw.get("ACV（万元）"))
        except (ValueError, InvalidOperation):
            issues.append("invalid_acv")
        normalized["forecast_year"] = year
        normalized["financials"] = []
        for quarter, metric, field, tax, record_kind in FINANCIAL_FIELDS:
            try:
                original_amount, amount = _amount(raw.get(field))
            except (ValueError, InvalidOperation):
                issues.append(f"invalid_amount:{field}")
                continue
            if original_amount is None:
                continue
            normalized["financials"].append({
                "year": year, "quarter": quarter, "kind": metric, "record_kind": record_kind,
                "source_field": field, "raw_amount": original_amount, "source_unit": "wan_cny",
                "amount_cny": amount, "tax_basis": tax,
            })
        normalized["collection_confidence"] = {}
        for quarter in (3, 4):
            confidence = _text(raw.get(f"Q{quarter}可回款信心度"))
            code = {"高(>=70%)": "high", "低(<70%)": "low"}.get(confidence)
            normalized["collection_confidence"][str(quarter)] = code
            if confidence and code is None:
                issues.append(f"unknown_confidence:Q{quarter}")
        if _text(raw.get("Q2真实确收")) is not None:
            warnings.append("q2_recognized_tax_unknown_keep_period_actual_snapshot")
    elif kind == "partners":
        normalized["original_channel_manager_name"] = _text(raw.get("渠道经理"))
        normalized["channel_manager_account"] = accounts.get(normalized["original_channel_manager_name"])
        for field, source_field in {
            "short_name": "伙伴简称", "principal_name": "伙伴负责人", "priority": "优先级",
            "progress": "进展", "grade": "伙伴等级", "partner_type": "伙伴类型",
            "region": "区域", "province": "省份", "note": "备注",
        }.items():
            normalized[field] = _text(raw.get(source_field))
        try:
            normalized["signed_on"] = _business_date(raw.get("签约日期"))
        except ValueError:
            normalized["signed_on"] = None
            issues.append("invalid_partner_signed_on")
    elif kind == "visits":
        normalized.update(
            original_recorder_name=_text(raw.get("跟进人")),
            original_recorder_account=accounts.get(_text(raw.get("跟进人"))),
            follow_up_record=_text(raw.get("沟通内容")),
            next_action=_text(raw.get("下一步计划")),
            contact_name_snapshot=_text(raw.get("对接人")),
            manager_account=None,
            transfer_personal_counts=False,
            import_mode="historical",
        )
        try:
            normalized["interaction_on"] = _business_date(raw.get("跟进日期"))
        except ValueError:
            normalized["interaction_on"] = None
            issues.append("invalid_original_followup_date")
        if normalized["interaction_on"] is None:
            issues.append("missing_original_followup_date")
        try:
            normalized["recorded_on"] = _business_date(raw.get("创建时间"))
        except ValueError:
            normalized["recorded_on"] = None
            issues.append("invalid_source_recording_date")
        if not normalized["follow_up_record"]:
            issues.append("missing_followup_content")
        if not normalized["relations"].get("opportunities") and not normalized["relations"].get("partners"):
            issues.append("unresolved_visit_subject")
        if not normalized["original_recorder_name"]:
            issues.append("original_author_unknown")
    return normalized, issues, warnings


def _validate_snapshot(snapshot):
    if snapshot.get("schema_version") != 1 or not snapshot.get("workspace_id"):
        raise SnapshotError("Expected schema_version=1 and explicit workspace_id")
    if not snapshot.get("as_of") or not isinstance(snapshot.get("financial_year"), int):
        raise SnapshotError("Freeze the business date and confirmed financial year")
    date.fromisoformat(snapshot["as_of"])
    tables = snapshot.get("tables", {})
    if set(tables) != set(KINDS):
        raise SnapshotError("All four source tables are required")
    for kind, table in tables.items():
        rows = table.get("records", [])
        if table.get("complete") is not True or table.get("expected_count") != len(rows):
            raise SnapshotError(f"Incomplete source table: {kind}")
        if not table.get("visible_fields") or len(set(table["visible_fields"])) != len(table["visible_fields"]):
            raise SnapshotError(f"Missing or duplicate visible field whitelist: {kind}")
        if table.get("records_sha256") != canonical_hash(rows):
            raise SnapshotError(f"Snapshot checksum mismatch: {kind}")
        ids = [row.get("record_id") for row in rows]
        if len(set(ids)) != len(ids) or any(not record for record in ids):
            raise SnapshotError(f"Missing or duplicate stable source IDs: {kind}")
        for record in ids:
            source_key(
                snapshot.get("source_system", "feishu"), snapshot.get("base_token"), table.get("table_id"), record
            )
    return tables


def build_preflight(snapshot, inventory):
    """Return an inert create/update/noop/quarantine/conflict plan, never SQL."""
    tables = _validate_snapshot(snapshot)
    if not isinstance(inventory, dict) or inventory.get("complete") is not True:
        raise SnapshotError("A complete, explicitly frozen target inventory is required")
    if inventory.get("workspace_id") != snapshot["workspace_id"]:
        raise SnapshotError("Inventory tenant differs from the frozen target")
    accounts = snapshot.get("accounts", {})
    existing_by_key = {}
    existing_by_id = {}
    existing_names = defaultdict(list)
    existing_opportunity_names = defaultdict(list)
    for kind, objects in inventory.get("objects", {}).items():
        if kind not in KINDS:
            raise SnapshotError("Unexpected inventory kind")
        for obj in objects:
            if obj.get("workspace_id", inventory["workspace_id"]) != inventory["workspace_id"]:
                raise SnapshotError("Inventory row belongs to another tenant")
            if not obj.get("id") or not isinstance(obj.get("version"), int):
                raise SnapshotError("Inventory requires target ID and current version")
            key = (kind, obj["id"])
            if key in existing_by_id:
                raise SnapshotError("Duplicate target ID in inventory")
            if kind == "opportunities":
                business_key = _opportunity_business_key(obj.get("values", {}))
                if business_key:
                    existing_opportunity_names[business_key].append(obj["id"])
            absent = set(obj.get("absent_fields", []))
            verified = set(inventory.get("schema", {}).get("verified_absent_fields", {}).get(kind, []))
            if absent and (not inventory.get("schema", {}).get("evidence_sha256")
                           or absent - verified or absent - ADDITIVE_FIELDS.get(kind, set())):
                raise SnapshotError("Additive fields require schema absence evidence and an explicit safe-field policy")
            if any(field in obj.get("values", {}) and obj["values"][field] is not None for field in absent):
                raise SnapshotError("A declared absent field contains a current business value")
            existing_by_id[key] = obj
            name = obj.get("values", {}).get("name")
            candidate_names = set(obj.get("candidate_names", []))
            if name:
                candidate_names.add(name)
            for candidate_name in candidate_names:
                if candidate_name and candidate_name.strip():
                    existing_names[(kind, candidate_name.strip())].append(obj["id"])
            for identity in obj.get("source_keys", []):
                if identity in existing_by_key:
                    raise SnapshotError("Source key points to multiple existing records")
                existing_by_key[identity] = (kind, obj)
    aliases = {}
    for alias in snapshot.get("legacy_aliases", []):
        if alias.get("approved") is not True or not alias.get("evidence"):
            raise SnapshotError("Legacy bridges must be explicit and reviewed")
        if alias["source_key"] in aliases:
            raise SnapshotError("Duplicate legacy source alias")
        target = existing_by_id.get((alias["kind"], alias["target_id"]))
        if target is None:
            raise SnapshotError("Legacy alias target is absent from inventory")
        aliases[alias["source_key"]] = (alias, target)
    prepared = {}
    for kind, table in tables.items():
        for row in table["records"]:
            raw = {field: row.get("fields", {}).get(field) for field in table["visible_fields"]}
            prepared[(kind, row["record_id"])] = (raw, *normalize_record(
                kind, raw, year=snapshot["financial_year"], accounts=accounts,
                owner_decision=snapshot.get("owner_decisions", {}).get(row["record_id"]),
                execution_contract=snapshot.get("execution_contract", {}),
            ))
    for (kind, _), (_, normalized, issues, _) in prepared.items():
        if kind != "visits":
            continue
        opportunities = [prepared[("opportunities", rid)][1]
                         for rid in normalized["relations"].get("opportunities", [])
                         if ("opportunities", rid) in prepared]
        partners = [prepared[("partners", rid)][1]
                    for rid in normalized["relations"].get("partners", [])
                    if ("partners", rid) in prepared]
        # Explicit opportunity management takes precedence over partner channel
        # managers. The original author never implies current management.
        managers = {row.get("current_owner_account") for row in opportunities}
        if not opportunities:
            managers = {row.get("channel_manager_account") for row in partners}
        if len(managers) == 1 and None not in managers:
            normalized["manager_account"] = next(iter(managers))
        else:
            issues.append("unresolved_visit_management")
        normalized["relations"]["customers"] = sorted({
            rid for row in opportunities for rid in row["relations"].get("customers", [])
        })
        if normalized.get("interaction_on") and normalized["interaction_on"] > snapshot["as_of"]:
            issues.append("followup_date_after_snapshot")
    plan = []
    by_kind_id = {}
    names = defaultdict(list)
    opportunity_names = defaultdict(list)
    targeted = defaultdict(list)
    for kind, table in tables.items():
        for row in table["records"]:
            record_id = row["record_id"]
            key = source_key(
                snapshot.get("source_system", "feishu"), snapshot["base_token"], table["table_id"], record_id
            )
            raw, normalized, issues, warnings = prepared[(kind, record_id)]
            issues.extend(snapshot.get("quarantine", {}).get(record_id, []))
            entry = {
                "kind": kind, "source_key": key, "source_record_id": record_id,
                "source_hash": canonical_hash(raw), "raw_fields": raw, "normalized": normalized,
                "action": "create", "can_execute": False, "issues": issues, "warnings": warnings,
                "target_id": None, "expected_target_version": None, "changes": {},
                "additive": {}, "preserve": {}, "evidence_only": {}, "related_objects": {},
                "can_execute_children": False,
            }
            target = None
            if key in existing_by_key:
                target_kind, target = existing_by_key[key]
                if target_kind != kind:
                    raise SnapshotError("Source identity mapped to a different entity kind")
            if key in aliases:
                alias, alias_target = aliases[key]
                if target and target["id"] != alias_target["id"]:
                    raise SnapshotError("Legacy alias conflicts with existing stable mapping")
                target = alias_target
                if alias.get("expected_target_version") != target["version"]:
                    issues.append("stale_legacy_alias_version")
                    entry["action"] = "conflict"
            if target:
                entry["target_id"] = target["id"]
                entry["expected_target_version"] = target["version"]
                targeted[(kind, target["id"])].append(entry)
                current, _, _ = persisted_projection(kind, target.get("values", {}))
                baseline, _, _ = persisted_projection(kind, target.get("last_imported_values", {}))
                desired, evidence, children = persisted_projection(kind, normalized)
                entry["evidence_only"] = evidence
                entry["related_objects"] = children
                for field, source_value in desired.items():
                    # Missing source facts never erase newer/richer business values.
                    if source_value is None:
                        entry["preserve"][field] = "source_unknown"
                        continue
                    if field.startswith("relations.") and source_value == []:
                        entry["preserve"][field] = "source_relation_empty_preserve_existing"
                        continue
                    if field in target.get("absent_fields", []):
                        entry["additive"][field] = source_value
                        continue
                    if field in current and _same_business_value(field, current[field], source_value):
                        entry["preserve"][field] = "unchanged"
                        continue
                    if kind == "customers" and field == "current_owner_account":
                        ownership = target.get("ownership", {})
                        if ownership.get("is_assigned") is True or current.get(field):
                            entry["preserve"][field] = "existing_customer_owner_preserved"
                            continue
                        if ownership.get("is_assigned") is False and field in current and current[field] is None:
                            entry["changes"][field] = {"before": None, "after": source_value}
                            warnings.append("restore_source_customer_owner_without_claim_approval")
                            continue
                    if (kind == "visits" and field == "contact_name_snapshot"
                            and isinstance(current.get(field), str) and isinstance(source_value, str)
                            and current[field].strip() == source_value.strip()):
                        entry["preserve"][field] = "boundary_whitespace_preserve_existing"
                        continue
                    if (kind == "visits" and _verified_format_preservation(
                            snapshot, record_id, field, source_value, target, current.get(field))):
                        entry["preserve"][field] = "format_only_preserve_existing"
                        warnings.append(f"format_only_preserve_existing:{field}")
                        continue
                    if kind == "visits" and field in IMMUTABLE_VISIT_FIELDS:
                        issues.append(f"historical_record_requires_correction_review:{field}")
                        entry["action"] = "conflict"
                        continue
                    if (field not in baseline or field not in current
                            or not _same_business_value(field, current[field], baseline[field])):
                        issues.append(f"current_business_value_protected:{field}")
                        entry["action"] = "conflict"
                    else:
                        entry["changes"][field] = {"before": current[field], "after": source_value}
                if current.get("expected_close_date") and normalized.get("expected_close_quarter"):
                    warnings.append("preserve_existing_close_date_review_quarter_precision")
                    close_date = date.fromisoformat(str(current["expected_close_date"])[:10])
                    if (close_date.year, (close_date.month - 1) // 3 + 1) != (
                            normalized["expected_close_year"], normalized["expected_close_quarter"]):
                        issues.append("existing_close_date_differs_from_source_quarter")
                        entry["action"] = "conflict"
                if entry["action"] != "conflict":
                    entry["action"] = "update" if entry["changes"] or entry["additive"] else "noop"
            elif normalized.get("name"):
                candidates = existing_names[(kind, normalized["name"])]
                if candidates:
                    issues.append("unmapped_existing_name_candidate")
                    entry["candidate_target_ids"] = candidates
            _, entry["evidence_only"], entry["related_objects"] = persisted_projection(kind, normalized)
            if entry["preserve"].get("follow_up_record") == "format_only_preserve_existing":
                entry["evidence_only"]["format_preservation"] = snapshot["format_preservation"][record_id]
            if kind == "opportunities":
                child_inventory = target or {"forecasts_complete": True, "forecast_reconciliation": []}
                entry["related_objects"]["forecast_reconciliation"] = reconcile_forecast_children(
                    normalized, child_inventory,
                )
            if normalized.get("name") and kind in {"customers", "partners"}:
                names[(kind, normalized["name"])].append(entry)
            if kind == "opportunities":
                business_key = _opportunity_business_key(normalized)
                if business_key:
                    opportunity_names[business_key].append(entry)
                    if any(target_id != entry["target_id"]
                           for target_id in existing_opportunity_names[business_key]):
                        issues.append("existing_customer_opportunity_name_conflict")
                        entry["action"] = "conflict"
            plan.append(entry)
            by_kind_id[(kind, record_id)] = entry
    for entries in names.values():
        if len(entries) > 1:
            for entry in entries:
                entry["issues"].append("duplicate_source_name_requires_review")
    for entries in opportunity_names.values():
        if len(entries) > 1:
            for entry in entries:
                entry["issues"].append("duplicate_customer_opportunity_name_requires_review")
    for entries in targeted.values():
        if len(entries) > 1:
            for entry in entries:
                entry["issues"].append("multiple_source_records_target_one_entity")
                entry["action"] = "conflict"
    # Reference absence is always visible. Only forward subject relations block:
    # optional reverse lookup links may refer to a quarantined future record.
    for entry in plan:
        for related_kind, ids in entry["normalized"]["relations"].items():
            for record_id in ids:
                if (related_kind, record_id) not in by_kind_id:
                    entry["issues"].append(f"missing_source_relation:{related_kind}:{record_id}")
        if entry["issues"] and entry["action"] != "conflict":
            entry["action"] = "quarantine"
    for _ in range(3):
        for entry in plan:
            if entry["kind"] not in {"visits", "opportunities"} or entry["action"] in {"conflict", "quarantine"}:
                continue
            subjects = {"customers", "partners"} if entry["kind"] == "opportunities" else {"opportunities", "partners"}
            for kind in subjects:
                for record_id in entry["normalized"]["relations"].get(kind, []):
                    target = by_kind_id.get((kind, record_id))
                    if target and target["action"] in {"conflict", "quarantine"}:
                        entry["issues"].append(f"subject_not_ready:{kind}:{record_id}")
                        entry["action"] = "quarantine"
    for entry in plan:
        entry["issues"] = sorted(set(entry["issues"]))
        entry["can_execute"] = entry["action"] in {"create", "update", "noop"}
        if entry["kind"] == "opportunities":
            child_rows = entry["related_objects"].get("forecast_reconciliation", [])
            entry["can_execute_children"] = entry["can_execute"] and all(
                row["action"] != "conflict" for row in child_rows
            )
    counts = {kind: dict(Counter(row["action"] for row in plan if row["kind"] == kind)) for kind in KINDS}
    return {
        "schema_version": 1, "mode": "preflight_only", "business_data_written": False,
        "workspace_id": snapshot["workspace_id"], "as_of": snapshot["as_of"],
        "snapshot_hash": canonical_hash(snapshot), "inventory_hash": canonical_hash(inventory),
        "counts": counts, "records": plan,
    }
