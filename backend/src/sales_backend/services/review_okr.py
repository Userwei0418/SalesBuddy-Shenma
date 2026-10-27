"""Normalize periods and preserve the database as the authorization authority."""

from datetime import date, datetime
from decimal import localcontext

from sales_backend.contracts.review_overview import ReviewSelection
from sales_backend.domain.finance_totals import actual_totals, date_scoped_actuals
from sales_backend.domain.work_report import SHANGHAI, WorkReportError
from sales_backend.repositories.opportunity_business_date import business_created_on
from sales_backend.repositories.review_okr import ReviewOkrRepository
from sales_backend.services.review_overview import read_metric_projection
from sales_backend.services.targets import target_period


async def save_objective(connection, body, entity_id=None):
    payload = body.model_dump(mode="json", exclude={"version_no"})
    if entity_id is None:
        period = target_period(body.period_type, body.anchor_date)
        payload.pop("anchor_date")
        payload.update(period_start=period["start"].isoformat(), period_end=period["end"].isoformat())
    return await ReviewOkrRepository().save(
        connection, "objective", entity_id, payload, getattr(body, "version_no", None)
    )


async def save_key_result(connection, body, entity_id=None):
    return await ReviewOkrRepository().save(
        connection,
        "key_result",
        entity_id,
        body.model_dump(mode="json", exclude={"version_no"}),
        getattr(body, "version_no", None),
    )


FORECAST_METRICS = {"forecast_collection", "collection_ppl", "forecast_recognized"}
ACTUAL_METRICS = {"collection", "recognized"}


def missing_progress(reason, *, metric, as_of):
    return {
        "value": None,
        "known_subtotal": None,
        "coverage": "unavailable",
        "complete": False,
        "status": "missing",
        "reason": reason,
        "missing_reasons": [reason],
        "source_refs": [],
        "metric_code": metric["code"],
        "definition_version": metric["definition_version"],
        "unit": metric["unit"],
        "data_as_of": as_of,
    }


def actual_progress(facts, *, metric, period, as_of):
    """Preserve the established actual/tax/period completeness semantics exactly."""
    code = metric["code"]
    with localcontext() as context:
        context.prec = 80
        scoped, warning = date_scoped_actuals(
            [row for row in facts if row["kind"] == code], period["start"], period["end"]
        )
        totals = actual_totals(scoped)
    complete = totals[f"{code}_complete"] and not warning["unallocated_period_count"]
    subtotal = totals[f"{code}_confirmed_subtotal"]
    reasons = []
    if not scoped:
        reasons.append("no_actual_evidence")
    elif not totals[f"{code}_complete"]:
        reasons.append("incomplete_actual_period")
    for key, reason in (
        ("missing_material_count", "missing_material"),
        ("tax_unknown_period_count", "tax_basis_unresolved"),
        ("conflict_period_count", "source_confirmation_required"),
    ):
        if totals[f"{code}_{key}"]:
            reasons.append(reason)
    if warning["unallocated_period_count"]:
        reasons.append("period_actual_not_allocated_to_selected_dates")
    return {
        "value": totals[f"{code}_amount"] if complete else None,
        "known_subtotal": subtotal,
        "coverage": "complete" if complete else "partial" if subtotal is not None else "unavailable",
        "complete": complete,
        "status": "ready" if complete else "missing" if not scoped else "incomplete",
        "reason": reasons[0] if reasons else None,
        "missing_reasons": reasons,
        "metric_code": code,
        "definition_version": metric["definition_version"],
        "unit": metric["unit"],
        "source_kind": "actual",
        "currency": "CNY",
        "tax_basis": "tax_exclusive" if code == "recognized" else "cash_collection_not_applicable",
        "data_as_of": as_of,
        "period_precision": warning,
        "source_refs": [
            {
                "kind": "effective_actual",
                "id": row["id"],
                "opportunity_id": row["opportunity_id"],
                "version_no": row.get("version_no"),
                "year": row["year"],
                "quarter": row["quarter"],
                "metric_code": code,
                "material_line_id": row.get("material_line_id"),
                "source": row.get("source"),
            }
            for row in scoped
        ],
    }


COUNT_METRICS = {"opportunity_count", "visit_count"}


