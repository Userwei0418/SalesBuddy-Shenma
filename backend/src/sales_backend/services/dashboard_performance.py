"""Six financial ranking metrics from current authorized quarterly facts."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

from sales_backend.domain.dashboard_actuals import actual_summary, distinct_actuals
from sales_backend.domain.materials import material_forecast_preview
from sales_backend.repositories import dashboard_metrics as metrics_repository
from sales_backend.repositories import dashboard_performance as repository
from sales_backend.repositories.dashboard_scope import DashboardSelection
from sales_backend.services.dashboard_metrics import direct_targets
from sales_backend.services.review_overview import contribution, metric_cards

METRICS = {
    "forecast_collection": ("季度预测回款", "collection", "latest_sales_forecast", "cash_collection_not_applicable"),
    "collection_ppl": ("季度回款 PPL 储备", "collection_ppl", "latest_sales_forecast",
                       "cash_collection_not_applicable"),
    "forecast_recognized_exclusive": ("季度预测不含税确收", None, "material_reference", "tax_exclusive"),
    "recognized_pipeline_exclusive": ("季度确收 Pipeline（不含税）", None, "material_reference", "tax_exclusive"),
    "actual_recognized_exclusive": ("季度实际不含税确收", "recognized", "adopted_actuals", "tax_exclusive"),
    "actual_collection": ("季度实际回款", "collection", "adopted_actuals", "cash_collection_not_applicable"),
}


def money(value):
    return None if value is None else format(value, ".2f")


def material_contribution(row, lines, relation, *, year, quarter, threshold):
    if row["status"] not in {"open", "won"}:
        return False, None, None
    probability = row.get("probability")
    if probability is None:
        return None, None, "unknown_eligibility"
    if Decimal(str(probability)) < threshold:
        return False, None, None
    if row.get("currency") != "CNY":
        return True, None, "currency_unavailable"
    if not lines:
        return True, None, "material_lines_missing"
    if relation not in {"direct", "distributor", "downstream"}:
        return True, None, "material_trade_relation_missing"
    try:
        prepared = [repository.material_line(line, relation) for line in lines]
        relevant = [line for line in prepared if not (line.get("usage") == "resale" and relation == "distributor")]
        if not relevant:
            return False, None, None
        for line in relevant:
            snapshot = line["catalog_snapshot"]
            if not isinstance(snapshot, dict):
                return True, None, "material_reference_invalid"
            if snapshot.get("tax_rate") is None:
                return True, None, "material_tax_rate_missing"
            rate = Decimal(str(snapshot["tax_rate"]))
            if not rate.is_finite() or not Decimal(0) <= rate <= Decimal(1):
                return True, None, "material_tax_rate_invalid"
            if snapshot.get("currency") != "CNY":
                return True, None, "currency_unavailable"
        with localcontext() as context:
            context.prec = 80
            result = material_forecast_preview(relevant, year, quarter)
        if all(line["status"] == "excluded" for line in result["lines"]):
            # Distributor resale belongs to downstream opportunities, never a
            # missing forecast or a second zero-value contribution here.
            return False, None, None
        if result["status"] != "complete" or result["recognized_exclusive"] is None:
            missing = result.get("missing_fields", [])
            reason = "material_manual_input_required" if any("manual_sales_input" in key for key in missing) else (
                "material_schedule_missing" if any(any(part in key for part in (
                    "expected_delivery", "expected_start", "expected_oa", "service_months", "expected_consumption",
                    "monthly_allocation", "oa_cutoff", "oa_deferral")) for key in missing)
                else "material_reference_incomplete")
            return True, None, reason
        value = Decimal(result["recognized_exclusive"])
        if not value.is_finite() or value < 0:
            return True, None, "material_reference_invalid"
        return True, value, None
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        # Only malformed deterministic source inputs are contained. Database,
        # authorization and transport failures remain errors, not missing data.
        return True, None, "material_reference_invalid"


def reference_summary(rows, results, *, complete):
    known, missing, unknown, reasons = [], 0, 0, set()
    for row in rows:
        eligible, value, reason = results[row["id"]]
        if eligible is False:
            continue
        unknown += int(eligible is None)
        missing += int(eligible is True and value is None)
        if value is not None:
            known.append(value)
        if reason:
            reasons.add(reason)
    if not complete:
        reasons.add("source_scope_not_fully_authorized")
    complete = complete and not (missing or unknown)
    with localcontext() as context:
        context.prec = 80
        subtotal = sum(known, Decimal(0)) if known or complete else None
    return dict(value=money(subtotal) if complete else None, known_subtotal=money(subtotal),
                coverage="complete" if complete else "partial" if known else "unavailable",
                missing_amount_count=missing, unknown_eligibility_count=unknown, missing_reasons=sorted(reasons))


def target_for(subject, targets, kind):
    if kind is None:
        return dict(kind="not_defined", id=None, version_no=None, value=None, known_subtotal=None,
                    status="not_configured", reason="metric_target_not_defined", coverage="unavailable",
                    aggregation="direct_effective_target", components=[])
    item = targets.get((subject["kind"], subject["id"]))
    return direct_targets({kind: item} if item else {}, subject["target_allowed"])[kind]


def subject_row(subject, rows, metric, target, *, year, quarter, material_results):
    complete = subject["source_complete"]
    if METRICS[metric][2] == "adopted_actuals":
        summary = actual_summary(rows, metric, scope_complete=complete)
    elif METRICS[metric][2] == "material_reference":
        summary = reference_summary(rows, material_results, complete=complete)
    else:
        summary = next(card for card in metric_cards(rows, {}, False, year=year, quarter=quarter,
                                                    scope_complete=complete) if card["metric_code"] == metric)
    value = summary["value"] if summary["value"] is not None else summary["known_subtotal"]
    denominator = target["value"] if target["status"] == "effective" else None
    with localcontext() as context:
        context.prec = 80
        rate = (format(Decimal(value) / Decimal(denominator), ".8f")
                if value is not None and denominator is not None and Decimal(denominator) > 0 else None)
    return dict(id=subject["id"], name=subject["name"], team_id=subject.get("team_id"),
                team_name=subject.get("team_name"),
                **{key: summary[key] for key in ("value", "known_subtotal", "coverage", "missing_amount_count",
                                                 "unknown_eligibility_count", "missing_reasons")},
                target=target, rate=rate, rank=None, rank_basis="amount", provisional=summary["coverage"] != "complete")


def ranked(rows, *, basis):
    def value(row):
        raw = row["value"] if row["value"] is not None else row["known_subtotal"]
        if basis == "rate":
            if row["rate"] is None:
                return None
            with localcontext() as context:
                context.prec = 80
                return Decimal(raw) / Decimal(row["target"]["value"])
        return Decimal(raw) if raw is not None else None

    rows.sort(key=lambda row: (value(row) is None, -(value(row) or Decimal(0)), row["id"]))
    previous, rank = None, None
    provisional = any(row["coverage"] != "complete" for row in rows)
    for position, row in enumerate(rows, 1):
        number = value(row)
        if number is not None and (previous is None or number != previous):
            rank = position
        row.update(rank=rank if number is not None else None, rank_basis=basis,
                   provisional=row["provisional"] or (provisional and number is not None))
        previous = number
    return rows


def coverage(rows):
    if rows and all(row["coverage"] == "complete" for row in rows):
        return "complete"
    return "partial" if any(row["known_subtotal"] is not None for row in rows) else "unavailable"


async def rankings(connection, actor, *, year, quarter, metric_code):
    if metric_code not in METRICS:
        raise ValueError("Unknown performance ranking metric")
    allowed = await connection.fetchval(
        "SELECT security.authorization_has('dashboard.ranking') AND security.authorization_has('dashboard.read')")
    if not allowed:
        raise PermissionError("当前账号未获查看经营业绩排名的授权")
    now = await connection.fetchval("SELECT transaction_timestamp()")
    label, target_kind, basis, tax = METRICS[metric_code]
    actual = basis == "adopted_actuals"
    authorized_subjects = await repository.cohort(connection, actor, actual=actual)
    role_excluded_people = {s["id"] for s in authorized_subjects
                            if s["kind"] == "person" and not s["participates"]}
    subjects = [s for s in authorized_subjects if s["participates"]]
    targets = await repository.targets(connection, subjects,
                                       start=date(year, (quarter - 1) * 3 + 1, 1), kind=target_kind)
    selected = DashboardSelection(False, actor.user_id, permission="dashboard.read")
    read = metrics_repository.actual_rows if actual else metrics_repository.opportunity_rows
    rows = await read(connection, actor, selected, year=year, quarter=quarter)
    ranked_rows = await repository.ranked_facts(connection, rows, actual=actual)
    ranked_ids = {r["id"] for r in ranked_rows}
    withheld_subjects = set()
    for row in rows:
        if row["id"] not in ranked_ids and (not actual or row["kind"] == target_kind):
            withheld_subjects.update((("person", row.get("owner_id")), ("team", row.get("team_id"))))
    for subject in subjects:
        if (subject["kind"], subject["id"]) in withheld_subjects:
            # An object-specific ranking denial must not become an apparently
            # complete zero. Do not disclose the denied objects or their count.
            subject["source_complete"] = False
    rows = ranked_rows
    if actual:
        rows = distinct_actuals([row for row in rows if row["kind"] == target_kind])
    else:
        distinct = {}
        for row in rows:
            if row["id"] in distinct and distinct[row["id"]] != row:
                raise RuntimeError("Conflicting quarterly ranking facts")
            distinct[row["id"]] = row
        rows = list(distinct.values())
    material_results = {}
    if basis == "material_reference":
        threshold = 70 if metric_code == "forecast_recognized_exclusive" else 30
        eligible = [r["id"] for r in rows if r.get("probability") is not None
                    and Decimal(str(r["probability"])) >= threshold]
        relations, lines = await repository.material_rows(connection, eligible)
        material_results = {r["id"]: material_contribution(r, lines.get(r["id"], []), relations.get(r["id"]),
                            year=year, quarter=quarter, threshold=threshold) for r in rows}
    by_person, by_team = defaultdict(list), defaultdict(list)
    unknown_attribution = set()
    people_ids = {s["id"] for s in subjects if s["kind"] == "person"}
    known_people_ids = people_ids | role_excluded_people
    team_ids = {s["id"] for s in subjects if s["kind"] == "team"}
    person_excluded, team_excluded = 0, 0
    for row in rows:
        included = actual or (material_results[row["id"]][0] is not False if basis == "material_reference"
                              else contribution(row, metric_code)[0] is not False)
        if not included:
            continue
        if actual and (row.get("attribution_status") == "unresolved" or row.get("_attribution_excluded")):
            unknown_attribution.add((row.get("opportunity_id") or row["id"], row["year"], row["quarter"]))
            continue
        # Known, authorized nonparticipants are a ranking rule, not missing
        # attribution. Their facts still contribute to the department bucket.
        person_excluded += int(row.get("owner_id") not in known_people_ids)
        team_excluded += int(row.get("team_id") not in team_ids)
        by_person[row.get("owner_id")].append(row)
        by_team[row.get("team_id")].append(row)
    departments, people = [], []
    for subject in subjects:
        bucket = by_team if subject["kind"] == "team" else by_person
        row = subject_row(subject, bucket[subject["id"]], metric_code, target_for(subject, targets, target_kind),
                          year=year, quarter=quarter, material_results=material_results)
        (departments if subject["kind"] == "team" else people).append(row)
    sort_basis = "rate" if any(row["rate"] is not None for row in departments) else "amount"
    if unknown_attribution:
        for row in departments + people:
            row["provisional"] = True
    department_coverage, people_coverage = coverage(departments), coverage(people)
    if unknown_attribution:
        department_coverage = "partial" if department_coverage == "complete" else department_coverage
        people_coverage = "partial" if people_coverage == "complete" else people_coverage
    return dict(contract_version="dashboard.performance_rankings.v1", data_as_of=now.isoformat(),
                year=year, quarter=quarter, metric_code=metric_code, label=label, unit="元", currency="CNY",
                tax_basis=tax, basis=basis, departments=ranked(departments, basis=sort_basis),
                people=ranked(people, basis="amount"), department_sort_basis=sort_basis,
                department_sort_reason="comparable_targets" if sort_basis == "rate" else "no_comparable_targets",
                departments_coverage=department_coverage, people_coverage=people_coverage,
                cohort=dict(scope="current_authorized_ranking_scope", departments_count=len(departments),
                            people_count=len(people)), own_user_id=actor.user_id,
                attribution_unknown_period_count=len(unknown_attribution), definitions=[
                    "仅当前公司授权范围；部门为有效叶子团队。个人仅有效销售及销售主管参榜，兼任有效销售总经理、FDE、FDE主管或运营不参榜。当前页面选人或选团队不缩窄榜单。",
                    "预测按商机当前负责人和团队；实绩沿用已采用财务事实的归属，未知归属不分配给现负责人。",
                    "榜单不代表公司总额；按角色不参个人榜的已授权事实仍计入部门汇总，不作为缺项或未匹配人员提示；离职人员、无有效部门等未纳入条数分别说明。",
                    "实绩归属待核对的期间不参与个人或部门排名，存在此类事实时排名为暂列；该数量与未纳入条数不相加。",
                    "源金额按元保存，展示单位以表头为准；缺失不补零，已知小计对应暂定排名；并列同名次。",
                    "部门优先按可比较目标达成率排名；整榜无可比较目标时按金额排名。个人按金额排名。",
                    "净额预测为物料参考：阶段分别≥70%和≥30%，按冻结税率逐行计算到分后汇总，不代替销售预测或实际。"
                    if basis == "material_reference" else
                    "预测复用原季度监控资格且不按概率加权；实绩只汇总当前采用的季度记录。",
                ], department_excluded_fact_count=team_excluded, people_excluded_fact_count=person_excluded)
