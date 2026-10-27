"""Private deterministic prose; frozen meeting facts remain the numeric authority."""

from uuid import uuid5

from sales_backend.contracts.work_report import ReportContent, SchemeConfig, SchemeSnapshot
from sales_backend.domain.work_report import WorkReportError, canonical_hash
from sales_backend.domain.work_report_meeting import NAMESPACE, schemes

TITLES = {
    "overview": "本期概况",
    "changes": "业务金额变化",
    "customers": "新认领客户",
    "opportunities": "新增与丢单商机",
    "followups": "正式跟进",
    "next_steps": "已登记下一步",
}


def render(facts, title, builtin_id):
    scheme = next((s for s in schemes() if s.builtin_id == builtin_id and s.period_kind == facts.period.kind), None)
    if scheme is None:
        raise WorkReportError("WORK_REPORT_INVALID_INPUT", "会议方案与快照周期不一致", status=422)
    config = SchemeConfig(
        default_period=facts.period.kind, sections=[dict(id=k, title=TITLES[k]) for k in scheme.section_ids]
    )
    snapshot = SchemeSnapshot(
        scheme_id=uuid5(NAMESPACE, builtin_id),
        version_no=1,
        name=scheme.name,
        config=config,
        config_hash=canonical_hash(config),
    )
    scope_text = facts.scope.label + ("团队" if facts.scope.kind == "managed_team" else "")

    def value(metric):
        return str(metric.value) if metric.value is not None else f"未知（已知{metric.known_count}条）"

    overview = (
        f"{scope_text}，{facts.period.label}。新增认领{value(facts.counts.new_claimed_customers)}；"
        f"正式跟进{value(facts.counts.formal_visits)}；新增商机{value(facts.counts.new_opportunities)}；"
        f"丢单商机{value(facts.counts.lost_opportunities)}。"
    )
    changes = []
    for key, name in [("acv", "ACV"), ("quarter_revenue", "预计确收"), ("quarter_collection", "预计回款")]:
        m = getattr(facts.amounts, key)
        changes.append(
            f"{name}：期初{m.opening or '未知'}元，净变化{m.change or '未知'}元，期末{m.closing or '未知'}元。"
        )
    changes.insert(0, f"金额季度：{facts.quarter.year} Q{facts.quarter.quarter}；未知不作为零。")

    def names(rows):
        # The full frozen board is retained; prose deliberately identifies its
        # finite digest and directs readers to the structured detail for the rest.
        chosen = rows[:100]
        text = "；".join(r.name for r in chosen) or "无已知记录"
        return text + (f"。正文展示前100条，完整{len(rows)}条见冻结看板明细。" if len(rows) > 100 else "")

    paragraphs = dict(
        overview=overview,
        changes="\n".join(changes),
        customers=names(facts.new_claimed_customers),
        opportunities="新增：" + names(facts.new_opportunities) + "\n丢单：" + names(facts.lost_opportunities),
        followups=names(facts.formal_visits),
        next_steps="\n".join(r.text for r in facts.next_steps[:20]) or "尚无已登记下一步",
    )
    if len(facts.next_steps) > 20:
        paragraphs["next_steps"] += f"\n正文展示前20条，完整{len(facts.next_steps)}条见冻结看板。"
    # Prose is a bounded reading aid. Long registered plans are explicitly
    # summarized here, never truncated in immutable meeting_facts.
    for key, text in paragraphs.items():
        if len(text) > 8000:
            paragraphs[key] = text[:7900] + "\n正文摘要已省略超长部分，完整内容见冻结看板明细。"
    content = ReportContent(
        title=title,
        sections=[
            dict(id=k, title=TITLES[k], items=[dict(kind="summary", text=paragraphs[k])]) for k in scheme.section_ids
        ],
        missing_data=facts.missing_reasons,
    )
    return content, snapshot


def validate_revision(content, previous):
    retained = {canonical_hash(item) for section in previous.sections for item in section.items}
    if any(
        item.kind != "manual" and canonical_hash(item) not in retained
        for section in content.sections
        for item in section.items
    ):
        raise WorkReportError("WORK_REPORT_INVALID_OUTPUT", "会议正文新增或改动段落须标识为人工编辑", status=422)
