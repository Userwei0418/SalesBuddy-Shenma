"""Use the backend's report schema and add generation-only provenance checks."""

from collections.abc import Iterable
from copy import deepcopy

from pydantic import ValidationError

from sales_backend.contracts.work_report import METRIC_TOKEN, GenerationInput, ReportContent, ReportItem, ReportSection
from sales_backend.contracts.work_report_overview import overview_missing_reasons
from sales_backend.domain.model_contract import ModelContractError
from sales_backend.domain.work_report import WorkReportError, validate_content
from sales_backend.services.agent_platform.routing import InvalidAgentResult

from .weekly import section_references
from .weekly import validate_section_references as _validate_weekly_references


def sections_for(request: GenerationInput) -> list[ReportSection]:
    if request.rewrite:
        selected = set(request.rewrite.section_ids)
        return [ReportSection(id=s.id, title=s.title) for s in request.rewrite.previous_content.sections
                if s.id in selected]
    return [ReportSection(id=s.id, title=s.title) for s in request.scheme.config.sections if s.enabled]


def overview_section(request):
    """Keep the destination stable when only another section is rewritten."""
    if request.sales_week_facts is not None:
        return "summary"
    sections = [section.id for section in request.scheme.config.sections if section.enabled]
    return "overview" if "overview" in sections else sections[0]


def overview_references(request):
    overview = getattr(request, "overview_facts", None)
    return set(overview.references.values()) if overview else set()


def _overview_view(request, allowed):
    """A reference view, not a replacement for the complete frozen facts.

    Aggregates and member breakdowns are retained deterministically at the end;
    they are not copied into every model batch or recomputed from a subset.
    """
    overview = request.overview_facts
    refs = {key: value for key, value in overview.references.items() if value in allowed}
    facts = overview.facts.model_dump(mode="json")
    view = {key: deepcopy(facts[key]) for key in ("contract_version", "period", "data_as_of", "quarter", "scope")}
    view["sources"] = [source for source in facts["sources"] if source["source_id"] in refs]

    def mapped(value):
        if isinstance(value, dict):
            return {key: ([refs[ref] for ref in child if ref in refs] if key == "source_refs" else mapped(child))
                    for key, child in value.items()}
        if isinstance(value, list):
            # A separately cited nested reason outside this batch must not lose
            # all attribution while keeping its prose in the model input.
            return [mapped(child) for child in value if not isinstance(child, dict)
                    or not child.get("source_refs") or set(child["source_refs"]) & refs.keys()]
        return value

    for key in ("opportunity_changes", "new_claimed_customers", "formal_visits", "new_opportunities",
                "lost_opportunities", "next_steps"):
        view[key] = [mapped(row) for row in facts[key] if set(row["source_refs"]) & refs.keys()]
    return {"facts": view, "references": refs}


def overview_material(request):
    if not getattr(request, "overview_facts", None) or overview_section(request) not in {
        section.id for section in sections_for(request)
    }:
        return []
    # Internal batch records, never FactSource rows or source coverage counts.
    return [{"source_id": ref, "overview_fragment": _overview_view(request, {ref})}
            for ref in sorted(overview_references(request))]


def project_overview(data, request, allowed_refs, expected_sections, *, phase):
    allowed = allowed_refs & overview_references(request)
    target = overview_section(request)
    if not allowed or target not in {section["id"] for section in expected_sections}:
        data.pop("overview_facts", None)
        return
    data["overview_facts"] = _overview_view(request, allowed)
    if phase == "combine":
        # Validated summaries carry prose at merge time, as for ordinary sources.
        # The full immutable facts are used only by the final deterministic pass.
        facts = data["overview_facts"]["facts"]
        for key in ("opportunity_changes", "new_claimed_customers", "formal_visits", "new_opportunities",
                    "lost_opportunities", "next_steps"):
            facts.pop(key, None)
    data["overview_projection"] = {
        "kind": "batch_reference_view", "coverage_basis": "full_frozen_snapshot",
        "omitted_references_mean": "outside_this_batch_not_absent",
        "aggregates_and_member_breakdowns": "retained_once_by_backend_after_generation",
    }
    constraints = data["generation_constraints"]
    constraints["overview_reference_section"] = target
    constraints["rules"].append(
        f"overview:引用只能放在章节ID={target}；其进展、风险和下一步也在该节内按kind分条，"
        "不能按其他章节标题移章、替换来源或删引用。概览预测变化不能改写已确认实收、首次30或当前50口径。"
    )
    references = constraints.setdefault("section_references", {})
    if request.sales_week_facts is None:
        # General/custom reports had only the global allowlist. Mirror the
        # existing guard: ordinary sources/metrics stay available to each
        # selected section, while overview refs belong to the frozen target.
        ordinary = sorted(allowed_refs - overview_references(request))
        metrics = sorted(constraints["metric_tokens"])
        for section in expected_sections:
            references[section["id"]] = {"source_refs": list(ordinary), "metric_refs": list(metrics)}
    target_refs = references.get(target)
    if target_refs is not None:
        target_refs["source_refs"] = sorted(set(target_refs["source_refs"]) | allowed)


