"""Regression boundaries for multi-team quantitative prose; no live model."""

import json
from copy import deepcopy

import pytest

from sales_backend.contracts.work_report_review import ReviewFactsV2, ReviewWireItem
from sales_backend.domain.work_report import canonical_hash
from sales_backend.services.agent_platform.routing import InvalidAgentResult
from sales_backend.services.work_report_generation.review import (
    review_wire_sections,
    validate_review_content,
    validate_review_wire,
)
from sales_backend.services.work_report_generation.review_numbers import (
    numeric_projection,
    protected_metric_sentences,
    validate_scoped_numbers,
)
from tests.test_work_report_review import facts_data


def multi_facts():
    data = facts_data()
    subjects = [
        {"scope_type": "team", "id": "01000000-0000-0000-0000-000000000001", "label": "东区"},
        {"scope_type": "team", "id": "01000000-0000-0000-0000-000000000002", "label": "北区"},
    ]
    data["contract_version"] = "work_report.review_facts.v2"
    data["scope"] = dict(
        kind="team", label="东区、北区", subject_ids=[s["id"] for s in subjects], subjects=subjects,
        resolved_member_ids=[], scope_key="review-scope:v2:" + "a" * 64, scope_revision="b" * 64,
    )
    source, measure = data["evidence"][0], data["measures"][0]
    data["evidence"], data["measures"] = [], []
    for index, subject in enumerate(subjects):
        ref = f"review:team{index}"
        namespace = canonical_hash({"scope_type": "team", "record_id": subject["id"]})[:16]
        data["evidence"].append({**source, "source_ref": ref})
        data["measures"].append({
            **measure, "measure_id": f"scope_{namespace}_forecast.current", "source_refs": [ref],
            "value": None if index == 0 else "0.00", "coverage": "unavailable" if index == 0 else "complete",
            "missing_reasons": ["unknown_eligibility"] if index == 0 else [],
        })
    data["groups"][0].update(
        source_refs=[s["source_ref"] for s in data["evidence"]],
        measure_refs=[s["measure_id"] for s in data["measures"]],
    )
    data["missing_data"] = ["unknown_eligibility"]
    return ReviewFactsV2.model_validate(data)


def numeric_item(facts):
    key, text = next(iter(protected_metric_sentences(facts).items()))
    return ReviewWireItem(kind="summary", text=text, source_refs=["review:team1"], metric_refs=[key])


def test_only_known_subject_gets_a_protected_sentence_and_no_selection_total():
    facts = multi_facts()
    before = facts.model_dump(mode="json")
    policy = numeric_projection(facts)
    assert len(policy["measure_subjects"]) == 2
    assert len(policy["numeric_sentences"]) == 1
    assert next(iter(policy["numeric_sentences"].values())).startswith("北区（仅此团队）")
    assert "not_provided" in policy["selection_total"]
    assert facts.model_dump(mode="json") == before
    validate_scoped_numbers(numeric_item(facts), facts)


@pytest.mark.parametrize("fault", ["prefix", "suffix", "old_bad_sentence", "foreign_source", "unknown_metric"])
def test_reject_population_expansion_even_when_north_label_is_present(fault):
    facts = multi_facts()
    item = numeric_item(facts)
    if fault == "prefix":
        item.text = "东区、北区整体：" + item.text
    elif fault == "suffix":
        item.text += "东区、北区均覆盖完整。"
    elif fault == "old_bad_sentence":
        item.text = "东区、北区口径下，当前为 {{metric:" + item.measure_refs[0] + "}} 元，覆盖完整。"
    elif fault == "foreign_source":
        item.source_refs.append("review:team0")
    else:
        item.measure_refs = [facts.measures[0].measure_id]
    with pytest.raises(ValueError):
        validate_scoped_numbers(item, facts)


