"""Company terminology changes labels, never role codes or authorization."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class BusinessRoleLabels(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sales: Literal["一线销售", "客户经理"] = "一线销售"


def business_role_labels(attributes):
    value = attributes.get("business_role_labels", {}) if isinstance(attributes, dict) else {}
    return BusinessRoleLabels.model_validate(value).model_dump()


def business_role_title(role, labels, department_kinds=()):
    if role in {"sales", "supervisor"} and department_kinds and all(
        kind == "product_sales" for kind in department_kinds
    ):
        return "产品销售" if role == "sales" else "产品销售主管"
    return labels.get("sales", "一线销售") if role == "sales" else None
