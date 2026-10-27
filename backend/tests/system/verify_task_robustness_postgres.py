"""Task qualification, explicit transfer and existing workflows in a disposable DB."""

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
    suites = ["test_task_current_owner_eligibility", "test_task_transfer_evidence", "test_task_cancellation",
              "test_task_completion_review", "test_authorization_tasks", "test_task_browse_pages",
              "test_task_reassignment_options", "test_fde_identity_tasks", "test_position_tasks"]
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", *(f"tests/integration/{suite}.py" for suite in suites),
        "-q", "-rs", "-p", "no:cacheprovider", cwd=str(ROOT / "backend"), env=env,
    )
    if await process.wait():
        raise RuntimeError("Task robustness integration verification failed")


if __name__ == "__main__":
    asyncio.run(main(serve=verify))
