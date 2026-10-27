"""Run history HTTP tests on the disposable, exact V278 managed-role fixture."""

import asyncio
import os
import sys

import verify_customer_draft_postgres as fixture


async def verify(config, name, roles):
    assert name.startswith("salegent_verify_integration_")
    env = {
        **os.environ,
        "SALES_TEST_DATABASE_URL": f"postgresql://{config['user']}@/{name}?host={config['host']}",
        "SALES_TEST_ROLE": roles["api"],
        "SALES_TEST_WORKSPACE": "demo-sales-workspace",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pytest",
        "tests/integration/test_opportunity_change_history.py",
        "-q",
        "-rs",
        "-p",
        "no:cacheprovider",
        cwd=fixture.ROOT / "backend",
        env=env,
    )
    if await process.wait():
        raise RuntimeError("Opportunity history managed PostgreSQL verification failed")


if __name__ == "__main__":
    fixture.verify = verify
    fixture.cluster.exercise = fixture.exercise
    fixture.cluster.main()
