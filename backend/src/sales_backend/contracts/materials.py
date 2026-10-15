"""Typed inputs for catalog, transaction lines and independent operational facts."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
TradeRelation = Literal["direct", "distributor", "downstream"]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _BusinessTextInput(StrictInput):
    # Human explanation is preserved as text, never interpreted as a rule.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    @field_validator('*')
    @classmethod
    def valid_plain_text(cls, value):
        if isinstance(value, str):
            if '\x00' in value:
                raise ValueError('业务说明不能包含空字符')
            try:
                value.encode('utf-8')
            except UnicodeError:
                raise ValueError('业务说明包含无效Unicode字符') from None
        return value


class MaterialBusinessExample(_BusinessTextInput):
    scenario: str = Field(default='', max_length=500, strict=True)
    explanation: str = Field(default='', max_length=1000, strict=True)


class MaterialBusinessDefinition(_BusinessTextInput):
    description: str = Field(default='', max_length=1000, strict=True)
    applicability: str = Field(default='', max_length=2000, strict=True)
    exceptions: str = Field(default='', max_length=2000, strict=True)
    examples: list[MaterialBusinessExample] = Field(default_factory=list, max_length=5, strict=True)


class MaterialRuleParameters(StrictInput):
    default_service_months: int | None = Field(default=None, ge=1, le=120)
    oa_required: bool = False
    oa_cutoff_day: int | None = Field(default=None, ge=1, le=31)
    monthly_allocation: Literal["equal_calendar_months"] | None = None
    oa_deferral_policy: Literal["shift_full_term", "keep_original_end"] | None = None
    # Missing/null preserves the existing definition; an explicit object updates
    # it, including {} to clear. Repository compatibility owns that distinction.
    business_definition: MaterialBusinessDefinition | None = None


class MaterialInput(StrictInput):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    product_line_id: UUID
    unit: str = Field(min_length=1, max_length=50)
    catalog_price: Money
    currency: Literal["CNY"] = "CNY"
    tax_rate: Decimal = Field(ge=0, le=1, max_digits=7, decimal_places=6)
    recognition_rule: Literal["manual", "delivery", "monthly", "consumption"]
    rule_parameters: MaterialRuleParameters = Field(default_factory=MaterialRuleParameters)
    is_active: bool = True
    reason: str = Field(min_length=1, max_length=1000)


class MaterialUpdate(MaterialInput):
    version_no: int = Field(ge=1)


class ProductLineInput(StrictInput):
    id: UUID | None = None
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)


class ProductLinesUpdate(StrictInput):
    version_no: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)
    items: list[ProductLineInput] = Field(max_length=200)

    @model_validator(mode="after")
    def distinct_items(self):
        codes = [r.code for r in self.items]
        ids = [r.id for r in self.items if r.id]
        if len(codes) != len(set(codes)) or len(ids) != len(set(ids)):
            raise ValueError("产品线编码或标识重复")
        return self


class OpportunityMaterialInput(StrictInput):
    id: UUID | None = None
    material_id: UUID
    # Optional for old clients; supplied versions bind saving to the previewed catalog.
    catalog_version_no: int | None = Field(default=None, ge=1)
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=4)
    unit_price: Money
    usage: Literal["self_use", "resale"] = "self_use"
    expected_delivery_on: date | None = None
    expected_start_on: date | None = None
    expected_oa_on: date | None = None
    service_months: int | None = Field(default=None, ge=1, le=120)
    expected_account_count: int | None = Field(default=None, ge=0, le=100000000)
    expected_consumption_amount: Money | None = None
    expected_consumption_year: int | None = Field(default=None, ge=2000, le=2100)
    expected_consumption_quarter: int | None = Field(default=None, ge=1, le=4)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def line_constraints(self):
        if (self.expected_consumption_year is None) != (self.expected_consumption_quarter is None):
            raise ValueError("预计消耗期间须同时提供年和季度")
        if self.quantity * self.unit_price >= Decimal("10000000000000000"):
            raise ValueError("物料行金额超出支持范围")
        return self


class MaterialPreviewInput(StrictInput):
    year: int = Field(ge=2000, le=2100)
    quarter: int = Field(ge=1, le=4)


class MaterialDraftLine(OpportunityMaterialInput):
    client_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    material_id: UUID | None = None
    catalog_version_no: int | None = Field(default=None, ge=1)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=4)
    unit_price: Money | None = None
    usage: Literal["self_use", "resale"] | None = None

    @model_validator(mode="after")
    def line_constraints(self):
        # Incomplete input is a draft state, never a zero amount. The same
        # bounds as saved lines still apply once both values are present.
        if self.quantity is not None and self.unit_price is not None:
            if self.quantity * self.unit_price >= Decimal("10000000000000000"):
                raise ValueError("物料行金额超出支持范围")
        return self


class MaterialDraftPreviewInput(MaterialPreviewInput):
    draft_revision: int = Field(ge=0)
    customer_id: UUID | None = None
    opportunity_id: UUID | None = None
    expected_opportunity_version: int | None = Field(default=None, ge=1)
    trade_relation: TradeRelation
    upstream_opportunity_id: UUID | None = None
    material_lines: list[MaterialDraftLine] = Field(max_length=200)

    @model_validator(mode="after")
    def draft_context(self):
        if (self.opportunity_id is None) != (self.expected_opportunity_version is None):
            raise ValueError("编辑试算须同时提供商机及其版本")
        if self.opportunity_id is None and self.customer_id is None:
            raise ValueError("新建试算请选择客户")
        if (self.trade_relation == "downstream") != (self.upstream_opportunity_id is not None):
            raise ValueError("仅下游交易必须选择上游总代商机")
        keys = [line.client_key for line in self.material_lines]
        ids = [line.id for line in self.material_lines if line.id is not None]
        effective_ids = [str(line.id or line.client_key) for line in self.material_lines]
        if (len(keys) != len(set(keys)) or len(ids) != len(set(ids))
                or len(effective_ids) != len(set(effective_ids))):
            raise ValueError("草稿物料标识不能重复")
        if self.opportunity_id is None and ids:
            raise ValueError("新建草稿不能引用已有商机物料行")
        return self


class MaterialDraftLineResult(BaseModel):
    line_id: str
    client_key: str
    catalog_version_no: int | None
    snapshot_source: Literal['saved_line', 'current_catalog', 'missing']
    status: Literal['complete', 'incomplete', 'excluded']
    amount_inclusive: str | None
    amount_exclusive: str | None
    missing_fields: list[str]
    calculation_trace: dict[str, Any]


class MaterialDraftPreviewResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    reference_kind: Literal['sales_forecast_draft_reference']
    persisted: Literal[False]
    draft_revision: int
    opportunity_id: str | None
    opportunity_version: int | None
    period: dict[str, int]
    status: Literal['complete', 'incomplete']
    recognized_inclusive: str | None
    recognized_exclusive: str | None
    lines: list[MaterialDraftLineResult]
    missing_fields: list[str]
    calculation_trace: dict[str, Any]


class OpportunityMaterialsUpdate(StrictInput):
    version_no: int = Field(ge=1)
    trade_relation: TradeRelation
    upstream_opportunity_id: UUID | None = None
    material_lines: list[OpportunityMaterialInput] = Field(max_length=200)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def relation_shape(self):
        if (self.trade_relation == "downstream") != (self.upstream_opportunity_id is not None):
            raise ValueError("仅下游交易必须选择上游总代商机")
        return self


class ActualConfirmationEntry(StrictInput):
    material_line_id: UUID | None = None
    expected_version: int = Field(ge=0)
    action: Literal["confirm", "withdraw"] = "confirm"
    amount: Money | None = None
    source_ref: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def amount_for_action(self):
        if self.action == "confirm" and (self.amount is None or not self.source_ref):
            raise ValueError("确认实际须填写金额及核对依据；0是合法金额")
        if self.action == "withdraw" and (self.amount is not None or self.expected_version == 0):
            raise ValueError("撤回须指定已有版本且不传金额")
        return self


class ActualConfirmationsInput(MaterialPreviewInput):
    kind: Literal["recognized", "collection"]
    expected_version: int = Field(ge=0)
    expected_opportunity_version: int = Field(ge=1)
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    replace_source: bool = False
    expected_material_line_ids: list[UUID] | None = Field(default=None, max_length=200)
    replace_expected_materials: bool = False
    reason: str = Field(min_length=1, max_length=1000)
    empty_action: Literal["confirm", "withdraw"] | None = None
    entries: list[ActualConfirmationEntry] = Field(max_length=200)

    @model_validator(mode="after")
    def correct_grain(self):
        ids = [r.material_line_id for r in self.entries]
        if self.empty_action is not None:
            if self.kind != "recognized" or ids or self.expected_material_line_ids != []:
                raise ValueError("空集合决议仅用于确收，须传空应登记集合及空变更列表")
        elif not ids:
            raise ValueError("请填写实际变更；无自用确收须显式确认空集合")
        if self.kind == "collection":
            if len(ids) != 1 or ids[0] is not None or self.expected_material_line_ids is not None:
                raise ValueError("回款按商机期间填写一项，不关联物料行")
        elif None in ids or len(ids) != len(set(ids)):
            raise ValueError("确收必须逐项关联不同的交易物料行")
        if self.kind == "recognized":
            expected = self.expected_material_line_ids
            if expected is None or len(expected) != len(set(expected)):
                raise ValueError("确收须明确本期间应登记物料行集合且不能重复")
            if any(r.action == "confirm" and r.material_line_id not in expected for r in self.entries):
                raise ValueError("确认物料须属于本期间应登记集合")
        return self
