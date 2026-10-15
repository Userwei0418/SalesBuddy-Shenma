"""Read-only creation history; separate from owner/FDE opportunity browsing."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.opportunity_creation_history import (
    HistoryOptions,
    HistoryOptionsQuery,
    HistoryPage,
    HistoryQuery,
)
from sales_backend.db import Database
from sales_backend.repositories import opportunity_creation_history as repository

router = APIRouter(prefix="/api/v1/opportunities/creation-history", tags=["Opportunity creation history"])


@router.get("", response_model=HistoryPage, description="按当前商机读取权限查看创建历史。self仅本人；"
            "team不带部门/成员为全部可见，含创建人未知的历史导入。部门按创建人当前有效任职。"
            "日期为上海业务创建日期，含首尾；未知日期不使用导入时间兜底。排序日期降序、未知末尾、ID升序。")
async def history(
    filters: Annotated[HistoryQuery, Query()],
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        return await repository.page(connection, identity.actor, filters)


@router.get("/options", response_model=HistoryOptions, description="专用创建人筛选目录；成员来自全部当前可读创建历史，"
            "保留停用成员，q仅搜索姓名。teams为授权有效叶子部门（含空部门），成员team_ids仅返回授权有效任职。"
            "total和分页均指筛选后的成员；self/team均可用，默认self。")
async def options(
    filters: Annotated[HistoryOptionsQuery, Query()],
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        return await repository.options(connection, identity.actor, filters)