def validate_section_references(content, request, allowed_refs):
    """Add one explicit namespace without loosening the original weekly guard."""
    frozen = overview_references(request)
    overview = getattr(request, "overview_facts", None)
    planned = ({overview.references[ref] for step in overview.facts.next_steps for ref in step.source_refs}
               if overview else set())
    target = overview_section(request)
    ordinary = content.model_copy(deep=True)
    for section in ordinary.sections:
        for item in section.items:
            refs = {ref for ref in item.source_refs if ref.startswith("overview:")}
            if refs and section.id != target:
                raise ModelContractError("work_report.section_references.overview_wrong_section")
            if refs and not refs <= frozen & allowed_refs:
                raise ModelContractError("work_report.section_references.overview_outside_batch")
            if item.kind == "fact" and refs & planned:
                raise ModelContractError("work_report.section_references.overview_planned_as_fact")
            item.source_refs = [ref for ref in item.source_refs if ref not in refs]
    _validate_weekly_references(ordinary, request, allowed_refs - frozen)


def known_gaps(request: GenerationInput) -> list[str]:
    reasons = [reason for coverage in request.coverage for reason in coverage.missing_reasons]
    weekly = getattr(request, "sales_week_facts", None)
    if weekly is not None:
        reasons.extend(reason for section in weekly.sections for reason in section.missing_reasons)
    reasons.extend(overview_missing_reasons(getattr(request, "overview_facts", None)))
    if request.review_facts is not None:
        reasons.extend(request.review_facts.missing_data)
    return list(dict.fromkeys(reasons))


def metric_fragment(metric) -> str:
    token = "{{metric:" + metric.metric_id + "}}"
    return token if metric.unit in metric.display_value else token + " " + metric.unit


# Deliberately horizontal only: line/paragraph boundaries are not formatting aliases.
_HORIZONTAL_SPACE = (
    " \t\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u202f\u205f\u3000"
)


def _missing_unit_boundary(text, start):
    """Only clear sentence/list punctuation or end can omit a trusted unit."""
    if start == len(text):
        return True
    if text[start] not in "。！？!?；;，,、":
        return False  # Decimal points, colons, closing wrappers and prose stay ambiguous.
    return not (text[start] in ",，" and text[start + 1:start + 2].isdigit())


def _normalize_metric_fragments(text, metrics):
    """Use the backend unit for clear omissions; never infer or convert a unit."""
    parts, cursor = [], 0
    for match in METRIC_TOKEN.finditer(text):
        metric = metrics[match.group(1)]  # validate_content already checked every reference.
        fragment = metric_fragment(metric)
        if fragment == match.group():  # display_value already supplies the unit.
            continue
        start = match.end()
        while start < len(text) and text[start] in _HORIZONTAL_SPACE:
            start += 1
        if text.startswith(metric.unit, start):
            end = start + len(metric.unit)
        elif _missing_unit_boundary(text, start):
            end = start  # Keep the punctuation and following text byte-for-byte.
        else:
            continue  # The unchanged unit guard below rejects this occurrence.
        parts.extend((text[cursor:match.start()], fragment))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _wrapped_exact_unit(tail, unit, left, right):
    if not tail.startswith(left):
        return False
    inner = tail[len(left):].lstrip(_HORIZONTAL_SPACE)
    return inner.startswith(unit) and inner[len(unit):].lstrip(_HORIZONTAL_SPACE).startswith(right)


def _metric_unit_failure_code(text, match, metric):
    """Classify only the already-rejected local syntax; never return model text.

    The display-includes-unit branch has a token-only fragment and cannot fail
    the caller's prefix guard. These codes therefore concern an external unit.
    """
    tail = text[match.end():].lstrip(_HORIZONTAL_SPACE)
    if not tail:
        return "work_report.metric_unit.missing_end"
    if METRIC_TOKEN.match(tail):
        return "work_report.metric_unit.missing_before_metric"
    if tail[0] in "\r\n\v\f\x85\u2028\u2029":
        return "work_report.metric_unit.nonhorizontal_separator"
    if any(_wrapped_exact_unit(tail, metric.unit, left, right)
           for left, right in (("(", ")"), ("（", "）"), ("[", "]"), ("【", "】"))):
        return "work_report.metric_unit.wrapped_exact_unit"
    if any(_wrapped_exact_unit(tail, metric.unit, mark, mark) for mark in ("**", "__", "*", "_", "`")):
        return "work_report.metric_unit.markdown_exact_unit"
    if tail[0] in ",，.。;；:：!！?？、)]）】》”’":
        return "work_report.metric_unit.missing_before_punctuation"
    if tail[0] in "+-0123456789":
        return "work_report.metric_unit.numeric_prefix"
    if any(tail.startswith(scale + metric.unit) for scale in ("十", "百", "千", "万", "亿", "十万", "百万", "千万")):
        return "work_report.metric_unit.scale_prefix"
    return "work_report.metric_unit.mismatched_suffix"



