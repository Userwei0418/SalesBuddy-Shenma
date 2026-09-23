"""Run a pinned older backend's SQL/API suite against current migrated test schema.

The shared runner creates and drops a disposable database with baseline company
policies. This does not prove compatibility after custom company rules publish.
"""

import argparse
import asyncio
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from run_integration_postgres import main


async def verify(config, name, role, legacy_root, commit):
    source = legacy_root / "backend/src"
    tests = legacy_root / "backend/tests/integration"
    assert source.is_dir() and tests.is_dir()
    assert (legacy_root / "REVISION").read_text().strip() == commit
    env = {
        **os.environ,
        "SALES_TEST_DATABASE_URL": f"postgresql://{config['user']}@/{name}?host={config['host']}",
        "SALES_TEST_ROLE": role,
        "SALES_TEST_WORKSPACE": "demo-sales-workspace",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(source),
    }
    # A venv may contain an editable installation. Assert import ownership before
    # accepting the old suite as evidence about the old application.
    probe = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sales_backend,pathlib,sys; "
        "p=pathlib.Path(sales_backend.__file__).resolve(); "
        "assert p.is_relative_to(pathlib.Path(sys.argv[1]).resolve()), p; print('LEGACY_IMPORT_VERIFIED',p)",
        str(source),
        env=env,
        cwd=str(legacy_root / "backend"),
    )
    if await probe.wait():
        raise RuntimeError("legacy source import verification failed")
    print("LEGACY_SOURCE_COMMIT=" + commit, flush=True)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pytest",
        str(tests),
        "-q",
        "-p",
        "no:cacheprovider",
        env=env,
        cwd=str(legacy_root / "backend"),
    )
    if await process.wait():
        raise RuntimeError("legacy application is not verified against the new schema")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument(
        "--adapt-rule-fixture",
        action="store_true",
        help="Use V060 baseline instead of the old sales-owned rule INSERT; keep all business assertions",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{40}", args.commit):
        parser.error("commit must be a full Git SHA")
    with tempfile.TemporaryDirectory(prefix="salegent-legacy-suite-") as temporary:
        target = args.legacy_root.resolve()
        if args.adapt_rule_fixture:
            target = Path(temporary) / "source"
            shutil.copytree(args.legacy_root, target)
            test = target / "backend/tests/integration/test_battle_map_platform.py"
            content = test.read_text()
            setup = """    await connection.execute(
        "INSERT INTO config.rule_set(workspace_id,rule_code,name,rule_type,version_no,status,definition) "
        "VALUES($1::uuid,'customer_quadrant','隔离象限规则','scoring',1,'active','{}'::jsonb)",
        sales_actor.workspace_id,
    )"""
            if content.count(setup) != 1:
                raise RuntimeError("legacy rule fixture changed; manual review required")
            replacement = (
                "    assert await connection.fetchval(\"SELECT security.active_company_rule('customer_quadrant')\")"
            )
            test.write_text(content.replace(setup, replacement))
            print(
                "FIXTURE_ONLY_ADAPTATION: removed sales rule INSERT; "
                "V060 baseline required; business assertions unchanged",
                flush=True,
            )
        asyncio.run(main(lambda config, name, role: verify(config, name, role, target, args.commit)))
