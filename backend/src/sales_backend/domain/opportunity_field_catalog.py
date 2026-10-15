"""Business field catalog. It describes capability, never grants record access."""

from copy import deepcopy

from sales_backend.domain.opportunities import STAGES, STAGE_BY_PROBABILITY

DEFINITION_VERSION = "opportunity_workspace_v2"
ENUM_OPTIONS = {
    "customer_source": [
        ("company_lead", "公司线索"), ("partner_existing", "伙伴存量客户"),
        ("marketing_event", "市场活动"), ("customer_referral", "以客带客"),
        ("partner_stock", "伙伴压货"), ("digital_marketing", "数字营销"), ("sales_outbound", "销售自拓"),
    ],
    "priority": [("p0", "P0")],
    "trial_status": [("preparing", "素材准备中"), ("requested", "申请试用"), ("enabled", "开通试用")],
    "ppl_status": [("created", "已创建"), ("not_created", "未创建")],
    "external_system_status": [
        ("commit", "commit 十拿九稳"), ("high", "High 很有可能"),
        ("medium", "Medium 一半赢率"), ("low", "low 三成把握"),
    ],
    "fde_support_stage": [("trial_support", "试用支持"), ("demo_build", "Demo 搭建"), ("project_delivery", "项目落地")],
    "stage": [(code, STAGES[probability].split("－")[0]) for probability, code in STAGE_BY_PROBABILITY.items()]
             + [("won", "赢单 Won"), ("lost", "丢单 Lost")],
    "status": [("open", "在推"), ("won", "赢单"), ("lost", "输单")],
    "sales_channel": [("direct", "直签"), ("partner", "伙伴签约")],
    "collection_confidence": [("high", "高"), ("low", "低")],
}
PARTICIPANT_ROLES = [{"value": key, "label": label} for key, label in
                     [("sales", "销售参与者"), ("fde", "FDE参与者"), ("other", "其他参与者")]]


def _field(key, label, kind, *, edit="none", nullable=True, group=False, width=160, length=None):
    empty = ["is_empty", "is_not_empty"]
    ops = {
        "text": ["eq", "ne", "in", "contains"],
        "enum": ["eq", "ne", "in"], "person": ["eq", "ne", "in"],
        "boolean": ["eq", "ne"],
        "date": ["eq", "ne", "gt", "gte", "lt", "lte", "between"],
        "datetime": ["eq", "ne", "gt", "gte", "lt", "lte", "between"],
        "money": ["eq", "ne", "gt", "gte", "lt", "lte", "between"],
        "number": ["eq", "ne", "gt", "gte", "lt", "lte", "between"],
        "multi_enum": ["has_any", "has_all"], "people": ["has_any", "has_all"],
    }[kind] + empty
    result = {"key": key, "label": label, "type": kind, "nullable": nullable,
              "read_only": edit == "none", "editable": False, "edit_mode": edit,
              "filter_ops": ops, "sortable": kind not in {"multi_enum", "people"},
              "groupable": group, "width": width}
    if key in ENUM_OPTIONS:
        result["options"] = [{"value": v, "label": label} for v, label in ENUM_OPTIONS[key]]
    if length is not None:
        result["max_length"] = length
    return result


_FIELDS = [
    _field("id", "商机ID", "text", nullable=False),
    _field("name", "商机名称", "text", edit="inline", nullable=False, width=240, length=200),
    _field("customer_name", "客户名称", "text", nullable=False, width=180),
    _field("stage", "商机阶段", "enum", edit="panel", nullable=False, group=True, width=130),
    _field("status", "商机状态", "enum", edit="panel", nullable=False, group=True, width=130),
    _field("owner", "商机销售", "person", group=True, width=130),
    _field("team", "所属团队", "person", group=True, width=140),
    _field("amount", "ACV（元）", "money", edit="panel", width=150),
    _field("sales_channel", "签单方式", "enum", edit="panel", group=True),
    _field("partner", "所属伙伴", "person", edit="panel", group=True),
    _field("product_line", "产品线", "text", group=True, length=200),
    _field("expected_close_date", "预计关单日期", "date", edit="panel"),
    _field("expected_close_year", "预计关单年份", "number", group=True),
    _field("expected_close_quarter", "预计关单季度", "number", group=True),
    _field("customer_source", "客户来源", "multi_enum", edit="inline", nullable=False),
    _field("priority", "优先级", "enum", edit="inline", group=True),
    _field("is_key", "是否重点", "boolean", edit="inline", group=True),
    _field("trial_status", "是否开通试用", "enum", edit="inline", group=True),
    _field("summary", "一句话总结", "text", edit="inline", width=260, length=500),
    _field("note", "备注", "text", edit="inline", width=260, length=5000),
    _field("ppl_status", "系统是否创建PPL", "enum", edit="inline", group=True),
    _field("external_system_status", "系统里状态", "enum", edit="inline", group=True),
    _field("primary_fde_id", "主责FDE", "person", edit="panel", group=True),
    _field("fde_member_ids", "长期协助FDE", "people", edit="panel"),
    _field("participants", "项目参与人员", "people", edit="panel"),
    _field("fde_support_stage", "FDE支持阶段", "enum", edit="inline", group=True),
    _field("fde_progress", "FDE进展", "text", edit="inline", width=260, length=5000),
    _field("follow_up_plan", "下一步计划", "text", edit="inline", width=260, length=5000),
    _field("customer_owner", "客户所属销售", "person", group=True),
    _field("region", "所属区域", "text", group=True),
    _field("creator", "创建人", "person", group=True),
    _field("business_created_on", "商机创建日期", "date"),
    _field("latest_followup_at", "最近一次跟进时间", "datetime"),
    _field("latest_progress", "最新进展", "text", width=260),
    _field("has_followup_7d", "7天内有跟进", "boolean", nullable=False, group=True),
    _field("demo_count", "Demo数量", "number", nullable=False),
    _field("forecast_recognized", "期间预测含税确收（元）", "money", edit="panel"),
    _field("forecast_collection", "期间预测回款（元）", "money", edit="panel"),
    _field("collection_confidence", "期间回款信心", "enum", edit="panel", group=True),
    _field("actual_recognized", "期间实际不含税确收（元）", "money", edit="panel"),
    _field("actual_collection", "期间实际回款（元）", "money", edit="panel"),
]
FIELD_CATALOG = {item["key"]: item for item in _FIELDS}
DIRECTORIES = {
    "partner": {"url": "/api/v1/directory/partners", "kind": "partner"},
    "primary_fde_id": {"url": "/api/v1/directory/opportunity-members?kind=fde", "kind": "person"},
    "fde_member_ids": {"url": "/api/v1/directory/opportunity-members?kind=fde", "kind": "person"},
    "participants": {"url": "/api/v1/directory/opportunity-members?kind=all", "kind": "person"},
    "team": {"source": "scope_options.teams", "kind": "team"},
    "owner": {"source": "scope_options.owners", "kind": "person"},
    "customer_owner": {"source": "scope_options.owners", "kind": "person"},
    "creator": {"url": "/api/v1/directory/opportunity-members?kind=all", "kind": "person"},
}


def field_catalog(*, editable_fields=()):
    """Caller supplies independently checked permissions; default is read-only."""
    fields = deepcopy(_FIELDS)
    for item in fields:
        item["editable"] = not item["read_only"] and item["key"] in editable_fields
        if item["key"] in DIRECTORIES:
            item["directory"] = deepcopy(DIRECTORIES[item["key"]])
    return fields
