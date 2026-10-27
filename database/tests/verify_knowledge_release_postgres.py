#!/usr/bin/env python3
"""Verify the checked-in installation policy on disposable, socket-only PG16.

Uses the exact four-role product installer, without test-only grants or policy
rewrites. --pytest-file may point to a private acceptance file outside the repo.
Never reads DATABASE_URL, deployment profiles, or an existing server.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
ROLES = {
    "api": "kb_runtime",
    "worker": "kb_worker",
    "platform": "kb_platform",
    "feishu": "kb_feishu",
}
DEFAULT_TESTS = (
    "tests/integration/test_opportunity_original_access.py",
    "tests/integration/test_knowledge_source_files.py",
    "tests/integration/test_knowledge_import.py",
    "tests/integration/test_knowledge_pdf_preview.py",
    "tests/integration/test_knowledge_distribution.py",
    "tests/integration/test_knowledge_distribution_boundaries.py",
    "tests/integration/test_knowledge_distribution_feishu.py",
    "tests/integration/test_knowledge_notification_intent.py",
)


async def initialize(socket, output):
    sys.path.insert(0, str(ROOT / "tools/installation"))
    import runtime_roles

    spec = importlib.util.spec_from_file_location(
        "release_knowledge_migrate", ROOT / "database/scripts/migrate.py"
    )
    migration = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = migration
    spec.loader.exec_module(migration)
    connection = await asyncpg.connect(
        host=str(socket), port=55438, user="kb_owner", database="postgres"
    )
    try:
        migrations = await migration.migrate(connection)
        policy_path = ROOT / "tools/installation/runtime_permissions.json"
        policy = runtime_roles.load_policy(policy_path)
        ledger = dict(
            await connection.fetch(
                "SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"
            )
        )
        if policy["schema"] != migrations[-1]["key"] or policy[
            "migration_fingerprint"
        ] != runtime_roles.fingerprint(ledger):
            raise RuntimeError(
                "Checked-in policy does not match the complete migration ledger"
            )
        await runtime_roles.provision_new(
            connection, database="postgres", roles=ROLES, policy=policy
        )
        verified = await runtime_roles.verify(connection, ROLES, policy)
        predecessor = "security.knowledge_import_before_source_files(uuid,uuid,jsonb,text,text,text,bigint,jsonb,boolean)"
        private_execute = {
            role: await connection.fetchval(
                "SELECT has_function_privilege($1,$2,'EXECUTE')", role, predecessor
            )
            for role in ROLES.values()
        }
        if any(private_execute.values()):
            raise RuntimeError(
                "Runtime role can execute the private import predecessor"
            )
        report = {
            "policy_source": "tools/installation/runtime_permissions.json; unmodified",
            "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
            "migration_count": len(migrations),
            "schema": migrations[-1]["key"],
            "fresh_registered_count": await connection.fetchval(
                "SELECT count(*) FROM ops.schema_migration"
            ),
            "migration_fingerprint": policy["migration_fingerprint"],
            "rls_fingerprint": policy["rls_fingerprint"],
            "installed_roles": verified,
            "role_flags": [
                dict(row)
                for row in await connection.fetch(
                    "SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=ANY($1::text[]) ORDER BY rolname",
                    list(ROLES.values()),
                )
            ],
            "private_predecessor_execute": private_execute,
            "test_only_grants": False,
            "temporary_policy": False,
            "private_unix_socket_only": True,
        }
        (output / "INSTALLED_POLICY.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(report), flush=True)
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pg-bin", type=Path, default=Path("/opt/homebrew/opt/postgresql@16/bin")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--pytest-file",
        action="append",
        help="Repeat to select test files; defaults to knowledge regressions",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    tests = args.pytest_file or list(DEFAULT_TESTS)
    for name in tests:
        path = Path(name)
        if not (path if path.is_absolute() else ROOT / "backend" / path).is_file():
            parser.error(f"Test file does not exist: {name}")
    # Keep only non-sensitive process settings; no app/provider/deployment env is inherited.
    env = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "TMPDIR")
        if key in os.environ
    }
    with tempfile.TemporaryDirectory(
        prefix="sb-knowledge-release-", dir="/tmp"
    ) as temp:
        base = Path(temp)
        socket = base / "socket"
        socket.mkdir(mode=0o700)
        (base / "PRIVATE_KNOWLEDGE_TEST").write_text("disposable-local-only\n")
        subprocess.run(
            [
                str(args.pg_bin / "initdb"),
                "-D",
                str(base / "pg"),
                "-U",
                "kb_owner",
                "-A",
                "trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            check=True,
            env=env,
            capture_output=True,
        )
        started = False
        try:
            subprocess.run(
                [
                    str(args.pg_bin / "pg_ctl"),
                    "-D",
                    str(base / "pg"),
                    "-l",
                    str(base / "postgres.log"),
                    "-o",
                    f"-k {socket} -p 55438 -c listen_addresses='' -c unix_socket_permissions=0700",
                    "-w",
                    "start",
                ],
                check=True,
                env=env,
                capture_output=True,
            )
            started = True
            asyncio.run(initialize(socket, output))
            env["KNOWLEDGE_TEST_CLUSTER"] = str(base)
            env["PYTHONPATH"] = str(ROOT / "backend/src") + ":" + str(ROOT / "backend")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=short", *tests],
                cwd=ROOT / "backend",
                env=env,
                check=False,
            )
            (output / "TEST_RESULT.json").write_text(
                json.dumps(
                    {
                        "pytest_files": tests,
                        "exit_code": result.returncode,
                        "source_root": str(ROOT),
                        "production_accessed": False,
                        "shared_local_accessed": False,
                    },
                    indent=2,
                )
                + "\n"
            )
            return result.returncode
        finally:
            if started:
                subprocess.run(
                    [
                        str(args.pg_bin / "pg_ctl"),
                        "-D",
                        str(base / "pg"),
                        "-m",
                        "immediate",
                        "-w",
                        "stop",
                    ],
                    check=True,
                    env=env,
                    capture_output=True,
                )


if __name__ == "__main__":
    raise SystemExit(main())
