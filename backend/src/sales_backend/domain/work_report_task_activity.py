"""Period task counts from actual creation/completion evidence and current owners."""

from datetime import datetime

from sales_backend.contracts.work_report_task_activity import TaskActivity
from sales_backend.domain.opportunity_history import canonical
from sales_backend.domain.work_report import canonical_hash

STATUS_LABELS = {
    "pending_confirm": "待接受",
    "pending_execution": "待执行",
    "in_progress": "执行中",
    "pending_review": "待发起人确认",
    "completed": "已完成",
    "deferred": "已延期",
    "cancelled": "已取消",
}
MISSING_COMPLETION = "存在已完成任务缺少完成时间和完成事件，本期完成数量未核定"


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def completion_times(row, data_as_of):
    events = sorted(
        timestamp(e["occurred_at"])
        for e in row["completion_events"]
        if e["to_status"] == "completed"
        and e.get("from_status") != "completed"
        and timestamp(e["occurred_at"]) <= data_as_of
    )
    completed = timestamp(row.get("completed_at"))
    if row["status"] == "completed" and completed is not None and completed <= data_as_of:
        # The task column is the authoritative timestamp of the current completed
        # transition. Its event is recorded just afterward in the same transaction.
        # Replace that last event instead of counting a boundary twice.
        if events:
            events[-1] = completed
        else:
            events.append(completed)
    return sorted(set(events))


def count(items, missing=False):
    return {
        "value": None if missing else len(items),
        "known_count": len(items),
        "coverage": ("partial" if items else "unavailable") if missing else "complete",
        "missing_reasons": [MISSING_COMPLETION] if missing else [],
    }


def assemble_task_activity(rows, *, period, scope, data_as_of):
    groups = {"new_tasks": {}, "completed_tasks": {}}
    sources, unknown_members = {}, set()
    for row in rows:
        ident = str(row["id"])
        owners = {str(owner["member_id"]): owner for owner in row["owners"]}
        if not owners:
            continue
        created = timestamp(row["created_at"])
        completions = completion_times(row, data_as_of)
        relevant = [at for at in completions if period.start <= at < period.cutoff]
        if row["status"] == "completed" and not completions:
            unknown_members.update(owners)
        is_new = period.start <= created < period.cutoff
        if not is_new and not relevant:
            continue
        ref = "task_activity:" + ident
        sources[ref] = dict(
            source_id=ref,
            kind="task",
            record_id=ident,
            version=str(row["version_no"]),
            observed_at=data_as_of,
            business_at=max(relevant) if relevant else created,
            source_hash=canonical_hash(canonical(row)),
        )
        item = dict(
            id=ident,
            task_id=ident,
            name=row["title"],
            owners=[owners[k] for k in sorted(owners)],
            member_ids=sorted(owners),
            customer_id=row.get("customer_id"),
            customer_name=row.get("customer_name"),
            opportunity_id=row.get("opportunity_id"),
            opportunity_name=row.get("opportunity_name"),
            created_at=created,
            completed_at=max(completions) if completions else None,
            status=row["status"],
            status_label=STATUS_LABELS[row["status"]],
            source_refs=[ref],
        )
        if is_new:
            groups["new_tasks"][ident] = item
        if relevant:
            groups["completed_tasks"][ident] = {**item, "completed_at": max(relevant)}
    lists = {
        key: sorted(
            items.values(),
            key=lambda item: (item["created_at"] if key == "new_tasks" else item["completed_at"], item["id"]),
            reverse=True,
        )
        for key, items in groups.items()
    }
    people = {str(i) for i in scope.member_ids} | unknown_members
    for items in lists.values():
        people.update(member for item in items for member in item["member_ids"])
    members = []
    for person in sorted(people):
        selected = {key: [item["id"] for item in items if person in item["member_ids"]] for key, items in lists.items()}
        members.append(
            dict(
                member_id=person,
                counts=dict(
                    new_tasks=count(selected["new_tasks"]),
                    completed_tasks=count(selected["completed_tasks"], person in unknown_members),
                ),
                new_task_ids=selected["new_tasks"],
                completed_task_ids=selected["completed_tasks"],
            )
        )
    return TaskActivity(
        period=period,
        scope=scope,
        data_as_of=data_as_of,
        counts=dict(
            new_tasks=count(lists["new_tasks"]), completed_tasks=count(lists["completed_tasks"], bool(unknown_members))
        ),
        **lists,
        members=members,
        sources=list(sources.values()),
        missing_reasons=[MISSING_COMPLETION] if unknown_members else [],
    )
