"""Run without app dependencies: python3 -m unittest discover -s backend/deploy/tests."""

import datetime as dt
import fcntl
import os
from pathlib import Path
import pwd
import runpy
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "sales-db-backup"


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backups = self.root / "backups"
        self.bin = self.root / "bin"
        self.backups.mkdir()
        self.bin.mkdir()
        self.config = self.root / "server-retention.conf"
        self.lock = self.root / "backup.lock"
        self.config.write_text(
            f"DB_BACKUP_DIR='{self.backups}'\nPG_BIN_DIR='{self.bin}'\n"
            f"DB_BACKUP_LOCK_FILE='{self.lock}'\nDB_OS_USER='{pwd.getpwuid(os.geteuid()).pw_name}'\n"
            "DB_KEEP_BACKUPS=3\nDB_MAX_AGE_DAYS=14\nMIN_FREE_GIB=0\n"
            "RELEASE_KEEP_COUNT=2 # shared non-database setting is ignored\n"
        )
        self.make_command("pg_dump", """import os, sys
sys.stdout.write('PGDMP_mock archive')
sys.exit(9 if os.environ.get('MOCK_DUMP_FAIL') else 0)
""")
        self.make_command("pg_restore", """import os, sys
from pathlib import Path
good = Path(sys.argv[-1]).read_bytes().startswith(b'PGDMP_')
sys.exit(1 if os.environ.get('MOCK_RESTORE_FAIL') or not good else 0)
""")

    def tearDown(self):
        self.temporary.cleanup()

    def make_command(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)

    def old_backup(self, days=1, seconds=0, directory=None):
        stamp = dt.datetime.now() - dt.timedelta(days=days, seconds=seconds)
        path = (directory or self.backups) / f"sales_saas_{stamp:%Y%m%d_%H%M%S}.dump"
        path.write_bytes(b"PGDMP_existing verified archive")
        return path

    def execute(self, **environment):
        return subprocess.run([sys.executable, str(SCRIPT), "--config", str(self.config)],
                              env={**os.environ, **environment}, text=True, capture_output=True)

    def prepare_long_export(self):
        marker = self.root / "export-pids"
        self.make_command("pg_dump", f"""import os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path({str(marker)!r}).write_text(f'{{os.getpid()}} {{child.pid}}')
sys.stdout.write('PGDMP_partial long export')
sys.stdout.flush()
time.sleep(60)
""")
        return marker

    def assert_export_stopped(self, marker):
        self.assertTrue(marker.exists(), "long export did not start")
        for pid in marker.read_text().split():
            status = subprocess.run(["ps", "-o", "stat=", "-p", pid], text=True, capture_output=True)
            self.assertTrue(status.returncode != 0 or status.stdout.strip().startswith("Z"),
                            f"export process {pid} still running: {status.stdout}")

    def cleanup_export(self, marker):
        if marker.exists():
            try:
                os.killpg(int(marker.read_text().split()[0]), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_success_keeps_three_and_never_traverses_rollback_directory(self):
        old = [self.old_backup(seconds=i) for i in range(5)]
        rollback = self.backups / "pre-switch-rollback"
        rollback.mkdir()
        nested = self.old_backup(days=90, directory=rollback)
        unrelated = self.backups / "sales_saas_older.dump"
        unrelated.write_bytes(b"do not remove")
        symlink = self.backups / "sales_saas_20000101_000000.dump"
        symlink.symlink_to(nested)
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BACKUP_OK", result.stdout)
        self.assertTrue(old[0].exists())
        self.assertTrue(old[1].exists())
        self.assertFalse(old[2].exists())
        self.assertEqual(len([p for p in self.backups.glob("sales_saas_*.dump")
                              if p.name != unrelated.name and not p.is_symlink()]), 3)
        self.assertTrue(nested.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(symlink.is_symlink())
        self.assertFalse(list(self.backups.glob(".*.partial")))

    def test_dump_failure_preserves_all_successful_backups_and_removes_only_own_partial(self):
        old = [self.old_backup(days=40, seconds=i) for i in range(5)]
        other_partial = self.backups / ".unrelated.partial"
        other_partial.write_text("unrelated")
        result = self.execute(MOCK_DUMP_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(all(p.exists() for p in old))
        self.assertEqual(set(self.backups.iterdir()), set(old + [other_partial]))
        self.assertNotIn("PRUNED", result.stdout)

    def test_verification_failure_does_not_publish_or_rotate(self):
        old = self.old_backup(days=60)
        result = self.execute(MOCK_RESTORE_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(list(self.backups.iterdir()), [old])

    def test_low_space_does_not_run_dump_delete_backups_or_control_services(self):
        old = self.old_backup(days=60)
        marker = self.root / "unsafe-call"
        for name in ("systemctl", "pg_dump"):
            self.make_command(name, f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.config.write_text(self.config.read_text().replace("MIN_FREE_GIB=0", "MIN_FREE_GIB=999999999"))
        result = self.execute(PATH=f"{self.bin}:{os.environ['PATH']}")
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertIn("insufficient free space", result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(list(self.backups.iterdir()), [old])

    def test_flock_skips_concurrent_run(self):
        with self.lock.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SKIPPED", result.stdout)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_space_falling_during_export_kills_group_and_preserves_backups(self):
        old = self.old_backup(days=60)
        marker = self.prepare_long_export()
        module = runpy.run_path(str(SCRIPT))
        config = module["configuration"](self.config)
        config["MIN_FREE_GIB"] = 8
        high = SimpleNamespace(free=9 * 1024**3)
        low = SimpleNamespace(free=7 * 1024**3)
        try:
            with mock.patch.object(module["shutil"], "disk_usage", side_effect=[high, high, low]):
                with self.assertRaises(module["InsufficientSpace"]):
                    module["backup"](config)
            self.assert_export_stopped(marker)
            self.assertEqual(list(self.backups.iterdir()), [old])
        finally:
            self.cleanup_export(marker)

    def test_export_timeout_kills_group_and_preserves_backups(self):
        old = self.old_backup(days=60)
        marker = self.prepare_long_export()
        self.config.write_text(self.config.read_text() + "DB_BACKUP_TIMEOUT_SECONDS=1\n")
        try:
            result = self.execute()
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("exceeded timeout", result.stderr)
            self.assert_export_stopped(marker)
            self.assertEqual(list(self.backups.iterdir()), [old])
        finally:
            self.cleanup_export(marker)

    def test_term_and_int_clean_partial_and_kill_export_group(self):
        old = self.old_backup(days=60)
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum):
                marker = self.prepare_long_export()
                marker.unlink(missing_ok=True)
                process = subprocess.Popen([sys.executable, str(SCRIPT), "--config", str(self.config)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 5
                    while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(marker.exists(), "long export did not start")
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=7)
                    self.assertEqual(process.returncode, 128 + signum, stderr)
                    self.assertIn("interrupted by", stderr)
                    self.assertNotIn("PRUNED", stdout)
                    self.assert_export_stopped(marker)
                    self.assertEqual(list(self.backups.iterdir()), [old])
                finally:
                    self.cleanup_export(marker)
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=3)

    def test_age_limit_always_preserves_one_verified_archive(self):
        latest = self.old_backup(days=40)
        oldest = self.old_backup(days=90)
        invalid = self.backups / "sales_saas_20000101_000000.dump"
        invalid.write_text("broken archive preserved for diagnosis")
        module = runpy.run_path(str(SCRIPT))
        module["rotate"](self.backups, 3, 14, self.bin / "pg_restore", dt.datetime.now())
        self.assertTrue(latest.exists())
        self.assertFalse(oldest.exists())
        self.assertTrue(invalid.exists())


if __name__ == "__main__":
    unittest.main()