@pytest.mark.parametrize("text", [
    "共涉及8个商机：5个已登记季度预测，3个未登记。",
    "共涉及8个商机：3个已登记季度预测，5个未登记。",
    "共涉及八个商机：其中五个已登记。", "商机总数为八。", "本批7条即全组总量。",
])
def test_free_counts_are_rejected_not_recomputed_or_repaired(text):
    item = ReviewWireItem(kind="summary", text=text, source_refs=["review:team0"])
    with pytest.raises(ValueError, match="unbound"):
        validate_scoped_numbers(item, multi_facts())


@pytest.mark.parametrize("text", ["建议2026-10-16核实登记情况。", "建议10月16日核实登记情况。", "请下周核实。"])
def test_dates_and_nonquantitative_suggestions_are_not_counts(text):
    item = ReviewWireItem(kind="suggestion", text=text, source_refs=["review:team0"])
    validate_scoped_numbers(item, multi_facts())


def test_frozen_period_and_cited_numeric_names_do_not_authorize_counts():
    facts = multi_facts()
    facts.evidence[0].text = json.dumps({"customer_name": "客户A2", "product_name": "数字人3D"})
    date = facts.period.start
    text = f"{date.year}年度Q{(date.month - 1) // 3 + 1}，建议核实客户名称“客户A2”的产品名称“数字人3D”需求。"
    item = ReviewWireItem(kind="suggestion", text=text, source_refs=["review:team0"])
    validate_scoped_numbers(item, facts)
    item.text += "共8条商机。"
    with pytest.raises(ValueError, match="unbound"):
        validate_scoped_numbers(item, facts)
    item.text = "建议核实客户名称“客户A2”。"
    item.source_refs = ["review:team1"]
    with pytest.raises(ValueError, match="unbound"):
        validate_scoped_numbers(item, facts)


@pytest.mark.parametrize("label", ["3", "三", "3个"])
def test_numeric_subject_label_cannot_cancel_out_a_free_count(label):
    data = multi_facts().model_dump(mode="json")
    data["scope"]["subjects"][0]["label"] = label
    facts = ReviewFactsV2.model_validate(data)
    item = ReviewWireItem(kind="summary", text=f"共{label}个商机。", source_refs=["review:team0"])
    with pytest.raises(ValueError, match="unbound"):
        validate_scoped_numbers(item, facts)


@pytest.mark.parametrize("form", ["chinese", "arabic", "short", "count", "chinese_count", "other_quarter"])
def test_frozen_quarter_labels_allow_calendar_context_but_not_counts(form):
    facts = multi_facts()
    quarter = (facts.period.start.month - 1) // 3 + 1
    label = f"第{'一二三四'[quarter - 1]}季度"
    if form == "arabic":
        label = f"第{quarter}季度"
    elif form == "short":
        label = f"Q{quarter}"
    elif form == "other_quarter":
        label = f"第{'一二三四'[quarter % 4]}季度"
    text = f"{facts.period.start.year}年{label}预测记录已纳入回顾，部分尚待核实。"
    if form == "count":
        text += "三条商机。"
    elif form == "chinese_count":
        text += "商机共八。"
    sections = [{**row, "items": []} for row in review_wire_sections(facts, mode="weekly")]
    sections[0]["items"] = [{"kind": "summary", "text": text, "source_refs": ["review:team0"], "metric_refs": []}]
    payload = dict(title="合成回顾", sections=sections, missing_data=facts.missing_data)
    before = deepcopy(payload)
    if form in {"count", "chinese_count", "other_quarter"}:
        with pytest.raises(InvalidAgentResult):
            validate_review_wire(payload, facts, mode="weekly", allowed_refs={"review:team0"})
    else:
        validate_review_wire(payload, facts, mode="weekly", allowed_refs={"review:team0"})
    assert payload == before


@pytest.mark.parametrize("text", ["商机共八，已登记五、未登记三。", "已成交三单。", "已成交叁单。"])
def test_chinese_counts_do_not_depend_on_a_counter_word(text):
    item = ReviewWireItem(kind="summary", text=text, source_refs=["review:team0"])
    with pytest.raises(ValueError, match="unbound"):
        validate_scoped_numbers(item, multi_facts())


