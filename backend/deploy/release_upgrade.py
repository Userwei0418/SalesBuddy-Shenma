"""Nonsecret archive and database guards for an existing managed release."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path, PurePosixPath


class ReleaseError(ValueError):
    pass


def version_number(value: str) -> int:
    if not re.fullmatch(r"V\d{3,}", value):
        raise ReleaseError("Invalid schema version")
    return int(value[1:])


def verify_migration_plan(plan, prior, registered, source, target):
    start, end = version_number(source), version_number(target)
    if end < start:
        raise ReleaseError("Schema downgrade is not supported")
    proposed = {row["key"]: row["sha256"] for row in plan}
    if len(proposed) != len(plan) or "BASELINE_V1" not in proposed:
        raise ReleaseError("Migration plan is incomplete or has duplicate keys")
    if max((version_number(key) for key in registered), default=0) != start:
        raise ReleaseError("Current database version differs from expected source")
    if max(version_number(key) for key in proposed if key.startswith("V")) != end:
        raise ReleaseError("Archive migration head differs from requested target")
    if any(key not in proposed or proposed[key] != digest for key, digest in prior.items()):
        raise ReleaseError("An applied migration is missing or has changed")
    first_increment = min(version_number(key) for key in proposed if key.startswith("V"))
    # Pre-baseline historical versions are attested by BASELINE_V1, not replayed.
    if "BASELINE_V1" not in prior or any(
        key not in prior for key in registered if version_number(key) >= first_increment
    ):
        raise ReleaseError("Registered migration lacks checksum evidence")
    if any(key.startswith("V") and key not in registered for key in prior):
        raise ReleaseError("Checksum ledger and registered migrations differ")
    pending = [row["key"] for row in plan if row["key"] not in prior]
    if any(not key.startswith("V") or not start < version_number(key) <= end for key in pending):
        raise ReleaseError("Upgrade contains an unreviewed baseline, seed or historical migration")
    return pending


def safe_path(name: str) -> str:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name:
        raise ReleaseError("Unsafe archive path")
    forbidden = {".git", ".env", ".npmrc", "id_rsa", "id_ed25519", "project.private.config.json"}
    if any(part in forbidden for part in path.parts) or path.suffix in {".pem", ".key", ".dump"}:
        raise ReleaseError("Private material is not permitted in release archives")
    return name


def unpack_archive(archive: Path, manifest: Path, commit: str, target: Path):
    meta = json.loads(manifest.read_text())
    if not re.fullmatch(r"[a-f0-9]{40}", commit) or meta["commit"] != commit:
        raise ReleaseError("Archive commit differs from requested revision")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != meta["sha256"]:
        raise ReleaseError("Archive checksum mismatch")
    expected = {safe_path(name): digest for name, digest in meta["file_hashes"].items()}
    required = {"backend/pyproject.toml", "backend/src/sales_backend/main.py", "database/scripts/migrate.py"}
    if not required <= expected.keys() or "REVISION" in expected:
        raise ReleaseError("Archive lacks required source files or includes generated revision")
    if target.exists():
        raise ReleaseError("Release directory already exists")
    with tarfile.open(archive) as source:
        members = source.getmembers()
        files = {}
        names = set()
        for member in members:
            name = safe_path(member.name.rstrip("/") if member.isdir() else member.name)
            if name in names or not (member.isfile() or member.isdir()):
                raise ReleaseError("Duplicate paths or special archive members are forbidden")
            names.add(name)
            if member.isfile():
                files[name] = member
        if files.keys() != expected.keys():
            raise ReleaseError("Archive files differ from tracked source manifest")
        for name, member in files.items():
            with source.extractfile(member) as stream:
                if hashlib.sha256(stream.read()).hexdigest() != expected[name]:
                    raise ReleaseError("Source file checksum mismatch")
        target.mkdir(mode=0o755)
        target.chmod(0o755)
        for name, member in files.items():
            destination = target / name
            directory = target
            for part in PurePosixPath(name).parent.parts:
                directory /= part
                directory.mkdir(exist_ok=True, mode=0o755)
                directory.chmod(0o755)
            # Explicit extraction ignores archive owners, links and special permissions.
            with source.extractfile(member) as stream:
                destination.write_bytes(stream.read())
            destination.chmod(0o755 if member.mode & 0o111 else 0o644)
        (target / "REVISION").write_text(commit + "\n")
        (target / "REVISION").chmod(0o644)
    return {"commit": commit, "verified_source_files": len(files)}


async def verify_database(plan, source, target):
    import asyncpg

    connection = await asyncpg.connect(
        host="/tmp", database="sales_saas", user="postgres", command_timeout=30  # noqa: S108 — PostgreSQL Unix socket
    )
    try:
        async with connection.transaction(readonly=True):
            if not await connection.fetchval("SELECT pg_try_advisory_xact_lock(202609102101)"):
                raise ReleaseError("Another database migration is running")
            prior = dict(await connection.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
            registered = {row["version"] for row in await connection.fetch("SELECT version FROM ops.schema_migration")}
            pending = verify_migration_plan(plan, prior, registered, source, target)
            running = await connection.fetchval("SELECT count(*) FROM ops.job WHERE status='running'")
            if running:
                raise ReleaseError("Running jobs must drain before release")
            role = await connection.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname='sales_runtime'")
            if not role or role["rolsuper"] or role["rolbypassrls"]:
                raise ReleaseError("Runtime role is missing or bypasses row security")
            return {"source_schema": source, "target_schema": target, "pending": pending,
                    "verified_checksums": len(prior), "running_jobs": running, "runtime_bypass_rls": False}
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    archive = sub.add_parser("unpack")
    for arg in ("archive", "manifest", "commit", "target"):
        archive.add_argument("--" + arg, required=True)
    database = sub.add_parser("verify-db", help="Read migration plan JSON from stdin; peer maintenance connection only")
    database.add_argument("--source", required=True)
    database.add_argument("--target", required=True)
    args = parser.parse_args()
    if args.action == "unpack":
        result = unpack_archive(Path(args.archive), Path(args.manifest), args.commit, Path(args.target))
    else:
        result = asyncio.run(verify_database(json.load(sys.stdin), args.source, args.target))
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except ReleaseError as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        # Never echo database errors, connection data or archive contents.
        raise SystemExit(f"Release verification failed ({type(exc).__name__})") from None
