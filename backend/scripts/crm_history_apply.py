"""Explicit reviewed manifest staging/application; no implicit environment config."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sales_backend.services.crm_history_apply import CrmHistoryApply


async def run(args):
    manifest = json.loads(args.manifest.read_text())
    connection = await asyncpg.connect(host=args.host, port=args.port, user=args.user, database=args.database)
    try:
        for name in ("json", "jsonb"):
            await connection.set_type_codec(name, schema="pg_catalog", encoder=json.dumps, decoder=json.loads)
        service = CrmHistoryApply(
            connection, workspace_id=args.workspace, actor_user_id=args.actor, actor_role=args.role
        )
        method = service.apply if args.apply else service.stage
        report = await method(manifest, approved_sha256=args.approved_sha256)
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
        print(json.dumps({key: value for key, value in report.items() if key != "records"}, ensure_ascii=False))
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approved-sha256", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--role", choices=("operations", "administrator"), required=True)
    parser.add_argument("--host", required=True, help="Explicit PostgreSQL host/socket; no DATABASE_URL loading")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True, help="Existing runtime role without superuser/BYPASSRLS")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Write eligible business objects; default stages evidence only"
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
