from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.contracts.types import UUIDString
from sales_backend.contracts.visit_management import (
    VisitManagementDetail, VisitManagementPage, VisitManagementRecorderPage, visit_management_filters,
)
from sales_backend.db import Database
from sales_backend.repositories.visit_management import VisitManagementRepository

router = APIRouter(prefix='/api/v1/console/visits', tags=['Follow-up management'])


@router.get('', response_model=VisitManagementPage)
async def visits(
    filters: dict = Depends(visit_management_filters),
    page_size: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database),
):
    """正式跟进记录。all 为当前 visit.read 授权范围；mine 为实际跟进人本人。摘要和统计不包含草稿/撤回记录。"""
    async with database.transaction(identity.actor, readonly=True, isolation='repeatable_read') as connection:
        return await VisitManagementRepository().page(connection, limit=page_size, offset=offset, **filters)


@router.get('/recorders', response_model=VisitManagementRecorderPage)
async def recorders(
    filters: dict = Depends(visit_management_filters), recorder_q: str = Query('', max_length=100),
    page_size: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database),
):
    """仅从可见正式跟进生成目录，保留停用账号；q 筛记录，recorder_q 搜跟进人姓名/账号。"""
    async with database.transaction(identity.actor, readonly=True, isolation='repeatable_read') as connection:
        return await VisitManagementRepository().recorders(connection, recorder_q=recorder_q.strip(),
                                                            limit=page_size, offset=offset, **filters)


@router.get('/{visit_id}', response_model=VisitManagementDetail)
async def detail(
    visit_id: UUIDString, identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    """白名单详情，不暴露质检内部策略；辅助补充及原件下载仍由原写入/下载接口重新授权。"""
    try:
        async with database.transaction(identity.actor, readonly=True, isolation='repeatable_read') as connection:
            return await VisitManagementRepository().detail(connection, visit_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