def count_progress(cohort, *, metric, scope, period, as_of):
    """Count distinct, evidenced source records, retaining unknown coverage."""
    code = metric["code"]
    now = datetime.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)
    start, end = (date.fromisoformat(str(period[key])[:10]) for key in ("start", "end"))
    today = now.astimezone(SHANGHAI).date()
    subject_key = "team_id" if scope["scope_type"] == "team" else "user_id"
    seen, refs, reasons = {}, [], set()
    unknown_dates = unknown_attribution = eligible_records = 0
    if not cohort["scope_complete"]:
        reasons.add("source_scope_not_fully_authorized")
    for fact in cohort["items"]:
        identity = str(fact["id"])
        if identity in seen:
            if seen[identity] != fact:
                raise ValueError("计数来源同一记录存在冲突快照")
            continue
        seen[identity] = fact
        subject = fact.get(subject_key)
        if subject is not None and str(subject) != str(scope[subject_key]):
            continue
        if code == "opportunity_count":
            creation = fact["creation_date"]
            if not creation.get("historical") and not fact.get("native_creation"):
                business_date = None
                date_reason = (
                    "creation_provenance_permission_denied"
                    if fact.get("creation_history_readable") is False
                    else "unproven_import_creation_provenance"
                )
            else:
                business_date = business_created_on(creation)
                date_reason = "unknown_creation_date"
            event_time = None
            # Preserve exact source timestamps when supplied: a future time on
            # today's business date is still not actual progress as of now.
            if business_date is not None:
                raw = creation.get("created_at")
                if creation.get("historical"):
                    raw = creation.get("source_created")
                    if raw is None or (isinstance(raw, str) and not raw.strip()):
                        raw = creation.get("legacy_created")
                if isinstance(raw, datetime):
                    event_time = raw
                elif isinstance(raw, str):
                    try:
                        event_time = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
                    except ValueError:
                        # Accepted slash-format dates have day precision only.
                        event_time = None
        else:
            if fact.get("status") not in {"confirmed", "archived"}:
                continue
            raw = fact.get("interaction_at")
            event_time = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else raw
            if event_time is not None and event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=SHANGHAI)
            business_date = event_time.astimezone(SHANGHAI).date() if event_time else None
            date_reason = "unknown_interaction_at"
        if event_time is not None:
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=SHANGHAI)
            if event_time > now:
                continue
        if business_date is not None and (business_date < start or business_date > end or business_date > today):
            continue
        eligible_records += 1
        if business_date is None:
            unknown_dates += 1
            reasons.add(date_reason)
        if subject is None:
            unknown_attribution += 1
            reasons.add(
                "unknown_recorded_team_where_needed"
                if code == "visit_count" and subject_key == "team_id"
                else "unknown_attribution"
            )
        if business_date is None or subject is None:
            continue
        refs.append(
            {
                "kind": "opportunity" if code == "opportunity_count" else "visit",
                "id": identity,
                "version_no": fact["version_no"],
                "business_date": business_date.isoformat(),
            }
        )
    complete = not reasons
    subtotal = len(refs)
    return {
        "metric_code": code,
        "definition_version": metric["definition_version"],
        "unit": metric["unit"],
        "display_name": "团队记录的正式跟进数"
        if code == "visit_count" and scope["scope_type"] == "team"
        else metric["name"],
        "definition": metric["definition"],
        "source_kind": "actual",
        "date_basis": "business_created_on" if code == "opportunity_count" else "interaction_at",
        "attribution_basis": "current_opportunity_owner_and_team"
        if code == "opportunity_count"
        else "recorder_user_ref_id"
        if subject_key == "user_id"
        else "recorded_recorder_team_id",
        "period": period,
        "data_as_of": now.isoformat(),
        "value": subtotal if complete else None,
        "known_subtotal": subtotal,
        "coverage": "complete" if complete else "partial" if eligible_records else "unavailable",
        "complete": complete,
        "status": "ready" if complete else "incomplete" if eligible_records else "missing",
        "authorization_coverage": "full_scope" if cohort["scope_complete"] else "authorized_subset",
        "reason": sorted(reasons)[0] if reasons else None,
        "missing_reasons": sorted(reasons),
        "unknown_date_count": unknown_dates,
        "unknown_attribution_count": unknown_attribution,
        "source_refs": refs,
    }


