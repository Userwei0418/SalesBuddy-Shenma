import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("finance_plan", Path(__file__).parents[1] / "scripts/configure_feishu_finance_view.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FinanceViewPlanTests(unittest.TestCase):
    def config(self):
        return {"base_url": "https://example.feishu.cn/base/base123", "admin_url": "https://crm.example/admin",
                "company_code": "tenant-one", "tables": {kind: {"id": "tbl" + kind, "name": name} for kind, name in
                (("opportunity", "商机"), ("forecast", "季度预测"), ("actual", "实绩"), ("history", "历史实绩"))}}

    def test_dynamic_forecast_and_unknown_amount(self):
        plan = module.build_plan(self.config())
        formulas = {field["name"]: field["expression"] for field in plan["field_groups"][-1]["fields"] if field["type"] == "formula"}
        for name in ("本年预计确收（含税元）", "本年预计回款（含税元）"):
            expression = formulas[name]
            self.assertIn("YEAR(TODAY())", expression)
            self.assertIn("NOT(ISBLANK(CurrentValue.", expression)
            self.assertIn('=0,"",', expression)
            self.assertIn('CurrentValue.[记录状态]="有效"', expression)
            self.assertNotIn("2026", expression)

    def test_stable_identity_and_no_formula_write_mapping(self):
        plan = module.build_plan(self.config())
        for group in plan["field_groups"][:3]:
            self.assertIn("COUNTA([关联商机].[系统记录ID])=1", group["fields"][0]["expression"])
        parent_fields = {field["name"]: field for field in plan["field_groups"][-1]["fields"]}
        for target in plan["writable_mapping_by_name"].values():
            self.assertNotEqual("formula", parent_fields[target]["type"])
        self.assertIn("company=tenant-one&finance=", parent_fields["财务核对入口"]["expression"])
        self.assertIn("#opportunities", parent_fields["财务核对入口"]["expression"])

    def test_reject_ambiguous_or_unsafe_coordinates(self):
        config = self.config()
        config["tables"]["actual"]["id"] = config["tables"]["history"]["id"]
        with self.assertRaises(ValueError):
            module.build_plan(config)
        config = self.config()
        config["admin_url"] = "http://crm.example/admin"
        with self.assertRaises(ValueError):
            module.build_plan(config)
