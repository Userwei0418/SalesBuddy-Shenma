from dataclasses import asdict, dataclass

CONTENT_KEYS = ("follow_up_record", "next_action")

FIRST_VISIT_KEYS = ("customer_main_business", "customer_needs", "customer_budget", "contact_role")

CONTACT_ROLES = ("使用者", "影响者", "决策者")

@dataclass(frozen=True)
class VisitField:
    field_key: str
    label: str
    data_type: str = "text"
    is_required: bool = False
    is_readonly: bool = False
    first_visit_required: bool = False

FIELDS = (
    VisitField("customer_name", "客户名称", is_required=True, is_readonly=True),
    VisitField("customer_type", "客户类型", is_readonly=True),
    VisitField("opportunity_name", "商机名称", is_readonly=True),
    VisitField("follow_up_record", "沟通内容", "long_text", True),
    VisitField("next_action", "下一步计划", "long_text", True),
    VisitField("interaction_at", "跟进日期", "date", True),
    VisitField("created_date", "创建时间", "date", True),
    VisitField("contact_name", "对接人", is_required=True),
    VisitField("partner_name", "伙伴名称"),
    VisitField("contact_title", "联系人职位"),
    VisitField("interaction_mode", "沟通方式"),
    VisitField("visit_location", "地点"),
    VisitField("visit_goal", "拜访目标", "long_text"),
    VisitField("is_first_visit", "首次拜访", "boolean"),
    VisitField("customer_main_business", "客户主营业务", "long_text", first_visit_required=True),
    VisitField("customer_needs", "客户需求", "long_text", first_visit_required=True),
    VisitField("customer_budget", "客户预算", first_visit_required=True),
    VisitField("contact_role", "联系人角色", "select", first_visit_required=True),
    VisitField("collaborator_ids", "协同人", "multi_select"),
)

TEXT_FIELDS = {f.field_key: f.label for f in FIELDS if f.data_type not in {"boolean", "multi_select"}}
