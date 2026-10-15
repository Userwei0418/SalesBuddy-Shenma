from sales_backend.domain.customer_identity import BusinessIdentityFilter
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.models import ActualCreate, ActualVoid
from sales_backend.db import Database
from sales_backend.domain.activity_window import MapActivityWindow, map_activity_window_bounds
from sales_backend.repositories.collaboration import scoped_opportunity_ids
from sales_backend.repositories.customer_assets import CustomerAssetRepository, can_manage, today
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.services.customer_map import has_map_scores, read_map, refresh_missing_map
from sales_backend.services.idempotency import execute_mutation

router = APIRouter(prefix="/api/v1/customer-assets", tags=["Customer assets"])


@router.get(
    "/map",
    description=("返回原作战地图权限范围内的客户，按客户去重；all 不要求正式跟进，quarter 为本季度。"
                 "其他窗口仅按正式跟进筛选，activity_start/activity_end 为含首尾日的上海业务日期。"
                 "all 的 activity_start/activity_end/activity_since 均为 null。"
                 "省略 activity_window 时保持旧版小程序的当年口径；新版显式传入所选窗口。"
                 "all/quarter 默认每页200、最多500；旧窗口未传分页参数时保留原读取方式。"
                 "has_more/next_offset 表示续页，不提供未经统计的总数；coverage 仅本页。"
                 "all 缺评分时 map_status=partial，表示本页未评估，不表示后台评估中，refresh_allowed=false。"
                 "scope 仅支持 self/person/team；部门级目标范围不属于作战地图接口契约。"),
)
async def map_customers(
    business_identity: BusinessIdentityFilter = "all",    activity_window: MapActivityWindow = "year",
    scope: Literal["self", "person", "team"] | None = None,
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    team_id: UUID | None = None,
    page_size: Annotated[int | None, Query(ge=1, le=500)] = None,
    offset: Annotated[int, Query(ge=0, le=2147483647)] = 0,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    as_of = today()
    limit = page_size or (200 if activity_window in {"all", "quarter"} or offset else None)
    async with database.transaction(identity.actor, readonly=True) as connection:
        try:
            items = await read_map(
                connection, identity.actor, as_of=as_of, activity_window=activity_window, scope=scope, business_identity=business_identity,
                member_id=member_id, member_ids=member_ids, team_id=team_id,
                row_limit=limit + 1 if limit else None, offset=offset,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    has_more = limit is not None and len(items) > limit
    items = items[:limit] if limit else items
    items = [{**item, "assessment_status": "assessed" if has_map_scores(item) else "unassessed"} for item in items]
    scored_count = sum(item["assessment_status"] == "assessed" for item in items)
    bounds = map_activity_window_bounds(as_of, activity_window)
    return dict(items=items, business_identity=business_identity, identity_base="customer", activity_window=bounds.key, activity_start=bounds.start, activity_end=bounds.end,
                activity_since=bounds.start, as_of=as_of, data_source="database",
                active_customer_count=len(items),
                returned_count=len(items), page_size=limit, offset=offset, has_more=has_more,
                next_offset=offset + len(items) if has_more else None,
                coverage={"returned_count": len(items), "scored_count": scored_count,
                          "unassessed_count": len(items) - scored_count},
                refresh_allowed=bounds.key != "all",
                map_status="ready" if scored_count == len(items) else "partial" if bounds.key == "all" else "updating")


@router.post("/map/refresh", description="为当前身份可见的所选活动时间范围客户补齐缺评分任务。"
             "只入队不直接调用模型，不代替他人生成评分；已有任务复用，终态十分钟内不重复入队。"
             "status 为 ready/updating/unavailable；不会制造默认坐标或改变活跃客户集合。"
             "省略 activity_window 时保持旧版当年口径，与地图读取一致。"
             "quarter 默认只补评本页200条；page_size/offset 与 GET 相同，显式分页时只处理指定页。"
             "all 在鉴权及所选成员范围检查后返回 unavailable / BATTLE_MAP_ALL_REFRESH_DISABLED，0入队；不执行幂等写入。"
             "scope 仅支持 self/person/team；部门级目标范围不属于作战地图接口契约。")
async def refresh_map(
    business_identity: BusinessIdentityFilter = "all",    activity_window: MapActivityWindow = "year",
    scope: Literal["self", "person", "team"] | None = None,
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    team_id: UUID | None = None,
    page_size: Annotated[int | None, Query(ge=1, le=500)] = None,
    offset: Annotated[int, Query(ge=0, le=2147483647)] = 0,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
):
    try:
        limit = page_size or (200 if activity_window in {"all", "quarter"} or offset else None)
        if activity_window == "all":
            async with database.transaction(identity.actor, readonly=True) as connection:
                return await refresh_missing_map(
                    connection, identity.actor, as_of=today(), activity_window=activity_window, scope=scope, business_identity=business_identity,
                    member_id=member_id, member_ids=member_ids, team_id=team_id,
                )
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(
                connection, identity.actor, idempotency_key, "battle_map.refresh",
                {
                    **({"business_identity": business_identity} if business_identity != "all" else {}),
                    "activity_window": activity_window, "scope": scope, "member_id": member_id,
                    "member_ids": member_ids, "team_id": team_id,
                    **({"page_size": limit, "offset": offset} if limit is not None else {}),
                },
                lambda: refresh_missing_map(
                    connection, identity.actor, as_of=today(), activity_window=activity_window, scope=scope, business_identity=business_identity,
                    member_id=member_id, member_ids=member_ids, team_id=team_id, row_limit=limit, offset=offset,
                ),
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc



@router.get("", description="经营实绩及完整经营客户资产；summary.portfolio_customer_count、acv_amount（元）"
            "和 unknown_acv_count 不受活跃窗口或实绩年度影响；ACV 空金额按零汇总，不改原值。"
            "basis=effective 为唯一采用实际；旧entries/historical/auto参数均兼容读取effective。"
            "部分登记只给专用小计；季度事实不分配到日，非整季范围返回未分配期间说明。")
async def read_assets(
    scope: Literal["self", "team"] | None = None,
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    period: Literal["year", "all"] = "year",
    basis: Literal["effective", "entries", "historical", "auto"] = "effective",
    kind: Literal["recognized", "collection"] | None = None,
    customer_id: UUID | None = None,
    opportunity_id: UUID | None = None,
    team_id: UUID | None = None,
    owner_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    page_size: int = Query(50, ge=1, le=100),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        if customer_id and not await CustomerRepository().exists(connection, customer_id):
            raise HTTPException(404, "客户不存在或不在当前权限范围内")
        customer_ids = None
        if scope or member_id or member_ids:
            try:
                _, _, _, scoped = await scoped_opportunity_ids(
                    connection, identity.actor, scope, member_id, member_ids, permission='actual.read'
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            customer_ids = list({r["customer_id"] for r in scoped})
        result = await CustomerAssetRepository().read(
            connection,
            period=period,
            basis=basis,
            kind=kind,
            customer_id=customer_id,
            opportunity_id=opportunity_id,
            team_id=team_id,
            owner_id=owner_id,
            customer_ids=customer_ids,
            offset=offset,
            limit=page_size,
        )
        editable = await can_manage(connection, customer_id, opportunity_id)
    return {**result, "can_manage": editable}


@router.post("", status_code=201)
async def create_actual(
    body: ActualCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
):
    data = body.model_dump()
    if "tax_basis" not in body.model_fields_set:
        data.pop("tax_basis")
    try:
        async with database.transaction(identity.actor) as connection:
            await CustomerAssetRepository().require_operator(connection)
            await CustomerAssetRepository().create_permission(connection, data)
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                "actuals.create",
                data,
                lambda: CustomerAssetRepository().create(connection, identity.actor, data),
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(409, "该客户此类实绩的来源编号已登记，请核对，勿重复提交") from exc
    except (asyncpg.SerializationError, asyncpg.DeadlockDetectedError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except asyncpg.CheckViolationError as exc:
        raise HTTPException(422, exc.message) from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise HTTPException(403, "当前账号无权执行此操作") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{record_id}/void")
async def void_actual(
    record_id: UUID,
    body: ActualVoid,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
):
    try:
        async with database.transaction(identity.actor) as connection:
            await CustomerAssetRepository().require_operator(connection)
            await CustomerAssetRepository().void_permission(connection, record_id)
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                f"actuals.void:{record_id}",
                body.model_dump(),
                lambda: CustomerAssetRepository().void(connection, identity.actor, record_id, body.reason),
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (asyncpg.SerializationError, asyncpg.DeadlockDetectedError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except asyncpg.CheckViolationError as exc:
        raise HTTPException(422, exc.message) from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise HTTPException(403, "当前账号无权执行此操作") from exc
