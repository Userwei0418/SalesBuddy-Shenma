"""Verify cancellation projections and lifecycle in a disposable RLS database."""

import asyncio
import os
import sys

from run_integration_postgres import ROOT, main


async def verify(config, name, role):
    assert name.startswith("salegent_verify_integration_")
    env = {**os.environ,
           "SALES_TEST_DATABASE_URL": f"postgresql://{config['user']}@/{name}?host={config['host']}",
           "SALES_TEST_ROLE": role, "SALES_TEST_WORKSPACE": "demo-sales-workspace",
           "PYTHONDONTWRITEBYTECODE": "1"}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", "tests/integration/test_task_cancellation.py",
        "tests/integration/test_task_completion_review.py", "tests/integration/test_authorization_tasks.py",
        "tests/integration/test_task_browse_pages.py", "tests/integration/test_task_reassignment_options.py",
        "-q", "-rs", "-p", "no:cacheprovider", cwd=str(ROOT / "backend"), env=env,
    )
    if await process.wait():
        raise RuntimeError("Task cancellation integration verification failed")


if __name__ == "__main__":
    asyncio.run(main(serve=verify))
