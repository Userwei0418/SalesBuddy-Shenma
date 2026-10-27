#!/usr/bin/env python3
"""Run knowledge tests on an owned, disposable Unix-socket-only PostgreSQL cluster.

Never reads DATABASE_URL, a deployment profile, or an existing server. Use the
backend Python environment. Requires initdb/pg_ctl (or --pg-bin).
"""

import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[2]


async def initialize(socket, policy_output=None):
    spec = importlib.util.spec_from_file_location(
        "knowledge_migrate", ROOT / "database/scripts/migrate.py"
    )
    migration = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = migration
    spec.loader.exec_module(migration)
    connection = await asyncpg.connect(
        host=socket, port=55438, user="kb_owner", database="postgres"
    )
    try:
        results = await migration.migrate(connection)
        await connection.execute("SET row_security=on")
        # Test-only role grants. Registration is a fallback for the isolated Agent slice;
        # preserve Backend's real catalog/module definitions once shared wiring is present.
        await connection.execute("""
            CREATE ROLE kb_runtime LOGIN NOSUPERUSER NOBYPASSRLS;
            GRANT USAGE ON SCHEMA knowledge,security,common,ops,config TO kb_runtime;
            GRANT SELECT ON ALL TABLES IN SCHEMA knowledge TO kb_runtime;
            GRANT SELECT,INSERT ON ops.mutation_receipt TO kb_runtime;
            GRANT SELECT ON config.authorization_scope_revision TO kb_runtime;
            GRANT EXECUTE ON FUNCTION security.authorization_refresh(),security.authorization_snapshot(uuid),
              common.current_workspace_id(),common.current_user_ref_id(),
              security.knowledge_feature(text),security.knowledge_can(uuid,text),
              security.knowledge_file_read(uuid),security.knowledge_projection(uuid),security.knowledge_can_create(),
              security.knowledge_store_file(uuid,text,text,bytea),
              security.knowledge_save_catalog(integer,jsonb),security.knowledge_resolve(jsonb,uuid),
              security.knowledge_save_material(uuid,integer,jsonb),
              security.knowledge_save_acl(uuid,integer,jsonb,uuid[]),
              security.knowledge_work_can(uuid,boolean),
              security.knowledge_create_work(uuid,jsonb),
              security.knowledge_publish_work(uuid,integer,jsonb),
              security.knowledge_work_recipients(uuid),
              security.knowledge_work_candidates(uuid,text,text,integer,integer),
              security.knowledge_self_teams(),
              security.knowledge_lock_work_parent(uuid,integer),
              security.knowledge_distribution_enqueue(uuid,uuid,uuid,uuid,text,integer,uuid),
              security.kd_admin(),security.kd_sales_name(uuid),security.kd_publication_visible(uuid),security.kd_publish(uuid,jsonb),
              security.kd_create_share(uuid,uuid,text),security.kd_revoke(uuid,boolean),
              security.kd_public_metadata(text,text),security.kd_public_response(text,text,uuid,text,integer,jsonb,text),
              security.kd_public_file(text,text,uuid,text) TO kb_runtime;
            INSERT INTO control.module_catalog(code,label,default_enabled) VALUES('knowledge','Knowledge test',false)
              ON CONFLICT(code) DO NOTHING;
            INSERT INTO config.permission_catalog(code,label,module,scopes)
              SELECT 'knowledge.'||action,action,'knowledge',CASE WHEN action='catalog_manage'
                THEN ARRAY['workspace'] ELSE ARRAY['self','assigned','teams','workspace'] END
              FROM unnest(ARRAY['read','create','edit','manage','catalog_manage']) action
              ON CONFLICT(code) DO NOTHING;
            INSERT INTO control.permission_module(permission_code,module_code)
              SELECT code,'knowledge' FROM config.permission_catalog WHERE module='knowledge'
              ON CONFLICT(permission_code,module_code) DO NOTHING;
        """)
        if policy_output:
            sys.path.insert(0, str(ROOT / "tools/installation"))
            from runtime_roles import SCHEMAS, fingerprint

            tables = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT n.nspname||'.'||c.relname AS name,c.relrowsecurity,c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1",
                    SCHEMAS,
                )
            ]
            policies = [
                dict(r)
                for r in await connection.fetch(
                    "SELECT schemaname,tablename,policyname,permissive,roles::text[],cmd,qual,with_check "
                    "FROM pg_policies WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3",
                    SCHEMAS,
                )
            ]
            for row in policies:
                row["roles"] = sorted(row["roles"])
            ledger = dict(
                await connection.fetch(
                    "SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"
                )
            )
            policy_output.write_text(
                json.dumps(
                    {
                        "schema": results[-1]["key"],
                        "migration_fingerprint": fingerprint(ledger),
                        "rls_fingerprint": fingerprint(
                            {"tables": tables, "policies": policies}
                        ),
                        "migration_count": len(ledger),
                        "rls_table_count": len(tables),
                    },
                    indent=2,
                )
                + "\n"
            )
        print(
            json.dumps(
                {
                    "migrations": len(results),
                    "last": results[-1],
                    "private_pg": True,
                    "runtime_superuser": False,
                    "runtime_bypassrls": False,
                }
            ),
            flush=True,
        )
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pg-bin", type=Path, default=Path("/opt/homebrew/opt/postgresql@16/bin")
    )
    parser.add_argument("--pytest-args", nargs="*", default=[])
    parser.add_argument(
        "--policy-output",
        type=Path,
        help="Write non-sensitive migrated ledger/RLS fingerprints",
    )
    args = parser.parse_args()
    # An explicit minimal subprocess environment prevents ambient deployment variables being inherited.
    env = {
        name: os.environ[name]
        for name in ("PATH", "LANG", "LC_ALL", "TMPDIR")
        if name in os.environ
    }
    with tempfile.TemporaryDirectory(prefix="sb-knowledge-", dir="/tmp") as temp:
        base = Path(temp)
        socket = base / "socket"
        socket.mkdir(mode=0o700)
        (base / "PRIVATE_KNOWLEDGE_TEST").write_text("disposable-local-only\n")
        subprocess.run(  # noqa: S603 -- fixed local tools; no shell or remote target
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
            subprocess.run(  # noqa: S603 -- fixed local tools; no shell or remote target
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
            asyncio.run(initialize(str(socket), args.policy_output))
            env["KNOWLEDGE_TEST_CLUSTER"] = str(base)
            result = subprocess.run(  # noqa: S603 -- fixed local tools; no shell or remote target
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "--tb=short",
                    "tests/integration/test_knowledge_persistence.py",
                    "tests/integration/test_knowledge_api.py",
                    "tests/integration/test_knowledge_works.py",
                    "tests/integration/test_knowledge_distribution.py",
                    "tests/integration/test_knowledge_distribution_feishu.py",
                    *args.pytest_args,
                ],
                cwd=ROOT / "backend",
                env=env,
                check=False,
            )
        finally:
            if started:
                subprocess.run(  # noqa: S603 -- fixed local tools; no shell or remote target
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
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
