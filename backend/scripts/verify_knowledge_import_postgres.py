"""Isolated Unix-socket PostgreSQL acceptance for source imports only.

No deployment environment/profile/database is read. Synthetic fixtures only.
"""

import argparse
import asyncio
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
PG = Path("/opt/homebrew/opt/postgresql@16/bin")


async def initialize(socket, policy_output=None):
    spec = importlib.util.spec_from_file_location(
        "private_kb_runner", ROOT / "database/tests/verify_knowledge_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    await module.initialize(socket, policy_output)
    conn = await asyncpg.connect(host=socket, port=55438, database="postgres", user="kb_owner")
    try:
        await conn.execute("""GRANT EXECUTE ON FUNCTION
          security.knowledge_import_authorize(uuid,uuid,jsonb),
          security.knowledge_import_record(uuid,uuid,jsonb,text,text,text,bigint,jsonb,boolean),
          security.knowledge_bind_provider(uuid,uuid,text,text) TO kb_runtime""")
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-output", type=Path)
    args, pytest_args = parser.parse_known_args()
    env = {name: os.environ[name] for name in ("PATH", "LANG", "LC_ALL", "TMPDIR") if name in os.environ}
    with tempfile.TemporaryDirectory(prefix="sb-knowledge-", dir="/tmp") as temp:
        base = Path(temp)
        socket = base / "socket"
        socket.mkdir(mode=0o700)
        (base / "PRIVATE_KNOWLEDGE_TEST").write_text("disposable-local-only\n")
        subprocess.run(  # noqa: S603 -- fixed local PostgreSQL binary, no shell or deployment connection
            [str(PG / "initdb"), "-D", str(base / "pg"), "-U", "kb_owner", "-A", "trust", "--no-locale", "-E", "UTF8"],
            check=True,
            capture_output=True,
            env=env,
        )
        started = False
        try:
            subprocess.run(  # noqa: S603 -- owned disposable cluster only
                [
                    str(PG / "pg_ctl"),
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
                capture_output=True,
                env=env,
            )
            started = True
            asyncio.run(initialize(str(socket), args.policy_output))
            env["KNOWLEDGE_TEST_CLUSTER"] = str(base)
            result = subprocess.run(  # noqa: S603 -- current interpreter and explicit pytest arguments
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "--tb=short",
                    "tests/integration/test_knowledge_import.py",
                    *pytest_args,
                ],
                cwd=ROOT / "backend",
                env=env,
                check=False,
            )
            return result.returncode
        finally:
            if started:
                subprocess.run(  # noqa: S603 -- stops only this owned disposable cluster
                    [str(PG / "pg_ctl"), "-D", str(base / "pg"), "-m", "immediate", "-w", "stop"],
                    check=True,
                    capture_output=True,
                    env=env,
                )


if __name__ == "__main__":
    raise SystemExit(main())
