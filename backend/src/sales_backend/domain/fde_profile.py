"""A factual collaboration portrait, with no weighted performance score."""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sales_backend.domain.fde_coaching import COACHING_CONTRACT_VERSION

CONTRACT_VERSION = "fde.profile.v1"
PORTRAIT_PROMPT_VERSION = "fde.profile.coaching.v3"
TZ = ZoneInfo("Asia/Shanghai")
DIMENSIONS = (
    ("record_quality", "记录质量", "记录质量"),
    ("plan_coverage", "行动计划完整度", "行动计划"),
    ("project_coverage", "协助项目跟进覆盖", "项目覆盖"),
    ("customer_coverage", "协助客户跟进覆盖", "客户覆盖"),
    ("task_completion", "本人任务完成率", "任务完成"),
    ("task_timeliness", "本人任务按期完成率", "按期完成"),
)


def window(now, days):
    if type(days) is not int or not 7 <= days <= 90:
        raise ValueError("画像周期为7至90天")
    today = now.astimezone(TZ).date()
    return (
        datetime.combine(today - timedelta(days=days - 1), time.min, TZ),
        datetime.combine(today + timedelta(days=1), time.min, TZ),
    )


def in_window(value, start, now):
    return value is not None and start <= value <= now


def factual_summary(facts):
    """Describe known counts without delegating numeric conclusions to a model."""
    coverage = facts["evidence_coverage"]
    text = (
        f"近{facts['period']['days']}天，本人归档{facts['sample_count']}条记录；"
        f"当前协助{coverage['assigned_projects']}个项目，涉及{coverage['assigned_customers']}家客户。"
    )
    if facts["sample_count"] <= 1:
        text += "记录较少，建议随跟进持续补充。"
    elif any(d["score"] is None for d in facts["dimensions"]):
        text += "部分维度暂无足够记录。"
    return text


def portrait(actor, *, days, now, permission_version, visits, projects, tasks, configuration=None,
             coaching=None, coaching_versions=None):
    """Input rows already come from self-only SQL and current RLS visibility."""
    start, end = window(now, days)
    visible_visits = [v for v in visits if v["detail_visible"]]
    graded = [v for v in visible_visits if v["follow_up_score"] is not None]
    project_ids = {p["id"] for p in projects}
    customer_ids = {p["customer_id"] for p in projects}
    covered_projects = {v["opportunity_id"] for v in visits} & project_ids
    covered_customers = {v["customer_id"] for v in visits} & customer_ids
    actionable = [
        t
        for t in tasks
        if t["status"] != "cancelled"
        and (in_window(t["due_at"], start, now) or in_window(t["completed_at"], start, now))
    ]
    completed = [t for t in actionable if t["status"] == "completed"]
    timed = [
        t
        for t in tasks
        if t["status"] == "completed" and t["due_at"] is not None and in_window(t["completed_at"], start, now)
    ]
    plans = sum(v["has_next_action"] for v in visible_visits)
    ratios = {
        "record_quality": (
            sum(float(v["follow_up_score"]) for v in graded),
            len(graded),
            False,
            "本人归档记录中已有AI审核分数的均值",
        ),
        "plan_coverage": (plans, len(visible_visits), True, "本人可读归档记录中有下一步计划的比例"),
        "project_coverage": (
            len(covered_projects),
            len(project_ids),
            True,
            "当前本人协助项目中，周期内有本人归档跟进的比例",
        ),
        "customer_coverage": (
            len(covered_customers),
            len(customer_ids),
            True,
            "当前本人协助客户中，周期内有本人归档跟进的比例",
        ),
        "task_completion": (
            len(completed),
            len(actionable),
            True,
            "周期内已到期或已完成的本人负责任务中，已完成的比例；取消任务不计",
        ),
        "task_timeliness": (
            sum(t["completed_at"] <= t["due_at"] for t in timed),
            len(timed),
            True,
            "周期内已完成且有截止时间的本人负责任务中，按期完成的比例",
        ),
    }
    dimensions = []
    for code, name, short_name in DIMENSIONS:
        numerator, denominator, percent, explanation = ratios[code]
        dimensions.append(
            {
                "code": code,
                "name": name,
                "short_name": short_name,
                "score": round(numerator / denominator * (100 if percent else 1), 1) if denominator else None,
                "assessment": explanation if denominator else "暂无可用于该维度的记录",
                "coaching_action": "",
                "evidence_count": denominator,
                "numerator": numerator,
                "denominator": denominator,
            }
        )
    period = {"days": days, "start": start.isoformat(), "end": end.isoformat()}
    # Exclude the moving clock value; include the calendar window and exact
    # source versions so repeated GETs remain stable until business facts change.
    sources = {
        "contract_version": CONTRACT_VERSION,
        "actor": actor.model_dump(mode="json"),
        "permission_version": permission_version,
        "period": period,
        "configuration": configuration,
        "visits": sorted(visits, key=lambda v: v["id"]),
        "projects": sorted(projects, key=lambda p: p["id"]),
        "tasks": sorted(tasks, key=lambda t: t["id"]),
        "dimensions": dimensions,
        "coaching_inputs": coaching or {"contract_version": COACHING_CONTRACT_VERSION, "sources": []},
        "coaching_versions": coaching_versions or [],
    }
    fingerprint = hashlib.sha256(
        json.dumps(sources, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "data_source": "database",
        "contract_version": CONTRACT_VERSION,
        "data_as_of": now.astimezone(UTC).isoformat(),
        "period": period,
        "scope": {"scope_type": "self", "scope_label": "本人", "user_id": actor.user_id, "role": actor.role.value},
        "sample_count": len(visits),
        "dimensions": dimensions,
        "evidence_coverage": {
            "archived_visits": len(visits),
            "readable_archived_visits": len(visible_visits),
            "assigned_projects": len(projects),
            "assigned_customers": len(customer_ids),
            "owned_tasks": len(tasks),
        },
        "scope_note": "只统计本人创建、记录并确认归档的拜访；销售勾选协同不计。"
        "记录质量与行动计划仅使用当前可读原文的样本，退出项目的不可读历史保留归档计数。"
        "项目和客户覆盖按当前本人有效协助关系。六维是客观比例与记录均值，不是绩效评定；"
        "没有总分、排名或项目金额分摊。",
        "permission_version": permission_version,
        "facts_fingerprint": fingerprint,
        "configuration": configuration or {},
        "coaching_inputs": sources["coaching_inputs"],
    }
