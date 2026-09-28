"""Policy selection and idempotent deployment tests; no customer environment used."""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("connection_lock", Path(__file__).with_name("configure-model-connection-lock.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConnectionLockTest(unittest.TestCase):
    def test_install_preserves_customer_config_and_toggle_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "default.conf"
            assets = root / "assets"
            assets.mkdir()
            original = 'server {\n    listen 443 ssl;\n    server_name customer.example;\n    location / { return 200 "customer-brand"; }\n}\n'
            config.write_text(original)
            module.configure(config, assets, True)
            enabled = config.read_text()
            module.configure(config, assets, True)
            self.assertEqual(config.read_text(), enabled)
            self.assertIn('return 200 "customer-brand"', enabled)
            self.assertIn("server_name customer.example", enabled)
            self.assertIn("default 1;", (assets / "model-connection-lock.conf").read_text().split("}")[0])
            module.configure(config, assets, False)
            self.assertEqual(config.read_text(), enabled)
            self.assertIn("default 0;", (assets / "model-connection-lock.conf").read_text().split("}")[0])

    def test_policy_covers_provider_model_switch_and_balance_but_not_agent_calls(self):
        source = (module.SOURCE / "assets/model-connection-lock.conf").read_text()
        pattern = re.search(r"    ~(\^1:[^\s]+) 1;", source).group(1)
        prefix = "/console/api/workspaces/current/"
        for path in (
            "model-providers/vendor/plugin/model/credentials", "model-providers/vendor/plugin/credentials/switch",
            "model-providers/vendor/plugin/models/credentials/switch", "model-providers/vendor/plugin/models/disable",
            "model-providers/vendor/plugin/models/load-balancing-configs/credentials-validate", "default-model",
        ):
            self.assertRegex("1:" + prefix + path, pattern)
            self.assertIsNone(re.search(pattern, "0:" + prefix + path))
        for path in ("/v1/chat-messages", "/console/api/apps/a/chat-messages", "/console/api/apps/a/model-config",
                     "/openapi/v1/apps/a/invoke", "/console/api/workspaces/current/models/model-types/llm"):
            self.assertIsNone(re.search(pattern, "1:" + path))


if __name__ == "__main__":
    unittest.main()
