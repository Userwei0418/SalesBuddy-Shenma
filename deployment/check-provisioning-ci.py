"""Validate exact customer grants and bootstrap against a disposable CI database."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "database/scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))
from migrate import migrate
from sales_backend.auth.passwords import verify_password


async def main():
    if os.environ.get("CI") != "true" or os.environ.get("PGHOST") != "127.0.0.1":
        raise SystemExit(
            "This check only runs inside the disposable GitHub CI environment"
        )
    settings = {"host": "127.0.0.1", "user": "postgres"}
    admin = await asyncpg.connect(database="postgres", **settings)
    name = "shenma_provision_ci_" + uuid4().hex[:12]
    created = False
    c = None
    try:
        if await admin.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='shenma_runtime')"
        ):
            raise RuntimeError(
                "Customer runtime role already exists; refusing shared environment"
            )
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **settings)
        await migrate(c)
        await admin.execute(
            "CREATE ROLE shenma_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
        )
        created = True
        await c.execute((ROOT / "deployment/runtime-grants.sql").read_text())
        spec = importlib.util.spec_from_file_location(
            "customer_bootstrap", ROOT / "deployment/bootstrap-customer.py"
        )
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        with tempfile.TemporaryDirectory(prefix="shenma-provision-check-") as temp:
            receipt = Path(temp) / "initial-admin.json"
            await bootstrap.bootstrap(c, receipt)
            assert receipt.stat().st_mode & 0o777 == 0o600
            credentials = json.loads(receipt.read_text())
            row = await c.fetchrow(
                "SELECT p.password_hash,p.must_change_password,u.account_code FROM platform.password_credential p JOIN platform.user_ref u ON u.id=p.user_ref_id"
            )
            assert (
                row["account_code"] == "CUSTOMERADMIN" and row["must_change_password"]
            )
            assert verify_password(credentials["password"], row["password_hash"])
            for table in ("crm.customer", "crm.opportunity", "activity.visit"):
                assert await c.fetchval(f"SELECT count(*) FROM {table}") == 0
            try:
                await bootstrap.bootstrap(c, receipt)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Bootstrap allowed overwrite")
        async with c.transaction():
            await c.execute("SET LOCAL ROLE shenma_runtime")
            flags = await c.fetchrow(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            assert not flags["rolsuper"] and not flags["rolbypassrls"]
            for table in ("platform.password_credential", "security.login_throttle"):
                assert not await c.fetchval(
                    "SELECT has_table_privilege(current_user,$1,'SELECT')", table
                )
            assert not await c.fetchval(
                "SELECT has_function_privilege(current_user,'security.reconcile_runtime_grants()','EXECUTE')"
            )
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM security.password_login_identifier('shenzhoukuntai','CUSTOMERADMIN')"
                )
                == 1
            )
        print(
            "Customer runtime grants, empty business data, bootstrap password and overwrite guards passed"
        )
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if created:
            await admin.execute("DROP ROLE shenma_runtime")
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
