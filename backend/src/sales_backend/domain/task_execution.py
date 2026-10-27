"""Deterministic progress projection from trusted current requirement facts.

No lifecycle, authority or KR mutation is implied by this projection. Repository
adapters must select active definitions and preserve historic submission facts.
"""

from dataclasses import dataclass
from typing import Literal

from sales_backend.contracts.task_execution import TaskProgress


@dataclass(frozen=True)
class ChecklistFact:
    item_id: str
    state: Literal["not_started", "in_progress", "completed", "rework"]
    completion_note: str = ""
    requires_revalidation: bool = False


def checklist_progress(requirements_version: int, items: tuple[ChecklistFact, ...]) -> TaskProgress:
    """Unknown is not zero; ongoing work contributes no fractional credit."""
    if len({item.item_id for item in items}) != len(items):
        raise ValueError("重复推进项不能重复计算")
    for item in items:
        if not item.item_id or item.state not in {"not_started", "in_progress", "completed", "rework"}:
            raise ValueError("无效的推进项事实")
        if item.state == "completed" and not item.completion_note.strip():
            raise ValueError("已完成推进项缺少成果依据")
    total = len(items)
    completed = sum(item.state == "completed" and not item.requires_revalidation for item in items)
    review = any(item.requires_revalidation for item in items)
    basis = "missing_checklist" if not total else "requires_revalidation" if review else "checklist"
    return TaskProgress(requirements_version=requirements_version, completed=completed, total=total,
                        percent=completed * 100 // total if total and not review else None, basis=basis)
