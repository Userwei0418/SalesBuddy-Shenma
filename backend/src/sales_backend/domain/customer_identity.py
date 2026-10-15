"""Enterprise roles are independent of acquisition source and referral partners."""
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from sales_backend.domain.customer_creation_options import CUSTOMER_CREATION_TYPES

BusinessIdentity = Literal["customer", "partner"]
BusinessIdentityFilter = Literal["all", "customer", "partner"]
IDENTITY_OPTIONS = [{"value": "customer", "label": "客户"}, {"value": "partner", "label": "合作伙伴"}]


def canonical_identities(values):
    if not isinstance(values, list) or not values or any(v not in ("customer", "partner") for v in values):
        raise ValueError("请至少选择客户或合作伙伴业务身份")
    return [v for v in ("customer", "partner") if v in values]


BusinessIdentities = Annotated[list[BusinessIdentity], Field(min_length=1, max_length=2), AfterValidator(canonical_identities)]


def identity_update(data, before):
    """Validate only changed identity/type fields; old unrelated edits preserve history."""
    result = dict(data)
    roles = before.get("business_identities") or ["customer"]
    if "business_identities" in result:
        roles = canonical_identities(result["business_identities"])
        result["business_identities"] = roles
    if "business_identities" in result or "customer_type" in result:
        kind = result.get("customer_type", before.get("customer_type_code"))
        if "customer" not in roles:
            if result.get("customer_type") is not None:
                raise ValueError("纯合作伙伴不填写客户类型")
            result["customer_type"] = None
        elif kind not in CUSTOMER_CREATION_TYPES:
            # Existing customer classifications may be echoed unchanged, including
            # when adding only the partner role. Adding customer still requires a
            # current type; never reinterpret a historical value as a new choice.
            prior_roles = before.get("business_identities") or ["customer"]
            if "customer" not in prior_roles or kind != before.get("customer_type_code"):
                raise ValueError("客户身份请选择潜在客户、商机客户或已成单客户")
    profile = result.get("partner_profile_id")
    prior_profile = before.get("partner_profile_id")
    if "partner_profile_id" in result and prior_profile and str(profile) != str(prior_profile):
        raise ValueError("已有自身伙伴档案关联不可替换或清空")
    if profile and not prior_profile and "partner" not in roles:
        raise ValueError("关联自身伙伴档案需要合作伙伴业务身份")
    return result


def legacy_compatible_payload(body, *, mode='python'):
    """Adding omitted optional fields must not change a legacy idempotency key's payload."""
    added = {'business_identities', 'partner_profile_id', 'partner_id'}
    return body.model_dump(mode=mode, exclude=added - body.model_fields_set)
