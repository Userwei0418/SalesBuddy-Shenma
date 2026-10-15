"""Generation views of the backend-owned optional sales-week facts.

No populations, totals, threshold events or business dates are inferred here.
Old snapshots omit the extension and retain their existing generation path.
"""

from sales_backend.domain.model_contract import ModelContractError


def section_references(request):
    weekly = getattr(request, "sales_week_facts", None)
    if weekly is None:
        return {}
    refs = {s.section_id: {"source_refs": set(s.source_refs), "metric_refs": set(s.metric_refs)}
            for s in weekly.sections}
    for supplement in request.supplements:
        section_id = getattr(supplement, "section_id", None)
        if section_id in refs:
            refs[section_id]["source_refs"].add(supplement.source_id)
    return refs


def validate_section_references(content, request, allowed_refs):
    refs = section_references(request)
    for section in content.sections:
        if section.id not in refs:
            continue  # Custom schemes keep their backend-selected structure.
        if section.id in {"ppl_first_30", "ppl_open_50"}:
            required = {ref for ref in refs[section.id]["source_refs"] & allowed_refs
                        if ref.startswith("opportunity:")}
            included = {ref for item in section.items for ref in item.source_refs}
            if not required <= included:
                raise ModelContractError("work_report.section_references.ppl_population_missing")
        for item in section.items:
            if not set(item.source_refs) <= refs[section.id]["source_refs"]:
                raise ModelContractError("work_report.section_references.weekly_source_wrong_section")
            if not set(item.metric_refs) <= refs[section.id]["metric_refs"]:
                raise ModelContractError("work_report.section_references.weekly_metric_wrong_section")
            if section.id in {"next_week_plan", "next_week_collection"} and item.kind not in {
                    "plan", "suggestion", "user_supplement"}:
                raise ModelContractError("work_report.section_references.weekly_future_kind")


def project_weekly(data, request, allowed_refs, expected_sections):
    """Project references, leaving frozen full-snapshot coverage unchanged.

The resulting prompt view is deliberately not a replacement SalesWeekFacts
snapshot. Missing batch references never change a population or progress state.
"""
    weekly = data.get("sales_week_facts")
    if weekly is None:
        return
    # V228 guards cover the full authorized population, including opportunities
    # absent from this report/batch; they must never become model material.
    weekly.pop("history_guards", None)
    refs = section_references(request)
    complete_metrics = {m.metric_id for m in request.metrics if m.complete and m.value is not None}
    selected = {s["id"] for s in expected_sections}
    constraints = {}
    for section in weekly["sections"]:
        section_id = section["section_id"]
        section["source_refs"] = sorted(refs[section_id]["source_refs"] & allowed_refs)
        section["metric_refs"] = sorted(refs[section_id]["metric_refs"] & complete_metrics)
        if section_id in selected:
            constraints[section_id] = {key: list(section[key]) for key in ("source_refs", "metric_refs")}
    weekly["opportunity_facts"] = [row for row in weekly["opportunity_facts"]
                                     if row["source_ref"] in allowed_refs]
    for row in weekly["opportunity_facts"]:
        # Private version/access dependencies are backend guards, not evidence
        # for model prose. first_ge30 remains the public threshold fact.
        row.pop("history_guard", None)
        for key in ("progress_refs", "next_action_refs"):
            row[key] = [ref for ref in row[key] if ref in allowed_refs]
    if weekly.get("finance"):
        weekly["finance"].pop("dependencies", None)
        weekly["finance"]["lines"] = [row for row in weekly["finance"]["lines"]
                                         if row["source_ref"] in allowed_refs]
    data["generation_constraints"]["section_references"] = constraints
    data["sales_week_projection"] = {
        "kind": "batch_reference_view",
        "coverage_basis": "full_frozen_snapshot",
        "omitted_references_mean": "outside_this_batch_not_absent",
    }