async def read_key_results(connection, actor, *, key_result_id=None, objective_id=None, limit=50, offset=0):
    repo = ReviewOkrRepository()
    result = await repo.key_results(
        connection, key_result_id=key_result_id, objective_id=objective_id, limit=limit, offset=offset
    )
    now = await connection.fetchval("SELECT transaction_timestamp()")
    as_of = now.isoformat()
    forecasts, actuals, counts = {}, {}, {}
    for row in result["items"]:
        if row["progress_mode"] != "automatic":
            continue
        scope, period, metric = row["scope"], row["period"], row["metric"]
        code = metric["code"]
        if scope["scope_type"] not in {"person", "team"}:
            row["progress"] = missing_progress("department_projection_not_defined", metric=metric, as_of=as_of)
            continue
        subject = (scope["scope_type"], str(scope.get("user_id")), str(scope.get("team_id")))
        if code in FORECAST_METRICS:
            if period["type"] != "quarter":
                row["progress"] = missing_progress("forecast_requires_quarter_period", metric=metric, as_of=as_of)
                continue
            cache_key = (*subject, str(period["start"]), str(period["end"]))
            if cache_key not in forecasts:
                selection = ReviewSelection(
                    scope="team" if scope["scope_type"] == "team" else "member",
                    member_id=scope.get("user_id"),
                    team_id=scope.get("team_id"),
                    anchor_date=date.fromisoformat(str(period["start"])[:10]),
                    period_type="quarter",
                )
                try:
                    forecasts[cache_key] = await read_metric_projection(connection, actor, selection)
                except (WorkReportError, PermissionError) as exc:
                    if isinstance(exc, WorkReportError) and exc.code != "WORK_REPORT_SCOPE_CHANGED":
                        raise
                    forecasts[cache_key] = None
            projection = forecasts[cache_key]
            if projection is None:
                row["progress"] = missing_progress("forecast_source_permission_denied", metric=metric, as_of=as_of)
                continue
            card = next(item for item in projection["metrics"] if item["metric_code"] == code)
            linked = row["effective_target"]
            if linked is not None and (
                str(card["target"]["id"]) != str(linked["id"]) or card["target"]["value"] != linked["value"]
            ):
                row["progress"] = missing_progress("effective_target_identity_mismatch", metric=metric, as_of=as_of)
                continue
            # Forecast value/coverage/refs are the actual card output, not a parallel formula.
            # The KR retains its explicit target reference; an unlinked KR never adopts
            # a card denominator merely because a target exists for that scope.
            row["progress"] = {
                key: value for key, value in card.items() if key not in {"target", "rate", "gap", "status"}
            }
            row["progress"].update(
                source_kind="forecast",
                complete=card["coverage"] == "complete",
                status="ready" if card["coverage"] == "complete" else "incomplete",
                reason=card["missing_reasons"][0] if card["missing_reasons"] else None,
                data_as_of=projection["data_as_of"],
            )
        elif code in ACTUAL_METRICS:
            if subject not in actuals:
                actuals[subject] = await repo.actual_facts(
                    connection, scope=scope, as_of=now.astimezone(SHANGHAI).date()
                )
            facts = actuals[subject]
            row["progress"] = (
                missing_progress("actual_source_permission_denied", metric=metric, as_of=as_of)
                if facts is None
                else actual_progress(facts, metric=metric, period=period, as_of=as_of)
            )
        elif code in COUNT_METRICS:
            cache_key = (*subject, code)
            if cache_key not in counts:
                counts[cache_key] = await repo.count_facts(connection, scope=scope, metric_code=code)
            row["progress"] = count_progress(counts[cache_key], metric=metric, scope=scope, period=period, as_of=now)
        else:
            row["progress"] = missing_progress("automatic_metric_projection_not_defined", metric=metric, as_of=as_of)
    return result


async def _authorize_scope_write(connection, scope):
    allowed = await connection.fetchval(
        "SELECT COALESCE(security.target_scope_read($1,$2::uuid,$3::uuid,$4)"
        " AND security.target_scope_write($1,$2::uuid,$3::uuid,$4),false)",
        scope["scope_type"],
        scope.get("user_ref_id", scope.get("user_id")),
        scope.get("team_id"),
        scope["department_code"],
    )
    if not allowed:
        raise PermissionError("当前账号无权维护这个范围的目标")


async def authorize_objective_mutation(connection, *, body=None, entity_id=None):
    """Always runs before idempotency receipts; versions are intentionally excluded."""
    if entity_id is None:
        scope = body.model_dump()
    else:
        record = await connection.fetchrow(
            "SELECT scope_type,user_ref_id,team_id,department_code FROM crm.review_objective"
            " WHERE id=$1::uuid AND workspace_id=common.current_workspace_id()",
            entity_id,
        )
        if record is None:
            raise LookupError("记录不存在")
        scope = dict(record)
    await _authorize_scope_write(connection, scope)


async def authorize_key_result_mutation(connection, *, entity_id=None, objective_id=None, confirm_progress=False):
    """Reading a cached result cannot outlive its current O/KR authorization."""
    if entity_id is None:
        return await authorize_objective_mutation(connection, entity_id=objective_id)
    record = await connection.fetchrow(
        "SELECT o.scope_type,o.user_ref_id,o.team_id,o.department_code,"
        " (k.owner_user_id=common.current_user_ref_id() OR security.authorization_subject("
        " 'target.manage',o.scope_type,o.user_ref_id,o.team_id)) AS may_confirm"
        " FROM crm.review_key_result k JOIN crm.review_objective o"
        " ON o.id=k.objective_id AND o.workspace_id=k.workspace_id"
        " WHERE k.id=$1::uuid AND k.workspace_id=common.current_workspace_id()",
        entity_id,
    )
    if record is None:
        raise LookupError("记录不存在")
    scope = dict(record)
    await _authorize_scope_write(connection, scope)
    if confirm_progress and not scope["may_confirm"]:
        raise PermissionError("只有当前KR负责人或该范围目标维护者可确认手工进展")
