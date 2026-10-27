"""Narrow read-only monitor; existing dashboard and Review APIs remain intact."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.dashboard_metrics import (
    ActualMetricCode,
    DashboardActualPage,
    DashboardMetricPage,
    DashboardMetrics,
    ForecastMetricCode,
)
from sales_backend.contracts.dashboard_performance import DashboardPerformanceRankings, PerformanceMetricCode
from sales_backend.contracts.dashboard_pipeline import DashboardActivity, DashboardActivityPage, DashboardPipeline
from sales_backend.db import Database
from sales_backend.services import dashboard_activity as activity_service
from sales_backend.services import dashboard_metrics as service
from sales_backend.services import dashboard_performance as performance_service
from sales_backend.services import dashboard_pipeline as pipeline_service

router = APIRouter(prefix="/api/v1/dashboard/metrics", tags=["Business"])


@router.get("/rankings", response_model=DashboardPerformanceRankings, description=(
    "单季度六项财务业绩双榜，同一只读快照返回当前授权内有效部门与个人，不接受页面选人/团队缩窄。"
    "个人仅有效销售及销售主管参榜，兼任有效销售总经理、FDE、FDE主管或运营不参榜；这些人员的合法数据仍计入部门榜。"
    "排名功能权限与看板、客户、商机读取取交集，实绩另交实际读取权限；目标另行核权。"
    "部门有可比目标时按达成率，否则整榜按金额；个人按金额，并列同名次、ID稳定排序。"
    "未知金额不补零、不赋名次；部分金额标暂排。净额预测和Pipeline为逐物料冻结税率参考，"
    "分别按阶段≥70%和≥30%，没有同口径目标时不借用含税目标。原监控卡与旧ACV排名不变。"
))
async def performance_rankings(
    metric_code: PerformanceMetricCode,
    year: int = Query(ge=2000, le=2100),
    quarter: int = Query(ge=1, le=4),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        return await performance_service.rankings(connection, identity.actor, year=year, quarter=quarter,
                                                  metric_code=metric_code)


def selection(
    year: int = Query(ge=2000, le=2100),
    quarter: int = Query(ge=1, le=4),
    personal: bool = False,
    member_id: UUID | None = None,
    team_groups: list[str] = Query(default=[], max_length=100),
):
    return dict(year=year, quarter=quarter, personal=personal, member_id=member_id, team_groups=team_groups)


@router.get("", response_model=DashboardMetrics, description=(
    "看板单季度五指标：原三项为高信心回款预测、概率≥30%回款PPL、概率≥70%含税预测确收，"
    "仅open/won商机且不按概率加权。追加实际不含税确收和实际回款，按当前采用的季度实绩汇总，"
    "保留财务冻结归属，包含已关闭商机和客户级无商机实绩；登记完整仅指已有事实，不将未登记补零。"
    "沿用dashboard.read选择范围，与客户、商机读取权限相交；实际还受actual.read和原表RLS限制。"
    "金额单位元、精确小数字符串；缺报与0不同。目标另行核权，不以个人目标推算公司目标。"
    "季度时间进度按上海当前日含当天，历史季度100%、未来季度0%。无同范围可信昨日快照时日变化为空。"
))
async def metrics(
    selected: dict = Depends(selection),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        try:
            return await service.overview(connection, identity.actor, **selected)
        except service.InvalidDashboardSelection as exc:
            raise HTTPException(422, "VALIDATION_ERROR") from exc


@router.get("/opportunities", response_model=DashboardMetricPage, description=(
    "与指标卡共用季度、范围和资格判定的授权明细，包含资格/金额未完整登记的可见记录。"
    "每次重验当前权限；按名称和ID稳定排序，授权过滤、去重、指标资格和搜索之后再分页。"
    "仅返回可查看事实，不增加创建任务或跟进权限。"
))
async def opportunities(
    metric_code: ForecastMetricCode,
    selected: dict = Depends(selection),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        try:
            return await service.opportunities(connection, identity.actor, metric_code=metric_code,
                                               q=q, limit=limit, offset=offset, **selected)
        except service.InvalidDashboardSelection as exc:
            raise HTTPException(422, "VALIDATION_ERROR") from exc


@router.get("/actuals", response_model=DashboardActualPage, description=(
    "与实际指标卡共用季度、授权范围、冻结负责人/团队和已采用实绩来源；不按商机阶段排除历史实绩。"
    "每行是有效财务事实或缺项占位，不保证是逐笔交易；客户级实绩的opportunity_id为空。"
    "保留季度精度、税口径和完整性状态，权限/去重/搜索后再分页；仅返回当前可读字段。"
))
async def actuals(
    metric_code: ActualMetricCode,
    selected: dict = Depends(selection),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        try:
            return await service.actuals(connection, identity.actor, metric_code=metric_code,
                                        q=q, limit=limit, offset=offset, **selected)
        except service.InvalidDashboardSelection as exc:
            raise HTTPException(422, "VALIDATION_ERROR") from exc


@router.get("/pipeline", response_model=DashboardPipeline, description=(
    "所选季度PPL阶段漏斗：open/won且概率≥30%的可读商机按当前概率互斥分组，数量不依赖金额是否登记。"
    "按季度预测记录聚合，不按预计关单日期；每阶段高信心预测回款、≥30%回款PPL及≥70%含税确收"
    "复用现有指标资格。人民币元精确字符串；未纳入、未填写、部分小计和明确0分别表达。"
    "仅当前dashboard、商机和客户读取权限交集，不提供写入或额外明细权限。"
))
async def pipeline(
    selected: dict = Depends(selection),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        try:
            return await pipeline_service.pipeline(connection, identity.actor, **selected)
        except service.InvalidDashboardSelection as exc:
            raise HTTPException(422, "VALIDATION_ERROR") from exc


@router.get("/activity", response_model=DashboardActivity, description=(
    "季度活跃商机沿用商机经营active_quarter：所选季度已填预测回款或含税确收任一>0，不额外排除商机状态。"
    "按当前北京时间近7自然日（今日及前6日）已发生的正式跟进分类，最近跟进不限所选季度。"
    "商机、客户、看板权限取交集，跟进日期另受visit.read与RLS约束；不返回跟进正文。"
    "近7日可见跟进可确认recent；没有近7日证据且对象跟进读取不完整归unknown。"
    "完整读取且无跟进为never，属于overdue子集。active=recent+overdue+unknown，never不能另加。"
))
async def activity(
    selected: dict = Depends(selection),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        try:
            return await activity_service.overview(connection, identity.actor, **selected)
        except service.InvalidDashboardSelection as exc:
            raise HTTPException(422, "VALIDATION_ERROR") from exc


@router.get("/activity/opportunities", response_model=DashboardActivityPage, description=(
    "与季度活跃环图共用完整授权数据源，仅列确定超7日或完整读取下从未跟进的商机。"
    "从未跟进优先，再按最后跟进时间升序、ID稳定排序；授权、分类、搜索之后才分页。"
    "total只在来源与跟进分类完整时给出，否则null并保留known_total。"
    "当季金额为原始已填计划（元、确收含税），不冒充高信心或≥70%预测；非人民币金额不混算。"
))
async def activity_opportunities(
    selected: dict = Depends(selection),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
):
    async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
        try:
            return await activity_service.opportunities(connection, identity.actor, q=q, limit=limit,
                                                        offset=offset, **selected)
        except service.InvalidDashboardSelection as exc:
            raise HTTPException(422, "VALIDATION_ERROR") from exc
