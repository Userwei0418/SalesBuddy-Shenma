from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.contracts.models import ActualCreate, ActualVoid
from sales_backend.db import Database
from sales_backend.repositories.collaboration import FDE_ROLES, scope_members, scoped_opportunity_ids
from sales_backend.repositories.customer_assets import CustomerAssetRepository, can_manage, today
from sales_backend.repositories.customer_map import CustomerMapRepository, activity_since
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.profile_customers import subject_customers
from sales_backend.repositories.profile_scope import resolve_scope, scope_options
from sales_backend.services.idempotency import execute_mutation

router = APIRouter(prefix="/api/v1/customer-assets", tags=["Customer assets"])


@router.get("/map", description="返回权限范围内近六个日历月有正式跟进的活跃客户。"
            "activity_since/as_of 为含首尾日的业务日期；仅认领不激活，缺评分保持为空。")
async def map_customers(
    scope: Literal["self", "person", "team", "department"] | None = None,
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    team_id: UUID | None = None,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    as_of = today()
    async with database.transaction(identity.actor, readonly=True) as connection:
        # No arbitrary cap; both map and list represent the complete active authorized set.
        fde_user_ids = None
        if identity.actor.role.value in FDE_ROLES:
            try:
                _, fde_user_ids, _ = await scope_members(
                    connection, identity.actor, scope, member_id, member_ids, team_id)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        items = await CustomerMapRepository().read(connection, fde_user_ids=fde_user_ids, as_of=as_of)
        if identity.actor.role.value in {'sales', 'supervisor', 'manager'}:
            try:
                actor = identity.actor
                if member_ids:
                    raise ValueError('销售个人视角请选择一名具体成员')
                if scope is None and actor.role.value == 'supervisor' and not member_id and not team_id:
                    directory = await scope_options(connection, actor)
                    selected = {'scope': 'team', 'member_ids': [m['id'] for m in directory['members']],
                                'team_ids': [t['id'] for t in directory['teams']]}
                else:
                    chosen_scope = scope or ('person' if member_id else 'team' if team_id else
                        'department' if actor.role.value == 'manager' else 'self')
                    selected = await resolve_scope(connection, actor, scope=chosen_scope,
                                                   member_id=member_id, team_id=team_id)
                assessments = {row['id']: row for row in await subject_customers(
                    connection, member_ids=selected['member_ids'], scope=selected['scope'],
                    team_ids=selected['team_ids'], customer_ids=[item['id'] for item in items])}
                items = [{**item, **{key: assessments[item['id']][key] for key in
                          ('potential_score', 'relationship_score', 'quadrant_code', 'quadrant_policy')}}
                         for item in items if item['id'] in assessments]
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
    return dict(items=items, activity_since=activity_since(as_of), as_of=as_of, data_source="database")


@router.get("", description="经营实绩及完整经营客户资产；summary.portfolio_customer_count、acv_amount（元）"
            "和 unknown_acv_count 不受活跃窗口或实绩年度影响；ACV 空金额按零汇总，不改原值。"
            "basis=entries 为逐笔实绩（默认），historical 为历史季度原值，auto 有历史原值时优先展示；"
            "两类金额独立返回，不相加。历史季度按原年季筛选，occurred_on 为空。")
async def read_assets(
    scope: Literal["self", "team"] | None = None,
    member_id: UUID | None = None,
    member_ids: list[UUID] = Query([], max_length=100),
    period: Literal["year", "all"] = "year",
    basis: Literal["entries", "historical", "auto"] = "entries",
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
        if identity.actor.role.value in FDE_ROLES:
            try:
                _, _, _, scoped = await scoped_opportunity_ids(connection, identity.actor, scope, member_id, member_ids)
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
    return {**result, "can_manage": can_manage(identity.actor)}


@router.post("", status_code=201)
async def create_actual(
    body: ActualCreate,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    idempotency_key: MutationKey = None,
):
    try:
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                "actuals.create",
                body.model_dump(),
                lambda: CustomerAssetRepository().create(connection, identity.actor, body.model_dump()),
            )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(409, "该客户此类实绩的来源编号已登记，请核对，勿重复提交") from exc
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
