"""Display terminology is tenant configuration, not a new role or permission."""

import pytest

from sales_backend.domain.role_presentation import BusinessRoleLabels, business_role_labels, business_role_title


@pytest.mark.parametrize("value", [None, [], "客户经理", {"sales": ""}, {"sales": " 客户经理"},
                                    {"sales": "管理员"}, {"sales": False}, {"fde": "其他"},
                                    {"sales": "客" * 200}])
def test_rejects_unknown_roles_or_non_whitelisted_titles(value):
    with pytest.raises(ValueError):
        BusinessRoleLabels.model_validate(value)


def test_default_and_company_override_do_not_change_other_roles():
    defaults = business_role_labels({"other_setting": "preserved"})
    demo = business_role_labels({"business_role_labels": {"sales": "客户经理"}})
    assert business_role_title("sales", defaults, ["sales"]) == "一线销售"
    assert business_role_title("sales", demo, ["sales"]) == "客户经理"
    for role in ("fde", "fde_lead", "administrator", "operations", "manager"):
        assert business_role_title(role, demo, ["product_sales"]) is None


def test_product_department_titles_keep_supervisor_and_mixed_appointments_distinct():
    labels = {"sales": "客户经理"}
    assert business_role_title("sales", labels, ["product_sales", "product_sales"]) == "产品销售"
    assert business_role_title("supervisor", labels, ["product_sales"]) == "产品销售主管"
    assert business_role_title("supervisor", labels, ["product_sales", "sales"]) is None
    assert business_role_title("sales", labels, ["product_sales", "sales"]) == "客户经理"
    assert business_role_title("sales", labels, []) == "客户经理"
