"""Server-owned input choices shared by read contracts and validation/presentation.

These are application rules, not tenant organization or facts. Team/member/partner
catalogs remain database-backed and permission-scoped. No business rows are used
as a substitute for the list of available choices.
"""
from sales_backend.domain.customer_identity import IDENTITY_OPTIONS
from sales_backend.contracts.visit_schema import CONTACT_ROLES
from sales_backend.domain.customer_creation_options import CUSTOMER_CREATION_SOURCES, CUSTOMER_CREATION_TYPES
from sales_backend.domain.opportunities import STAGES, STAGE_BY_PROBABILITY

CUSTOMER_OPTIONS = {
    "industry": ["智能制造", "企业软件", "新能源", "医药健康", "交通物流", "人工智能", "其他"],
    "customer_type": [*CUSTOMER_CREATION_TYPES, "新拓客户", "存量客户"],
    "level_code": ["Tier-1", "Tier-2", "Tier-3"],
    "source": [*CUSTOMER_CREATION_SOURCES[:-1], "公司分配", "自主拓展", CUSTOMER_CREATION_SOURCES[-1]],
    "contact_role": list(CONTACT_ROLES),
}
CUSTOMER_CREATION_OPTIONS = {
    "business_identities": IDENTITY_OPTIONS,
    **{field: list(values) for field, values in CUSTOMER_OPTIONS.items()},
    "customer_type": list(CUSTOMER_CREATION_TYPES),
    "source": list(CUSTOMER_CREATION_SOURCES),
}
OPPORTUNITY_GRADES = [
    {"code": "A", "label": "A｜100万以上", "amountLabel": "100万以上", "min": 1000000, "max": None},
    {"code": "B", "label": "B｜50万–100万", "amountLabel": "50万–100万", "min": 500000, "max": 1000000},
    {"code": "C", "label": "C｜10万–50万", "amountLabel": "10万–50万", "min": 100000, "max": 500000},
    {"code": "D", "label": "D｜10万以下", "amountLabel": "10万以下", "min": 0, "max": 100000},
]

MAP_AMOUNT_RANGES = [
    {"value":"0-50","label":"0–50 万","min":0,"max":499999.99},
    {"value":"50-100","label":"50–100 万","min":500000,"max":999999.99},
    {"value":"100-300","label":"100–300 万","min":1000000,"max":2999999.99},
    {"value":"300-500","label":"300–500 万","min":3000000,"max":4999999.99},
    {"value":"500+","label":"500 万以上","min":5000000,"max":None},
]


def business_options():
    stages = [{"code": code, "label": STAGES[probability].split("－")[0],
               "probability": probability, "status": "open", "text": STAGES[probability]}
              for probability, code in STAGE_BY_PROBABILITY.items()]
    stages += [{"code": "won", "label": "赢单 Won", "probability": 100, "status": "won", "text": STAGES[100]},
               {"code": "lost", "label": "丢单 Lost", "probability": None, "status": "lost", "text": "丢单 Lost"}]
    return {"contract_version": 1, "data_source": "server_rules", "customer": CUSTOMER_OPTIONS,
            "customer_creation": CUSTOMER_CREATION_OPTIONS,
            "opportunity": {"stages": stages, "grades": OPPORTUNITY_GRADES}, "map_amount_ranges": MAP_AMOUNT_RANGES}
