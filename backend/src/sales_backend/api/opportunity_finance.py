from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.contracts.materials import ActualConfirmationsInput
from sales_backend.db import Database
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.finance_workspace import FinanceWorkspaceRepository
from sales_backend.repositories.opportunity_finance import OpportunityFinanceRepository
from sales_backend.services.operations import management_write
from sales_backend.services.opportunity_finance import confirm_actuals_idempotently, confirm_source_idempotently

router = APIRouter(tags=["Opportunity finance"])


class FinanceTotal(BaseModel):
    amount: str | None = Field(description="全部已登记实绩，单位元；有冲突或没有数据时为null，不冒充0。")
    confirmed_subtotal: str | None
    conflict_period_count: int
    resolved_period_count: int
    tax_unknown_period_count: int
    partial_period_count: int = 0
    missing_period_count: int = 0
    complete: bool = False


class FinancialPeriod(BaseModel):
    year: int
    quarter: int
    kind: Literal["recognized", "collection"]
    amount: str | None
    source: Literal["entries", "historical", "operations"] | None
    status: Literal[
        "missing", "single_source", "confirmed", "needs_confirmation", "stale_confirmation", "partial", "withdrawn"
    ]
    source_fingerprint: str
    entry_amount: str | None
    entries: list[dict[str, Any]]
    history: list[dict[str, Any]]
    decision: dict[str, Any] | None
    tax_basis: str | None
    tax_unknown: bool
    confirmed_subtotal: str | None = None
    known_exclusive_subtotal: str | None = None
    confirmations: list[dict[str, Any]] = Field(default_factory=list)
    expected_material_line_ids: list[str] = Field(default_factory=list)
    expected_material_count: int = 0
    confirmed_material_count: int = 0
    missing_material_count: int = 0
    complete: bool = False
    empty_confirmation: bool = False
    reference_changed: bool = False
    version_no: int = 0
    period_precision: Literal['quarter'] = 'quarter'
    occurred_on: None = None


class FinancialOverview(BaseModel):
    schema_version: int
    currency: Literal["CNY"]
    opportunity_id: str
    opportunity_version: int = 0
    customer_id: str
    name: str
    totals: dict[Literal["recognized", "collection"], FinanceTotal]
    conflict_period_count: int
    quality: str
    coverage_note: str
    periods: list[FinancialPeriod]
    can_confirm: bool
    review_path: str


class FinanceWorkspaceActor(BaseModel):
    id: str | None
    name: str | None


class FinanceWorkspaceForecast(BaseModel):
    status: Literal["saved", "missing"]
    year: int
    quarter: int
    recognized_amount: str | None = Field(description="最新已保存的销售含税确收预测，单位元；null不等于0。")
    collection_amount: str | None = Field(description="最新已保存的销售回款预测，单位元。")
    collection_confidence: str | None
    updated_at: datetime | None
    updated_by: FinanceWorkspaceActor


class FinanceWorkspaceChangeField(BaseModel):
    model_config = ConfigDict(extra="allow")
    field: str | None
    label: str = Field(description="保存时的真实字段标签；标签内的万元等单位须原样呈现。")
    before: Any
    after: Any


class FinanceWorkspaceChange(BaseModel):
    id: str
    version_no: int
    created_at: datetime
    actor: FinanceWorkspaceActor
    fields: list[FinanceWorkspaceChangeField]
    detail_status: Literal["recorded", "unavailable"]


class FinanceWorkspaceChanges(BaseModel):
    items: list[FinanceWorkspaceChange]
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    order: Literal["created_at_desc_id_desc"]
    coverage_note: str


class FinanceWorkspace(BaseModel):
    schema_version: Literal[1]
    currency: Literal["CNY"]
    period: dict[str, int]
    history_revision: str = Field(pattern=r'^[0-9a-f]{64}$', description='首屏变化记录和商机版本摘要；后续翻页须传回，变化则409并保留输入')
    opportunity: dict[str, Any]
    sales_forecast: FinanceWorkspaceForecast
    materials: dict[str, Any]
    calculation_reference: dict[str, Any]
    actual: FinancialOverview
    changes: FinanceWorkspaceChanges


class SourceConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=2000, le=2100)
    quarter: int = Field(ge=1, le=4)
    kind: Literal["recognized", "collection"]
    source: Literal["entries", "historical"]
    selected_history_id: UUID | None = None
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$", description="GET返回的完整期间证据指纹，变动时409。")
    expected_version: int = Field(ge=0, description="当前来源决议version_no；尚无决议传0。")
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def selection(self):
        if (self.source == "historical") != (self.selected_history_id is not None):
            raise ValueError("历史来源必须明确选择一条原始证据；逐笔来源不得指定历史记录")
        if not self.reason.strip():
            raise ValueError("请填写核对理由")
        return self


async def _read(database, identity, opportunity_id, year=None, quarter=None):
    if (year is None) != (quarter is None):
        raise HTTPException(422, '年份和季度须同时提供')
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            return await OpportunityFinanceRepository().read(connection, opportunity_id, year=year, quarter=quarter)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get(
    "/api/v1/opportunities/{opportunity_id}/financial-overview",
    response_model=FinancialOverview,
    description="唯一当前实际与原始参考分开；运营事实不因旧资料变更失效。金额为元，确收不含税，部分登记单列小计。",
)
async def read_financial_overview(
    opportunity_id: UUID, identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database),
    year: int | None = Query(None,ge=2000,le=2100), quarter: int | None = Query(None,ge=1,le=4),
):
    return await _read(database, identity, opportunity_id, year, quarter)


@router.get("/api/v1/console/opportunities/{opportunity_id}/financial-overview", response_model=FinancialOverview)
async def management_financial_overview(
    opportunity_id: UUID,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
    year: int | None = Query(None,ge=2000,le=2100), quarter: int | None = Query(None,ge=1,le=4),
):
    return await _read(database, identity, opportunity_id, year, quarter)


@router.get(
    "/api/v1/console/opportunities/{opportunity_id}/finance-workspace",
    response_model=FinanceWorkspace,
    description="同一只读快照返回本季度销售已保存预测、冻结物料参考、独立实际和可分页变化过程；不执行推理或业务写入。",
)
async def management_finance_workspace(
    opportunity_id: UUID,
    year: int = Query(..., ge=2000, le=2100),
    quarter: int = Query(..., ge=1, le=4),
    history_limit: int = Query(20, ge=1, le=100),
    history_offset: int = Query(0, ge=0),
    expected_history_revision: str | None = Query(None, pattern=r'^[0-9a-f]{64}$'),
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    if history_offset and expected_history_revision is None:
        raise HTTPException(422, '继续翻页须携带首屏history_revision，请重新打开财务核对')
    try:
        async with database.transaction(identity.actor, readonly=True, isolation="repeatable_read") as connection:
            return await FinanceWorkspaceRepository().read(
                connection, opportunity_id, year=year, quarter=quarter,
                history_limit=history_limit, history_offset=history_offset,
                expected_history_revision=expected_history_revision,
            )
    except VersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post(
    "/api/v1/console/opportunities/{opportunity_id}/financial-sources/confirm",
    response_model=FinancialOverview,
    description="仅管理员/运营且有actual.create客户和商机双范围许可；只选择来源，不接收金额。必须幂等键，冲突409。",
)
async def confirm_financial_source(
    opportunity_id: UUID,
    body: SourceConfirmation,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    if idempotency_key is None:
        raise HTTPException(422, "来源确认必须携带Idempotency-Key")
    data = body.model_dump()
    return await management_write(
        database,
        identity.actor,
        None,
        f"opportunity.finance.confirm:{opportunity_id}",
        data,
        lambda c: confirm_source_idempotently(c, identity.actor, opportunity_id, data, idempotency_key),
    )


@router.post('/api/v1/console/opportunities/{opportunity_id}/actual-confirmations',response_model=FinancialOverview)
async def confirm_operational_actuals(
    opportunity_id: UUID, body: ActualConfirmationsInput, idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database),
):
    if idempotency_key is None:
        raise HTTPException(422, '运营确认必须携带Idempotency-Key')
    data = body.model_dump()
    return await management_write(
        database,identity.actor,None,f'opportunity.actual.confirm:{opportunity_id}',data,
        lambda c: confirm_actuals_idempotently(c,identity.actor,opportunity_id,data,idempotency_key),
    )
