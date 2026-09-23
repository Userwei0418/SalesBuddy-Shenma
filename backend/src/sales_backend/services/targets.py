"""Period normalization and target scope; PostgreSQL owns the approval transition."""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from sales_backend.repositories.targets import TargetRepository


def target_period(period_type, anchor):
    if period_type == "week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
    elif period_type in {"month", "quarter", "year"}:
        month = (
            anchor.month
            if period_type == "month"
            else (anchor.month - 1) // 3 * 3 + 1
            if period_type == "quarter"
            else 1
        )
        last_month = month if period_type == "month" else month + 2 if period_type == "quarter" else 12
        start = date(anchor.year, month, 1)
        end = date(anchor.year, last_month, calendar.monthrange(anchor.year, last_month)[1])
    else:
        raise ValueError("不支持的目标周期")
    return {"type": period_type, "start": start, "end": end}


def target_scope(actor, scope="self", user_id=None, team_id=None, department_code="sales"):
    if department_code not in {"sales", "fde"} or (scope != "department" and department_code != "sales"):
        raise ValueError("部门类别只用于部门目标")
    if scope == "self":
        if user_id or team_id:
            raise ValueError("本人目标不接受其他人员或部门参数")
        return {"scope_type": "person", "user_id": actor.user_id, "team_id": None, "department_code": "sales"}
    if scope == "person" and user_id and not team_id:
        return {"scope_type": scope, "user_id": str(user_id), "team_id": None, "department_code": "sales"}
    if scope == "team" and team_id and not user_id:
        return {"scope_type": scope, "user_id": None, "team_id": str(team_id), "department_code": "sales"}
    if scope == "department" and not user_id and not team_id:
        return {"scope_type": scope, "user_id": None, "team_id": None, "department_code": department_code}
    raise ValueError("请选择有效的目标人员或部门范围")


async def read_targets(
    connection,
    actor,
    *,
    period_type,
    anchor_date,
    scope="self",
    user_id=None,
    team_id=None,
    department_code="sales",
    kind=None,
    limit=50,
    offset=0,
    q=None,
):
    repo = TargetRepository()
    selected = target_scope(actor, scope, user_id, team_id, department_code) if scope is not None else None
    if selected and not await repo.allowed(connection, selected):
        raise PermissionError("当前账号无权查看这个范围的目标")
    if selected is None and actor.role.value not in {"operations", "administrator"}:
        raise PermissionError("目标管理需要运营权限")
    period = target_period(period_type, anchor_date)
    result = await repo.list(
        connection, actor, period=period, scope=selected, kind=kind, limit=limit, offset=offset, q=q
    )
    pending = await repo.requests(connection, actor, period=period, scope=selected, status="pending", limit=100)
    batches = await repo.batches(connection, actor, period=period, scope=selected, status="pending", limit=100)
    recent = await repo.batches(connection, actor, period=period, scope=selected, limit=10)
    editable = await repo.allowed(connection, selected, write=True) if selected else True
    if actor.role.value not in {"operations", "administrator"}:
        current = await connection.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        editable = editable and period == target_period("quarter", current)
    return {
        **result,
        "period": period,
        "scope": selected,
        "pending_requests": pending["items"],
        "pending_total": pending["total"],
        "pending_batches": batches["items"],
        "pending_batch_total": batches["total"],
        "recent_batches": recent["items"],
        "editable": editable,
        "data_source": "database",
    }


async def save_target(connection, actor, body):
    from sales_backend.contracts.targets import TargetBatchSave

    payload = body.model_dump(exclude={"kind", "amount", "version_no"})
    payload["items"] = [body.model_dump(include={"kind", "amount", "version_no"})]
    result = await save_target_batch(connection, actor, TargetBatchSave(**payload))
    return {**result, "target": next((row for row in result["items"] if row["kind"] == body.kind), None)}


async def save_target_batch(connection, actor, body):
    scope = target_scope(actor, body.scope, body.user_id, body.team_id, body.department_code)
    return await TargetRepository().save_batch(
        connection,
        scope=scope,
        period=target_period(body.period_type, body.anchor_date),
        items=[{
            **item.model_dump(mode="json"),
            "amount": format(item.amount.quantize(Decimal("0.01")), "f"),
        } for item in body.items],
        reason=body.reason,
    )