def test_names_with_counters_require_explicit_quoted_field_context():
    facts = multi_facts()
    facts.evidence[0].text = json.dumps({"customer_name": "8个"})
    item = ReviewWireItem(kind="summary", text="共8个商机。", source_refs=["review:team0"])
    with pytest.raises(ValueError, match="unbound"):
        validate_scoped_numbers(item, facts)
    item.text = "下一步建议进一步核实客户名称“8个”，统一登记信息。"
    validate_scoped_numbers(item, facts)
    item.text += "已成交三单。"
    with pytest.raises(ValueError, match="unbound"):
        validate_scoped_numbers(item, facts)


@pytest.mark.parametrize("case,text", [
    ("subject", "共3个商机。"), ("customer", "共8个商机。"),
    ("plain", "商机共八，已登记五、未登记三。"), ("plain", "已成交三单。"),
    ("plain", "本周三条商机。"), ("plain", "本周一单成交。"),
])
def test_qa_counterexamples_are_rejected_by_complete_wire(case, text):
    data = multi_facts().model_dump(mode="json")
    if case == "subject":
        data["scope"]["subjects"][0]["label"] = "3个"
    if case == "customer":
        data["evidence"][0]["text"] = json.dumps({"customer_name": "8个"})
    facts = ReviewFactsV2.model_validate(data)
    sections = [{**row, "items": []} for row in review_wire_sections(facts, mode="weekly")]
    sections[0]["items"] = [{"kind": "summary", "text": text, "source_refs": ["review:team0"], "metric_refs": []}]
    payload = dict(title="合成回顾", sections=sections, missing_data=facts.missing_data)
    before = deepcopy(payload)
    with pytest.raises(InvalidAgentResult):
        validate_review_wire(payload, facts, mode="weekly", allowed_refs={"review:team0"})
    assert payload == before


def test_two_complete_subject_sentences_stay_separate_never_become_a_sum():
    facts = multi_facts()
    facts.measures[0] = facts.measures[0].model_copy(update={
        "value": "10.00", "coverage": "complete", "missing_reasons": [],
    })
    sentences = protected_metric_sentences(facts)
    item = ReviewWireItem(kind="summary", text="\n".join(sentences.values()),
                          source_refs=["review:team0", "review:team1"], metric_refs=list(sentences))
    validate_scoped_numbers(item, facts)
    item.text = "东区和北区合计：" + item.text
    with pytest.raises(ValueError):
        validate_scoped_numbers(item, facts)


def test_historical_content_remains_readable_but_new_wire_is_guarded():
    facts = multi_facts()
    item = numeric_item(facts)
    old = item.model_dump(mode="json")
    old["kind"] = "current"
    old["text"] = "东区、北区口径下，当前为 {{metric:" + item.measure_refs[0] + "}} 元，覆盖完整。"
    payload = {"groups": [{"group_id": facts.groups[0].group_id, "progress": [old],
                           "risks": [], "next_week_tasks": []}], "missing_data": facts.missing_data}
    assert validate_review_content(payload, facts, mode="weekly").groups[0].progress[0].text == old["text"]


def test_wire_guard_runs_before_acceptance_and_preserves_payload():
    facts = multi_facts()
    sections = [{**row, "items": []} for row in review_wire_sections(facts, mode="weekly")]
    sections[0]["items"] = [numeric_item(facts).model_dump(mode="json", by_alias=True)]
    payload = dict(title="合成回顾", sections=sections, missing_data=facts.missing_data)
    validate_review_wire(payload, facts, mode="weekly", allowed_refs={"review:team1"})
    payload["sections"][0]["items"][0]["text"] = "东区、北区：" + payload["sections"][0]["items"][0]["text"]
    before = deepcopy(payload)
    with pytest.raises(InvalidAgentResult):
        validate_review_wire(payload, facts, mode="weekly", allowed_refs={"review:team1"})
    assert payload == before
