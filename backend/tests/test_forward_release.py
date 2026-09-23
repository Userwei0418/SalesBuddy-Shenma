import hashlib
import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_upgrade", Path(__file__).parents[1] / "deploy/release_upgrade.py"
)
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)
COMMIT = "a" * 40


def migration_state():
    plan = [{"key": key, "sha256": hashlib.sha256(key.encode()).hexdigest()}
            for key in ("BASELINE_V1", "S001", "V035", "V067", "V068")]
    return plan, {row["key"]: row["sha256"] for row in plan[:-1]}, {"V001", "V035", "V067"}


def test_upgrade_verifies_baseline_history_and_allows_next_migration():
    plan, prior, registered = migration_state()
    assert upgrade.verify_migration_plan(plan, prior, registered, "V067", "V068") == ["V068"]
    prior["V068"] = plan[-1]["sha256"]
    registered.add("V068")
    assert upgrade.verify_migration_plan(plan, prior, registered, "V068", "V068") == []
    plan.append({"key": "V069", "sha256": "f" * 64})
    assert upgrade.verify_migration_plan(plan, prior, registered, "V068", "V069") == ["V069"]


@pytest.mark.parametrize("problem", [
    "changed_applied_file", "deleted_applied_file", "missing_historical_checksum",
    "unregistered_checksum", "new_seed", "missing_baseline", "wrong_source", "wrong_target", "downgrade",
])
def test_upgrade_refuses_unreviewed_or_inconsistent_migrations(problem):
    plan, prior, registered = migration_state()
    source, target = "V067", "V068"
    if problem == "changed_applied_file":
        prior["V035"] = "0" * 64
    elif problem == "deleted_applied_file":
        plan = [row for row in plan if row["key"] != "V035"]
    elif problem == "missing_historical_checksum":
        prior.pop("V035")
    elif problem == "unregistered_checksum":
        registered.remove("V035")
    elif problem == "new_seed":
        plan.append({"key": "S002", "sha256": "b" * 64})
    elif problem == "missing_baseline":
        prior.pop("BASELINE_V1")
    elif problem == "wrong_source":
        source = "V066"
    elif problem == "wrong_target":
        target = "V069"
    else:
        target = "V066"
    with pytest.raises(upgrade.ReleaseError):
        upgrade.verify_migration_plan(plan, prior, registered, source, target)


def make_archive(tmp_path, *, extra=None, symlink=False):
    files = {
        "backend/pyproject.toml": b"[project]\n",
        "backend/src/sales_backend/main.py": b"# source\n",
        "database/scripts/migrate.py": b"# migration entry\n",
    }
    archive, manifest = tmp_path / "source.tar", tmp_path / "manifest.json"
    with tarfile.open(archive, "w") as output:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(content), 0o644
            output.addfile(member, io.BytesIO(content))
        if extra:
            member = tarfile.TarInfo(extra)
            if symlink:
                member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
                output.addfile(member)
            else:
                member.size = 1
                output.addfile(member, io.BytesIO(b"x"))
    meta = {"commit": COMMIT, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "file_hashes": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    manifest.write_text(json.dumps(meta))
    return archive, manifest, files


def test_archive_checks_source_and_extracts_readable_independent_release(tmp_path):
    archive, manifest, files = make_archive(tmp_path)
    target = tmp_path / "release"
    previous = os.umask(0o077)
    try:
        report = upgrade.unpack_archive(archive, manifest, COMMIT, target)
    finally:
        os.umask(previous)
    assert report["verified_source_files"] == 3
    assert (target / "REVISION").read_text() == COMMIT + "\n"
    for name, content in files.items():
        assert (target / name).read_bytes() == content
        assert (target / name).stat().st_mode & 0o777 == 0o644
    assert target.stat().st_mode & 0o777 == 0o755
    assert (target / "backend/src/sales_backend").stat().st_mode & 0o777 == 0o755
    with pytest.raises(upgrade.ReleaseError, match="already exists"):
        upgrade.unpack_archive(archive, manifest, COMMIT, target)


@pytest.mark.parametrize(("extra", "symlink"), [
    ("untracked.txt", False), ("../outside", False), ("backend/.env", False),
    ("backend/link", True), ("backend/pyproject.toml", False),
])
def test_archive_rejects_untracked_private_escaping_and_special_members(tmp_path, extra, symlink):
    archive, manifest, _ = make_archive(tmp_path, extra=extra, symlink=symlink)
    target = tmp_path / "release"
    with pytest.raises(upgrade.ReleaseError):
        upgrade.unpack_archive(archive, manifest, COMMIT, target)
    assert not target.exists()


@pytest.mark.parametrize("problem", ["archive_digest", "source_digest", "commit"])
def test_archive_rejects_digest_or_commit_mismatch_before_extracting(tmp_path, problem):
    archive, manifest, _ = make_archive(tmp_path)
    data = json.loads(manifest.read_text())
    if problem == "archive_digest":
        data["sha256"] = "0" * 64
    elif problem == "source_digest":
        data["file_hashes"]["backend/pyproject.toml"] = "0" * 64
    else:
        data["commit"] = "b" * 40
    manifest.write_text(json.dumps(data))
    target = tmp_path / "release"
    with pytest.raises(upgrade.ReleaseError):
        upgrade.unpack_archive(archive, manifest, COMMIT, target)
    assert not target.exists()
