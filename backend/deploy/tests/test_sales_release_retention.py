"""Destructive release retention safety checks; no server or app dependencies."""

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "sales-release-retention"
MODULE = runpy.run_path(str(SCRIPT))


class ReleaseRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.releases = self.root / "releases"
        self.releases.mkdir()
        self.proc = self.root / "mock-proc"
        self.proc.mkdir()
        self.config = self.root / "server-retention.conf"
        self.config.write_text(f"APP_ROOT='{self.root}'\nAPP_KEEP_RELEASES=3\nDB_KEEP_BACKUPS=3\n")
        self.names = [f"release-{i}" for i in range(7)]
        for i, name in enumerate(self.names):
            path = self.releases / name
            (path / "backend/src/sales_backend").mkdir(parents=True)
            (path / "backend" / "pyproject.toml").write_text("[project]\nname='app'\n")
            os.utime(path, ns=(i * 10**9, i * 10**9))
        (self.root / "current").symlink_to(self.releases / self.names[-1])

    def tearDown(self):
        self.temporary.cleanup()

    def plan(self):
        return MODULE["build_plan"](self.root, 3, self.proc)

    def deleted_names(self, plan):
        return {entry["name"] for entry in plan["delete"]}

    def apply(self, plan):
        with contextlib.redirect_stdout(io.StringIO()):
            return MODULE["apply_plan"](plan, self.proc)

    def test_current_older_than_latest_keeps_current_and_latest_two(self):
        (self.root / "current").unlink()
        (self.root / "current").symlink_to(self.releases / "release-0")
        plan = self.plan()
        self.assertEqual(self.deleted_names(plan), {"release-1", "release-2", "release-3", "release-4"})
        self.apply(plan)
        self.assertEqual({p.name for p in self.releases.iterdir()}, {"release-0", "release-5", "release-6"})

    def test_root_runtime_previous_and_rollback_are_extra_protected(self):
        (self.releases / "release-0" / ".venv").mkdir()
        (self.root / ".venv").symlink_to(self.releases / "release-0" / ".venv")
        (self.root / "previous").symlink_to(self.releases / "release-1")
        (self.root / "rollback").symlink_to(self.releases / "release-2")
        # Restore original sort time after adding the runtime directory.
        os.utime(self.releases / "release-0", ns=(0, 0))
        self.assertEqual(self.deleted_names(self.plan()), {"release-3"})

    def test_live_process_cwd_and_exe_are_protected(self):
        pid = self.proc / "123"
        pid.mkdir()
        (pid / "cwd").symlink_to(self.releases / "release-0" / "backend")
        (pid / "exe").symlink_to(self.releases / "release-1" / ".venv/bin/python")
        self.assertEqual(self.deleted_names(self.plan()), {"release-2", "release-3"})

    def test_flat_current_and_old_flat_release_match_real_server_layout(self):
        for name in ("release-0", "release-6"):
            release = self.releases / name
            (release / "backend/pyproject.toml").rename(release / "pyproject.toml")
            (release / "backend/src").rename(release / "src")
            (release / "backend").rmdir()
            stamp = int(name.split("-")[1]) * 10**9
            os.utime(release, ns=(stamp, stamp))
        plan = self.plan()
        self.assertEqual(plan["current"], "release-6")
        self.assertIn("release-0", self.deleted_names(plan))
        self.apply(plan)
        self.assertTrue((self.releases / "release-6/src/sales_backend").is_dir())

    def test_pyproject_without_real_application_source_is_unknown(self):
        for name in ("marker-only", "source-link", "package-link"):
            release = self.releases / name
            release.mkdir()
            (release / "pyproject.toml").write_text("[project]\n")
        (self.releases / "source-link/src").symlink_to(self.releases / "release-6/backend/src")
        (self.releases / "package-link/src").mkdir()
        (self.releases / "package-link/src/sales_backend").symlink_to(
            self.releases / "release-6/backend/src/sales_backend")
        skipped = {entry["name"] for entry in self.plan()["skipped"]}
        self.assertTrue({"marker-only", "source-link", "package-link"}.issubset(skipped))

    def test_symlink_and_unknown_directories_and_outside_target_are_not_deleted(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "proof").write_text("preserve")
        (self.releases / "external").symlink_to(outside)
        unknown = self.releases / "unknown"
        unknown.mkdir()
        (unknown / "important").write_text("preserve")
        (self.root / "rollback").symlink_to(outside)
        # A recognised release may contain an external symlink; rmtree must not follow it.
        (self.releases / "release-0" / "external-content").symlink_to(outside)
        os.utime(self.releases / "release-0", ns=(0, 0))
        self.apply(self.plan())
        self.assertEqual((outside / "proof").read_text(), "preserve")
        self.assertEqual((unknown / "important").read_text(), "preserve")
        self.assertTrue((self.releases / "external").is_symlink())

    def test_symlinked_backend_or_marker_is_unrecognised(self):
        alias = self.releases / "alias"
        alias.mkdir()
        (alias / "backend").symlink_to(self.releases / "release-6/backend")
        marker = self.releases / "marker"
        (marker / "backend").mkdir(parents=True)
        (marker / "backend/pyproject.toml").symlink_to(self.releases / "release-6/backend/pyproject.toml")
        plan = self.plan()
        self.assertTrue({"alias", "marker"}.issubset({e["name"] for e in plan["skipped"]}))

    @unittest.skipUnless(Path("/proc").is_dir(), "CLI process protection requires Linux /proc")
    def test_default_cli_dry_run_deletes_nothing_and_apply_uses_same_plan(self):
        run = subprocess.run([sys.executable, str(SCRIPT), "--config", str(self.config)],
                             text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        plan = json.loads(run.stdout.splitlines()[0])
        self.assertEqual(plan["mode"], "dry-run")
        self.assertEqual(len(list(self.releases.iterdir())), 7)
        applied = subprocess.run([sys.executable, str(SCRIPT), "--config", str(self.config), "--apply"],
                                 text=True, capture_output=True)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(len(list(self.releases.iterdir())), 3)

    def test_new_process_protection_before_apply_aborts_without_deletion(self):
        plan = self.plan()
        pid = self.proc / "234"
        pid.mkdir()
        first = plan["delete"][0]["name"]
        (pid / "cwd").symlink_to(self.releases / first)
        with self.assertRaisesRegex(RuntimeError, "became protected"):
            self.apply(plan)
        self.assertEqual(len(list(self.releases.iterdir())), 7)

    def test_missing_or_external_current_fails_closed(self):
        (self.root / "current").unlink()
        with self.assertRaisesRegex(ValueError, "current"):
            self.plan()
        (self.root / "current").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "current"):
            self.plan()

    def test_replaced_release_identity_aborts_before_removal(self):
        plan = self.plan()
        candidate = self.releases / plan["delete"][0]["name"]
        candidate.rename(self.root / "original-release")
        (candidate / "backend/src/sales_backend").mkdir(parents=True)
        (candidate / "backend/pyproject.toml").write_text("[project]\nname='replacement'\n")
        os.utime(candidate, ns=(plan["delete"][0]["mtime_ns"],) * 2)
        with self.assertRaisesRegex(RuntimeError, "changed since plan"):
            self.apply(plan)
        self.assertTrue(candidate.exists())

    def test_nested_mount_aborts_before_removal(self):
        candidate = self.releases / "release-3"
        mounted = candidate / "data"
        mounted.mkdir()
        os.utime(candidate, ns=(3 * 10**9,) * 2)
        plan = self.plan()
        original = Path.is_mount
        with patch.object(Path, "is_mount", lambda path: path == mounted or original(path)):
            with self.assertRaisesRegex(RuntimeError, "mounted directory"):
                self.apply(plan)
        self.assertEqual(len(list(self.releases.iterdir())), 7)

    def test_shared_deployment_lock_prevents_execution(self):
        with (self.root / ".deploy.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run = subprocess.run([sys.executable, str(SCRIPT), "--config", str(self.config), "--apply"],
                                 text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("deployment-lock-busy", run.stdout)
        self.assertEqual(len(list(self.releases.iterdir())), 7)

    def test_process_inspection_permission_failure_is_not_ignored(self):
        pid = self.proc / "345"
        pid.mkdir()
        with patch.object(os, "readlink", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(RuntimeError, "cannot inspect process"):
                MODULE["process_references"](self.releases, self.proc)

    def test_shell_configuration_is_data_never_executed(self):
        proof = self.root / "must-not-exist"
        self.config.write_text(f"APP_ROOT='$(touch {proof})'\nAPP_KEEP_RELEASES=3\n")
        with self.assertRaises(ValueError):
            MODULE["configuration"](self.config)
        self.assertFalse(proof.exists())


if __name__ == "__main__":
    unittest.main()
