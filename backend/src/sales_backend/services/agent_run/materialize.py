"""模型条目的校验与卡片拼装。

这里只放不碰数据库的纯逻辑：判断模型给的风险/待办条目能不能落库、落库前怎么规范化、
以及回读后怎么排序和拼成前端卡片。写库留在 persist.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sales_backend.domain.follow_up_schedule import follow_up_due
from sales_backend.services.agent_run.constants import CHINA_TZ

RISK_TYPE_CODES = frozenset(
    {
        "expectation_gap",
        "engagement_stalled",
        "decision_maker_gap",
        "budget_risk",
        "competition_risk",
        "commercial_process_risk",
        "schedule_risk",
        "technical_validation_risk",
        "relationship_risk",
    }
)
SEVERITY_LABELS = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
TASK_PRIORITY_CODES = frozenset({"normal", "medium", "high", "urgent"})
TASK_PRIORITY_LABELS = {"urgent": "紧急", "high": "高", "medium": "中", "normal": "普通"}


def future_due(value: Any) -> datetime:
    """把模型给的到期时间收敛到未来：不可用时退到今天 09:00，已过则退到次日 02:00。"""
    now = datetime.now(UTC)
    parsed: datetime | None = None
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            else:
                parsed = parsed.astimezone(UTC)
        except ValueError:
            parsed = None
    if parsed and parsed > now + timedelta(minutes=5):
        return parsed
    today_09 = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if today_09 > now + timedelta(minutes=5):
        return today_09
    return (now + timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class RiskDraft:
    """一条通过校验、可以落库的风险。"""

    source_visit_id: str
    risk_type: str
    title: str
    description: str
    severity: str
    suggested_action: str
    evidence_detail: str
    due_at: datetime
    visit: dict[str, Any]

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_visit_id, self.risk_type)


@dataclass(frozen=True, slots=True)
class FollowUpTaskDraft:
    """一条由已归档拜访的下一步行动生成的待办。"""

    source_visit_id: str
    title: str
    description: str
    priority: str
    due_at: datetime
    agent_reason: Any
    candidate: dict[str, Any]


def _compact_title(text: str, limit: int = 42) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}…"


def risk_drafts(model_result: dict[str, Any], visits: dict[str, dict[str, Any]]) -> list[RiskDraft]:
    """挑出能落库的风险：必须挂在本次事实里的拜访上、风险类型合法、描述非空。"""
    analyzed = model_result.get("risks")
    analyzed = analyzed if isinstance(analyzed, list) else []
    drafts: list[RiskDraft] = []
    for item in analyzed:
        if not isinstance(item, dict):
            continue
        source_visit_id = str(item.get("source_visit_id") or "")
        risk_type = str(item.get("risk_type") or "")
        visit = visits.get(source_visit_id)
        if not visit or risk_type not in RISK_TYPE_CODES:
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        severity = str(item.get("severity") or "medium")
        if severity not in SEVERITY_LABELS:
            severity = "medium"
        drafts.append(
            RiskDraft(
                source_visit_id=source_visit_id,
                risk_type=risk_type,
                title=" ".join(str(item.get("title") or "客户经营风险").split())[:200],
                description=description,
                severity=severity,
                suggested_action=str(item.get("suggested_action") or "").strip(),
                evidence_detail=str(item.get("evidence_detail") or description).strip(),
                due_at=future_due(item.get("due_at")),
                visit=visit,
            )
        )
    return drafts


def risk_card(
    model_result: dict[str, Any],
    risks: list[dict[str, Any]],
    *,
    written_keys: list[tuple[str, str]],
    scope: Any,
) -> dict[str, Any]:
    """本次刚写入的风险排在前面，其余按严重度和到期时间排。"""
    rank = {key: index for index, key in enumerate(written_keys)}
    risks = sorted(
        risks,
        key=lambda item: (
            rank.get(
                (str(item.get("source_visit_id") or ""), str(item.get("risk_type_code") or "")),
                len(rank),
            ),
            SEVERITY_ORDER.get(item.get("severity_code"), 4),
            item.get("due_at") or item.get("opened_at"),
        ),
    )
    card_rows = []
    high_count = 0
    customers: set[str] = set()
    for risk in risks:
        severity = str(risk.get("severity_code") or "medium")
        if severity in {"critical", "high"}:
            high_count += 1
        customer_name = str(risk.get("customer_name") or "未关联客户")
        customers.add(customer_name)
        action = str(
            risk.get("suggested_action")
            or (risk.get("attributes") or {}).get("suggested_action")
            or ""
        ).strip()
        card_rows.append(
            {
                "risk_id": risk["risk_id"],
                "title": risk["title"],
                "detail": f"{customer_name} · {action or risk.get('description') or '点击查看分析依据'}",
                "tone": SEVERITY_LABELS.get(severity, "中"),
                "severity": severity,
                "customer": customer_name,
            }
        )
    total = len(card_rows)
    return {
        "title": model_result.get("title") or "个人客户风险清单",
        "summary": (
            f"资深销售Agent已结合历史跟进记录识别出{total}项待处理风险，"
            f"其中高风险{high_count}项，涉及{len(customers)}个客户。"
            if total
            else "根据当前跟进记录，暂未识别出需要处理的个人客户风险。"
        ),
        "metrics": [
            {"label": "待处理", "value": str(total)},
            {"label": "高风险", "value": str(high_count)},
            {"label": "涉及客户", "value": str(len(customers))},
        ],
        "rows": card_rows,
        "scope": scope,
    }


def follow_up_task_drafts(
    model_result: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    *, schedule_policy=None,
) -> list[FollowUpTaskDraft]:
    """跟进待办来自原文；截止日期由服务端依据确认，模型仅排序和解释。"""
    model_items = {
        (str(item.get("source_type")), str(item.get("source_id"))): item
        for item in ordered_model_items(model_result)
        if item.get("source_id")
    }
    drafts: list[FollowUpTaskDraft] = []
    for source_id, candidate in candidates.items():
        description = str(candidate.get("next_action") or "").strip()
        if not description:
            continue
        agent_item = model_items.get(("visit_follow_up", source_id), {})
        priority = str(agent_item.get("priority") or "medium")
        if priority not in TASK_PRIORITY_CODES:
            priority = "medium"
        drafts.append(
            FollowUpTaskDraft(
                source_visit_id=source_id,
                title=_compact_title(description),
                description=description,
                priority=priority,
                due_at=follow_up_due(candidate, agent_item.get("due_at"), schedule_policy),
                agent_reason=agent_item.get("reason"),
                candidate=candidate,
            )
        )
    return drafts


def ordered_model_items(model_result: dict[str, Any]) -> list[dict[str, Any]]:
    ordered = model_result.get("ordered_items")
    ordered = ordered if isinstance(ordered, list) else []
    return [item for item in ordered if isinstance(item, dict)]


def task_card(
    model_result: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    scope: Any,
) -> dict[str, Any]:
    """按模型给的顺序排待办，模型没提到的排在后面，再按到期时间。"""
    rank = {
        (str(item.get("source_type")), str(item.get("source_id"))): index
        for index, item in enumerate(ordered_model_items(model_result))
    }

    def task_key(task: dict[str, Any]) -> tuple[str, str]:
        if task.get("task_type") == "visit_follow_up":
            return ("visit_follow_up", str(task.get("source_visit_id") or ""))
        return ("management_task", str(task.get("task_id") or ""))

    tasks = sorted(tasks, key=lambda task: (rank.get(task_key(task), len(rank)), task.get("due_at")))
    card_rows = []
    management_count = 0
    follow_up_count = 0
    for task in tasks:
        is_follow_up = task.get("task_type") == "visit_follow_up"
        if is_follow_up:
            follow_up_count += 1
        else:
            management_count += 1
        source_label = "跟进记录提取" if is_follow_up else f"{task.get('creator_name') or '管理层'}下发"
        due = task.get("due_at")
        due_text = due.astimezone(CHINA_TZ).strftime("%m月%d日 %H:%M") if due else "待安排"
        card_rows.append(
            {
                "task_id": task["task_id"],
                "title": task["title"],
                "detail": f"{task.get('customer_name') or '未关联客户'} · {source_label} · {due_text}",
                "tone": TASK_PRIORITY_LABELS.get(task.get("priority_code"), "普通"),
                "source": "visit_follow_up" if is_follow_up else "management_task",
                "due_at": due.isoformat() if due else None,
            }
        )
    total = len(card_rows)
    return {
        "title": model_result.get("title") or "今日行动计划",
        "summary": (
            f"Agent已结合历史跟进记录与管理任务，按时间整理出{total}项待办；"
            f"其中管理任务{management_count}项、跟进行动{follow_up_count}项。"
            if total
            else "当前没有未完成的待办事项。"
        ),
        "metrics": [
            {"label": "待处理", "value": str(total)},
            {"label": "管理任务", "value": str(management_count)},
            {"label": "跟进行动", "value": str(follow_up_count)},
        ],
        "rows": card_rows,
        "scope": scope,
    }
