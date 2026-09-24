"""Publication copies must not displace customer-reviewed fact safeguards."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("spec_builder", Path(__file__).with_name("build-agent-specs.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class AgentSpecTests(unittest.TestCase):
    def test_all_twelve_prompts_resolve(self):
        self.assertEqual(len(builder.LABELS), 12)
        for capability in builder.LABELS:
            source, prompt = builder.prompt_for(capability)
            self.assertTrue(source.is_file())
            self.assertTrue(prompt.strip())

    def test_customer_guards_survive_publication_copies(self):
        for capability, guard in {
            "customer_advice": "事实一致性检查", "opportunity_advice": "时间与状态证据",
            "visit_advice": "空资料与证据边界", "operating_report": "空资料边界",
            "battle_map_review": "可见文字只说明业务事实", "personal_risks": "不能从 current_time 借用时分",
        }.items():
            self.assertIn(guard, builder.prompt_for(capability)[1])


if __name__ == "__main__":
    unittest.main()
