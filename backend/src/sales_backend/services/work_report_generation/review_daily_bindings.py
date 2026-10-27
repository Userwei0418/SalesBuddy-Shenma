"""Snapshot-bound daily metric clauses, with a separate business prose channel.

Only protected metric clauses are deterministic. Free business interpretation
still needs semantic acceptance; this is not a universal prose truth checker.
"""

import re
from copy import deepcopy

from sales_backend.contracts.work_report_review import ReviewWireContent
from sales_backend.domain.work_report import canonical_hash

from . import review_fact_bindings as weekly
from .review_numbers import _without_trusted_labels, validate_scoped_numbers

TOKEN = re.compile(r"\{\{review_claim:([a-f0-9]{24})\}\}")
BUSINESS_KINDS = {"task", "activity", "opportunity"}
# This is a surplus-literal guard, not the binding mechanism or a semantic judge.
PROTECTED = re.compile(r"回款|确收|ppl|forecast_|recognized|生效目标|待审批目标|达成率|日变化|周变化|kr进度", re.I)
PROMPT_VERSION = "work_report.review_daily_claims.v1_20261015"


def _incomplete_impact(measure):
    reasons = set(measure.missing_reasons)
    if "no_baseline" in reasons:
        return (
            "当前没有可比较的昨日基线，不能判断该指标的升降或目标趋势；"
            "今天的优先级判断需暂缓，避免把未知变化当成风险或进展。"
        )
    if "missing_forecast_amount" in reasons:
        return (
            "当前预测金额不完整，目标差额和达成率不能作为可靠判断依据；"
            "相关目标判断应先按待补数据处理。"
        )
    if "unknown_eligibility" in reasons:
        return (
            "当前统计纳入条件尚未确认，汇总结果不能代表完整团队范围；"
            "在口径确认前不应据此比较团队表现或安排跟进。"
        )
    return "当前数据覆盖不完整，暂不将该指标用于趋势或目标判断；需先补齐缺失数据。"


def _incomplete_next(measure):
    reasons = set(measure.missing_reasons)
    if "no_baseline" in reasons:
        return "下一步：补齐并确认昨日定时快照或可信比较基线，再重新计算日变化；本条需人工确认。"
    if "missing_forecast_amount" in reasons:
        return "下一步：补齐相关商机的预测金额并确认金额口径，再重新计算目标差额和达成率；本条需人工确认。"
    if "unknown_eligibility" in reasons:
        return "下一步：确认统计纳入条件和团队范围，再重新计算汇总值；本条需人工确认。"
    return "下一步：补齐缺失数据后重新计算该指标；本条需人工确认。"


def enabled(facts, mode):
    return mode == "daily" and facts.contract_version == "work_report.review_facts.v2"


def catalog(facts, *, for_render=False):
    """Reuse the verified own-subject/role rules, without weekly packing limits."""
    sources = {row.source_ref: row for row in facts.evidence}
    measures = {row.measure_id: row for row in facts.measures}
    frozen_hash = canonical_hash(facts)
    result = {}
    for group in facts.groups:
        for key in group.measure_refs:
            measure = measures[key]
            refs = set(measure.source_refs)
            # Manual and task-linked KR metadata retains its own scope.
            refs.update(ref for ref in group.source_refs if sources[ref].kind == "key_result")
            isolated = group.model_copy(update={"measure_refs": [key], "source_refs": sorted(refs)})
            projection = weekly.catalog(facts.model_copy(update={"groups": [isolated]}),
                                        include_business_context=False)[0]
            rows = projection["progress"] if measure.coverage == "complete" else projection["risks"]
            if len(rows) != 1:
                raise ValueError("daily measure requires one indivisible frozen clause")
            base = {k: deepcopy(rows[0][k]) for k in ("text", "source_refs", "measure_refs")}
            for role in ("fact", "impact", "next"):
                clause = deepcopy(base)
                claim_clause = deepcopy(clause)
                if role == "impact":
                    if measure.coverage != "complete":
                        if for_render:
                            clause["text"] = "当前数据限制：" + _incomplete_impact(measure)
                        else:
                            clause["text"] = (
                                "当前数据限制：" + clause["text"]
                                + "因此不能判断相对昨日的上升或下降，也不能据此推断目标达成变化；"
                                "相关判断需保留不确定性。"
                            )
                    else:
                        clause["text"] = (
                            "业务影响：" + clause["text"]
                            + "该统计本身不能证明任务或商机的因果影响，需结合实际业务动作判断。"
                        )
                elif role == "next":
                    if measure.coverage != "complete":
                        clause["text"] = _incomplete_next(measure) if for_render else (
                            "下一步：先补齐并确认昨日定时快照或可信比较基线，再重新计算日变化；"
                            "本条需人工确认。"
                        )
                    else:
                        token = "{{metric:" + measure.measure_id + "}}"
                        clause["text"] = (
                            "下一步：以当前指标 " + token + " " + measure.unit
                            + " 为基准，结合今日已发生的任务、活动或商机动作核实影响，再决定是否需要跟进；"
                            "本条需人工确认。"
                        )
                # Keep the token stable between model choices and final rendering;
                # the latter may improve deterministic prose without changing the
                # model-facing selection contract.
                claim_id = canonical_hash({"snapshot": frozen_hash, "role": role, "clause": claim_clause})[:24]
                result[claim_id] = {"role": role, **clause}
    return result