_SCHEMA_ERROR_TYPES = frozenset({
    "missing", "extra_forbidden", "literal_error", "string_type", "string_too_long",
    "string_too_short", "list_type", "too_long", "too_short", "value_error",
    "string_pattern_mismatch", "model_type", "dict_type",
})
_SCHEMA_ERROR_FIELDS = frozenset({
    "title", "sections", "id", "items", "kind", "text", "source_refs", "metric_refs", "missing_data",
})


def _safe_schema_code(error):
    """Finite labels only: no model text, values, messages, arbitrary keys or IDs."""
    errors = error.errors(include_input=False, include_context=False, include_url=False)
    first = errors[0] if errors else {}
    kind = first.get("type")
    kind = kind if kind in _SCHEMA_ERROR_TYPES else "other"
    parts = [("item" if isinstance(part, int) else part if part in _SCHEMA_ERROR_FIELDS else "other")
             for part in first.get("loc", ())[:8]]
    return "work_report.content_schema." + kind + "." + (".".join(parts) or "root")


def model_output_schema():
    """The backend-only extension is never part of the model output contract."""
    schema = ReportContent.model_json_schema()
    schema["properties"].pop("review_content", None)
    schema["$defs"]["ReportItem"]["properties"].pop("source_notes")
    schema["$defs"].pop("ReportSourceNote")
    # Omitted backend-only extensions must not bloat or alter the legacy wire schema.
    definitions = schema["$defs"]
    used = set()

    def visit(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                if name not in used:
                    used.add(name)
                    visit(definitions[name])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit({key: value for key, value in schema.items() if key != "$defs"})
    schema["$defs"] = {key: value for key, value in definitions.items() if key in used}
    return schema


def validate_model_content(payload, request: GenerationInput, *, allowed_refs: set[str], text_limit=64000):
    """This checks schema and attribution, not the truth of arbitrary prose."""
    code = "work_report.content_schema"
    try:
        if isinstance(payload, dict) and any(
            isinstance(item, dict) and "source_notes" in item
            for section in (payload.get("sections") if isinstance(payload.get("sections"), list) else [])
            if isinstance(section, dict)
            for item in (section.get("items") if isinstance(section.get("items"), list) else [])
        ):
            code = "work_report.model_source_notes_forbidden"
            raise ValueError("models cannot supply backend source notes, including empty notes")
        content = ReportContent.model_validate(payload).model_copy(deep=True)
        if any(item.source_notes for section in content.sections for item in section.items):
            code = "work_report.model_source_notes_forbidden"
            raise ValueError("models cannot supply backend source notes")
        code = "work_report.content_rules"
        validate_content(content, request, generated=True)
        code = "work_report.section_references"
        validate_section_references(content, request, allowed_refs)
        code = "work_report.coverage_gaps"
        if content.missing_data != known_gaps(request):
            raise ValueError("generated gaps must match the backend-owned coverage gaps")
        items = [item for section in content.sections for item in section.items]
        code = "work_report.empty_or_oversized"
        if not items or sum(len(item.text) for item in items) > text_limit:
            raise ValueError("empty or oversized generated content")
        code = "work_report.missing_source_refs"
        if allowed_refs and not any(item.source_refs for item in items):
            raise ValueError("material batch cannot be replaced with only global statistics")
        metrics = {metric.metric_id: metric for metric in request.metrics}
        for item in items:
            code = "work_report.metric_unit"
            item.text = _normalize_metric_fragments(item.text, metrics)
            for match in METRIC_TOKEN.finditer(item.text):
                metric = metrics[match.group(1)]
                if not item.text.startswith(metric_fragment(metric), match.start()):
                    code = _metric_unit_failure_code(item.text, match, metric)
                    raise ValueError("metric fragment must retain its declared unit")
            code = "work_report.source_outside_batch"
            if not set(item.source_refs) <= allowed_refs:
                raise ValueError("reference is outside this material batch")
            code = "work_report.user_source_kind"
            if any(ref.startswith("user:") for ref in item.source_refs) and item.kind != "user_supplement":
                raise ValueError("user material must remain explicitly attributed")
            code = "work_report.planned_metric_as_fact"
            if item.kind == "fact" and any(
                metrics[ref].period_basis == "planned_in_period" for ref in item.metric_refs
            ):
                raise ValueError("planned statistics cannot become accomplished facts")
        # Inserting the canonical unit/separator can increase length. Revalidate the
        # complete schema and per-batch limit; never truncate or bypass bounds.
        code = "work_report.content_schema"
        content = ReportContent.model_validate(content.model_dump(mode="python"))
        code = "work_report.empty_or_oversized"
        if sum(len(item.text) for section in content.sections for item in section.items) > text_limit:
            raise ValueError("oversized normalized content")
        return content.model_dump(mode="json")
    except (ValidationError, WorkReportError, ValueError) as exc:
        if isinstance(exc, ValidationError) and code == "work_report.content_schema":
            code = _safe_schema_code(exc)
        if isinstance(exc, ModelContractError):
            code = exc.contract_code  # Only the guard's fixed code, never prose or IDs.
        # Reuse the router's fixed-code projection; never retain the original
        # model prose, validation exception or input in the explicit cause.
        raise InvalidAgentResult("work_report.invalid_content") from ModelContractError(code)


def _literal(text: str) -> str:
    # Original material is text, not a generated metric interpolation command.
    return text.replace("{{", "｛｛").replace("}}", "｝｝")


def basic_summary(request: GenerationInput) -> ReportContent:
    """A labelled deterministic material list; no AI success or inferred outcome.

    All material remains in the backend snapshot. This bounded display explicitly
    records excerpts and omissions instead of manufacturing a complete narrative.
    """
    gaps = known_gaps(request)
    gaps.append("当前为基础汇总，未完成 AI 报告生成；材料与用户补充保留在原快照中。")
    sections = sections_for(request)
    title = (request.rewrite.previous_content.title if request.rewrite
             else ("基础汇总 · " + request.period.label)[:240])

    def entries() -> Iterable[ReportItem]:
        for metric in request.metrics:
            if metric.complete and metric.value is not None:
                basis = {"occurred_in_period": "本期已发生", "planned_in_period": "本期计划",
                         "current_at_cutoff": "截止时点状态", "current_at_snapshot": "取数时当前状态"}[
                             metric.period_basis]
                yield ReportItem(
                    kind="plan" if metric.period_basis == "planned_in_period" else "fact",
                    text=f"{basis} · {_literal(metric.label)}：{{{{metric:{metric.metric_id}}}}}"
                         f"（{_literal(metric.unit)}）。",
                    metric_refs=[metric.metric_id],
                )
        for source in request.sources:
            label = {"occurred": "已记录事实", "current": "取数时当前状态", "planned": "计划材料"}[source.state_kind]
            raw = f"{label} · {source.title}：{source.text}"
            if len(raw) > 600:
                gaps.append("基础汇总对长原文使用节选；完整原文保留在材料快照中。")
            yield ReportItem(kind="plan" if source.state_kind == "planned" else "fact",
                             text=_literal(raw[:600]), source_refs=[source.source_id])
        for supplement in request.supplements:
            label = {"completed": "用户声明已完成", "context": "用户补充背景", "plan": "用户补充计划"}[
                supplement.declared_kind]
            if getattr(request, "sales_week_facts", None) is not None:
                label += "（用户补充，尚未核实）"
            if len(supplement.text) > 600:
                gaps.append("基础汇总对长补充使用节选；完整补充保留在材料快照中。")
            yield ReportItem(kind="user_supplement", text=f"{label}：{_literal(supplement.text[:600])}",
                             source_refs=[supplement.source_id])

    # Sales-week assignment uses only backend-frozen references. Legacy/custom
    # schemes keep a labelled list; no model or keyword inference chooses slots.
    refs = section_references(request)
    mapped = bool(refs) and all(section.id in refs for section in sections)
    chars = count = 0
    for item in entries():
        targets = [section for section in sections
                   if set(item.source_refs) <= refs[section.id]["source_refs"]
                   and set(item.metric_refs) <= refs[section.id]["metric_refs"]] if mapped else sections[:1]
        if not targets:
            gaps.append("部分材料未配置到本次启用章节，未在基础汇总中展示；原材料仍保留在快照中。")
            continue
        if count + len(targets) > 190 or chars + len(item.text) * len(targets) > 60000:
            gaps.append("基础汇总未逐条展示全部材料；全部来源、统计与补充仍保留在原快照中。")
            break
        for section in targets:
            section.items.append(item.model_copy(deep=True))
        chars += len(item.text) * len(targets)
        count += len(targets)
    content = ReportContent(title=title, sections=sections, missing_data=list(dict.fromkeys(gaps)))
    validate_content(content, request, generated=True)
    return content
