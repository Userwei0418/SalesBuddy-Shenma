"""Use existing target transitions and scoped permissions, never duplicate money."""

from decimal import Decimal

from sales_backend.services.targets import normalize_imported_target_amounts
from sales_backend.repositories.review import ReviewRepository
from sales_backend.repositories.targets import TargetRepository
from sales_backend.services.targets import target_period, target_scope


async def authorize_metric_management(connection):
    if not await connection.fetchval("SELECT security.authorization_subject('target.manage','department',NULL,NULL)"):
        raise PermissionError("当前账号无权维护公司指标")


async def authorize_target_write(connection, actor, body):
    selected = target_scope(actor, body.scope, body.user_id, body.team_id, body.department_code)
    repository = TargetRepository()
    if not await repository.allowed(connection, selected, write=True) or not await repository.allowed(connection, selected):
        raise PermissionError("当前账号无权维护这个范围的目标")


def money_strings(value):
    """Postgres JSON numeric target fields must not escape as binary floats."""
    if isinstance(value, dict):
        return {k: (format(Decimal(str(v)), ".2f") if v is not None and k in {
            "amount", "previous_amount", "proposed_amount", "effective_amount", "amount_text",
        } else money_strings(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [money_strings(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


async def target_rows(connection, actor, *, period_type, anchor_date, scope=None, user_id=None, team_id=None,
                      department_code="sales", kind=None, q=None, limit=50, offset=0):
    if scope is None and (user_id or team_id or department_code != "sales"):
        raise ValueError("指定对象时必须指定目标范围")
    selected = target_scope(actor, scope, user_id, team_id, department_code) if scope is not None else None
    if selected and not await TargetRepository().allowed(connection, selected):
        raise PermissionError("当前账号无权查看这个范围的目标")
    if not await connection.fetchval("SELECT security.authorization_has('target.read')"):
        raise PermissionError("当前账号未获目标查看权限")
    period = target_period(period_type, anchor_date)
    result = await ReviewRepository().target_rows(connection, actor, period=period, scope=selected,
                                                 kind=kind, q=q, limit=limit, offset=offset)
    options = await ReviewRepository().target_options(connection, actor)
    current = await connection.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
    editable = any(row["can_manage"] or period == target_period("quarter", current)
                   for key in ("people", "teams", "departments") for row in options[key])
    if selected:
        manages = await connection.fetchval("SELECT security.authorization_subject('target.manage',$1,$2::uuid,$3::uuid)",
                                           selected["scope_type"], selected["user_id"], selected["team_id"])
        editable = (manages or period == target_period("quarter", current)) and await TargetRepository().allowed(
            connection, selected, write=True,
        )
    return {**normalize_imported_target_amounts(money_strings(result)), "period": period, "scope": selected,
            "editable": editable, "data_source": "database"}


async def save_target_row(connection, actor, body):
    selected = target_scope(actor, body.scope, body.user_id, body.team_id, body.department_code)
    # This read is under the same transaction; the database function and trigger
    # enforce the active metric, integer precision and current target version too.
    row = await connection.fetchrow(
        "SELECT * FROM crm.review_metric WHERE workspace_id=$1::uuid AND code=$2",
        actor.workspace_id, body.kind,
    )
    if row is None or not row["active"] or not row["goal_selectable"]:
        raise ValueError("指标不存在、已停用或不可设置目标")
    if row["decimal_places"] == 0 and body.amount != body.amount.to_integral_value():
        raise ValueError("数量目标须为正整数")
    # jsonb numeric serialization is performed by SQL before the JSON decoder,
    # preserving values above JavaScript's exact integer range and all cents.
    result = await connection.fetchval("""WITH result AS (
        SELECT security.save_target_batch($1,$2::uuid,$3::uuid,$4,$5,$6,$7::jsonb,$8,$9) AS value
      ) SELECT security.review_target_money_json(value) FROM result""",
        selected["scope_type"], selected["user_id"], selected["team_id"], body.period_type,
        target_period(body.period_type, body.anchor_date)["start"],
        target_period(body.period_type, body.anchor_date)["end"],
        [{"kind": body.kind, "amount": format(body.amount, ".2f"), "version_no": body.version_no}],
        body.reason, selected["department_code"],
    )
    from sales_backend.db import json_value

    result = json_value(result)
    return {**result, "target": next((item for item in result["items"] if item["kind"] == body.kind), None)}