def accepted_claims(summaries):
    """Scope to the supplied child subtree, preserving each statement role."""
    return {(section["id"].rsplit("_", 1)[-1], match.group(1))
            for draft in summaries for section in draft["sections"]
            for item in section["items"] for match in TOKEN.finditer(item["text"])}


def choices(facts, allowed_refs, accepted=None):
    return {key: {**row, "token": "{{review_claim:" + key + "}}"}
            for key, row in catalog(facts).items()
            if set(row["source_refs"]) <= allowed_refs
            and (accepted is None or (row["role"], key) in accepted)}


def render_wire(payload, facts, *, allowed_refs, accepted=None, text_limit=64000):
    """Decode whole-line anchors; never repair, append refs, or relabel prose."""
    wire = ReviewWireContent.model_validate(payload)
    index = catalog(facts, for_render=True)
    sources = {row.source_ref: row for row in facts.evidence}
    result = wire.model_dump(mode="json", by_alias=True)
    for section, rendered in zip(wire.sections, result["sections"], strict=True):
        role = section.id.rsplit("_", 1)[-1]
        for item, output in zip(section.items, rendered["items"], strict=True):
            if not set(item.source_refs) <= allowed_refs:
                raise ValueError("daily source outside current subtree")
            lines, clauses, prose, used = item.text.splitlines(), [], [], set()
            for line in lines:
                match = TOKEN.fullmatch(line)
                if match:
                    key = match.group(1)
                    if prose or key in used or key not in index:
                        raise ValueError("daily anchors must be distinct leading whole clauses")
                    claim = index[key]
                    if claim["role"] != role or (accepted is not None and (role, key) not in accepted):
                        raise ValueError("daily claim outside role or accepted subtree")
                    if not set(claim["source_refs"]) <= set(item.source_refs):
                        raise ValueError("daily claim lacks its own cited evidence")
                    used.add(key)
                    clauses.append(claim)
                else:
                    if "{{review_claim:" in line:
                        raise ValueError("daily claim cannot be embedded in free syntax")
                    prose.append(line)
            claim_refs = {ref for row in clauses for ref in row["source_refs"]}
            metric_refs = list(dict.fromkeys(ref for row in clauses for ref in row["measure_refs"]))
            if item.measure_refs:
                raise ValueError("daily wire metric references are owned by the claim renderer")
            remaining = set(item.source_refs) - claim_refs
            if any(ref not in sources or sources[ref].kind not in BUSINESS_KINDS for ref in remaining):
                raise ValueError("daily protected evidence requires a bound clause")
            business = "\n".join(prose).strip()
            if business:
                business_refs = [ref for ref in item.source_refs if sources[ref].kind in BUSINESS_KINDS]
                if not business_refs:
                    raise ValueError("daily business analysis requires business material")
                # Keep source-bound names/dates, not arbitrary quoted exemptions.
                free = item.model_copy(update={"text": business, "source_refs": business_refs, "measure_refs": []})
                checked = _without_trusted_labels(business, free, facts)
                # Explicit exact source quotations remain quotations. Their
                # content is never interpreted as a verified metric claim.
                for ref in business_refs:
                    data = weekly._source_data(sources[ref])
                    for key in ("summary_excerpt", "completion_note", "next_action"):
                        raw = data.get(key)
                        if isinstance(raw, str) and raw.strip():
                            checked = checked.replace("材料原文“" + raw.strip() + "”", "")
                if PROTECTED.search(checked):
                    raise ValueError("daily protected metric text requires a whole clause")
                validate_scoped_numbers(free, facts)
            elif remaining:
                raise ValueError("daily unused business references")
            if not clauses and not business:
                raise ValueError("empty daily statement")
            output["text"] = "\n".join([row["text"] for row in clauses] + ([business] if business else []))
            output["metric_refs"] = metric_refs
    if sum(len(item["text"]) for s in result["sections"] for item in s["items"]) > text_limit:
        raise ValueError("expanded daily text exceeds bounds")
    # Enforce per-statement bounds after expansion as well as before expansion.
    ReviewWireContent.model_validate(result)
    return result


def prove(content, payload, request):
    content._review_daily_binding = {"snapshot_hash": request.snapshot_hash,
        "facts_hash": canonical_hash(request.review_facts), "wire": deepcopy(payload),
        "content_hash": canonical_hash(content)}


def validate_generated(content, request):
    """Recompute the private witness; never trust a provider's approved flag."""
    from .review import combined_review_content

    proof = content._review_daily_binding
    if not isinstance(proof, dict) or proof.get("snapshot_hash") != request.snapshot_hash:
        raise ValueError("daily generation binding witness missing")
    if proof.get("facts_hash") != canonical_hash(request.review_facts):
        raise ValueError("daily witness belongs to other frozen facts")
    rebuilt = combined_review_content(proof["wire"], request)
    if canonical_hash(rebuilt) != canonical_hash(content) or proof.get("content_hash") != canonical_hash(content):
        raise ValueError("daily rendered content changed after binding")
