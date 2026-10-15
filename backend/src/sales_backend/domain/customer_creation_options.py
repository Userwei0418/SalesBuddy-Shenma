"""Canonical creation choices; historical customer values remain separate."""

from typing import Literal, get_args

CustomerCreationType = Literal["潜在客户", "商机客户", "已成单客户"]
CustomerCreationSource = Literal["销售自拓", "客户转介绍", "市场活动", "销售线索", "合作伙伴", "其他"]

CUSTOMER_CREATION_TYPES = get_args(CustomerCreationType)
CUSTOMER_CREATION_SOURCES = get_args(CustomerCreationSource)

# This is the only accepted legacy creation alias. Assignment channel and
# customer tenure do not determine lead origin or customer lifecycle.
_SOURCE_ALIASES = {"自主拓展": "销售自拓"}


def normalize_creation_source(value: object) -> object:
    return _SOURCE_ALIASES.get(value, value) if isinstance(value, str) else value


def creation_choice_error(field: str) -> str | None:
    """Trusted messages only: never interpolate a rejected input or validator text."""
    if field == "source":
        return "客户来源选项不符合建档要求，请重新选择：" + "、".join(CUSTOMER_CREATION_SOURCES) + "。"
    if field == "customer_type":
        return "客户类型选项不符合建档要求，请重新选择：" + "、".join(CUSTOMER_CREATION_TYPES) + "。"
    return None
