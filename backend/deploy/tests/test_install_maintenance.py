"""Offline installer fixtures; never call real systemctl or install into /etc."""

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


DEPLOY = Path(__file__).resolve().parents[1]


class MaintenanceInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stage = self.root / "staging"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "systemctl-calls"
        for name in ("systemctl", "flock"):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + """import os, sys
from pathlib import Path
with Path(os.environ['MOCK_CALL_LOG']).open('a') as out:
    out.write(' '.join(sys.argv) + '\\n')
if sys.argv[1:2] == ['show']:
    print('active' if os.environ.get('MOCK_BUSY') else 'inactive')
""")
            path.chmod(0o755)
        self.environment = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
                            "MOCK_CALL_LOG": str(self.log)}

    def tearDown(self):
        self.temp.cleanup()

    def install(self, *arguments, **environment):
        return subprocess.run(["bash", str(DEPLOY / "install-maintenance.sh"), *arguments],
                              env={**self.environment, **environment}, text=True, capture_output=True)

    def config(self):
        result = {}
        for line in (self.stage / "etc/server-retention.conf").read_text().splitlines():
            tokens = shlex.split(line, comments=True)
            if tokens:
                key, value = tokens[0].split("=", 1)
                self.assertNotIn(key, result, f"duplicate config key {key}")
                result[key] = value
        return result

    def test_first_install_and_repeat_stage_complete_files_without_host_commands(self):
        first = self.install("--destdir", str(self.stage))
        self.assertEqual(first.returncode, 0, first.stderr)
        expected = ["sales-db-backup", "sales-release-retention", "server-retention"]
        for name in expected:
            path = self.stage / "usr/local/sbin" / name
            self.assertEqual(path.read_bytes(), (DEPLOY / name).read_bytes())
            self.assertTrue(path.stat().st_mode & 0o100)
        self.assertEqual(self.config()["BLOG_ENABLED"], "0")
        before = (self.stage / "etc/server-retention.conf").read_bytes()
        second = self.install("--destdir", str(self.stage))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.stage / "etc/server-retention.conf").read_bytes(), before)
        snapshots = list((self.stage / "var/backups/server-retention").glob("install-salegent-*"))
        self.assertEqual(len(snapshots), 2)
        saved_configs = [path / "etc/server-retention.conf" for path in snapshots]
        self.assertEqual(sum(path.exists() for path in saved_configs), 1)
        self.assertFalse(self.log.exists(), "offline staging called a host service command")

    def test_existing_blog_custom_values_and_exact_files_preserved_in_snapshot(self):
        conf = self.stage / "etc/server-retention.conf"
        conf.parent.mkdir(parents=True)
        previous = "# Existing shared server policy\nBLOG_ENABLED=1\nAPP_KEEP_RELEASES=5\nDB_KEEP_BACKUPS=7\nBLOG_PROJECT_NAME=custom.example\n"
        conf.write_text(previous)
        script = self.stage / "usr/local/sbin/sales-db-backup"
        script.parent.mkdir(parents=True)
        script.write_text("existing backup implementation\n")
        untouched = self.stage / "etc/unrelated-user-file"
        untouched.write_text("do not touch")
        result = self.install("--destdir", str(self.stage))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config()["BLOG_ENABLED"], "1")
        self.assertEqual(self.config()["DB_KEEP_BACKUPS"], "7")
        self.assertEqual(self.config()["APP_KEEP_RELEASES"], "5")
        self.assertEqual(self.config()["BLOG_PROJECT_NAME"], "custom.example")
        self.assertEqual(self.config()["DB_BACKUP_TIMEOUT_SECONDS"], "3300")
        snapshot = next((self.stage / "var/backups/server-retention").glob("install-salegent-*"))
        self.assertEqual((snapshot / "etc/server-retention.conf").read_text(), previous)
        self.assertEqual((snapshot / "usr/local/sbin/sales-db-backup").read_text(), "existing backup implementation\n")
        self.assertEqual(len([p for p in snapshot.rglob("*") if p.is_file()]), 2)
        self.assertEqual(untouched.read_text(), "do not touch")
        self.assertFalse(self.log.exists())

    def test_busy_preflight_refuses_without_stop_start_or_file_changes(self):
        result = self.install("--check", MOCK_BUSY="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Refusing install", result.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 1)
        self.assertIn(" show server-retention.service ", calls[0])
        self.assertFalse(self.stage.exists())


if __name__ == "__main__":
    unittest.main()
